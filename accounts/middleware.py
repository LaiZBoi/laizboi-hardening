"""
2FA enforcement middleware and language middleware
"""
import time
from django.contrib.auth import logout
from django.shortcuts import redirect
from django.urls import reverse
from django.conf import settings
from django.utils import translation
from django_otp import user_has_device


class SessionIdleTimeoutMiddleware:
    """
    Expire authenticated sessions after a period of inactivity.

    Idle timeout is controlled by SESSION_IDLE_TIMEOUT (seconds, default 3600).
    On expiry the user is logged out and redirected to the login page.
    """

    EXEMPT_PATHS = ['/account/login/', '/account/logout/', '/static/', '/media/']

    def __init__(self, get_response):
        self.get_response = get_response
        self.idle_timeout = getattr(settings, 'SESSION_IDLE_TIMEOUT', 3600)

    def __call__(self, request):
        if request.user.is_authenticated and not any(
            request.path.startswith(p) for p in self.EXEMPT_PATHS
        ):
            now = time.time()
            last_activity = request.session.get('_last_activity')

            if last_activity and (now - last_activity) > self.idle_timeout:
                logout(request)
                login_url = getattr(settings, 'LOGIN_URL', '/account/login/')
                return redirect(f'{login_url}?next={request.path}')

            request.session['_last_activity'] = now

        return self.get_response(request)


class UserLanguageMiddleware:
    """
    Activate the UI language saved in the user's profile.

    Runs after Enforce2FAMiddleware (and after LocaleMiddleware) so the
    authenticated user's preference always wins over the Accept-Language header.
    Language is deactivated at the end of each request so it doesn't leak
    across requests on the same thread.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            try:
                lang = request.user.profile.locale  # e.g. 'fr', 'pt-br'
                translation.activate(lang)
                request.LANGUAGE_CODE = lang
            except Exception:
                pass
        response = self.get_response(request)
        translation.deactivate()
        return response


class Enforce2FAMiddleware:
    """
    Enforce or encourage 2FA enrollment for users.

    When REQUIRE_2FA=True: Forces 2FA enrollment (cannot be skipped)
    When REQUIRE_2FA=False: Prompts for 2FA enrollment once per session (can be dismissed)
    """
    ALLOWED_PATHS = [
        '/account/login/',
        '/account/logout/',
        '/account/two_factor/',
        '/admin/login/',
        '/static/',
        '/media/',
    ]

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Skip for unauthenticated users
        if not request.user.is_authenticated:
            return self.get_response(request)

        # Skip for allowed paths
        if any(request.path.startswith(path) for path in self.ALLOWED_PATHS):
            return self.get_response(request)

        # Skip 2FA for Azure AD authenticated users (SSO is already secure)
        # FIX: Add audit logging for 2FA bypass
        if request.session.get('azure_ad_authenticated', False):
            # Log Azure AD 2FA bypass (once per session to avoid spam)
            if not request.session.get('azure_2fa_bypass_logged', False):
                from audit.models import AuditLog
                try:
                    AuditLog.objects.create(
                        user=request.user,
                        username=request.user.username,
                        action='azure_ad_2fa_bypass',
                        description=f'User authenticated via Azure AD SSO - 2FA requirement bypassed',
                        ip_address=request.META.get('REMOTE_ADDR'),
                        user_agent=request.META.get('HTTP_USER_AGENT', '')[:255]
                    )
                    request.session['azure_2fa_bypass_logged'] = True
                except Exception:
                    pass  # Don't break authentication flow if logging fails

            return self.get_response(request)

        # Check if user has 2FA device configured
        if not user_has_device(request.user):
            setup_url = reverse('two_factor:setup')

            if settings.REQUIRE_2FA:
                # REQUIRED: Force redirect to 2FA setup (cannot skip)
                if request.path != setup_url:
                    return redirect(setup_url)
            else:
                # OPTIONAL: Prompt once per session, can be dismissed
                # Check if we've already prompted in this session
                if not request.session.get('2fa_prompted', False):
                    # Mark as prompted so we don't keep redirecting
                    request.session['2fa_prompted'] = True

                    # Only redirect if not already on setup page
                    if request.path != setup_url:
                        # Add a flag to indicate this is optional
                        request.session['2fa_optional'] = True
                        return redirect(setup_url)

        response = self.get_response(request)
        return response
