"""
Feature-flag helpers for the native PSA module.

PSA is OFF by default at both the system and per-client level. Routes,
background jobs, and navigation must consult these helpers and return
404/PermissionDenied when disabled — never silently expose data.
"""
from functools import wraps

from django.http import Http404
from django.shortcuts import redirect


def is_psa_enabled():
    """True if the PSA feature is globally enabled in SystemSetting."""
    try:
        from core.models import SystemSetting
        settings = SystemSetting.get_settings()
        return bool(getattr(settings, 'psa_enabled', False))
    except Exception:
        # If settings can't be loaded for any reason, fail closed.
        return False


def is_psa_enabled_for_client(organization):
    """
    True if PSA is globally enabled AND not explicitly opted out for this
    organization.

    UX rule: when the system-wide flag is on, every client is enabled by
    default. Admins can still opt a specific client OUT via the per-client
    settings page (`ClientPSASettings.enabled = False`). The per-surface
    flags (portal_enabled, sms_notifications_enabled, etc.) remain OFF
    by default — those are the truly sensitive ones.
    """
    if not is_psa_enabled():
        return False
    if organization is None:
        return False
    try:
        from psa.models import ClientPSASettings
        cps = ClientPSASettings.objects.filter(organization=organization).first()
        if cps is None:
            # No explicit row → inherit "enabled" from the global flag.
            return True
        return bool(cps.enabled)
    except Exception:
        return False


def enable_psa_for_all_clients():
    """
    Bulk-enable PSA on every existing ClientPSASettings row, and create a
    row for any organization that doesn't have one yet.

    Called from the Settings → Features page when the global flag flips
    False → True so admins don't have to walk every client manually.
    Per-surface flags (portal, SMS, etc.) are NOT touched — they stay OFF.

    Returns the number of ClientPSASettings rows touched (created + updated).
    """
    try:
        from core.models import Organization
        from psa.models import ClientPSASettings
    except Exception:
        return 0

    touched = 0
    # Update any existing rows that are explicitly disabled.
    touched += ClientPSASettings.objects.filter(enabled=False).update(enabled=True)

    # Create rows for orgs that don't have one yet (defence-in-depth — the
    # lazy default in is_psa_enabled_for_client also covers this).
    existing_org_ids = set(ClientPSASettings.objects.values_list('organization_id', flat=True))
    missing = Organization.objects.exclude(id__in=existing_org_ids)
    for org in missing:
        ClientPSASettings.objects.create(organization=org, enabled=True)
        touched += 1

    return touched


def require_psa_enabled(view_func):
    """
    View decorator: 404 if PSA is globally disabled.
    Use on every PSA route. Pair with @login_required first.
    """
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not is_psa_enabled():
            raise Http404("PSA is not enabled")
        return view_func(request, *args, **kwargs)
    return wrapper


def require_client_psa_enabled(view_func):
    """
    View decorator: 404 if PSA is disabled globally OR for the active
    organization on the request. Use on routes that operate within a
    client/org context.
    """
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not is_psa_enabled():
            raise Http404("PSA is not enabled")
        org = getattr(request, 'current_organization', None)
        if not is_psa_enabled_for_client(org):
            raise Http404("PSA is not enabled for this client")
        return view_func(request, *args, **kwargs)
    return wrapper
