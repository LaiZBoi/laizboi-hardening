"""
PSA staff-side views.

Phase 1: list + detail + minimal create — enough to exercise the feature
flag gating, RBAC integration, audit logging, and tenant scoping. Phase 2
will flesh out merge/split, macros, canned replies, etc.
"""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
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
    ClientPSASettings,
    Queue,
    Ticket,
    TicketAttachment,
    TicketComment,
    TicketPriority,
    TicketStatus,
    TicketType,
)


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

    return render(request, 'psa/ticket_create.html', {
        'queues': queues,
        'statuses': statuses,
        'priorities': priorities,
        'types': types,
        'eligible_clients': eligible_clients,
        'preselected_client_id': preselected_id,
        'no_eligible_clients': False,
    })


@login_required
@require_psa_enabled
def client_settings_view(request):
    """
    Per-client PSA settings page. The native PSA auto-decides per client:
      * has external PSA connection → auto opt-out (no manual action)
      * no external PSA            → auto opt-in via the global flag

    This page is the explicit OVERRIDE — admins only need to visit it
    when the auto-decision is wrong for a particular client.
    """
    if not (request.user.is_superuser or getattr(request, 'is_staff_user', False)):
        raise Http404()

    org = get_request_organization(request)
    if org is None:
        messages.error(request, 'Select a client first.')
        return redirect('core:dashboard')

    # Don't materialise a row on GET — the absence of a row IS the
    # "use auto-detection" signal. Materialise only on POST.
    cps = ClientPSASettings.objects.filter(organization=org).first()

    from psa.feature_flags import (
        client_has_external_psa,
        get_external_psa_summary,
        is_psa_enabled_for_client,
    )
    has_external = client_has_external_psa(org)
    external_summary = get_external_psa_summary(org)
    effective_enabled = is_psa_enabled_for_client(org)

    if request.method == 'POST':
        if cps is None:
            cps = ClientPSASettings(organization=org)
        previous = {
            'enabled': cps.enabled,
            'portal_enabled': cps.portal_enabled,
            'anonymous_ticket_form_enabled': cps.anonymous_ticket_form_enabled,
            'email_to_ticket_enabled': cps.email_to_ticket_enabled,
            'sms_notifications_enabled': cps.sms_notifications_enabled,
            'desktop_alerts_enabled': cps.desktop_alerts_enabled,
            'external_alert_ingest_enabled': cps.external_alert_ingest_enabled,
        }
        # Special handling: a "Reset to auto-detect" submission deletes the
        # explicit row instead of saving any value.
        if request.POST.get('reset_to_auto') == '1':
            if cps.pk:
                cps_pk = cps.pk
                cps.delete()
                AuditLog.log(
                    user=request.user,
                    action='delete',
                    organization=org,
                    object_type='psa.ClientPSASettings',
                    object_id=cps_pk,
                    object_repr=f'PSA settings reset to auto-detect for {org}',
                    description='Reset PSA client settings to auto-detect (row removed)',
                    ip_address=_client_ip(request),
                    path=request.path,
                )
                messages.success(request, 'PSA settings reset — this client now follows the global auto-detect rule.')
            else:
                messages.info(request, 'No explicit settings to reset — auto-detect was already in use.')
            return redirect('psa:client_settings')

        cps.enabled = request.POST.get('enabled') == 'on'
        cps.portal_enabled = request.POST.get('portal_enabled') == 'on'
        cps.anonymous_ticket_form_enabled = request.POST.get('anonymous_ticket_form_enabled') == 'on'
        cps.email_to_ticket_enabled = request.POST.get('email_to_ticket_enabled') == 'on'
        cps.sms_notifications_enabled = request.POST.get('sms_notifications_enabled') == 'on'
        cps.desktop_alerts_enabled = request.POST.get('desktop_alerts_enabled') == 'on'
        cps.external_alert_ingest_enabled = request.POST.get('external_alert_ingest_enabled') == 'on'
        cps.save()

        # Build a diff for the audit record
        changed = {k: (previous[k], getattr(cps, k)) for k in previous if previous[k] != getattr(cps, k)}
        AuditLog.log(
            user=request.user,
            action='update',
            organization=org,
            object_type='psa.ClientPSASettings',
            object_id=cps.pk,
            object_repr=str(cps),
            description=f'Updated PSA client settings ({len(changed)} change(s))',
            ip_address=_client_ip(request),
            path=request.path,
            extra_data={'changed_fields': {k: {'from': v[0], 'to': v[1]} for k, v in changed.items()}},
        )

        messages.success(request, 'PSA client settings updated.')
        return redirect('psa:client_settings')

    return render(request, 'psa/client_settings.html', {
        'cps': cps,
        'has_external_psa': has_external,
        'external_psa_summary': external_summary,
        'effective_enabled': effective_enabled,
        'using_auto_detect': cps is None,
        'current_organization': org,
    })


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
    messages.success(request, 'Comment added.')
    return redirect(reverse('psa:ticket_detail', kwargs={'ticket_number': ticket.ticket_number}))


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
