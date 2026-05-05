"""
Browser-extension API endpoints (Phase 28).

These views are the server-side surface for the WebExtension binary
(separate codebase, distributed via Chrome / Firefox / Edge stores). They
all use bearer-token auth except token-issue / token-list / token-revoke
which require the user's Django session (the extension can only get a
token by being signed in to the app first).
"""
import json

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from audit.models import AuditLog
from core.middleware import get_request_organization
from .models import WebExtensionAuthToken


# ---------------------------------------------------------------------------
# Token lifecycle — session-authed (the user issues / revokes from the app)
# ---------------------------------------------------------------------------


@login_required
@require_http_methods(['POST'])
def token_issue(request):
    """
    Issue a new browser-extension auth token for the current user.

    POST body (JSON or form-encoded):
      * label (str, optional) — user-friendly label
      * organization_id (int, optional) — pin to a specific org
      * ttl_days (int, optional) — override default TTL (capped at 365)

    Returns the token string in the response body — the **only** time the
    server will ever surface the secret. If the user loses it, they revoke
    and reissue.
    """
    payload = _parse_body(request)
    label = (payload.get('label') or '')[:120]
    org_id = payload.get('organization_id') or None
    ttl_days = payload.get('ttl_days')
    try:
        ttl_days = int(ttl_days) if ttl_days else None
    except (TypeError, ValueError):
        ttl_days = None
    if ttl_days is not None:
        ttl_days = max(1, min(ttl_days, 365))

    organization = None
    if org_id:
        from core.models import Organization
        try:
            organization = Organization.objects.get(id=int(org_id), is_active=True)
        except (Organization.DoesNotExist, TypeError, ValueError):
            return JsonResponse({'error': 'Invalid organization_id.'}, status=400)
        if not _user_has_org_access(request.user, organization):
            return JsonResponse({'error': 'Forbidden.'}, status=403)

    token_str, row = WebExtensionAuthToken.issue(
        user=request.user,
        organization=organization,
        label=label,
        ttl_days=ttl_days,
    )

    AuditLog.log(
        user=request.user,
        action='create',
        organization=organization,
        object_type='vault.WebExtensionAuthToken',
        object_id=row.pk,
        object_repr=row.label or 'extension token',
        description='Issued browser-extension auth token',
        ip_address=request.META.get('REMOTE_ADDR'),
        user_agent=request.META.get('HTTP_USER_AGENT', '')[:255],
    )

    return JsonResponse({
        'id': row.pk,
        'token': token_str,
        'label': row.label,
        'organization_id': row.organization_id,
        'expires_at': row.expires_at.isoformat() if row.expires_at else None,
        'created_at': row.created_at.isoformat(),
    }, status=201)


@login_required
@require_http_methods(['GET'])
def token_list(request):
    """List the calling user's extension tokens (no secret material)."""
    rows = (WebExtensionAuthToken.objects
            .filter(user=request.user)
            .order_by('-created_at'))
    return JsonResponse({
        'tokens': [
            {
                'id': r.pk,
                'label': r.label,
                'organization_id': r.organization_id,
                'created_at': r.created_at.isoformat(),
                'last_used_at': r.last_used_at.isoformat() if r.last_used_at else None,
                'expires_at': r.expires_at.isoformat() if r.expires_at else None,
                'revoked_at': r.revoked_at.isoformat() if r.revoked_at else None,
                'is_active': r.is_active,
            }
            for r in rows
        ],
    })


@login_required
@require_http_methods(['DELETE', 'POST'])
def token_revoke(request, pk):
    """
    Revoke a token. Accepts DELETE (preferred) or POST (for form fallback).
    Only the token's owner — or a superuser — can revoke.
    """
    try:
        row = WebExtensionAuthToken.objects.get(pk=pk)
    except WebExtensionAuthToken.DoesNotExist:
        return JsonResponse({'error': 'Token not found.'}, status=404)
    if row.user_id != request.user.pk and not request.user.is_superuser:
        return JsonResponse({'error': 'Forbidden.'}, status=403)
    if row.revoked_at is None:
        row.revoke()
        AuditLog.log(
            user=request.user,
            action='delete',
            organization=row.organization,
            object_type='vault.WebExtensionAuthToken',
            object_id=row.pk,
            object_repr=row.label or 'extension token',
            description='Revoked browser-extension auth token',
            ip_address=request.META.get('REMOTE_ADDR'),
        )
    return JsonResponse({'id': row.pk, 'revoked': True})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_body(request):
    """Best-effort: JSON body, fall back to form POST."""
    if request.content_type and 'application/json' in request.content_type:
        try:
            return json.loads(request.body or b'{}')
        except (ValueError, json.JSONDecodeError):
            return {}
    return request.POST.dict() if request.method == 'POST' else {}


def _user_has_org_access(user, organization):
    """True if the user is a superuser or has an active membership in `organization`."""
    if user.is_superuser:
        return True
    if hasattr(user, 'profile') and getattr(user.profile, 'is_staff_user', lambda: False)():
        return True
    return user.memberships.filter(organization=organization, is_active=True).exists()
