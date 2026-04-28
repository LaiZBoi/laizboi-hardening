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
    TicketPriority,
    TicketStatus,
    TicketType,
)


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

    return render(request, 'psa/ticket_detail.html', {
        'ticket': ticket,
        'comments': ticket.comments.select_related('author').order_by('created_at'),
        'vault_entries': vault_entries,
        'vault_count': vault_count,
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
