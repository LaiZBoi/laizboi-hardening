"""
Permission resolvers for AI suggestions.

Maps the user's role / RoleTemplate booleans onto:
  * can_view_suggestion(user, suggestion)
  * can_send_reply(user, suggestion)            — Phase 10b: writes the reply as a public TicketComment
  * can_apply_action(user, suggestion)          — gated by risk_level
  * can_approve_others(user, suggestion)        — for the approval flow

The granular RoleTemplate booleans (psa_ai_send_low_risk, etc.) are
resolved best-effort with a sane fallback: if the boolean isn't yet in
the schema, fall back to `Membership.can_admin()` / `can_write()`.

This lets us ship 10b/10c without first running a migration on every
production install — the new RoleTemplate fields land as a separate,
optional commit.
"""
from __future__ import annotations


def _is_staff_or_super(user, request=None):
    if user.is_superuser:
        return True
    if request is not None and getattr(request, 'is_staff_user', False):
        return True
    profile = getattr(user, 'profile', None)
    if profile is not None and hasattr(profile, 'is_staff_user'):
        try:
            return bool(profile.is_staff_user())
        except Exception:
            return False
    return False


def _membership_for(user, organization):
    if not hasattr(user, 'memberships'):
        return None
    return user.memberships.filter(organization=organization, is_active=True).first()


def _role_flag(user, organization, attr_name: str, default: bool = False) -> bool:
    """Best-effort lookup for a RoleTemplate boolean — falls back to
    `default` if the field doesn't exist yet (not migrated)."""
    m = _membership_for(user, organization)
    if m is None:
        return False
    template = getattr(m, 'role_template', None) or getattr(m, 'role_template_obj', None)
    if template is None:
        return default
    val = getattr(template, attr_name, None)
    if val is None:
        return default
    return bool(val)


# -- View / list ------------------------------------------------------------

def can_view_suggestion(user, suggestion, request=None) -> bool:
    """Anyone in the org (staff/superuser cross-org) can view."""
    if _is_staff_or_super(user, request):
        return True
    m = _membership_for(user, suggestion.organization)
    return m is not None


# -- Replies ---------------------------------------------------------------

def can_send_reply(user, suggestion, request=None) -> bool:
    """Phase 10b: a *send* turns the suggestion into a public TicketComment.
    Risk-level gates the role:
      low → editor+ (psa_ai_send_low_risk if present, else can_write)
      medium → admin+
      high → admin+ AND psa_ai_send_high_risk explicit
    """
    if suggestion.kind != 'reply':
        return False
    if _is_staff_or_super(user, request):
        return True
    m = _membership_for(user, suggestion.organization)
    if m is None:
        return False
    risk = (suggestion.risk_level or 'medium').lower()
    if risk == 'low':
        return _role_flag(user, suggestion.organization, 'psa_ai_send_low_risk',
                          default=m.can_write())
    if risk == 'medium':
        return _role_flag(user, suggestion.organization, 'psa_ai_send_high_risk',
                          default=m.can_admin())
    # high
    return _role_flag(user, suggestion.organization, 'psa_ai_send_high_risk',
                      default=m.can_admin())


# -- Actions ----------------------------------------------------------------

def can_apply_action(user, suggestion, request=None) -> bool:
    """Phase 10b: a *direct apply* runs the action. Risk-level gates the role."""
    if suggestion.kind != 'action':
        return False
    if _is_staff_or_super(user, request):
        return True
    m = _membership_for(user, suggestion.organization)
    if m is None:
        return False
    risk = (suggestion.risk_level or 'medium').lower()
    if risk == 'low':
        return _role_flag(user, suggestion.organization, 'psa_ai_apply_low_risk',
                          default=m.can_write())
    if risk == 'medium':
        return _role_flag(user, suggestion.organization, 'psa_ai_apply_low_risk',
                          default=m.can_admin())
    # high — requires explicit admin + the high-risk flag (with fallback to
    # owner role)
    if not m.can_admin():
        return False
    return _role_flag(user, suggestion.organization, 'psa_ai_apply_high_risk',
                      default=m.can_manage_users())


# -- Triage (read-only advisory) -------------------------------------------

def can_request_triage(user, organization, request=None) -> bool:
    """
    Triage suggestions are read-only advisory output (the AI never acts).
    Default: any active member of the org can request, plus
    staff/superusers. RoleTemplate flag `psa_ai_request_triage` may
    explicitly grant or revoke per template; falls back to
    `psa_ai_view`, then to "is the user an active member of the org".

    The role-template field is optional — installs that haven't migrated
    the new field still get the safe default (any member, gated by
    psa_ai_view if present).
    """
    if user is None or not getattr(user, 'is_authenticated', False):
        return False
    if _is_staff_or_super(user, request):
        return True
    m = _membership_for(user, organization)
    if m is None:
        return False
    # Explicit per-template flag if present, else the existing
    # psa_ai_view flag (every system template defaults this to True).
    template = getattr(m, 'role_template', None) or getattr(m, 'role_template_obj', None)
    if template is not None:
        explicit = getattr(template, 'psa_ai_request_triage', None)
        if explicit is not None:
            return bool(explicit)
        view_flag = getattr(template, 'psa_ai_view', None)
        if view_flag is not None:
            return bool(view_flag)
    # No template / no flag — fall back to active membership.
    return True


def can_approve_others(user, suggestion, request=None) -> bool:
    """Approve someone else's pending_review request."""
    if _is_staff_or_super(user, request):
        return True
    m = _membership_for(user, suggestion.organization)
    if m is None:
        return False
    if suggestion.kind == 'reply':
        return _role_flag(user, suggestion.organization, 'psa_ai_approve_reply',
                          default=m.can_admin())
    return _role_flag(user, suggestion.organization, 'psa_ai_approve_action',
                      default=m.can_admin())
