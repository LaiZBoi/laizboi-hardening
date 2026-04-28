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


def client_has_external_psa(organization):
    """
    True if this organization is already managed by a third-party PSA
    integration (ConnectWise, Halo, Autotask, etc.) via integrations.PSAConnection.

    The native PSA is intended for clients WITHOUT another PSA — having an
    active external connection is the natural opt-out signal.
    """
    if organization is None:
        return False
    try:
        from integrations.models import PSAConnection
    except Exception:
        return False
    try:
        return PSAConnection.objects.filter(
            organization=organization,
            is_active=True,
        ).exists()
    except Exception:
        return False


def get_external_psa_summary(organization):
    """
    Return a human-readable summary of the external PSA(s) connected to this
    org, e.g. "ConnectWise Manage". Used for the client-settings page banner.
    Empty string if none.
    """
    if organization is None:
        return ''
    try:
        from integrations.models import PSAConnection
    except Exception:
        return ''
    try:
        names = list(
            PSAConnection.objects
            .filter(organization=organization, is_active=True)
            .values_list('provider_type', 'name')
        )
    except Exception:
        return ''
    if not names:
        return ''
    return ', '.join(f'{name or provider} ({provider})' for provider, name in names)


def is_psa_enabled_for_client(organization):
    """
    Decide whether the native PSA is active for `organization`.

    Resolution order (first match wins):
      1. Global flag off                         → False
      2. No organization                         → False
      3. Explicit ClientPSASettings row exists   → return its `enabled` field
         (admin override — beats everything else)
      4. Active external PSAConnection exists    → False (auto opt-out;
         this client is managed by another PSA)
      5. Otherwise                               → True (cascade default)

    Per-surface flags (portal, SMS, desktop alerts, anonymous form,
    email-to-ticket, external alert ingest) are independent from this
    helper and remain OFF by default at the model level.
    """
    if not is_psa_enabled():
        return False
    if organization is None:
        return False
    try:
        from psa.models import ClientPSASettings
        cps = ClientPSASettings.objects.filter(organization=organization).first()
    except Exception:
        return False
    if cps is not None:
        # Admin made an explicit choice — respect it absolutely. This is how
        # an admin force-enables native PSA even for a client that also has
        # an external PSA connection (rare, but supported).
        return bool(cps.enabled)
    # No row → auto. Skip if the client is already on an external PSA.
    if client_has_external_psa(organization):
        return False
    return True


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
