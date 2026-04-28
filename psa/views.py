"""
PSA staff-side views.

Phase 1: list + detail + minimal create — enough to exercise the feature
flag gating, RBAC integration, audit logging, and tenant scoping. Phase 2
will flesh out merge/split, macros, canned replies, etc.
"""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import models
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from audit.models import AuditLog
from core.decorators import require_write
from core.middleware import get_request_organization
from vault.models import Password

from .feature_flags import (
    is_psa_enabled,
    is_psa_enabled_for_client,
    require_client_psa_enabled,
    require_psa_enabled,
)
from .models import (
    CannedReply,
    ClientPSASettings,
    Contract,
    EmailIngestionConfig,
    Project,
    PSAApproval,
    Queue,
    RecurringTicketSchedule,
    ServiceCatalogItem,
    Ticket,
    TicketAttachment,
    TicketComment,
    TicketKBLink,
    TicketPriority,
    TicketStatus,
    TicketTimeEntry,
    TicketType,
    TicketWatcher,
)
from .sla import apply_due_dates, hygiene_flags, status_chip


# Phase 2a constants
ATTACHMENT_MAX_BYTES = 25 * 1024 * 1024  # 25 MB
ATTACHMENT_ALLOWED_MIMES = {
    'application/pdf',
    'application/msword',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'application/vnd.ms-excel',
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    'application/vnd.ms-powerpoint',
    'application/vnd.openxmlformats-officedocument.presentationml.presentation',
    'image/png', 'image/jpeg', 'image/gif', 'image/webp', 'image/svg+xml',
    'text/plain', 'text/csv', 'text/markdown',
    'application/json',
    'application/zip',
}


def _scoped_ticket_qs(request):
    """
    Tickets visible to the current request — PSA is now a global tool.

      * superuser / staff_user → every ticket across every client
      * org user               → only tickets for orgs they're a member of
        (regardless of which org they have currently "selected" — the PSA
        page is global, internal filtering replaces per-page client scoping)
    """
    qs = Ticket.objects.select_related(
        'organization', 'status', 'priority', 'queue', 'ticket_type', 'assigned_to'
    )
    if request.user.is_superuser or getattr(request, 'is_staff_user', False):
        return qs
    if hasattr(request.user, 'memberships'):
        org_ids = list(
            request.user.memberships.filter(is_active=True).values_list('organization_id', flat=True)
        )
        return qs.filter(organization_id__in=org_ids)
    return qs.none()


@login_required
@require_psa_enabled
def ticket_list(request):
    """
    Global ticket list with internal filtering. Filters are URL params:
      ?client=<org_id>&status=<status_id>&priority=<priority_id>
      &queue=<queue_id>&assigned=<user_id|me|unassigned>&q=<text>
    """
    qs = _scoped_ticket_qs(request)

    # Filters
    client_id = request.GET.get('client') or ''
    status_id = request.GET.get('status') or ''
    priority_id = request.GET.get('priority') or ''
    queue_id = request.GET.get('queue') or ''
    assigned = request.GET.get('assigned') or ''
    search = (request.GET.get('q') or '').strip()

    if client_id:
        qs = qs.filter(organization_id=client_id)
    if status_id:
        qs = qs.filter(status_id=status_id)
    if priority_id:
        qs = qs.filter(priority_id=priority_id)
    if queue_id:
        qs = qs.filter(queue_id=queue_id)
    if assigned == 'me':
        qs = qs.filter(assigned_to=request.user)
    elif assigned == 'unassigned':
        qs = qs.filter(assigned_to__isnull=True)
    elif assigned.isdigit():
        qs = qs.filter(assigned_to_id=int(assigned))
    if search:
        from django.db.models import Q
        qs = qs.filter(Q(ticket_number__icontains=search) | Q(subject__icontains=search))

    # Bound the page; full pagination is Phase 2 polish
    tickets = qs.order_by('-created_at')[:200]

    # Filter dropdown options — limited to what makes sense for the user.
    # For org-bound users, the client filter only shows their member orgs.
    from core.models import Organization
    if request.user.is_superuser or getattr(request, 'is_staff_user', False):
        available_clients = Organization.objects.filter(is_active=True).order_by('name')
    elif hasattr(request.user, 'memberships'):
        ids = request.user.memberships.filter(is_active=True).values_list('organization_id', flat=True)
        available_clients = Organization.objects.filter(id__in=list(ids), is_active=True).order_by('name')
    else:
        available_clients = Organization.objects.none()

    return render(request, 'psa/ticket_list.html', {
        'tickets': tickets,
        'available_clients': available_clients,
        'available_statuses': TicketStatus.objects.all(),
        'available_priorities': TicketPriority.objects.all(),
        'available_queues': Queue.objects.filter(is_active=True),
        'filter_values': {
            'client': client_id, 'status': status_id, 'priority': priority_id,
            'queue': queue_id, 'assigned': assigned, 'q': search,
        },
        'has_filters': any([client_id, status_id, priority_id, queue_id, assigned, search]),
    })


@login_required
@require_psa_enabled
def ticket_detail(request, ticket_number):
    org = get_request_organization(request)
    qs = _scoped_ticket_qs(request)
    ticket = get_object_or_404(qs, ticket_number=ticket_number)
    # If the requester's active org doesn't match the ticket's org and the
    # user is not staff/superuser, refuse — defence-in-depth.
    if not (request.user.is_superuser or getattr(request, 'is_staff_user', False)):
        if ticket.organization_id != getattr(org, 'id', None):
            raise Http404("Ticket not found")

    vault_qs = Password.objects.filter(
        organization=ticket.organization,
        is_personal=False,
    )
    vault_entries = vault_qs.only('id', 'title', 'username', 'updated_at')[:5]
    vault_count = vault_qs.count()

    is_closed = bool(ticket.closed_at) or (ticket.status_id and ticket.status.is_terminal)

    # Phase 2b — watchers + canned replies
    watchers_count = ticket.watchers.count()
    is_watcher = ticket.watchers.filter(user=request.user).exists()

    # Phase 2c — SLA + time + hygiene + similar tickets (recurring detect)
    sla = status_chip(ticket)
    hygiene = hygiene_flags(ticket)
    time_entries = list(ticket.time_entries.select_related('user').order_by('-started_at')[:30])
    running_timer = ticket.time_entries.filter(user=request.user, ended_at__isnull=True).first()
    total_minutes = sum(te.duration_minutes for te in time_entries)
    billable_minutes = sum(te.duration_minutes for te in time_entries if te.is_billable)

    from psa.sla import find_similar_tickets
    similar = find_similar_tickets(ticket)

    # Canned replies visible for this ticket: global ones + ones scoped to the
    # ticket's client. Pre-render each so the template can drop the result
    # into the textarea on click without a round-trip.
    canned = (
        CannedReply.objects
        .filter(is_active=True)
        .filter(models.Q(organization__isnull=True) | models.Q(organization=ticket.organization))
        .order_by('sort_order', 'name')
    )
    canned_replies = [
        {'id': c.id, 'name': c.name, 'rendered': c.render(ticket=ticket, user=request.user)}
        for c in canned
    ]

    # Phase 10a — surface AI suggestions on the ticket
    ai_suggestions = []
    ai_suggestions_json = []
    ai_enabled = False
    try:
        from core.models import SystemSetting
        from psa_ai.models import AISuggestion
        ss = SystemSetting.get_settings()
        ai_enabled = bool(ss.psa_ai_enabled)
        if ai_enabled:
            ai_suggestions = list(
                AISuggestion.objects
                .filter(native_ticket=ticket)
                .order_by('-created_at')[:10]
            )
            ai_suggestions_json = [
                {'id': s.id, 'suggested_body': s.suggested_body}
                for s in ai_suggestions
            ]
    except Exception:
        ai_enabled = False

    return render(request, 'psa/ticket_detail.html', {
        'ticket': ticket,
        'comments': ticket.comments.select_related('author').order_by('created_at'),
        'attachments': ticket.attachments.select_related('uploaded_by').order_by('-created_at'),
        'vault_entries': vault_entries,
        'vault_count': vault_count,
        'available_statuses': TicketStatus.objects.all(),
        'closure_categories': Ticket.CLOSURE_CATEGORIES,
        'is_closed': is_closed,
        'attachment_max_mb': ATTACHMENT_MAX_BYTES // (1024 * 1024),
        'watchers_count': watchers_count,
        'is_watcher': is_watcher,
        'canned_replies': canned_replies,
        'ai_enabled': ai_enabled,
        'ai_suggestions': ai_suggestions,
        'ai_suggestions_json': ai_suggestions_json,
        'sla': sla,
        'hygiene': hygiene,
        'time_entries': time_entries,
        'running_timer': running_timer,
        'total_minutes': total_minutes,
        'billable_minutes': billable_minutes,
        'similar_tickets': similar,
    })


@login_required
@require_write
@require_psa_enabled
@require_http_methods(['GET', 'POST'])
def ticket_create(request):
    """
    Create a ticket. The client/organization is chosen from a dropdown in
    the form (filtered to clients without an active external PSA — the
    hard rule). PSA is global; we don't depend on `current_organization`.
    """
    from psa.feature_flags import clients_eligible_for_native_psa
    queues = Queue.objects.filter(is_active=True)
    statuses = TicketStatus.objects.all()
    priorities = TicketPriority.objects.all()
    types = TicketType.objects.filter(is_active=True)
    eligible_clients = clients_eligible_for_native_psa(request.user)

    # If the user has zero eligible clients, we can't proceed — show a
    # friendly dead end (every client they could pick has an external PSA,
    # OR they have no memberships at all).
    if not eligible_clients.exists():
        return render(request, 'psa/ticket_create.html', {
            'queues': [], 'statuses': [], 'priorities': [], 'types': [],
            'eligible_clients': eligible_clients,
            'no_eligible_clients': True,
        })

    if request.method == 'POST':
        # If a catalog template was used, harvest the dynamic field values
        # and render the subject/body templates.
        catalog_slug = request.POST.get('catalog_slug') or ''
        catalog_post = ServiceCatalogItem.objects.filter(slug=catalog_slug, is_active=True).first() if catalog_slug else None

        if catalog_post is not None:
            values = {}
            missing = []
            for f in (catalog_post.fields_json or []):
                key = f.get('key') or ''
                if not key:
                    continue
                # Checkbox = "on"/None → "yes"/"no"; everything else is text-like.
                if f.get('type') == 'checkbox':
                    raw = (request.POST.get(f'field_{key}') or '').lower()
                    values[key] = 'yes' if raw in ('1', 'true', 'on', 'yes') else 'no'
                else:
                    raw = (request.POST.get(f'field_{key}') or '').strip()
                    values[key] = raw
                    if f.get('required') and not raw:
                        missing.append(f.get('label') or key)
            if missing:
                messages.error(request, 'Required field(s) missing: ' + ', '.join(missing))
                return redirect(reverse('psa:ticket_create') + f'?from_catalog={catalog_slug}')
            subject = ServiceCatalogItem.render_template(catalog_post.default_subject, values).strip()
            description = ServiceCatalogItem.render_template(catalog_post.default_body, values).strip()
        else:
            subject = (request.POST.get('subject') or '').strip()
            description = (request.POST.get('description') or '').strip()

        client_id = request.POST.get('client') or ''
        if not subject:
            messages.error(request, 'Subject is required.')
            return redirect(reverse('psa:ticket_create'))
        if not client_id:
            messages.error(request, 'Please pick a client for this ticket.')
            return redirect(reverse('psa:ticket_create'))

        try:
            org = eligible_clients.get(pk=client_id)
        except Exception:
            messages.error(request, 'That client is not eligible for native PSA tickets.')
            return redirect(reverse('psa:ticket_create'))

        try:
            queue = queues.get(pk=request.POST.get('queue'))
            status = statuses.get(pk=request.POST.get('status'))
            priority = priorities.get(pk=request.POST.get('priority'))
            ticket_type = types.get(pk=request.POST.get('ticket_type'))
        except (Queue.DoesNotExist, TicketStatus.DoesNotExist,
                TicketPriority.DoesNotExist, TicketType.DoesNotExist):
            messages.error(request, 'Invalid queue/status/priority/type selection.')
            return redirect(reverse('psa:ticket_create'))

        ticket = Ticket.objects.create(
            organization=org,
            subject=subject,
            description=description,
            queue=queue,
            status=status,
            priority=priority,
            ticket_type=ticket_type,
            source='manual',
            created_by=request.user,
            updated_by=request.user,
        )
        # Compute SLA due-dates from the priority's targets
        apply_due_dates(ticket)

        AuditLog.log(
            user=request.user,
            action='create',
            organization=org,
            object_type='psa.Ticket',
            object_id=ticket.pk,
            object_repr=ticket.ticket_number,
            description=f'Created PSA ticket {ticket.ticket_number}: {ticket.subject[:120]}',
            ip_address=_client_ip(request),
            path=request.path,
        )

        messages.success(request, f'Ticket {ticket.ticket_number} created.')
        return redirect(reverse('psa:ticket_detail', kwargs={'ticket_number': ticket.ticket_number}))

    # Pre-select the active org if the user has one and it's eligible.
    preselected = get_request_organization(request)
    preselected_id = preselected.id if preselected and eligible_clients.filter(id=preselected.id).exists() else None

    # If invoked via /psa/new/?from_catalog=<slug>, pre-fill from the
    # service-catalog template
    catalog_slug = request.GET.get('from_catalog') or ''
    catalog_item = None
    if catalog_slug:
        catalog_item = ServiceCatalogItem.objects.filter(slug=catalog_slug, is_active=True).first()

    return render(request, 'psa/ticket_create.html', {
        'queues': queues,
        'statuses': statuses,
        'priorities': priorities,
        'types': types,
        'eligible_clients': eligible_clients,
        'preselected_client_id': preselected_id,
        'no_eligible_clients': False,
        'catalog_item': catalog_item,
    })


@login_required
@require_psa_enabled
def psa_global_settings_view(request):
    """
    Global PSA settings page. Replaces the previous per-client settings —
    per-surface flags now live on `core.SystemSetting` and apply to every
    client. Per-client manual opt-outs (rare; used to override the
    auto-detect that already excludes external-PSA clients) are listed
    here too with un-opt-out buttons.
    """
    if not (request.user.is_superuser or getattr(request, 'is_staff_user', False)):
        raise Http404()

    from core.models import SystemSetting
    settings = SystemSetting.get_settings()

    # All explicit ClientPSASettings rows that are opt-OUTs (the only useful
    # use of the row in the global model — admin-disabled native PSA for
    # a specific client even though they have no external PSA).
    opt_outs = ClientPSASettings.objects.filter(enabled=False).select_related('organization')

    if request.method == 'POST':
        action = request.POST.get('action') or 'save_globals'

        if action == 'remove_opt_out':
            org_id = request.POST.get('organization_id') or ''
            cps = ClientPSASettings.objects.filter(organization_id=org_id, enabled=False).first()
            if cps is not None:
                cps_pk = cps.pk
                org_repr = str(cps.organization)
                cps.delete()
                AuditLog.log(
                    user=request.user, action='delete',
                    organization_id=int(org_id),
                    object_type='psa.ClientPSASettings', object_id=cps_pk,
                    object_repr=f'PSA opt-out removed for {org_repr}',
                    description='Removed PSA opt-out — client returns to auto-detect',
                    ip_address=_client_ip(request), path=request.path,
                )
                messages.success(request, f'Removed opt-out for {org_repr}.')
            return redirect('psa:settings')

        # Default: save global per-surface flags + AI behavior knobs
        previous = {
            'psa_portal_enabled': settings.psa_portal_enabled,
            'psa_anonymous_ticket_form_enabled': settings.psa_anonymous_ticket_form_enabled,
            'psa_email_to_ticket_enabled': settings.psa_email_to_ticket_enabled,
            'psa_sms_notifications_enabled': settings.psa_sms_notifications_enabled,
            'psa_desktop_alerts_enabled': settings.psa_desktop_alerts_enabled,
            'psa_external_alert_ingest_enabled': settings.psa_external_alert_ingest_enabled,
            'psa_ai_enabled': settings.psa_ai_enabled,
            'psa_ai_voice': settings.psa_ai_voice,
            'psa_ai_min_confidence': str(settings.psa_ai_min_confidence),
            'psa_ai_blocked_subject_keywords': settings.psa_ai_blocked_subject_keywords,
        }
        settings.psa_portal_enabled = request.POST.get('psa_portal_enabled') == 'on'
        settings.psa_anonymous_ticket_form_enabled = request.POST.get('psa_anonymous_ticket_form_enabled') == 'on'
        settings.psa_email_to_ticket_enabled = request.POST.get('psa_email_to_ticket_enabled') == 'on'
        settings.psa_sms_notifications_enabled = request.POST.get('psa_sms_notifications_enabled') == 'on'
        settings.psa_desktop_alerts_enabled = request.POST.get('psa_desktop_alerts_enabled') == 'on'
        settings.psa_external_alert_ingest_enabled = request.POST.get('psa_external_alert_ingest_enabled') == 'on'

        settings.psa_ai_enabled = request.POST.get('psa_ai_enabled') == 'on'
        settings.psa_ai_voice = (request.POST.get('psa_ai_voice') or '').strip()[:1000]
        try:
            from decimal import Decimal as _D
            v = _D(request.POST.get('psa_ai_min_confidence') or '0.75')
            if v < 0:
                v = _D('0')
            if v > 1:
                v = _D('1')
            settings.psa_ai_min_confidence = v
        except Exception:
            pass
        settings.psa_ai_blocked_subject_keywords = (
            request.POST.get('psa_ai_blocked_subject_keywords') or ''
        ).strip()[:5000]

        settings.updated_by = request.user
        settings.save()

        changed = {k: (previous[k], getattr(settings, k)) for k in previous if previous[k] != getattr(settings, k)}
        AuditLog.log(
            user=request.user, action='update',
            object_type='core.SystemSetting',
            object_id=settings.pk, object_repr='Global PSA settings',
            description=f'Updated global PSA settings ({len(changed)} change(s))',
            ip_address=_client_ip(request), path=request.path,
            extra_data={'changed_fields': {k: {'from': v[0], 'to': v[1]} for k, v in changed.items()}},
        )
        messages.success(request, 'PSA global settings saved.')
        return redirect('psa:settings')

    return render(request, 'psa/global_settings.html', {
        'settings': settings,
        'opt_outs': opt_outs,
    })


# Backwards-compat shim: the old per-client URL still resolves but
# redirects to the new global page so any bookmarks keep working.
@login_required
@require_psa_enabled
def client_settings_view(request):
    return redirect('psa:settings')


@login_required
@require_psa_enabled
def ticket_vault_context(request, ticket_number):
    """
    Read-only metadata view of the ticket organization's vault entries.

    Renders titles + links to the existing vault detail page only — never
    inlines secret values or loads encrypted columns. The vault detail
    view enforces its own permission and audit checks when the tech opens
    an entry in a new tab.
    """
    org = get_request_organization(request)
    qs = _scoped_ticket_qs(request)
    ticket = get_object_or_404(qs, ticket_number=ticket_number)
    # Defence-in-depth: non-staff users must be acting in the ticket's org.
    if not (request.user.is_superuser or getattr(request, 'is_staff_user', False)):
        if ticket.organization_id != getattr(org, 'id', None):
            raise Http404("Ticket not found")

    vault_entries = (
        Password.objects
        .filter(organization=ticket.organization, is_personal=False)
        .only('id', 'title', 'username', 'updated_at', 'organization_id')
        .order_by('title')
    )

    AuditLog.log(
        user=request.user,
        action='read',
        organization=ticket.organization,
        object_type='psa.TicketContext',
        object_id=ticket.pk,
        object_repr=ticket.ticket_number,
        description=f'Opened vault context for ticket {ticket.ticket_number}',
        ip_address=_client_ip(request),
        path=request.path,
    )

    return render(request, 'psa/ticket_vault_context.html', {
        'ticket': ticket,
        'vault_entries': vault_entries,
    })


def _client_ip(request):
    xff = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if xff:
        return xff.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')


def _scoped_ticket_for_write(request, ticket_number):
    """Resolve a ticket the user can write to (404 if outside their scope)."""
    qs = _scoped_ticket_qs(request)
    return get_object_or_404(qs, ticket_number=ticket_number)


# ---------------------------------------------------------------------------
# Phase 2a — comments / internal notes / attachments / quick actions / close
# ---------------------------------------------------------------------------

@login_required
@require_write
@require_psa_enabled
@require_http_methods(['POST'])
def ticket_post_comment(request, ticket_number):
    """Add a reply or internal note. POST: body, is_internal."""
    ticket = _scoped_ticket_for_write(request, ticket_number)
    body = (request.POST.get('body') or '').strip()
    if not body:
        messages.error(request, 'Comment cannot be empty.')
        return redirect(reverse('psa:ticket_detail', kwargs={'ticket_number': ticket.ticket_number}))

    is_internal = (request.POST.get('is_internal') or '').lower() in ('1', 'true', 'on', 'yes')
    comment = TicketComment.objects.create(
        ticket=ticket, author=request.user,
        body=body, is_internal=is_internal, is_system=False,
    )

    now = timezone.now()
    update_fields = ['updated_by', 'updated_at', 'last_tech_response_at']
    ticket.updated_by = request.user
    ticket.last_tech_response_at = now
    if not ticket.first_response_at and not is_internal:
        ticket.first_response_at = now
        update_fields.append('first_response_at')
    ticket.save(update_fields=update_fields)

    AuditLog.log(
        user=request.user, action='create', organization=ticket.organization,
        object_type='psa.TicketComment', object_id=comment.pk,
        object_repr=f'{"internal note" if is_internal else "reply"} on {ticket.ticket_number}',
        description=f'Added {"internal note" if is_internal else "reply"} to {ticket.ticket_number}',
        ip_address=_client_ip(request), path=request.path,
        extra_data={'is_internal': is_internal, 'length': len(body)},
    )
    # Notify watchers (best-effort; SMTP failures don't block the request).
    _notify_watchers(ticket, comment, request.user)
    # Parse @mentions, auto-add as watcher + notify.
    _process_mentions(ticket, comment, request.user)
    messages.success(request, 'Comment added.')
    return redirect(reverse('psa:ticket_detail', kwargs={'ticket_number': ticket.ticket_number}))


# ---------------------------------------------------------------------------
# Phase 2c — @mentions
# ---------------------------------------------------------------------------

import re as _re

# Match @username — letters, digits, underscore, dot, hyphen. Anchor on
# whitespace or start-of-string so we don't grab `email@domain.com`.
_MENTION_RE = _re.compile(r'(?:^|\s)@([A-Za-z0-9_.\-]{2,150})\b')


def _extract_mentions(body: str):
    if not body:
        return []
    # Dedupe while preserving order.
    seen = set()
    out = []
    for m in _MENTION_RE.finditer(body):
        u = m.group(1)
        if u.lower() not in seen:
            seen.add(u.lower())
            out.append(u)
    return out


def _process_mentions(ticket, comment, actor):
    """Resolve @username mentions, add each mentioned user as a watcher,
    and (best-effort) email them. Mentions only fire on staff-side
    comments — internal AND external both notify, since we treat all
    mentioned users as staff. Phase 3 portal will gate this."""
    from django.contrib.auth import get_user_model
    User = get_user_model()
    usernames = _extract_mentions(comment.body or '')
    if not usernames:
        return 0
    qs = User.objects.filter(username__in=usernames, is_active=True)
    notified = 0
    for u in qs:
        # Tenant-scope: only staff/superuser can be mentioned cross-tenant;
        # everyone else must be a member of the ticket's org.
        if not (u.is_superuser or _user_is_org_member(u, ticket.organization_id)):
            continue
        TicketWatcher.objects.get_or_create(ticket=ticket, user=u)
        if u.email and u != actor:
            _send_mention_email(ticket, comment, actor, u)
        notified += 1
    if notified:
        AuditLog.log(
            user=actor, action='create', organization=ticket.organization,
            object_type='psa.TicketComment', object_id=comment.pk,
            object_repr=f'mentions on {ticket.ticket_number}',
            description=f'Mentioned {notified} user(s) in {ticket.ticket_number}',
            extra_data={'usernames': [u.username for u in qs]},
        )
    return notified


def _user_is_org_member(user, org_id):
    if not hasattr(user, 'memberships'):
        return False
    return user.memberships.filter(organization_id=org_id, is_active=True).exists()


def _send_mention_email(ticket, comment, actor, recipient):
    try:
        from core.models import SystemSetting
        from django.core.mail import send_mail, get_connection
    except Exception:
        return
    s = SystemSetting.get_settings()
    if not s.smtp_enabled or not s.smtp_host:
        return
    try:
        from vault.encryption import decrypt
        smtp_password = decrypt(s.smtp_password) if s.smtp_password else ''
    except Exception:
        smtp_password = s.smtp_password or ''
    try:
        connection = get_connection(
            backend='django.core.mail.backends.smtp.EmailBackend',
            host=s.smtp_host, port=s.smtp_port,
            username=s.smtp_username, password=smtp_password,
            use_tls=s.smtp_use_tls, use_ssl=s.smtp_use_ssl, timeout=15,
        )
    except Exception:
        return
    site_url = (s.site_url or '').rstrip('/')
    brand = s.custom_company_name or s.site_name or 'Client St0r'
    subject = f'[{brand}] {ticket.ticket_number}: you were mentioned'
    body = (
        f'{actor.username} mentioned you in {ticket.ticket_number} '
        f'({ticket.organization.name}):\n\n'
        f'{comment.body[:1500]}\n\n'
        f'View ticket: {site_url}/psa/t/{ticket.ticket_number}/\n'
    )
    from_email = (
        f'{s.smtp_from_name} <{s.smtp_from_email}>'
        if s.smtp_from_email else s.smtp_username
    )
    try:
        send_mail(subject=subject, message=body, from_email=from_email,
                  recipient_list=[recipient.email], connection=connection,
                  fail_silently=True)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Phase 2c — Ticket merge
# ---------------------------------------------------------------------------

@login_required
@require_write
@require_psa_enabled
@require_http_methods(['POST'])
def ticket_merge(request, ticket_number):
    """
    Merge `ticket_number` (source) INTO the `target` ticket (POST param).
    Both must be in the same organization. Operations:
      * Move all source comments + attachments to target (preserve
        ordering by created_at).
      * Add a system internal-note on the target referencing the source.
      * Mark the source as `closed` with a system comment + closure
        category 'duplicate' + duplicate_of=target.
      * Audit-log both sides.
    """
    source = _scoped_ticket_for_write(request, ticket_number)
    target_number = (request.POST.get('target') or '').strip()
    if not target_number or target_number == source.ticket_number:
        messages.error(request, 'Pick a different target ticket to merge into.')
        return redirect(reverse('psa:ticket_detail', kwargs={'ticket_number': source.ticket_number}))

    target = (
        _scoped_ticket_qs(request)
        .filter(ticket_number=target_number, organization=source.organization)
        .first()
    )
    if target is None:
        messages.error(request, 'Target ticket not found in this client.')
        return redirect(reverse('psa:ticket_detail', kwargs={'ticket_number': source.ticket_number}))
    if target.closed_at or (target.status_id and target.status.is_terminal):
        messages.error(request, 'Cannot merge into a closed ticket.')
        return redirect(reverse('psa:ticket_detail', kwargs={'ticket_number': source.ticket_number}))

    # Move comments + attachments
    moved_comments = source.comments.update(ticket=target)
    moved_attachments = source.attachments.update(ticket=target)

    # System note on target
    TicketComment.objects.create(
        ticket=target, author=request.user, is_internal=True, is_system=True,
        body=(f'[merge] {source.ticket_number} merged into {target.ticket_number} '
              f'by {request.user.username}. Moved {moved_comments} comment(s) and '
              f'{moved_attachments} attachment(s).'),
    )

    # Close the source
    closed_status = (
        TicketStatus.objects.filter(slug='closed').first()
        or TicketStatus.objects.filter(is_terminal=True).order_by('sort_order').first()
    )
    source.duplicate_of = target
    source.closure_category = 'duplicate'
    source.resolution_summary = f'Merged into {target.ticket_number}'
    source.closed_at = timezone.now()
    source.resolved_at = source.resolved_at or source.closed_at
    if closed_status:
        source.status = closed_status
    source.updated_by = request.user
    source.save(update_fields=[
        'duplicate_of', 'closure_category', 'resolution_summary',
        'closed_at', 'resolved_at', 'status', 'updated_by', 'updated_at',
    ])
    TicketComment.objects.create(
        ticket=source, author=request.user, is_internal=True, is_system=True,
        body=f'[merge] Closed as duplicate of {target.ticket_number}.',
    )

    AuditLog.log(
        user=request.user, action='update', organization=source.organization,
        object_type='psa.Ticket', object_id=source.pk,
        object_repr=source.ticket_number,
        description=f'Merged {source.ticket_number} → {target.ticket_number}',
        ip_address=_client_ip(request), path=request.path,
        extra_data={'target': target.ticket_number,
                    'comments_moved': moved_comments,
                    'attachments_moved': moved_attachments},
    )
    messages.success(request,
        f'Merged {source.ticket_number} into {target.ticket_number} '
        f'({moved_comments} comment(s), {moved_attachments} attachment(s) moved).')
    return redirect(reverse('psa:ticket_detail', kwargs={'ticket_number': target.ticket_number}))


@login_required
@require_write
@require_psa_enabled
@require_http_methods(['POST'])
def ticket_attach(request, ticket_number):
    """Upload a file. Enforces size, MIME allowlist, tenant-scoped storage."""
    ticket = _scoped_ticket_for_write(request, ticket_number)
    f = request.FILES.get('file')
    if not f:
        messages.error(request, 'No file selected.')
        return redirect(reverse('psa:ticket_detail', kwargs={'ticket_number': ticket.ticket_number}))

    if f.size > ATTACHMENT_MAX_BYTES:
        messages.error(request, f'File too large (max {ATTACHMENT_MAX_BYTES // (1024 * 1024)} MB).')
        return redirect(reverse('psa:ticket_detail', kwargs={'ticket_number': ticket.ticket_number}))

    content_type = (f.content_type or '').lower().split(';')[0].strip()
    if content_type not in ATTACHMENT_ALLOWED_MIMES:
        messages.error(request, f'File type "{content_type or "unknown"}" not allowed.')
        return redirect(reverse('psa:ticket_detail', kwargs={'ticket_number': ticket.ticket_number}))

    is_internal = (request.POST.get('is_internal') or '').lower() in ('1', 'true', 'on', 'yes')
    safe_name = (f.name or 'attachment').rsplit('/', 1)[-1].rsplit('\\', 1)[-1].replace('\x00', '')[:255]

    attachment = TicketAttachment.objects.create(
        ticket=ticket, uploaded_by=request.user, file=f,
        filename=safe_name, content_type=content_type, size_bytes=f.size,
        is_internal=is_internal,
    )
    AuditLog.log(
        user=request.user, action='create', organization=ticket.organization,
        object_type='psa.TicketAttachment', object_id=attachment.pk,
        object_repr=safe_name,
        description=f'Attached {safe_name} ({f.size} bytes) to {ticket.ticket_number}',
        ip_address=_client_ip(request), path=request.path,
        extra_data={'is_internal': is_internal, 'mime': content_type, 'size': f.size},
    )
    messages.success(request, f'Attached {safe_name}.')
    return redirect(reverse('psa:ticket_detail', kwargs={'ticket_number': ticket.ticket_number}))


@login_required
@require_write
@require_psa_enabled
@require_http_methods(['POST'])
def ticket_quick_action(request, ticket_number):
    """One-button actions: assign_me, set_status, reopen, close."""
    ticket = _scoped_ticket_for_write(request, ticket_number)
    action = request.POST.get('action') or ''
    now = timezone.now()
    audit_extra = {'action': action}
    description = ''

    if action == 'assign_me':
        prev = ticket.assigned_to_id
        ticket.assigned_to = request.user
        ticket.updated_by = request.user
        ticket.save(update_fields=['assigned_to', 'updated_by', 'updated_at'])
        TicketComment.objects.create(
            ticket=ticket, author=request.user,
            body=f'Assigned to {request.user.username}.',
            is_internal=True, is_system=True,
        )
        description = f'Assigned {ticket.ticket_number} to {request.user.username}'
        audit_extra['previous_assignee_id'] = prev

    elif action == 'set_status':
        try:
            new_status = TicketStatus.objects.get(pk=request.POST.get('status') or '')
        except TicketStatus.DoesNotExist:
            messages.error(request, 'Invalid status.')
            return redirect(reverse('psa:ticket_detail', kwargs={'ticket_number': ticket.ticket_number}))
        prev = ticket.status.name if ticket.status_id else '—'
        ticket.status = new_status
        ticket.updated_by = request.user
        update_fields = ['status', 'updated_by', 'updated_at']
        if new_status.is_terminal and not ticket.resolved_at:
            ticket.resolved_at = now
            update_fields.append('resolved_at')
        ticket.save(update_fields=update_fields)
        TicketComment.objects.create(
            ticket=ticket, author=request.user,
            body=f'Status changed: {prev} → {new_status.name}',
            is_internal=True, is_system=True,
        )
        description = f'Status of {ticket.ticket_number}: {prev} → {new_status.name}'
        audit_extra['from'] = prev
        audit_extra['to'] = new_status.name

    elif action == 'reopen':
        target = (
            TicketStatus.objects.filter(slug='in-progress').first()
            or TicketStatus.objects.filter(is_terminal=False).order_by('sort_order').first()
        )
        if not target:
            messages.error(request, 'No non-terminal status defined to reopen into.')
            return redirect(reverse('psa:ticket_detail', kwargs={'ticket_number': ticket.ticket_number}))
        prev = ticket.status.name if ticket.status_id else '—'
        ticket.status = target
        ticket.resolved_at = None
        ticket.closed_at = None
        ticket.closure_category = ''
        ticket.updated_by = request.user
        ticket.save(update_fields=['status', 'resolved_at', 'closed_at', 'closure_category', 'updated_by', 'updated_at'])
        TicketComment.objects.create(
            ticket=ticket, author=request.user,
            body=f'Reopened. Status: {prev} → {target.name}',
            is_internal=True, is_system=True,
        )
        description = f'Reopened {ticket.ticket_number}'

    elif action == 'close':
        category = request.POST.get('closure_category') or ''
        summary = (request.POST.get('resolution_summary') or '').strip()
        if not summary:
            messages.error(request, 'A resolution summary is required to close a ticket.')
            return redirect(reverse('psa:ticket_detail', kwargs={'ticket_number': ticket.ticket_number}))
        valid_categories = {key for key, _ in Ticket.CLOSURE_CATEGORIES}
        if category not in valid_categories:
            messages.error(request, 'Pick a valid closure category.')
            return redirect(reverse('psa:ticket_detail', kwargs={'ticket_number': ticket.ticket_number}))
        closed_status = (
            TicketStatus.objects.filter(slug='closed').first()
            or TicketStatus.objects.filter(is_terminal=True).order_by('sort_order').first()
        )
        if not closed_status:
            messages.error(request, 'No terminal status defined.')
            return redirect(reverse('psa:ticket_detail', kwargs={'ticket_number': ticket.ticket_number}))
        ticket.status = closed_status
        ticket.closure_category = category
        ticket.resolution_summary = summary
        ticket.closed_at = now
        if not ticket.resolved_at:
            ticket.resolved_at = now
        ticket.updated_by = request.user
        ticket.save(update_fields=[
            'status', 'closure_category', 'resolution_summary', 'closed_at',
            'resolved_at', 'updated_by', 'updated_at',
        ])
        TicketComment.objects.create(
            ticket=ticket, author=request.user,
            body=f'Closed ({dict(Ticket.CLOSURE_CATEGORIES).get(category, category)}). Resolution: {summary}',
            is_internal=False, is_system=True,
        )
        description = f'Closed {ticket.ticket_number} ({category})'
        audit_extra['closure_category'] = category
        audit_extra['summary_length'] = len(summary)

    else:
        messages.error(request, 'Unknown action.')
        return redirect(reverse('psa:ticket_detail', kwargs={'ticket_number': ticket.ticket_number}))

    AuditLog.log(
        user=request.user, action='update', organization=ticket.organization,
        object_type='psa.Ticket', object_id=ticket.pk,
        object_repr=ticket.ticket_number, description=description,
        ip_address=_client_ip(request), path=request.path, extra_data=audit_extra,
    )
    messages.success(request, description)
    return redirect(reverse('psa:ticket_detail', kwargs={'ticket_number': ticket.ticket_number}))


# ---------------------------------------------------------------------------
# Phase 2b — watchers + canned replies
# ---------------------------------------------------------------------------

def _notify_watchers(ticket, comment, actor):
    """
    Email each watcher (excluding the comment author) about new activity.
    Best-effort: SMTP failures are logged but don't block the request.
    Internal notes go to staff watchers only — current model has all
    watchers as authenticated staff users, so we send all of them.
    """
    try:
        from core.models import SystemSetting
        from django.core.mail import send_mail, get_connection
        from django.contrib.auth import get_user_model
    except Exception:
        return 0

    settings = SystemSetting.get_settings()
    if not settings.smtp_enabled or not settings.smtp_host:
        return 0

    watchers = TicketWatcher.objects.filter(ticket=ticket).exclude(user=actor).select_related('user')
    recipients = [w.user.email for w in watchers if w.user.email]
    if not recipients:
        return 0

    try:
        from vault.encryption import decrypt
        smtp_password = decrypt(settings.smtp_password) if settings.smtp_password else ''
    except Exception:
        smtp_password = settings.smtp_password or ''

    try:
        connection = get_connection(
            backend='django.core.mail.backends.smtp.EmailBackend',
            host=settings.smtp_host, port=settings.smtp_port,
            username=settings.smtp_username, password=smtp_password,
            use_tls=settings.smtp_use_tls, use_ssl=settings.smtp_use_ssl,
            timeout=15,
        )
    except Exception:
        return 0

    from_email = (
        f'{settings.smtp_from_name} <{settings.smtp_from_email}>'
        if settings.smtp_from_email else settings.smtp_username
    )
    site_url = (settings.site_url or '').rstrip('/')
    brand = settings.custom_company_name or settings.site_name or 'Client St0r'
    detail_url = f'{site_url}/psa/t/{ticket.ticket_number}/'
    flag = ' [INTERNAL NOTE]' if comment.is_internal else ''
    subject = f'[{brand}] {ticket.ticket_number}: {ticket.subject}{flag}'
    body = (
        f'{actor.username} added a {"internal note" if comment.is_internal else "reply"} '
        f'to {ticket.ticket_number} ({ticket.organization.name}):\n\n'
        f'{comment.body[:2000]}\n\n'
        f'View ticket: {detail_url}\n'
    )

    sent = 0
    for email in recipients:
        try:
            send_mail(subject=subject, message=body, from_email=from_email,
                      recipient_list=[email], connection=connection, fail_silently=False)
            sent += 1
        except Exception:
            continue
    return sent


@login_required
@require_write
@require_psa_enabled
@require_http_methods(['POST'])
def ticket_watch_toggle(request, ticket_number):
    """Subscribe / unsubscribe the current user from ticket activity."""
    ticket = _scoped_ticket_for_write(request, ticket_number)
    existing = TicketWatcher.objects.filter(ticket=ticket, user=request.user).first()
    if existing:
        existing.delete()
        messages.success(request, 'Unwatched. You will no longer receive emails for activity on this ticket.')
        action_label = 'unwatch'
    else:
        TicketWatcher.objects.create(ticket=ticket, user=request.user)
        messages.success(request, 'Watching. You will receive emails for new activity on this ticket.')
        action_label = 'watch'
    AuditLog.log(
        user=request.user, action='update',
        organization=ticket.organization,
        object_type='psa.TicketWatcher',
        object_id=ticket.pk, object_repr=ticket.ticket_number,
        description=f'{request.user.username} {action_label}ed {ticket.ticket_number}',
        ip_address=_client_ip(request), path=request.path,
    )
    return redirect(reverse('psa:ticket_detail', kwargs={'ticket_number': ticket.ticket_number}))


@login_required
@require_psa_enabled
def canned_reply_list(request):
    """List available canned replies. Staff/superuser see global + every
    org. Org-bound users see global + their member orgs only."""
    if request.user.is_superuser or getattr(request, 'is_staff_user', False):
        qs = CannedReply.objects.select_related('organization')
    else:
        org_ids = list(
            request.user.memberships.filter(is_active=True).values_list('organization_id', flat=True)
        ) if hasattr(request.user, 'memberships') else []
        qs = CannedReply.objects.select_related('organization').filter(
            models.Q(organization__isnull=True) | models.Q(organization_id__in=org_ids)
        )
    return render(request, 'psa/canned_reply_list.html', {
        'replies': qs.order_by('organization__name', 'sort_order', 'name'),
    })


@login_required
@require_write
@require_psa_enabled
@require_http_methods(['GET', 'POST'])
def canned_reply_create(request):
    """Create a canned reply. Org dropdown filtered by user's eligible
    clients (no external PSA, no opt-out). Empty value = global."""
    from psa.feature_flags import clients_eligible_for_native_psa
    eligible_clients = clients_eligible_for_native_psa(request.user)
    can_create_global = request.user.is_superuser or getattr(request, 'is_staff_user', False)

    if request.method == 'POST':
        name = (request.POST.get('name') or '').strip()
        body = (request.POST.get('body') or '').strip()
        org_id = request.POST.get('organization') or ''
        if not name or not body:
            messages.error(request, 'Name and body are required.')
            return redirect(reverse('psa:canned_reply_create'))
        org = None
        if org_id:
            try:
                org = eligible_clients.get(pk=org_id)
            except Exception:
                messages.error(request, 'That client is not eligible.')
                return redirect(reverse('psa:canned_reply_create'))
        elif not can_create_global:
            messages.error(request, 'Only staff/superusers can create global canned replies. Pick a client.')
            return redirect(reverse('psa:canned_reply_create'))
        reply = CannedReply.objects.create(
            name=name, body=body, organization=org, created_by=request.user,
            is_active=True,
        )
        AuditLog.log(
            user=request.user, action='create',
            organization=org,
            object_type='psa.CannedReply', object_id=reply.pk,
            object_repr=reply.name,
            description=f'Created canned reply "{name}" ({"global" if not org else org.name})',
            ip_address=_client_ip(request), path=request.path,
        )
        messages.success(request, f'Canned reply "{name}" created.')
        return redirect(reverse('psa:canned_reply_list'))

    return render(request, 'psa/canned_reply_form.html', {
        'reply': None,
        'eligible_clients': eligible_clients,
        'can_create_global': can_create_global,
    })


@login_required
@require_write
@require_psa_enabled
@require_http_methods(['GET', 'POST'])
def canned_reply_edit(request, pk):
    from psa.feature_flags import clients_eligible_for_native_psa
    reply = get_object_or_404(CannedReply, pk=pk)
    # Visibility check: org-bound users can edit only their org or global
    # replies they created.
    if not (request.user.is_superuser or getattr(request, 'is_staff_user', False)):
        if reply.organization_id is None and reply.created_by_id != request.user.id:
            raise Http404()
        if reply.organization_id is not None:
            if not request.user.memberships.filter(
                organization_id=reply.organization_id, is_active=True
            ).exists():
                raise Http404()

    eligible_clients = clients_eligible_for_native_psa(request.user)
    can_create_global = request.user.is_superuser or getattr(request, 'is_staff_user', False)

    if request.method == 'POST':
        if request.POST.get('delete') == '1':
            org = reply.organization
            name = reply.name
            pk_ = reply.pk
            reply.delete()
            AuditLog.log(
                user=request.user, action='delete',
                organization=org,
                object_type='psa.CannedReply', object_id=pk_,
                object_repr=name,
                description=f'Deleted canned reply "{name}"',
                ip_address=_client_ip(request), path=request.path,
            )
            messages.success(request, f'Deleted "{name}".')
            return redirect(reverse('psa:canned_reply_list'))

        reply.name = (request.POST.get('name') or '').strip() or reply.name
        reply.body = (request.POST.get('body') or '').strip() or reply.body
        reply.is_active = request.POST.get('is_active') == 'on'
        org_id = request.POST.get('organization') or ''
        if org_id:
            try:
                reply.organization = eligible_clients.get(pk=org_id)
            except Exception:
                messages.error(request, 'That client is not eligible.')
                return redirect(reverse('psa:canned_reply_edit', kwargs={'pk': reply.pk}))
        elif can_create_global:
            reply.organization = None
        reply.save()
        AuditLog.log(
            user=request.user, action='update',
            organization=reply.organization,
            object_type='psa.CannedReply', object_id=reply.pk,
            object_repr=reply.name,
            description=f'Updated canned reply "{reply.name}"',
            ip_address=_client_ip(request), path=request.path,
        )
        messages.success(request, 'Saved.')
        return redirect(reverse('psa:canned_reply_list'))

    return render(request, 'psa/canned_reply_form.html', {
        'reply': reply,
        'eligible_clients': eligible_clients,
        'can_create_global': can_create_global,
    })


# ---------------------------------------------------------------------------
# Phase 2c — time tracking endpoints + service catalog
# ---------------------------------------------------------------------------

@login_required
@require_write
@require_psa_enabled
@require_http_methods(['POST'])
def timer_start(request, ticket_number):
    """Start a running timer for the current user on this ticket. Idempotent
    — if a running timer already exists, it's returned unchanged."""
    ticket = _scoped_ticket_for_write(request, ticket_number)
    existing = TicketTimeEntry.objects.filter(
        ticket=ticket, user=request.user, ended_at__isnull=True,
    ).first()
    if existing:
        messages.info(request, 'Timer already running.')
        return redirect(reverse('psa:ticket_detail', kwargs={'ticket_number': ticket.ticket_number}))
    is_billable = (request.POST.get('is_billable') or '').lower() in ('1', 'true', 'on', 'yes')
    entry = TicketTimeEntry.objects.create(
        ticket=ticket, user=request.user,
        started_at=timezone.now(), is_billable=is_billable,
    )
    AuditLog.log(
        user=request.user, action='create', organization=ticket.organization,
        object_type='psa.TicketTimeEntry', object_id=entry.pk,
        object_repr=f'timer started on {ticket.ticket_number}',
        description=f'Started timer on {ticket.ticket_number} (billable={is_billable})',
        ip_address=_client_ip(request), path=request.path,
    )
    messages.success(request, 'Timer started.')
    return redirect(reverse('psa:ticket_detail', kwargs={'ticket_number': ticket.ticket_number}))


@login_required
@require_write
@require_psa_enabled
@require_http_methods(['POST'])
def timer_stop(request, ticket_number):
    """Stop the user's running timer on this ticket and finalise duration."""
    ticket = _scoped_ticket_for_write(request, ticket_number)
    entry = TicketTimeEntry.objects.filter(
        ticket=ticket, user=request.user, ended_at__isnull=True,
    ).first()
    if entry is None:
        messages.info(request, 'No running timer to stop.')
        return redirect(reverse('psa:ticket_detail', kwargs={'ticket_number': ticket.ticket_number}))
    entry.ended_at = timezone.now()
    entry.notes = (request.POST.get('notes') or entry.notes or '').strip()[:2000]
    entry.save()  # save() computes duration_minutes
    AuditLog.log(
        user=request.user, action='update', organization=ticket.organization,
        object_type='psa.TicketTimeEntry', object_id=entry.pk,
        object_repr=f'timer stopped on {ticket.ticket_number}',
        description=f'Stopped timer on {ticket.ticket_number} ({entry.duration_minutes}m, billable={entry.is_billable})',
        ip_address=_client_ip(request), path=request.path,
        extra_data={'minutes': entry.duration_minutes, 'billable': entry.is_billable},
    )
    messages.success(request, f'Timer stopped — {entry.duration_minutes}m logged.')
    return redirect(reverse('psa:ticket_detail', kwargs={'ticket_number': ticket.ticket_number}))


@login_required
@require_write
@require_psa_enabled
@require_http_methods(['POST'])
def time_entry_manual(request, ticket_number):
    """Add a manual time entry (e.g. for offline work). POST: minutes, is_billable, notes."""
    ticket = _scoped_ticket_for_write(request, ticket_number)
    try:
        minutes = int(request.POST.get('minutes') or 0)
    except ValueError:
        minutes = 0
    if minutes <= 0 or minutes > 24 * 60:
        messages.error(request, 'Minutes must be between 1 and 1440.')
        return redirect(reverse('psa:ticket_detail', kwargs={'ticket_number': ticket.ticket_number}))
    is_billable = (request.POST.get('is_billable') or '').lower() in ('1', 'true', 'on', 'yes')
    notes = (request.POST.get('notes') or '').strip()[:2000]
    now = timezone.now()
    entry = TicketTimeEntry.objects.create(
        ticket=ticket, user=request.user,
        started_at=now - timezone.timedelta(minutes=minutes),
        ended_at=now, duration_minutes=minutes,
        is_billable=is_billable, notes=notes,
    )
    AuditLog.log(
        user=request.user, action='create', organization=ticket.organization,
        object_type='psa.TicketTimeEntry', object_id=entry.pk,
        object_repr=f'manual time entry on {ticket.ticket_number}',
        description=f'Logged {minutes}m on {ticket.ticket_number} (billable={is_billable})',
        ip_address=_client_ip(request), path=request.path,
        extra_data={'minutes': minutes, 'billable': is_billable},
    )
    messages.success(request, f'Logged {minutes}m.')
    return redirect(reverse('psa:ticket_detail', kwargs={'ticket_number': ticket.ticket_number}))


# Service catalog ---------------------------------------------------------

@login_required
@require_psa_enabled
def service_catalog(request):
    """Browse-the-catalog grid. Click a tile → /psa/new/?from_catalog=<slug>."""
    is_admin = request.user.is_superuser or getattr(request, 'is_staff_user', False)
    qs = ServiceCatalogItem.objects.select_related(
        'default_priority', 'default_queue', 'default_type',
    ).order_by('sort_order', 'name')
    if not is_admin:
        qs = qs.filter(is_active=True)
    return render(request, 'psa/service_catalog.html', {
        'items': qs,
        'is_catalog_admin': is_admin,
    })


def _catalog_admin_or_404(request):
    if not (request.user.is_superuser or getattr(request, 'is_staff_user', False)):
        raise Http404()


@login_required
@require_psa_enabled
@require_http_methods(['GET', 'POST'])
def service_catalog_form(request, pk=None):
    """Create or edit a ServiceCatalogItem. Staff/superuser only."""
    _catalog_admin_or_404(request)
    item = get_object_or_404(ServiceCatalogItem, pk=pk) if pk else None

    queues = Queue.objects.filter(is_active=True).order_by('name')
    priorities = TicketPriority.objects.all().order_by('sort_order', 'code')
    types = TicketType.objects.filter(is_active=True).order_by('name')

    if request.method == 'POST':
        if request.POST.get('delete') == '1' and item is not None:
            name = item.name
            pk_ = item.pk
            item.delete()
            AuditLog.log(
                user=request.user, action='delete',
                object_type='psa.ServiceCatalogItem', object_id=pk_,
                object_repr=name,
                description=f'Deleted service catalog item "{name}"',
                ip_address=_client_ip(request), path=request.path,
            )
            messages.success(request, f'Deleted "{name}".')
            return redirect('psa:service_catalog')

        from django.utils.text import slugify
        import json as _json

        name = (request.POST.get('name') or '').strip()
        description = (request.POST.get('description') or '').strip()
        subject_tpl = (request.POST.get('default_subject') or '').strip()
        body_tpl = (request.POST.get('default_body') or '').strip()
        icon = (request.POST.get('icon') or '').strip()[:80]
        is_active = request.POST.get('is_active') == 'on'
        sort_order = int(request.POST.get('sort_order') or 0)
        fields_raw = (request.POST.get('fields_json') or '[]').strip()

        if not name:
            messages.error(request, 'Name is required.')
            return redirect(request.path)
        try:
            fields = _json.loads(fields_raw) if fields_raw else []
        except Exception as e:
            messages.error(request, f'Fields JSON is not valid: {e}')
            return redirect(request.path)
        if not isinstance(fields, list) or any(
            not isinstance(f, dict) or 'key' not in f or 'label' not in f for f in fields
        ):
            messages.error(request, 'Fields JSON must be a list of objects with at least "key" and "label".')
            return redirect(request.path)
        for f in fields:
            allowed_types = {'text', 'email', 'date', 'number', 'textarea', 'select', 'checkbox'}
            if f.get('type', 'text') not in allowed_types:
                messages.error(request, f'Invalid field type: {f.get("type")!r}. Allowed: {sorted(allowed_types)}')
                return redirect(request.path)

        try:
            queue = queues.filter(pk=request.POST.get('default_queue') or 0).first()
            priority = priorities.filter(pk=request.POST.get('default_priority') or 0).first()
            ttype = types.filter(pk=request.POST.get('default_type') or 0).first()
        except Exception:
            queue = priority = ttype = None

        if item is None:
            slug = slugify(name)
            # ensure unique
            base = slug
            n = 1
            while ServiceCatalogItem.objects.filter(slug=slug).exists():
                n += 1
                slug = f'{base}-{n}'
            item = ServiceCatalogItem(slug=slug)
        item.name = name
        item.description = description
        item.default_subject = subject_tpl
        item.default_body = body_tpl
        item.icon = icon
        item.is_active = is_active
        item.sort_order = sort_order
        item.fields_json = fields
        item.default_queue = queue
        item.default_priority = priority
        item.default_type = ttype
        item.save()

        AuditLog.log(
            user=request.user, action='update' if pk else 'create',
            object_type='psa.ServiceCatalogItem', object_id=item.pk,
            object_repr=item.name,
            description=f'{"Updated" if pk else "Created"} service catalog item "{item.name}"',
            ip_address=_client_ip(request), path=request.path,
            extra_data={'field_count': len(fields)},
        )
        messages.success(request, f'Saved "{item.name}".')
        return redirect('psa:service_catalog')

    import json as _json
    return render(request, 'psa/service_catalog_form.html', {
        'item': item,
        'queues': queues,
        'priorities': priorities,
        'types': types,
        'fields_json_pretty': _json.dumps(item.fields_json if item else [], indent=2),
    })


# ---------------------------------------------------------------------------
# Phase 3 — Projects, Recurring Tickets, KB linking, Approvals
# ---------------------------------------------------------------------------


@login_required
@require_psa_enabled
def project_list(request):
    """Tenant-scoped list of PSA projects with status filter."""
    org = get_request_organization(request)
    qs = Project.objects.select_related('client_org', 'owner')
    if org is not None:
        qs = qs.filter(organization=org)
    elif not (request.user.is_superuser or getattr(request, 'is_staff_user', False)):
        qs = qs.none()
    status = request.GET.get('status')
    if status in {'planning', 'active', 'on_hold', 'completed', 'cancelled'}:
        qs = qs.filter(status=status)
    return render(request, 'psa/project_list.html', {
        'projects': qs.order_by('-created_at')[:200],
        'status_filter': status or '',
    })


@login_required
@require_write
@require_psa_enabled
@require_http_methods(['GET', 'POST'])
def project_form(request, pk=None):
    org = get_request_organization(request)
    if org is None:
        messages.error(request, 'Pick a client first.')
        return redirect('psa:project_list')
    item = get_object_or_404(Project, pk=pk, organization=org) if pk else None

    if request.method == 'POST':
        name = (request.POST.get('name') or '').strip()
        if not name:
            messages.error(request, 'Name is required.')
            return redirect(request.path)
        if item is None:
            item = Project(organization=org, name=name)
        else:
            item.name = name
        item.description = (request.POST.get('description') or '').strip()
        item.status = request.POST.get('status') or 'planning'
        item.start_date = request.POST.get('start_date') or None
        item.due_date = request.POST.get('due_date') or None
        item.is_billable = request.POST.get('is_billable') == 'on'
        try:
            item.estimated_hours = request.POST.get('estimated_hours') or None
        except (TypeError, ValueError):
            item.estimated_hours = None
        client_org_id = request.POST.get('client_org') or ''
        if client_org_id:
            from core.models import Organization
            item.client_org = Organization.objects.filter(pk=client_org_id).first()
        else:
            item.client_org = None
        if not item.owner_id:
            item.owner = request.user
        item.save()

        AuditLog.log(
            user=request.user, action='update' if pk else 'create',
            organization=org,
            object_type='psa.Project', object_id=item.pk,
            object_repr=item.name,
            description=f'{"Updated" if pk else "Created"} PSA project "{item.name}"',
            ip_address=_client_ip(request), path=request.path,
        )
        messages.success(request, f'Saved "{item.name}".')
        return redirect('psa:project_detail', pk=item.pk)

    from core.models import Organization
    return render(request, 'psa/project_form.html', {
        'item': item,
        'client_orgs': Organization.objects.filter(is_active=True).order_by('name'),
        'status_choices': Project.STATUS_CHOICES,
    })


@login_required
@require_psa_enabled
def project_detail(request, pk):
    org = get_request_organization(request)
    qs = Project.objects.select_related('client_org', 'owner')
    if org is not None:
        qs = qs.filter(organization=org)
    item = get_object_or_404(qs, pk=pk)
    tickets = item.tickets.select_related('status', 'priority').order_by('-created_at')[:100]
    return render(request, 'psa/project_detail.html', {
        'item': item,
        'tickets': tickets,
    })


@login_required
@require_psa_enabled
def recurring_list(request):
    """Tenant-scoped list of recurring ticket schedules."""
    org = get_request_organization(request)
    qs = RecurringTicketSchedule.objects.select_related('queue', 'priority', 'ticket_type', 'assigned_to')
    if org is not None:
        qs = qs.filter(organization=org)
    elif not (request.user.is_superuser or getattr(request, 'is_staff_user', False)):
        qs = qs.none()
    return render(request, 'psa/recurring_list.html', {
        'schedules': qs.order_by('next_run_at')[:200],
    })


@login_required
@require_write
@require_psa_enabled
@require_http_methods(['GET', 'POST'])
def recurring_form(request, pk=None):
    org = get_request_organization(request)
    if org is None:
        messages.error(request, 'Pick a client first.')
        return redirect('psa:recurring_list')
    item = get_object_or_404(RecurringTicketSchedule, pk=pk, organization=org) if pk else None

    queues = Queue.objects.filter(is_active=True)
    priorities = TicketPriority.objects.all()
    types = TicketType.objects.all()

    if request.method == 'POST':
        name = (request.POST.get('name') or '').strip()
        subject = (request.POST.get('template_subject') or '').strip()
        if not name or not subject:
            messages.error(request, 'Name and template subject are required.')
            return redirect(request.path)
        try:
            queue = queues.get(pk=request.POST.get('queue'))
            priority = priorities.get(pk=request.POST.get('priority'))
            ticket_type = types.get(pk=request.POST.get('ticket_type'))
        except (Queue.DoesNotExist, TicketPriority.DoesNotExist,
                TicketType.DoesNotExist, ValueError):
            messages.error(request, 'Pick a valid queue / priority / type.')
            return redirect(request.path)

        if item is None:
            item = RecurringTicketSchedule(
                organization=org,
                queue=queue, priority=priority, ticket_type=ticket_type,
                next_run_at=timezone.now(),
                created_by=request.user,
            )
        else:
            item.queue = queue
            item.priority = priority
            item.ticket_type = ticket_type
        item.name = name
        item.template_subject = subject
        item.template_body = (request.POST.get('template_body') or '').strip()
        item.frequency = request.POST.get('frequency') or 'monthly'
        try:
            item.interval = max(1, int(request.POST.get('interval') or 1))
        except ValueError:
            item.interval = 1
        item.is_active = request.POST.get('is_active') == 'on'

        first_run = request.POST.get('next_run_at')
        if first_run:
            try:
                from django.utils.dateparse import parse_datetime
                parsed = parse_datetime(first_run)
                if parsed:
                    item.next_run_at = parsed
            except (TypeError, ValueError):
                pass

        client_org_id = request.POST.get('client_org') or ''
        if client_org_id:
            from core.models import Organization
            item.client_org = Organization.objects.filter(pk=client_org_id).first()
        else:
            item.client_org = None

        item.save()
        messages.success(request, f'Saved schedule "{item.name}".')
        return redirect('psa:recurring_list')

    from core.models import Organization
    return render(request, 'psa/recurring_form.html', {
        'item': item,
        'queues': queues, 'priorities': priorities, 'types': types,
        'frequency_choices': RecurringTicketSchedule.FREQUENCY_CHOICES,
        'client_orgs': Organization.objects.filter(is_active=True).order_by('name'),
    })


@login_required
@require_psa_enabled
def kb_browse(request):
    """KB browser — wraps docs.Document filtered to global KB articles."""
    from docs.models import Document
    qs = Document.objects.filter(is_global=True).order_by('-updated_at')
    q = (request.GET.get('q') or '').strip()
    if q:
        qs = qs.filter(models.Q(title__icontains=q) | models.Q(content__icontains=q))
    return render(request, 'psa/kb_browse.html', {
        'articles': qs[:50],
        'query': q,
    })


@login_required
@require_write
@require_psa_enabled
@require_http_methods(['POST'])
def ticket_kb_link(request, ticket_number):
    """Link a docs.Document KB article to a ticket."""
    from docs.models import Document
    org = get_request_organization(request)
    qs = Ticket.objects.all()
    if org is not None:
        qs = qs.filter(organization=org)
    ticket = get_object_or_404(qs, ticket_number=ticket_number)

    article_id = request.POST.get('article_id')
    if not article_id:
        messages.error(request, 'Pick an article.')
        return redirect('psa:ticket_detail', ticket_number=ticket_number)

    article = get_object_or_404(Document, pk=article_id, is_global=True)
    link, created = TicketKBLink.objects.get_or_create(
        ticket=ticket, article=article,
        defaults={'linked_by': request.user, 'note': request.POST.get('note', '')[:300]},
    )
    if created:
        messages.success(request, f'Linked KB: {article.title}')
        AuditLog.log(
            user=request.user, action='update',
            organization=ticket.organization,
            object_type='psa.Ticket', object_id=ticket.pk,
            object_repr=ticket.ticket_number,
            description=f'Linked KB article "{article.title}" to {ticket.ticket_number}',
            ip_address=_client_ip(request), path=request.path,
        )
    else:
        messages.info(request, f'Already linked to "{article.title}".')
    return redirect('psa:ticket_detail', ticket_number=ticket_number)


@login_required
@require_write
@require_psa_enabled
@require_http_methods(['POST'])
def ticket_kb_unlink(request, ticket_number, link_pk):
    org = get_request_organization(request)
    qs = TicketKBLink.objects.select_related('ticket', 'article')
    if org is not None:
        qs = qs.filter(ticket__organization=org)
    link = get_object_or_404(qs, pk=link_pk, ticket__ticket_number=ticket_number)
    title = link.article.title
    link.delete()
    messages.success(request, f'Unlinked "{title}".')
    return redirect('psa:ticket_detail', ticket_number=ticket_number)


@login_required
@require_psa_enabled
def approval_list(request):
    """Tenant-scoped list of PSA approvals (pending first)."""
    org = get_request_organization(request)
    qs = PSAApproval.objects.select_related('requested_by', 'decided_by', 'related_ticket')
    if org is not None:
        qs = qs.filter(organization=org)
    elif not (request.user.is_superuser or getattr(request, 'is_staff_user', False)):
        qs = qs.none()
    status = request.GET.get('status', 'pending')
    if status in {'pending', 'approved', 'denied', 'cancelled'}:
        qs = qs.filter(status=status)
    return render(request, 'psa/approval_list.html', {
        'approvals': qs.order_by('-requested_at')[:200],
        'status_filter': status,
    })


@login_required
@require_write
@require_psa_enabled
@require_http_methods(['POST'])
def approval_decide(request, pk):
    org = get_request_organization(request)
    qs = PSAApproval.objects.all()
    if org is not None:
        qs = qs.filter(organization=org)
    approval = get_object_or_404(qs, pk=pk)
    if approval.status != 'pending':
        messages.error(request, 'This approval has already been decided.')
        return redirect('psa:approval_list')

    decision = request.POST.get('decision')
    comment = (request.POST.get('comment') or '').strip()
    if decision not in {'approve', 'deny'}:
        messages.error(request, 'Pick approve or deny.')
        return redirect('psa:approval_list')

    approval.decide(user=request.user, approved=(decision == 'approve'), comment=comment)
    AuditLog.log(
        user=request.user, action='update',
        organization=approval.organization,
        object_type='psa.PSAApproval', object_id=approval.pk,
        object_repr=str(approval),
        description=f'{decision.title()}d {approval.get_kind_display()} approval #{approval.pk}',
        ip_address=_client_ip(request), path=request.path,
    )
    messages.success(request, f'{decision.title()}d.')
    return redirect('psa:approval_list')


# ---------------------------------------------------------------------------
# Phase 4 — Contracts + Email Ingestion
# ---------------------------------------------------------------------------


@login_required
@require_psa_enabled
def contract_list(request):
    """Tenant-scoped contract list."""
    org = get_request_organization(request)
    qs = Contract.objects.select_related('client_org')
    if org is not None:
        qs = qs.filter(organization=org)
    elif not (request.user.is_superuser or getattr(request, 'is_staff_user', False)):
        qs = qs.none()
    return render(request, 'psa/contract_list.html', {
        'contracts': qs.order_by('-start_date')[:200],
    })


@login_required
@require_write
@require_psa_enabled
@require_http_methods(['GET', 'POST'])
def contract_form(request, pk=None):
    org = get_request_organization(request)
    if org is None:
        messages.error(request, 'Pick a client first.')
        return redirect('psa:contract_list')
    item = get_object_or_404(Contract, pk=pk, organization=org) if pk else None

    from core.models import Organization

    if request.method == 'POST':
        name = (request.POST.get('name') or '').strip()
        client_org_id = request.POST.get('client_org')
        if not name or not client_org_id:
            messages.error(request, 'Name and client are required.')
            return redirect(request.path)
        try:
            client_org = Organization.objects.get(pk=client_org_id)
        except Organization.DoesNotExist:
            messages.error(request, 'Client not found.')
            return redirect(request.path)

        if item is None:
            item = Contract(organization=org, client_org=client_org, name=name,
                            created_by=request.user, start_date=timezone.now().date())
        else:
            item.client_org = client_org
            item.name = name
        item.contract_type = request.POST.get('contract_type') or 'block_hours'
        item.status = request.POST.get('status') or 'draft'
        item.start_date = request.POST.get('start_date') or item.start_date
        item.end_date = request.POST.get('end_date') or None
        try:
            item.total_hours = request.POST.get('total_hours') or 0
        except (TypeError, ValueError):
            item.total_hours = 0
        try:
            item.hourly_rate = request.POST.get('hourly_rate') or 0
            item.overage_rate = request.POST.get('overage_rate') or 0
        except (TypeError, ValueError):
            pass
        item.notes = (request.POST.get('notes') or '').strip()
        item.save()

        AuditLog.log(
            user=request.user, action='update' if pk else 'create',
            organization=org,
            object_type='psa.Contract', object_id=item.pk,
            object_repr=str(item),
            description=f'{"Updated" if pk else "Created"} contract "{item.name}"',
            ip_address=_client_ip(request), path=request.path,
        )
        messages.success(request, f'Saved "{item.name}".')
        return redirect('psa:contract_list')

    return render(request, 'psa/contract_form.html', {
        'item': item,
        'client_orgs': Organization.objects.filter(is_active=True).order_by('name'),
        'contract_types': Contract.CONTRACT_TYPES,
        'status_choices': Contract.STATUS_CHOICES,
    })


@login_required
@require_psa_enabled
def email_config_list(request):
    org = get_request_organization(request)
    qs = EmailIngestionConfig.objects.select_related('default_queue', 'default_priority', 'default_type')
    if org is not None:
        qs = qs.filter(organization=org)
    elif not (request.user.is_superuser or getattr(request, 'is_staff_user', False)):
        qs = qs.none()
    return render(request, 'psa/email_config_list.html', {
        'configs': qs.order_by('name')[:100],
    })


@login_required
@require_write
@require_psa_enabled
@require_http_methods(['GET', 'POST'])
def email_config_form(request, pk=None):
    org = get_request_organization(request)
    if org is None:
        messages.error(request, 'Pick a client first.')
        return redirect('psa:email_config_list')
    item = get_object_or_404(EmailIngestionConfig, pk=pk, organization=org) if pk else None

    queues = Queue.objects.filter(is_active=True)
    priorities = TicketPriority.objects.all()
    types = TicketType.objects.all()

    if request.method == 'POST':
        name = (request.POST.get('name') or '').strip()
        host = (request.POST.get('imap_host') or '').strip()
        username = (request.POST.get('username') or '').strip()
        password = request.POST.get('password') or ''
        if not name or not host or not username:
            messages.error(request, 'Name, host, username are required.')
            return redirect(request.path)
        try:
            port = int(request.POST.get('imap_port') or 993)
        except ValueError:
            port = 993

        try:
            queue = queues.get(pk=request.POST.get('default_queue'))
            priority = priorities.get(pk=request.POST.get('default_priority'))
            ticket_type = types.get(pk=request.POST.get('default_type'))
        except (Queue.DoesNotExist, TicketPriority.DoesNotExist, TicketType.DoesNotExist, ValueError):
            messages.error(request, 'Pick valid defaults.')
            return redirect(request.path)

        if item is None:
            item = EmailIngestionConfig(organization=org, default_queue=queue,
                                        default_priority=priority, default_type=ticket_type)
        else:
            item.default_queue = queue
            item.default_priority = priority
            item.default_type = ticket_type
        item.name = name
        item.imap_host = host
        item.imap_port = port
        item.use_ssl = request.POST.get('use_ssl') == 'on'
        item.username = username
        item.folder = (request.POST.get('folder') or 'INBOX').strip()
        item.subject_ticket_pattern = (request.POST.get('subject_ticket_pattern') or 'PSA-\\d{4}-\\d{6}')[:200]
        item.is_active = request.POST.get('is_active') == 'on'
        try:
            item.poll_interval_minutes = max(1, int(request.POST.get('poll_interval_minutes') or 5))
        except ValueError:
            item.poll_interval_minutes = 5
        if password:
            item.set_password(password)
        item.save()

        messages.success(request, f'Saved "{item.name}".')
        return redirect('psa:email_config_list')

    return render(request, 'psa/email_config_form.html', {
        'item': item,
        'queues': queues, 'priorities': priorities, 'types': types,
    })
