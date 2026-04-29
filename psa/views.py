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
from core.decorators import require_admin, require_write
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
    Quote,
    QuoteLineItem,
    RecurringTicketSchedule,
    ServiceCatalogItem,
    Ticket,
    TicketAttachment,
    TicketComment,
    TicketExpense,
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

    # Phase 4 — surface the active client contract on the ticket
    contract = Contract.for_ticket(ticket)

    # Phase 5 — list expenses on the ticket (most recent first)
    expenses = list(ticket.expenses.select_related('user').order_by('-incurred_on')[:30])
    total_expense = sum(float(e.amount) for e in expenses if e.is_billable)

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

    # The most recent triage suggestion is rendered expanded with the
    # full warning banner; older triages collapse into <details> blocks.
    ai_latest_triage_id = None
    for _s in ai_suggestions:
        if getattr(_s, 'kind', '') == 'triage':
            ai_latest_triage_id = _s.id
            break

    # Workflow executions tied to this ticket (Operations → Workflows)
    # Eager-load stage completions so the ticket detail page can render the
    # full inline stage checklist without N+1 queries.
    workflow_executions = []
    try:
        from django.db.models import Prefetch
        from processes.models import (
            ProcessExecution, ProcessStageCompletion, ProcessExecutionAuditLog,
        )
        workflow_executions = list(
            ProcessExecution.objects.filter(native_psa_ticket=ticket)
            .select_related('process', 'assigned_to', 'started_by')
            .prefetch_related(
                Prefetch(
                    'stage_completions',
                    queryset=ProcessStageCompletion.objects.select_related(
                        'stage',
                        'stage__linked_document',
                        'stage__linked_password',
                        'stage__linked_secure_note',
                        'stage__linked_asset',
                        'completed_by',
                    ).order_by('stage__order'),
                ),
                # Recent audit-log entries (sign-off history) so the ticket
                # detail page can render an inline timeline of stage events.
                Prefetch(
                    'audit_logs',
                    queryset=ProcessExecutionAuditLog.objects
                    .select_related('user', 'stage')
                    .order_by('-created_at')[:25],
                    to_attr='recent_audit_logs',
                ),
            )
            .order_by('-created_at')[:10]
        )
    except Exception:
        pass

    # v3.17.129 — admin-only "Assign to tech" sub-menu in the Actions dropdown.
    can_assign = _can_assign(request, ticket.organization)
    eligible_assignees = _eligible_assignees(ticket.organization) if can_assign else []

    return render(request, 'psa/ticket_detail.html', {
        'ticket': ticket,
        'workflow_executions': workflow_executions,
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
        'ai_latest_triage_id': ai_latest_triage_id,
        'sla': sla,
        'hygiene': hygiene,
        'time_entries': time_entries,
        'running_timer': running_timer,
        'total_minutes': total_minutes,
        'billable_minutes': billable_minutes,
        'similar_tickets': similar,
        'contract': contract,
        'expenses': expenses,
        'total_expense_billable': total_expense,
        'expense_categories': TicketExpense.CATEGORY_CHOICES,
        'can_assign': can_assign,
        'eligible_assignees': eligible_assignees,
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

        # v3.17.129 — admin-only "Assign to" picker on ticket creation.
        # The form may post `assigned_to`; only honour it when the requester
        # has permission to assign work in this org AND the target is
        # eligible (org member, or staff/superuser).
        initial_assignee = None
        posted_assignee = (request.POST.get('assigned_to') or '').strip()
        if posted_assignee and _can_assign(request, org):
            from django.contrib.auth.models import User
            try:
                cand = User.objects.get(pk=int(posted_assignee), is_active=True)
                eligible_ids = set(_eligible_assignees(org).values_list('id', flat=True))
                if cand.id in eligible_ids:
                    initial_assignee = cand
            except (User.DoesNotExist, ValueError, TypeError):
                pass

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
            assigned_to=initial_assignee,
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

        # Optional workflow attachment — if the user picked a Process template,
        # spawn a ProcessExecution linked to the new ticket and seed the stage
        # completions so the embedded checklist appears immediately.
        workflow_id = (request.POST.get('workflow_id') or '').strip()
        if workflow_id:
            try:
                from processes.models import (
                    Process, ProcessExecution, ProcessStageCompletion,
                    ProcessExecutionAuditLog,
                )
                process = Process.objects.filter(
                    pk=int(workflow_id), is_published=True, is_archived=False,
                ).first()
                if process is not None:
                    execution = ProcessExecution.objects.create(
                        process=process,
                        organization=org,
                        assigned_to=request.user,
                        started_by=request.user,
                        status='in_progress',
                        started_at=timezone.now(),
                        native_psa_ticket=ticket,
                        notes=f'Attached at ticket creation.',
                    )
                    for stage in process.stages.all().order_by('order'):
                        ProcessStageCompletion.objects.create(
                            execution=execution, stage=stage, is_completed=False,
                        )
                    ProcessExecutionAuditLog.log_action(
                        execution=execution,
                        action_type='execution_created',
                        user=request.user,
                        description=(
                            f"{request.user.username} attached workflow "
                            f"'{process.title}' at ticket creation"
                        ),
                        request=request,
                    )
            except (ValueError, TypeError):
                pass  # invalid workflow_id — silently ignore

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

    # Workflow templates available to attach at creation. List ALL published,
    # non-archived templates — the user picks one and the org is set when the
    # ticket is saved. (Earlier we tried to scope by current-org, but most
    # processes are tied to a specific org so the dropdown went empty in
    # Global view.)
    available_workflows = []
    try:
        from processes.models import Process
        available_workflows = list(
            Process.objects.filter(is_published=True, is_archived=False)
            .order_by('organization__name', 'title')
            .values('pk', 'title', 'organization_id', 'organization__name')
        )
    except Exception:
        pass

    # v3.17.129 — show the optional "Assign to" picker to org admins +
    # staff/superusers. At GET time we don't know which client the user will
    # pick, so we offer the union of eligible techs across every eligible
    # client. The save path re-validates against the chosen org.
    can_assign = (
        request.user.is_superuser
        or getattr(request, 'is_staff_user', False)
        or any(_can_assign(request, c) for c in eligible_clients)
    )
    eligible_assignees = []
    if can_assign:
        from django.contrib.auth.models import User
        client_ids = list(eligible_clients.values_list('id', flat=True))
        eligible_assignees = list(
            User.objects.filter(is_active=True)
            .filter(
                models.Q(is_staff=True) | models.Q(is_superuser=True)
                | models.Q(memberships__organization_id__in=client_ids,
                           memberships__is_active=True)
            )
            .distinct()
            .order_by('username')
        )

    return render(request, 'psa/ticket_create.html', {
        'queues': queues,
        'statuses': statuses,
        'priorities': priorities,
        'types': types,
        'eligible_clients': eligible_clients,
        'preselected_client_id': preselected_id,
        'no_eligible_clients': False,
        'catalog_item': catalog_item,
        'available_workflows': available_workflows,
        'can_assign': can_assign,
        'eligible_assignees': eligible_assignees,
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


def _can_assign(request, org):
    """
    True if `request.user` is allowed to assign work in `org`.

    Admin = any of:
      * Django superuser
      * Django staff
      * Membership with role in {'admin', 'owner'} for `org`
      * Membership whose RoleTemplate grants `org_manage_members`
    `org=None` means MSP-wide context (staff/superuser only).
    """
    u = request.user
    if not u.is_authenticated:
        return False
    if u.is_superuser or u.is_staff or getattr(request, 'is_staff_user', False):
        return True
    if org is None:
        return False
    from accounts.models import Membership, Role
    org_id = getattr(org, 'id', None) or getattr(org, 'pk', None) or org
    qs = Membership.objects.filter(
        user=u, organization_id=org_id, is_active=True,
    ).select_related('role_template')
    for m in qs:
        if m.role in (Role.ADMIN, Role.OWNER):
            return True
        # Granular RBAC permission grants admin-equivalent assignment power.
        if m.role_template and getattr(m.role_template, 'org_manage_members', False):
            return True
    return False


def _eligible_assignees(org):
    """
    Users who can be assigned work in `org`:
      * any user with active Membership in this org
      * plus all staff / superusers (they can be assigned anywhere)
    Returns a queryset ordered by username.
    `org=None` returns staff/superusers only.
    """
    from django.contrib.auth.models import User
    org_id = getattr(org, 'id', None) or getattr(org, 'pk', None) or org if org else None
    q = models.Q(is_staff=True) | models.Q(is_superuser=True)
    if org_id:
        q = q | models.Q(memberships__organization_id=org_id, memberships__is_active=True)
    return (
        User.objects.filter(is_active=True)
        .filter(q)
        .distinct()
        .order_by('username')
    )


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

    elif action == 'set_assignee':
        # Admin-only: assign the ticket to any eligible tech (or unassign).
        if not _can_assign(request, ticket.organization):
            messages.error(request, "You don't have permission to reassign this ticket.")
            return redirect(reverse('psa:ticket_detail', kwargs={'ticket_number': ticket.ticket_number}))
        from django.contrib.auth.models import User
        prev = ticket.assigned_to
        prev_id = ticket.assigned_to_id
        target_id = (request.POST.get('assignee_id') or '').strip()
        if target_id in ('', '0', 'unassigned'):
            ticket.assigned_to = None
            new_label = 'unassigned'
        else:
            try:
                target = User.objects.get(pk=int(target_id), is_active=True)
            except (User.DoesNotExist, ValueError, TypeError):
                messages.error(request, 'Invalid assignee.')
                return redirect(reverse('psa:ticket_detail', kwargs={'ticket_number': ticket.ticket_number}))
            # Defence-in-depth: the target must be staff/superuser OR a member
            # of the ticket's org. Prevents arbitrary user IDs from being
            # injected via the form.
            eligible_ids = set(_eligible_assignees(ticket.organization).values_list('id', flat=True))
            if target.id not in eligible_ids:
                messages.error(request, 'That user cannot be assigned to this ticket.')
                return redirect(reverse('psa:ticket_detail', kwargs={'ticket_number': ticket.ticket_number}))
            ticket.assigned_to = target
            new_label = target.username
        ticket.updated_by = request.user
        ticket.save(update_fields=['assigned_to', 'updated_by', 'updated_at'])
        TicketComment.objects.create(
            ticket=ticket, author=request.user,
            body=f'Reassigned: {prev.username if prev else "unassigned"} → {new_label}.',
            is_internal=True, is_system=True,
        )
        description = (
            f'Reassigned {ticket.ticket_number}: '
            f'{prev.username if prev else "unassigned"} → {new_label}'
        )
        audit_extra['previous_assignee_id'] = prev_id

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
    view_mode = request.GET.get('view', 'tile')
    if view_mode not in ('tile', 'list'):
        view_mode = 'tile'
    return render(request, 'psa/service_catalog.html', {
        'items': qs,
        'is_catalog_admin': is_admin,
        'view_mode': view_mode,
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
    from core.models import Organization
    if pk:
        item = get_object_or_404(Project, pk=pk)
        org = item.organization
    else:
        item = None
        org = get_request_organization(request)

    client_orgs = Organization.objects.filter(is_active=True).order_by('name')

    def _render(selected_org_id=None):
        # v3.17.129 — admin-only Owner picker. Show eligible techs for
        # whichever org is currently picked (falls back to current org or
        # the project's saved org).
        scope_org_id = (selected_org_id if selected_org_id is not None
                        else (item.organization_id if item else (org.pk if org else None)))
        scope_org = None
        if scope_org_id:
            scope_org = Organization.objects.filter(pk=scope_org_id).first()
        can_assign = _can_assign(request, scope_org) if scope_org else (
            request.user.is_superuser or getattr(request, 'is_staff_user', False)
        )
        eligible_assignees = _eligible_assignees(scope_org) if can_assign else []
        return render(request, 'psa/project_form.html', {
            'item': item,
            'client_orgs': client_orgs,
            'status_choices': Project.STATUS_CHOICES,
            'selected_client_org_id': scope_org_id,
            'can_assign': can_assign,
            'eligible_assignees': eligible_assignees,
        })

    if request.method == 'POST':
        posted_org_id = (request.POST.get('client_org_id') or '').strip()
        if posted_org_id:
            try:
                item_org = Organization.objects.get(pk=int(posted_org_id), is_active=True)
            except (Organization.DoesNotExist, ValueError, TypeError):
                messages.error(request, 'Invalid client.')
                return _render()
        else:
            item_org = org

        if item_org is None:
            messages.error(request, 'Please choose a client for this project.')
            return _render()

        name = (request.POST.get('name') or '').strip()
        if not name:
            messages.error(request, 'Name is required.')
            return _render(selected_org_id=item_org.pk)
        if item is None:
            item = Project(organization=item_org, name=name)
        else:
            item.organization = item_org
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
            item.client_org = Organization.objects.filter(pk=client_org_id).first()
        else:
            item.client_org = None
        # v3.17.129 — admins can choose an owner; otherwise default to creator.
        owner_id = (request.POST.get('owner_id') or '').strip()
        if owner_id and _can_assign(request, item_org):
            from django.contrib.auth.models import User
            try:
                cand = User.objects.get(pk=int(owner_id), is_active=True)
                eligible_ids = set(_eligible_assignees(item_org).values_list('id', flat=True))
                if cand.id in eligible_ids:
                    item.owner = cand
            except (User.DoesNotExist, ValueError, TypeError):
                pass
        if not item.owner_id:
            item.owner = request.user
        item.save()

        AuditLog.log(
            user=request.user, action='update' if pk else 'create',
            organization=item_org,
            object_type='psa.Project', object_id=item.pk,
            object_repr=item.name,
            description=f'{"Updated" if pk else "Created"} PSA project "{item.name}"',
            ip_address=_client_ip(request), path=request.path,
        )
        messages.success(request, f'Saved "{item.name}".')
        return redirect('psa:project_detail', pk=item.pk)

    return _render()


@login_required
@require_psa_enabled
def project_detail(request, pk):
    org = get_request_organization(request)
    qs = Project.objects.select_related('client_org', 'owner')
    if org is not None:
        qs = qs.filter(organization=org)
    item = get_object_or_404(qs, pk=pk)
    tickets = item.tickets.select_related('status', 'priority').order_by('-created_at')[:100]
    tasks = item.tasks.select_related('assigned_to').order_by('sort_order', 'created_at')
    from .models import ProjectTask
    # v3.17.129 — surface assignee picker on tasks for admins of the
    # project's org.
    can_assign = _can_assign(request, item.organization)
    eligible_assignees = _eligible_assignees(item.organization) if can_assign else []
    return render(request, 'psa/project_detail.html', {
        'item': item,
        'tickets': tickets,
        'tasks': tasks,
        'task_status_choices': ProjectTask.STATUS_CHOICES,
        'can_assign': can_assign,
        'eligible_assignees': eligible_assignees,
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
    from core.models import Organization
    if pk:
        item = get_object_or_404(RecurringTicketSchedule, pk=pk)
        org = item.organization
    else:
        item = None
        org = get_request_organization(request)

    queues = Queue.objects.filter(is_active=True)
    priorities = TicketPriority.objects.all()
    types = TicketType.objects.all()
    client_orgs = Organization.objects.filter(is_active=True).order_by('name')

    def _render(selected_org_id=None):
        # v3.17.129 — admins can pre-pick a default assignee for the schedule.
        scope_org_id = (selected_org_id if selected_org_id is not None
                        else (item.organization_id if item else (org.pk if org else None)))
        scope_org = None
        if scope_org_id:
            scope_org = Organization.objects.filter(pk=scope_org_id).first()
        can_assign = _can_assign(request, scope_org) if scope_org else (
            request.user.is_superuser or getattr(request, 'is_staff_user', False)
        )
        eligible_assignees = _eligible_assignees(scope_org) if can_assign else []
        return render(request, 'psa/recurring_form.html', {
            'item': item,
            'queues': queues, 'priorities': priorities, 'types': types,
            'frequency_choices': RecurringTicketSchedule.FREQUENCY_CHOICES,
            'client_orgs': client_orgs,
            'selected_client_org_id': scope_org_id,
            'can_assign': can_assign,
            'eligible_assignees': eligible_assignees,
        })

    if request.method == 'POST':
        posted_org_id = (request.POST.get('client_org_id') or '').strip()
        if posted_org_id:
            try:
                item_org = Organization.objects.get(pk=int(posted_org_id), is_active=True)
            except (Organization.DoesNotExist, ValueError, TypeError):
                messages.error(request, 'Invalid client.')
                return _render()
        else:
            item_org = org

        if item_org is None:
            messages.error(request, 'Please choose a client for this schedule.')
            return _render()

        name = (request.POST.get('name') or '').strip()
        subject = (request.POST.get('template_subject') or '').strip()
        if not name or not subject:
            messages.error(request, 'Name and template subject are required.')
            return _render(selected_org_id=item_org.pk)
        try:
            queue = queues.get(pk=request.POST.get('queue'))
            priority = priorities.get(pk=request.POST.get('priority'))
            ticket_type = types.get(pk=request.POST.get('ticket_type'))
        except (Queue.DoesNotExist, TicketPriority.DoesNotExist,
                TicketType.DoesNotExist, ValueError):
            messages.error(request, 'Pick a valid queue / priority / type.')
            return _render(selected_org_id=item_org.pk)

        if item is None:
            item = RecurringTicketSchedule(
                organization=item_org,
                queue=queue, priority=priority, ticket_type=ticket_type,
                next_run_at=timezone.now(),
                created_by=request.user,
            )
        else:
            item.organization = item_org
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
            item.client_org = Organization.objects.filter(pk=client_org_id).first()
        else:
            item.client_org = None

        # v3.17.129 — admins can set the default assignee for generated tickets.
        if 'assigned_to' in request.POST and _can_assign(request, item_org):
            from django.contrib.auth.models import User
            posted = (request.POST.get('assigned_to') or '').strip()
            if posted in ('', '0', 'unassigned'):
                item.assigned_to = None
            else:
                try:
                    cand = User.objects.get(pk=int(posted), is_active=True)
                    eligible_ids = set(
                        _eligible_assignees(item_org).values_list('id', flat=True)
                    )
                    if cand.id in eligible_ids:
                        item.assigned_to = cand
                except (User.DoesNotExist, ValueError, TypeError):
                    pass

        item.save()
        messages.success(request, f'Saved schedule "{item.name}".')
        return redirect('psa:recurring_list')

    return _render()


def _check_kb_perm(user, perm_name):
    """Cross-org KB permission check.

    Superusers / staff users always pass. For everyone else we walk all
    of their active memberships — having the permission on ANY membership
    grants it (typical for KB which is mostly cross-org / global).
    """
    if not user.is_authenticated:
        return False
    if user.is_superuser or getattr(user, 'is_staff', False):
        return True
    from accounts.models import Membership
    qs = Membership.objects.filter(user=user, is_active=True).select_related('role_template')
    for m in qs:
        perms = m.get_permissions()
        if getattr(perms, perm_name, False):
            return True
    return False


def _flatten_categories(cats_by_parent, parent_id=None, depth=0):
    """Walk a parent_id→children dict, yielding (cat, depth) in tree order."""
    for c in cats_by_parent.get(parent_id, []):
        yield c, depth
        yield from _flatten_categories(cats_by_parent, c.id, depth + 1)


@login_required
@require_psa_enabled
def kb_browse(request):
    """KB browser — wraps docs.Document filtered to global KB articles.
    Shows a category tree sidebar; ?category=<slug> filters to that
    category and all its descendants.
    """
    from docs.models import Document, DocumentCategory

    # Global categories tree (parent first, then children)
    cats_qs = DocumentCategory.objects.filter(
        organization__isnull=True
    ).order_by('order', 'name')
    cats = list(cats_qs)
    # Build a parent_id → [children] map for cheap recursive rendering
    cats_by_parent = {}
    for c in cats:
        cats_by_parent.setdefault(c.parent_id, []).append(c)
    roots = cats_by_parent.get(None, [])

    # Resolve ?category=<slug> selection
    cat_slug = (request.GET.get('category') or '').strip()
    selected_cat = None
    selected_breadcrumb = []
    descendant_ids = []
    if cat_slug:
        selected_cat = next((c for c in cats if c.slug == cat_slug), None)
        if selected_cat:
            # Walk up the tree to build breadcrumbs
            walk = selected_cat
            while walk:
                selected_breadcrumb.insert(0, walk)
                walk = walk.parent if walk.parent_id else None
            # Walk down to gather all descendant IDs (inclusive)
            stack = [selected_cat.id]
            descendant_ids = [selected_cat.id]
            while stack:
                pid = stack.pop()
                for child in cats_by_parent.get(pid, []):
                    descendant_ids.append(child.id)
                    stack.append(child.id)

    # Articles
    qs = Document.objects.filter(is_global=True, is_archived=False)
    if descendant_ids:
        qs = qs.filter(category_id__in=descendant_ids)

    q = (request.GET.get('q') or '').strip()
    if q:
        qs = qs.filter(models.Q(title__icontains=q) | models.Q(body__icontains=q))

    articles = qs.select_related('category', 'created_by').order_by('-updated_at')[:100]

    # Permission flags — drive button visibility in the template.
    can_edit_kb = _check_kb_perm(request.user, 'kb_edit_articles')
    can_move_kb = _check_kb_perm(request.user, 'kb_move_articles')
    can_manage_categories = _check_kb_perm(request.user, 'kb_manage_categories')

    # Pre-built flat category list for the "Move to →" dropdown.
    flat_cats = list(_flatten_categories(cats_by_parent))

    return render(request, 'psa/kb_browse.html', {
        'articles': articles,
        'query': q,
        'category_roots': roots,
        'cats_by_parent': cats_by_parent,
        'selected_cat': selected_cat,
        'selected_breadcrumb': selected_breadcrumb,
        'all_categories_count': cats_qs.count(),
        'can_edit_kb': can_edit_kb,
        'can_move_kb': can_move_kb,
        'can_manage_categories': can_manage_categories,
        'flat_categories': flat_cats,
    })


@login_required
@require_psa_enabled
@require_http_methods(['POST'])
def kb_move_articles(request):
    """Bulk-move KB articles between categories.

    POST body:
      article_ids[] -- list of Document IDs (must be is_global=True)
      target_category_id -- DocumentCategory.id, or empty/'' for "uncategorized"

    Permission: kb_move_articles (cross-org check via _check_kb_perm).
    """
    from django.core.exceptions import PermissionDenied
    from docs.models import Document, DocumentCategory

    if not _check_kb_perm(request.user, 'kb_move_articles'):
        raise PermissionDenied("You don't have permission to move KB articles.")

    raw_ids = request.POST.getlist('article_ids') or request.POST.getlist('article_ids[]')
    try:
        article_ids = [int(x) for x in raw_ids if str(x).strip()]
    except (TypeError, ValueError):
        article_ids = []

    if not article_ids:
        messages.error(request, 'Select at least one article to move.')
        return redirect(request.META.get('HTTP_REFERER') or reverse('psa:kb_browse'))

    target_raw = (request.POST.get('target_category_id') or '').strip()
    target_id = None
    target_cat = None
    if target_raw:
        try:
            target_id = int(target_raw)
        except (TypeError, ValueError):
            target_id = None
        if target_id is not None:
            target_cat = DocumentCategory.objects.filter(
                pk=target_id, organization__isnull=True
            ).first()
            if not target_cat:
                messages.error(request, 'Invalid target category.')
                return redirect(request.META.get('HTTP_REFERER') or reverse('psa:kb_browse'))

    # Single-query update across all selected global KB articles.
    qs = Document.objects.filter(pk__in=article_ids, is_global=True)
    n = qs.update(category=target_cat)

    AuditLog.log(
        user=request.user,
        action='update',
        organization=None,
        object_type='docs.Document',
        object_id=None,
        object_repr=f'KB bulk move ({n} article{"s" if n != 1 else ""})',
        description=(
            f'Moved {n} KB article(s) to '
            f'{target_cat.name if target_cat else "Uncategorized"} '
            f'(ids={article_ids})'
        ),
        ip_address=_client_ip(request),
        path=request.path,
    )

    if n:
        target_label = target_cat.name if target_cat else 'Uncategorized'
        messages.success(
            request,
            f'Moved {n} article{"s" if n != 1 else ""} to "{target_label}".'
        )
    else:
        messages.warning(request, 'No matching KB articles to move.')

    # Preserve the user's current category filter on redirect.
    next_url = request.POST.get('next') or reverse('psa:kb_browse')
    return redirect(next_url)


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
    from core.models import Organization
    if pk:
        item = get_object_or_404(Contract, pk=pk)
        org = item.organization
    else:
        item = None
        org = get_request_organization(request)

    client_orgs = Organization.objects.filter(is_active=True).order_by('name')

    def _render(selected_org_id=None):
        return render(request, 'psa/contract_form.html', {
            'item': item,
            'client_orgs': client_orgs,
            'contract_types': Contract.CONTRACT_TYPES,
            'status_choices': Contract.STATUS_CHOICES,
            'priorities': TicketPriority.objects.all().order_by('sort_order'),
            'selected_client_org_id': selected_org_id if selected_org_id is not None
                                       else (item.organization_id if item else (org.pk if org else None)),
        })

    if request.method == 'POST':
        posted_org_id = (request.POST.get('client_org_id') or '').strip()
        if posted_org_id:
            try:
                item_org = Organization.objects.get(pk=int(posted_org_id), is_active=True)
            except (Organization.DoesNotExist, ValueError, TypeError):
                messages.error(request, 'Invalid client.')
                return _render()
        else:
            item_org = org

        if item_org is None:
            messages.error(request, 'Please choose a client for this contract.')
            return _render()

        name = (request.POST.get('name') or '').strip()
        client_org_id = request.POST.get('client_org')
        if not name or not client_org_id:
            messages.error(request, 'Name and client are required.')
            return _render(selected_org_id=item_org.pk)
        try:
            client_org = Organization.objects.get(pk=client_org_id)
        except Organization.DoesNotExist:
            messages.error(request, 'Client not found.')
            return _render(selected_org_id=item_org.pk)

        if item is None:
            item = Contract(organization=item_org, client_org=client_org, name=name,
                            created_by=request.user, start_date=timezone.now().date())
        else:
            item.organization = item_org
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

        # Phase 1 contract-engine fields (rollover, auto-renew, role gates).
        # rolled_over_minutes / rollover_expires_at / parent_contract are
        # intentionally NOT exposed here — they're set by the renewal cron.
        from decimal import Decimal, InvalidOperation

        def _decimal(name, default='0'):
            try:
                return Decimal(request.POST.get(name) or default)
            except (InvalidOperation, TypeError):
                return Decimal(default)

        def _int(name, default=0):
            try:
                return int(request.POST.get(name) or default)
            except (TypeError, ValueError):
                return default

        def _bool(name):
            return (request.POST.get(name) or '').lower() in ('1', 'true', 'on', 'yes')

        def _csv_list(name):
            raw = (request.POST.get(name) or '').strip()
            if not raw:
                return []
            return [s.strip() for s in raw.split(',') if s.strip()]

        item.rollover_percent = _decimal('rollover_percent')
        item.rollover_expiry_days = _int('rollover_expiry_days')
        item.auto_renew = _bool('auto_renew')
        item.auto_renew_period_months = _int('auto_renew_period_months', 12) or 12
        item.proration_enabled = _bool('proration_enabled')
        item.billable_role_codes = _csv_list('billable_role_codes')
        item.excluded_role_codes = _csv_list('excluded_role_codes')

        # SLA matrix — POST keys are sla_<code>_response and sla_<code>_resolution.
        # Empty string = "use priority default" (omit from matrix).
        matrix = {}
        for p in TicketPriority.objects.all():
            r_raw = request.POST.get(f'sla_{p.code}_response') or ''
            x_raw = request.POST.get(f'sla_{p.code}_resolution') or ''
            entry = {}
            if r_raw.strip():
                try:
                    entry['response_minutes'] = max(0, int(r_raw))
                except ValueError:
                    pass
            if x_raw.strip():
                try:
                    entry['resolution_minutes'] = max(0, int(x_raw))
                except ValueError:
                    pass
            if entry:
                matrix[p.code] = entry
        item.sla_matrix = matrix
        item.save()

        # Bundle items — JSON payload from the dynamic editor on the form.
        # Reconcile by pk: update existing rows, create new ones, delete
        # those no longer in the submitted list.
        import json
        bundle_json = request.POST.get('bundle_items_json') or '[]'
        try:
            bundle_rows = json.loads(bundle_json)
        except (ValueError, TypeError):
            bundle_rows = []

        if isinstance(bundle_rows, list):
            from psa.models import ContractBundleItem
            from decimal import Decimal, InvalidOperation

            seen_pks = set()
            for i, row in enumerate(bundle_rows):
                if not isinstance(row, dict):
                    continue
                row_name = (row.get('name') or '').strip()
                if not row_name:
                    continue
                try:
                    qty = Decimal(str(row.get('quantity') or '1'))
                    price = Decimal(str(row.get('unit_price') or '0'))
                except InvalidOperation:
                    qty, price = Decimal('1'), Decimal('0')
                period = row.get('recurring_period') or 'monthly'
                if period not in ('one_time', 'monthly', 'quarterly', 'yearly'):
                    period = 'monthly'

                row_pk = row.get('pk') or ''
                existing = None
                if row_pk:
                    try:
                        existing = ContractBundleItem.objects.filter(
                            pk=int(row_pk), contract=item
                        ).first()
                    except (ValueError, TypeError):
                        existing = None
                if existing:
                    existing.name = row_name[:200]
                    existing.quantity = qty
                    existing.unit_label = (row.get('unit_label') or '')[:40]
                    existing.unit_price = price
                    existing.recurring_period = period
                    existing.sort_order = i
                    existing.save()
                    seen_pks.add(existing.pk)
                else:
                    new_b = ContractBundleItem.objects.create(
                        contract=item,
                        name=row_name[:200],
                        quantity=qty,
                        unit_label=(row.get('unit_label') or '')[:40],
                        unit_price=price,
                        recurring_period=period,
                        sort_order=i,
                    )
                    seen_pks.add(new_b.pk)

            # Delete rows the user removed (pks in DB but not in submitted JSON)
            item.bundle_items.exclude(pk__in=seen_pks).delete()

        AuditLog.log(
            user=request.user, action='update' if pk else 'create',
            organization=item_org,
            object_type='psa.Contract', object_id=item.pk,
            object_repr=str(item),
            description=f'{"Updated" if pk else "Created"} contract "{item.name}"',
            ip_address=_client_ip(request), path=request.path,
        )
        messages.success(request, f'Saved "{item.name}".')
        return redirect('psa:contract_detail', pk=item.pk)

    return _render()


@login_required
@require_psa_enabled
def contract_detail(request, pk):
    """Read-only contract detail with bundle items + profitability snapshot.

    v3.17.130 — Phase 1.2 of the contract engine.
    """
    org = get_request_organization(request)
    qs = Contract.objects.select_related('client_org', 'organization', 'parent_contract')
    if org is not None:
        qs = qs.filter(organization=org)
    elif not (request.user.is_superuser or getattr(request, 'is_staff_user', False)):
        qs = qs.none()
    item = get_object_or_404(qs, pk=pk)

    bundle_items = item.bundle_items.all()
    snapshot = item.profitability_snapshot()

    # Pull priority list so we can render the SLA-matrix preview with
    # human labels alongside any per-priority overrides.
    priorities = list(TicketPriority.objects.all().order_by('sort_order'))

    return render(request, 'psa/contract_detail.html', {
        'item': item,
        'bundle_items': bundle_items,
        'snapshot': snapshot,
        'priorities': priorities,
        'effective_hours_remaining': item.effective_hours_remaining(),
        'renewal_history': item.renewals.all().order_by('-start_date') if hasattr(item, 'renewals') else [],
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
    from core.models import Organization
    if pk:
        item = get_object_or_404(EmailIngestionConfig, pk=pk)
        org = item.organization
    else:
        item = None
        org = get_request_organization(request)

    queues = Queue.objects.filter(is_active=True)
    priorities = TicketPriority.objects.all()
    types = TicketType.objects.all()
    client_orgs = Organization.objects.filter(is_active=True).order_by('name')

    def _render(selected_org_id=None):
        return render(request, 'psa/email_config_form.html', {
            'item': item,
            'queues': queues, 'priorities': priorities, 'types': types,
            'client_orgs': client_orgs,
            'selected_client_org_id': selected_org_id if selected_org_id is not None
                                       else (item.organization_id if item else (org.pk if org else None)),
        })

    if request.method == 'POST':
        posted_org_id = (request.POST.get('client_org_id') or '').strip()
        if posted_org_id:
            try:
                item_org = Organization.objects.get(pk=int(posted_org_id), is_active=True)
            except (Organization.DoesNotExist, ValueError, TypeError):
                messages.error(request, 'Invalid client.')
                return _render()
        else:
            item_org = org

        if item_org is None:
            messages.error(request, 'Please choose a client for this email config.')
            return _render()

        name = (request.POST.get('name') or '').strip()
        host = (request.POST.get('imap_host') or '').strip()
        username = (request.POST.get('username') or '').strip()
        password = request.POST.get('password') or ''
        if not name or not host or not username:
            messages.error(request, 'Name, host, username are required.')
            return _render(selected_org_id=item_org.pk)
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
            return _render(selected_org_id=item_org.pk)

        if item is None:
            item = EmailIngestionConfig(organization=item_org, default_queue=queue,
                                        default_priority=priority, default_type=ticket_type)
        else:
            item.organization = item_org
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

    return _render()


# ---------------------------------------------------------------------------
# Phase 5 — Quotes + Expenses
# ---------------------------------------------------------------------------


@login_required
@require_psa_enabled
def quote_list(request):
    org = get_request_organization(request)
    qs = Quote.objects.select_related('client_org', 'created_by')
    if org is not None:
        qs = qs.filter(organization=org)
    elif not (request.user.is_superuser or getattr(request, 'is_staff_user', False)):
        qs = qs.none()
    status = request.GET.get('status')
    if status in {'draft', 'sent', 'accepted', 'rejected', 'expired'}:
        qs = qs.filter(status=status)
    return render(request, 'psa/quote_list.html', {
        'quotes': qs.order_by('-created_at')[:200],
        'status_filter': status or '',
    })


@login_required
@require_write
@require_psa_enabled
@require_http_methods(['GET', 'POST'])
def quote_form(request, pk=None):
    from core.models import Organization, SystemSetting
    if pk:
        item = get_object_or_404(Quote, pk=pk)
        org = item.organization
    else:
        item = None
        org = get_request_organization(request)

    client_orgs = Organization.objects.filter(is_active=True).order_by('name')

    def _render(selected_org_id=None):
        settings = SystemSetting.get_settings()
        default_tax_rate = settings.psa_default_tax_rate if not item else item.tax_rate
        return render(request, 'psa/quote_form.html', {
            'item': item,
            'client_orgs': client_orgs,
            'status_choices': Quote.STATUS_CHOICES,
            'line_items': item.line_items.all() if item else [],
            'default_tax_rate': default_tax_rate,
            'selected_client_org_id': selected_org_id if selected_org_id is not None
                                       else (item.organization_id if item else (org.pk if org else None)),
        })

    if request.method == 'POST':
        posted_org_id = (request.POST.get('client_org_id') or '').strip()
        if posted_org_id:
            try:
                item_org = Organization.objects.get(pk=int(posted_org_id), is_active=True)
            except (Organization.DoesNotExist, ValueError, TypeError):
                messages.error(request, 'Invalid client.')
                return _render()
        else:
            item_org = org

        if item_org is None:
            messages.error(request, 'Please choose a client for this quote.')
            return _render()

        title = (request.POST.get('title') or '').strip()
        client_org_id = request.POST.get('client_org')
        if not title or not client_org_id:
            messages.error(request, 'Title and client are required.')
            return _render(selected_org_id=item_org.pk)
        try:
            client_org = Organization.objects.get(pk=client_org_id)
        except Organization.DoesNotExist:
            messages.error(request, 'Client not found.')
            return _render(selected_org_id=item_org.pk)

        if item is None:
            item = Quote(organization=item_org, client_org=client_org, title=title,
                         created_by=request.user)
        else:
            item.organization = item_org
            item.client_org = client_org
            item.title = title
        item.description = (request.POST.get('description') or '').strip()
        item.status = request.POST.get('status') or 'draft'
        item.valid_until = request.POST.get('valid_until') or None
        try:
            item.tax_rate = request.POST.get('tax_rate') or 0
        except (TypeError, ValueError):
            item.tax_rate = 0
        item.save()

        # Replace line items if posted
        line_descs = request.POST.getlist('li_description')
        line_qtys = request.POST.getlist('li_quantity')
        line_prices = request.POST.getlist('li_unit_price')
        if line_descs:
            item.line_items.all().delete()
            for i, (d, q, p) in enumerate(zip(line_descs, line_qtys, line_prices)):
                if not (d or '').strip():
                    continue
                try:
                    qf = float(q or 1)
                    pf = float(p or 0)
                except ValueError:
                    qf, pf = 1, 0
                QuoteLineItem.objects.create(
                    quote=item, sort_order=i,
                    description=d.strip()[:300], quantity=qf, unit_price=pf,
                )
        item.recompute_totals()

        messages.success(request, f'Saved "{item.quote_number}".')
        return redirect('psa:quote_list')

    return _render()


@login_required
@require_write
@require_psa_enabled
@require_http_methods(['POST'])
def quote_accept(request, pk):
    org = get_request_organization(request)
    qs = Quote.objects.all()
    if org is not None:
        qs = qs.filter(organization=org)
    item = get_object_or_404(qs, pk=pk)
    create = request.POST.get('create_ticket') == 'on'
    queue = Queue.objects.filter(is_active=True).first()
    priority = TicketPriority.objects.first()
    ttype = TicketType.objects.first()
    status = TicketStatus.objects.filter(slug='new').first()
    item.mark_accepted(user=request.user, create_ticket=create,
                       queue=queue, priority=priority,
                       ticket_type=ttype, status=status)
    AuditLog.log(
        user=request.user, action='update',
        organization=org or item.organization,
        object_type='psa.Quote', object_id=item.pk,
        object_repr=item.quote_number,
        description=f'Accepted quote {item.quote_number}; ticket={item.converted_ticket_id or "—"}',
        ip_address=_client_ip(request), path=request.path,
    )
    if item.converted_ticket:
        messages.success(request, f'Accepted. Ticket {item.converted_ticket.ticket_number} created.')
        return redirect('psa:ticket_detail', ticket_number=item.converted_ticket.ticket_number)
    messages.success(request, 'Quote accepted.')
    return redirect('psa:quote_list')


@login_required
@require_write
@require_psa_enabled
@require_http_methods(['POST'])
def ticket_expense_add(request, ticket_number):
    org = get_request_organization(request)
    qs = Ticket.objects.all()
    if org is not None:
        qs = qs.filter(organization=org)
    ticket = get_object_or_404(qs, ticket_number=ticket_number)

    description = (request.POST.get('description') or '').strip()
    if not description:
        messages.error(request, 'Description required.')
        return redirect('psa:ticket_detail', ticket_number=ticket_number)
    try:
        amount = float(request.POST.get('amount') or 0)
    except (TypeError, ValueError):
        amount = 0

    expense = TicketExpense.objects.create(
        ticket=ticket, user=request.user,
        category=request.POST.get('category') or 'other',
        description=description[:300],
        amount=amount,
        currency=(request.POST.get('currency') or 'USD')[:8],
        incurred_on=request.POST.get('incurred_on') or timezone.now().date(),
        is_billable=request.POST.get('is_billable') == 'on',
        is_reimbursable=request.POST.get('is_reimbursable') == 'on',
        receipt_file=request.FILES.get('receipt_file'),
    )
    AuditLog.log(
        user=request.user, action='create',
        organization=ticket.organization,
        object_type='psa.TicketExpense', object_id=expense.pk,
        object_repr=expense.description,
        description=f'Added {expense.amount} {expense.currency} expense to {ticket.ticket_number}',
        ip_address=_client_ip(request), path=request.path,
    )
    messages.success(request, f'Expense added: {amount:.2f} {expense.currency}.')
    return redirect('psa:ticket_detail', ticket_number=ticket_number)


# ---------------------------------------------------------------------------
# Project tasks (Workstream 3 expansion)
# ---------------------------------------------------------------------------


@login_required
@require_write
@require_psa_enabled
@require_http_methods(['POST'])
def project_task_add(request, pk):
    org = get_request_organization(request)
    qs = Project.objects.all()
    if org is not None:
        qs = qs.filter(organization=org)
    project = get_object_or_404(qs, pk=pk)

    title = (request.POST.get('title') or '').strip()
    if not title:
        messages.error(request, 'Task title is required.')
        return redirect('psa:project_detail', pk=project.pk)

    # v3.17.129 — admins can pick the task's assignee from any eligible tech
    # in the project's org (falls back to creator-assignment if not allowed).
    initial_assignee = None
    posted_assignee = (request.POST.get('assigned_to') or '').strip()
    if posted_assignee and _can_assign(request, project.organization):
        from django.contrib.auth.models import User
        try:
            cand = User.objects.get(pk=int(posted_assignee), is_active=True)
            eligible_ids = set(
                _eligible_assignees(project.organization).values_list('id', flat=True)
            )
            if cand.id in eligible_ids:
                initial_assignee = cand
        except (User.DoesNotExist, ValueError, TypeError):
            pass

    from .models import ProjectTask
    ProjectTask.objects.create(
        project=project,
        title=title[:300],
        description=(request.POST.get('description') or '').strip()[:5000],
        is_milestone=request.POST.get('is_milestone') == 'on',
        due_date=request.POST.get('due_date') or None,
        created_by=request.user,
        assigned_to=initial_assignee,
        sort_order=project.tasks.count(),
    )
    messages.success(request, f'Added task "{title}".')
    return redirect('psa:project_detail', pk=project.pk)


@login_required
@require_write
@require_psa_enabled
@require_http_methods(['POST'])
def project_task_update(request, task_pk):
    from .models import ProjectTask
    org = get_request_organization(request)
    qs = ProjectTask.objects.select_related('project')
    if org is not None:
        qs = qs.filter(project__organization=org)
    task = get_object_or_404(qs, pk=task_pk)

    new_status = request.POST.get('status')
    changed = False
    if new_status in {'todo', 'in_progress', 'blocked', 'done', 'cancelled'}:
        task.status = new_status
        changed = True
    # v3.17.129 — admins can reassign a project task. Posting `assigned_to`
    # with empty value clears the assignee.
    if 'assigned_to' in request.POST and _can_assign(request, task.project.organization):
        from django.contrib.auth.models import User
        posted = (request.POST.get('assigned_to') or '').strip()
        if posted in ('', '0', 'unassigned'):
            task.assigned_to = None
            changed = True
        else:
            try:
                cand = User.objects.get(pk=int(posted), is_active=True)
                eligible_ids = set(
                    _eligible_assignees(task.project.organization).values_list('id', flat=True)
                )
                if cand.id in eligible_ids:
                    task.assigned_to = cand
                    changed = True
            except (User.DoesNotExist, ValueError, TypeError):
                pass
    if changed:
        task.save()
        messages.success(request, f'Updated "{task.title}".')
    return redirect('psa:project_detail', pk=task.project.pk)


@login_required
@require_write
@require_psa_enabled
@require_http_methods(['POST'])
def project_task_delete(request, task_pk):
    from .models import ProjectTask
    org = get_request_organization(request)
    qs = ProjectTask.objects.select_related('project')
    if org is not None:
        qs = qs.filter(project__organization=org)
    task = get_object_or_404(qs, pk=task_pk)
    project_pk = task.project_id
    title = task.title
    task.delete()
    messages.success(request, f'Deleted "{title}".')
    return redirect('psa:project_detail', pk=project_pk)


# ---------------------------------------------------------------------------
# Workflow Rules (Workstream 9)
# ---------------------------------------------------------------------------


@login_required
@require_psa_enabled
def workflow_rule_list(request):
    """Workflow rules are MSP-level config — they apply to ANY ticket. No
    client filter, even when a client is currently in scope."""
    from .models import WorkflowRule
    qs = WorkflowRule.objects.select_related('organization').order_by('sort_order', 'name')
    return render(request, 'psa/workflow_rule_list.html', {
        'rules': qs[:200],
    })


@login_required
@require_admin
@require_psa_enabled
@require_http_methods(['GET', 'POST'])
def workflow_rule_form(request, pk=None):
    """Create/edit an MSP-level workflow rule. The rule's organization is
    optional — leave blank to apply to every client's tickets, or pick a
    specific client to scope it. No 'pick a client first' requirement."""
    from .models import WorkflowRule
    from core.models import Organization
    import json as _json
    item = get_object_or_404(WorkflowRule, pk=pk) if pk else None

    if request.method == 'POST':
        name = (request.POST.get('name') or '').strip()
        trigger = request.POST.get('trigger') or ''
        if not name or trigger not in {c[0] for c in WorkflowRule.TRIGGER_CHOICES}:
            messages.error(request, 'Name and a valid trigger are required.')
            return redirect(request.path)
        try:
            conditions = _json.loads(request.POST.get('conditions') or '{}')
        except _json.JSONDecodeError:
            messages.error(request, 'Conditions must be valid JSON.')
            return redirect(request.path)
        try:
            actions = _json.loads(request.POST.get('actions') or '[]')
        except _json.JSONDecodeError:
            messages.error(request, 'Actions must be valid JSON.')
            return redirect(request.path)
        if not isinstance(conditions, dict):
            messages.error(request, 'Conditions must be a JSON object.')
            return redirect(request.path)
        if not isinstance(actions, list):
            messages.error(request, 'Actions must be a JSON list.')
            return redirect(request.path)

        # Optional client scope
        scoped_org = None
        org_id = (request.POST.get('organization') or '').strip()
        if org_id:
            try:
                scoped_org = Organization.objects.get(pk=int(org_id))
            except (Organization.DoesNotExist, ValueError):
                scoped_org = None

        if item is None:
            item = WorkflowRule(name=name, trigger=trigger, created_by=request.user,
                                organization=scoped_org)
        else:
            item.name = name
            item.trigger = trigger
            item.organization = scoped_org
        item.description = (request.POST.get('description') or '').strip()
        item.conditions = conditions
        item.actions = actions
        item.is_active = request.POST.get('is_active') == 'on'
        try:
            item.sort_order = max(0, int(request.POST.get('sort_order') or 0))
        except ValueError:
            item.sort_order = 0
        item.save()
        messages.success(request, f'Saved rule "{item.name}".')
        return redirect('psa:workflow_rule_list')

    return render(request, 'psa/workflow_rule_form.html', {
        'item': item,
        'trigger_choices': WorkflowRule.TRIGGER_CHOICES,
        'conditions_pretty': _json.dumps(item.conditions if item else {}, indent=2),
        'actions_pretty': _json.dumps(item.actions if item else [], indent=2),
        'conditions_json': _json.dumps(item.conditions if item else {}),
        'actions_json': _json.dumps(item.actions if item else []),
        'client_orgs': Organization.objects.filter(is_active=True).order_by('name'),
        'priorities': TicketPriority.objects.all().order_by('sort_order'),
        'queues': Queue.objects.filter(is_active=True).order_by('name'),
        'statuses': TicketStatus.objects.all().order_by('sort_order'),
    })


@login_required
@require_admin
@require_psa_enabled
@require_http_methods(['POST'])
def workflow_rule_delete(request, pk):
    from .models import WorkflowRule
    org = get_request_organization(request)
    qs = WorkflowRule.objects.all()
    if org is not None:
        qs = qs.filter(organization=org)
    item = get_object_or_404(qs, pk=pk)
    name = item.name
    item.delete()
    messages.success(request, f'Deleted "{name}".')
    return redirect('psa:workflow_rule_list')


# ---------------------------------------------------------------------------
# Dispatch board — weekly grid of assigned tickets per tech
# ---------------------------------------------------------------------------


@login_required
@require_psa_enabled
def dispatch_board(request):
    """
    Dispatch board: 7-day grid + an Other column for tickets outside the
    window or without a due date. Shows ALL tickets (open + closed) so
    nothing is hidden — closed/resolved still appear so you can see what
    a tech wrapped up. Unassigned tickets get their own row at the top.
    Overdue open tickets surface in a separate panel above the grid.
    """
    from datetime import date, timedelta

    org = get_request_organization(request)
    qs = Ticket.objects.select_related('assigned_to', 'priority', 'status', 'organization')
    if org is not None:
        qs = qs.filter(organization=org)

    days = []
    today = date.today()
    for i in range(7):
        d = today + timedelta(days=i)
        days.append(d)

    # Cells use day-key for the 7 columns + the literal string 'other' for
    # tickets without a due date OR with a due date outside the window.
    OTHER = 'other'
    cells = {}
    techs = {}
    unassigned_by_day = {d: [] for d in days}
    unassigned_by_day[OTHER] = []
    overdue = []

    for t in qs.order_by('-created_at')[:1000]:
        due = t.resolution_due_at or t.first_response_due_at
        is_open = not (t.status_id and t.status.is_terminal)
        bucket = OTHER  # default for no-due-date or outside window
        if due:
            d_local = timezone.localtime(due).date() if hasattr(due, 'date') else due
            if d_local < today and is_open:
                overdue.append(t)
                continue
            if today <= d_local <= today + timedelta(days=6):
                bucket = d_local
        if t.assigned_to_id:
            techs[t.assigned_to_id] = t.assigned_to
            cells.setdefault((t.assigned_to_id, bucket), []).append(t)
        else:
            unassigned_by_day[bucket].append(t)

    # Admins see ALL eligible techs (org members + staff/superusers) as rows
    # so they can drag a card to a tech who currently has zero tickets.
    # Non-admins still only see techs who have tickets in the visible scope.
    can_assign = _can_assign(request, org)
    if can_assign:
        for u in _eligible_assignees(org):
            techs.setdefault(u.id, u)

    techs_sorted = sorted(techs.values(), key=lambda u: (u.username or '').lower())
    rows = []
    for u in techs_sorted:
        rows.append({
            'tech': u,
            'cells': [cells.get((u.id, d), []) for d in days],
            'other': cells.get((u.id, OTHER), []),
        })

    # Phase 2.3 — skill ranking. For each ticket card, top 5 candidates
    # by skill+availability score. Only computed for admins who can assign.
    skill_rankings = {}
    if can_assign:
        try:
            from resourcing.views import rank_techs_for_ticket
            seen = set()
            all_tickets_in_view = []
            for bucket_list in cells.values():
                for t in bucket_list:
                    if t.pk not in seen:
                        seen.add(t.pk); all_tickets_in_view.append(t)
            for bucket_list in unassigned_by_day.values():
                for t in bucket_list:
                    if t.pk not in seen:
                        seen.add(t.pk); all_tickets_in_view.append(t)
            for t in overdue:
                if t.pk not in seen:
                    seen.add(t.pk); all_tickets_in_view.append(t)
            skill_rankings = {
                t.pk: rank_techs_for_ticket(t)[:5]
                for t in all_tickets_in_view
            }
        except Exception:
            skill_rankings = {}

    return render(request, 'psa/dispatch_board.html', {
        'days': days,
        'rows': rows,
        'unassigned_by_day': [unassigned_by_day[d] for d in days],
        'unassigned_other': unassigned_by_day[OTHER],
        'overdue': overdue,
        'can_assign': can_assign,
        'skill_rankings': skill_rankings,
    })


# ---------------------------------------------------------------------------
# Phase 8 — Invoices + Payments + Accounting integration handoff
# ---------------------------------------------------------------------------


@login_required
@require_psa_enabled
def invoice_list(request):
    from .models import Invoice
    org = get_request_organization(request)
    qs = Invoice.objects.select_related('client_org', 'created_by')
    if org is not None:
        qs = qs.filter(organization=org)
    elif not (request.user.is_superuser or getattr(request, 'is_staff_user', False)):
        qs = qs.none()
    status = request.GET.get('status')
    if status in {'draft', 'sent', 'partial', 'paid', 'overdue', 'void'}:
        qs = qs.filter(status=status)
    return render(request, 'psa/invoice_list.html', {
        'invoices': qs.order_by('-invoice_date', '-created_at')[:200],
        'status_filter': status or '',
    })


@login_required
@require_psa_enabled
def invoice_detail(request, pk):
    from .models import Invoice
    org = get_request_organization(request)
    qs = Invoice.objects.select_related('client_org', 'source_quote', 'source_ticket', 'source_contract')
    if org is not None:
        qs = qs.filter(organization=org)
    item = get_object_or_404(qs, pk=pk)
    return render(request, 'psa/invoice_detail.html', {
        'item': item,
        'line_items': item.line_items.all(),
        'payments': item.payments.all(),
    })


@login_required
@require_write
@require_psa_enabled
@require_http_methods(['GET', 'POST'])
def invoice_form(request, pk=None):
    from .models import Invoice, InvoiceLineItem
    from core.models import Organization, SystemSetting
    from datetime import date as _date
    if pk:
        item = get_object_or_404(Invoice, pk=pk)
        org = item.organization
    else:
        item = None
        org = get_request_organization(request)

    client_orgs = Organization.objects.filter(is_active=True).order_by('name')

    def _render(selected_org_id=None):
        settings = SystemSetting.get_settings()
        default_tax_rate = settings.psa_default_tax_rate if not item else item.tax_rate
        default_currency = settings.psa_default_currency if not item else item.currency
        return render(request, 'psa/invoice_form.html', {
            'item': item,
            'client_orgs': client_orgs,
            'status_choices': Invoice.STATUS_CHOICES,
            'line_items': item.line_items.all() if item else [],
            'default_tax_rate': default_tax_rate,
            'default_currency': default_currency,
            'selected_client_org_id': selected_org_id if selected_org_id is not None
                                       else (item.organization_id if item else (org.pk if org else None)),
        })

    if request.method == 'POST':
        posted_org_id = (request.POST.get('client_org_id') or '').strip()
        if posted_org_id:
            try:
                item_org = Organization.objects.get(pk=int(posted_org_id), is_active=True)
            except (Organization.DoesNotExist, ValueError, TypeError):
                messages.error(request, 'Invalid client.')
                return _render()
        else:
            item_org = org

        if item_org is None:
            messages.error(request, 'Please choose a client for this invoice.')
            return _render()

        title = (request.POST.get('title') or '').strip()
        client_org_id = request.POST.get('client_org')
        if not title or not client_org_id:
            messages.error(request, 'Title and client are required.')
            return _render(selected_org_id=item_org.pk)
        try:
            client_org = Organization.objects.get(pk=client_org_id)
        except Organization.DoesNotExist:
            messages.error(request, 'Client not found.')
            return _render(selected_org_id=item_org.pk)

        if item is None:
            item = Invoice(organization=item_org, client_org=client_org, title=title,
                           invoice_date=_date.today(), created_by=request.user)
        else:
            item.organization = item_org
            item.client_org = client_org
            item.title = title
        item.description = (request.POST.get('description') or '').strip()
        item.status = request.POST.get('status') or 'draft'
        item.invoice_date = request.POST.get('invoice_date') or item.invoice_date
        item.due_date = request.POST.get('due_date') or None
        item.currency = (request.POST.get('currency') or 'USD')[:8]
        try:
            item.tax_rate = request.POST.get('tax_rate') or 0
        except (TypeError, ValueError):
            item.tax_rate = 0
        item.notes = (request.POST.get('notes') or '').strip()
        item.save()

        # Replace line items
        descs = request.POST.getlist('li_description')
        qtys = request.POST.getlist('li_quantity')
        prices = request.POST.getlist('li_unit_price')
        if descs:
            item.line_items.all().delete()
            for i, (d, q, p) in enumerate(zip(descs, qtys, prices)):
                if not (d or '').strip():
                    continue
                try:
                    qf = float(q or 1)
                    pf = float(p or 0)
                except ValueError:
                    qf, pf = 1, 0
                InvoiceLineItem.objects.create(
                    invoice=item, sort_order=i,
                    description=d.strip()[:300], quantity=qf, unit_price=pf,
                )
        item.recompute_totals()
        AuditLog.log(
            user=request.user, action='update' if pk else 'create',
            organization=item_org,
            object_type='psa.Invoice', object_id=item.pk,
            object_repr=item.invoice_number,
            description=f'{"Updated" if pk else "Created"} invoice {item.invoice_number}',
            ip_address=_client_ip(request), path=request.path,
        )
        messages.success(request, f'Saved {item.invoice_number}.')
        return redirect('psa:invoice_detail', pk=item.pk)

    return _render()


@login_required
@require_write
@require_psa_enabled
@require_http_methods(['POST'])
def invoice_from_ticket(request, ticket_number):
    """Generate a draft invoice from a ticket's billable time + expenses."""
    from .models import Invoice, InvoiceLineItem
    from datetime import date as _date
    org = get_request_organization(request)
    qs = Ticket.objects.select_related('organization')
    if org is not None:
        qs = qs.filter(organization=org)
    ticket = get_object_or_404(qs, ticket_number=ticket_number)

    # Default rate from contract, fallback to 0
    contract = Contract.for_ticket(ticket)
    rate = float(contract.hourly_rate) if contract else 0.0

    invoice = Invoice.objects.create(
        organization=org or ticket.organization,
        client_org=ticket.organization,
        title=f'Invoice for {ticket.ticket_number} — {ticket.subject}'[:300],
        description=f'Billable time + expenses for ticket {ticket.ticket_number}',
        invoice_date=_date.today(),
        source_ticket=ticket,
        source_contract=contract,
        created_by=request.user,
    )
    sort_order = 0
    for te in ticket.time_entries.filter(is_billable=True):
        if not te.duration_minutes:
            continue
        InvoiceLineItem.objects.create(
            invoice=invoice, sort_order=sort_order,
            description=f'Time: {te.notes or "work"}'[:300],
            quantity=round(te.duration_minutes / 60.0, 2),
            unit_price=rate,
            source='time', source_id=str(te.pk),
        )
        sort_order += 1
    for e in ticket.expenses.filter(is_billable=True):
        InvoiceLineItem.objects.create(
            invoice=invoice, sort_order=sort_order,
            description=f'Expense: {e.description}'[:300],
            quantity=1, unit_price=float(e.amount),
            source='expense', source_id=str(e.pk),
        )
        sort_order += 1
    invoice.recompute_totals()
    messages.success(request, f'Draft invoice {invoice.invoice_number} created from {ticket.ticket_number}.')
    return redirect('psa:invoice_detail', pk=invoice.pk)


@login_required
@require_write
@require_psa_enabled
@require_http_methods(['POST'])
def payment_add(request, invoice_pk):
    from .models import Invoice, Payment
    from datetime import date as _date
    org = get_request_organization(request)
    qs = Invoice.objects.all()
    if org is not None:
        qs = qs.filter(organization=org)
    invoice = get_object_or_404(qs, pk=invoice_pk)
    try:
        amount = float(request.POST.get('amount') or 0)
    except (TypeError, ValueError):
        amount = 0
    if amount <= 0:
        messages.error(request, 'Amount must be positive.')
        return redirect('psa:invoice_detail', pk=invoice.pk)
    Payment.objects.create(
        invoice=invoice, amount=amount,
        paid_on=request.POST.get('paid_on') or _date.today(),
        method=request.POST.get('method') or 'ach',
        reference=(request.POST.get('reference') or '')[:120],
        notes=(request.POST.get('notes') or '').strip(),
        created_by=request.user,
    )
    messages.success(request, f'Payment of {amount:.2f} recorded.')
    return redirect('psa:invoice_detail', pk=invoice.pk)


@login_required
@require_admin
@require_psa_enabled
@require_http_methods(['POST'])
def invoice_push_to_accounting(request, pk):
    """Push the invoice to the configured accounting provider for this org."""
    from .models import Invoice
    from integrations.models import AccountingConnection
    from integrations.providers.accounting import get_accounting_provider
    org = get_request_organization(request)
    qs = Invoice.objects.all()
    if org is not None:
        qs = qs.filter(organization=org)
    invoice = get_object_or_404(qs, pk=pk)

    conn = AccountingConnection.objects.filter(
        organization=invoice.organization,
        is_active=True, sync_enabled=True,
    ).first()
    if conn is None:
        messages.error(request, 'No active accounting connection with sync enabled. Configure one in Integrations.')
        return redirect('psa:invoice_detail', pk=invoice.pk)

    provider = get_accounting_provider(conn)
    if provider is None:
        messages.error(request, 'Provider class not registered.')
        return redirect('psa:invoice_detail', pk=invoice.pk)

    try:
        result = provider.push_invoice(invoice)
    except Exception as exc:
        messages.error(request, f'Push failed: {exc}')
        return redirect('psa:invoice_detail', pk=invoice.pk)

    if result.get('success'):
        messages.success(request, f'Pushed to {conn.get_provider_type_display()} (id {result.get("invoice_id")}).')
    else:
        messages.error(request, f'Push failed: {result.get("error", "unknown")}')
    return redirect('psa:invoice_detail', pk=invoice.pk)


# ---------------------------------------------------------------------------
# Phase 9 — PDF downloads + customer email for quotes and invoices
# ---------------------------------------------------------------------------


@login_required
@require_psa_enabled
def quote_pdf(request, pk):
    from django.http import HttpResponse
    from .pdf import render_quote_pdf
    org = get_request_organization(request)
    qs = Quote.objects.all()
    if org is not None:
        qs = qs.filter(organization=org)
    quote = get_object_or_404(qs.select_related('client_org', 'organization'), pk=pk)
    sign_url = request.build_absolute_uri(
        f'/portal/quote/{quote.customer_token}/sign/'
    ) if quote.customer_token and quote.status not in ('accepted', 'rejected') else ''
    pdf_bytes = render_quote_pdf(quote, sign_url=sign_url)
    resp = HttpResponse(pdf_bytes, content_type='application/pdf')
    disposition = 'attachment' if request.GET.get('download') else 'inline'
    resp['Content-Disposition'] = f'{disposition}; filename="{quote.quote_number}.pdf"'
    return resp


@login_required
@require_psa_enabled
def invoice_pdf(request, pk):
    from django.http import HttpResponse
    from .pdf import render_invoice_pdf
    org = get_request_organization(request)
    qs = Invoice.objects.all()
    if org is not None:
        qs = qs.filter(organization=org)
    invoice = get_object_or_404(qs.select_related('client_org', 'organization'), pk=pk)
    pdf_bytes = render_invoice_pdf(invoice)
    resp = HttpResponse(pdf_bytes, content_type='application/pdf')
    disposition = 'attachment' if request.GET.get('download') else 'inline'
    resp['Content-Disposition'] = f'{disposition}; filename="{invoice.invoice_number}.pdf"'
    return resp


@login_required
@require_write
@require_psa_enabled
@require_http_methods(['POST'])
def quote_email(request, pk):
    from .pdf import email_quote
    org = get_request_organization(request)
    qs = Quote.objects.all()
    if org is not None:
        qs = qs.filter(organization=org)
    quote = get_object_or_404(qs.select_related('client_org', 'organization'), pk=pk)
    recipient = (request.POST.get('recipient') or '').strip()
    subject = (request.POST.get('subject') or '').strip()
    body = (request.POST.get('body') or '').strip()
    if not recipient or '@' not in recipient:
        messages.error(request, 'Provide a valid recipient email.')
        return redirect('psa:quote_list')
    try:
        ok = email_quote(quote, recipient=recipient, subject=subject, body=body, request=request)
    except Exception as exc:
        messages.error(request, f'Email failed: {exc}')
        return redirect('psa:quote_list')
    if ok:
        # Mark sent if it was draft
        if quote.status == 'draft':
            quote.status = 'sent'
            quote.sent_at = timezone.now()
            quote.save(update_fields=['status', 'sent_at', 'updated_at'])
        AuditLog.log(
            user=request.user, action='update',
            organization=quote.organization,
            object_type='psa.Quote', object_id=quote.pk,
            object_repr=quote.quote_number,
            description=f'Emailed quote {quote.quote_number} to {recipient}',
            ip_address=_client_ip(request), path=request.path,
        )
        messages.success(request, f'Sent quote {quote.quote_number} to {recipient}.')
    else:
        messages.error(request, 'Email send returned 0 recipients.')
    return redirect('psa:quote_list')


@login_required
@require_write
@require_psa_enabled
@require_http_methods(['POST'])
def invoice_email(request, pk):
    from .pdf import email_invoice
    org = get_request_organization(request)
    qs = Invoice.objects.all()
    if org is not None:
        qs = qs.filter(organization=org)
    invoice = get_object_or_404(qs.select_related('client_org', 'organization'), pk=pk)
    recipient = (request.POST.get('recipient') or '').strip()
    subject = (request.POST.get('subject') or '').strip()
    body = (request.POST.get('body') or '').strip()
    if not recipient or '@' not in recipient:
        messages.error(request, 'Provide a valid recipient email.')
        return redirect('psa:invoice_detail', pk=invoice.pk)
    try:
        ok = email_invoice(invoice, recipient=recipient, subject=subject, body=body)
    except Exception as exc:
        messages.error(request, f'Email failed: {exc}')
        return redirect('psa:invoice_detail', pk=invoice.pk)
    if ok:
        if invoice.status == 'draft':
            invoice.status = 'sent'
            invoice.sent_at = timezone.now()
            invoice.save(update_fields=['status', 'sent_at', 'updated_at'])
        AuditLog.log(
            user=request.user, action='update',
            organization=invoice.organization,
            object_type='psa.Invoice', object_id=invoice.pk,
            object_repr=invoice.invoice_number,
            description=f'Emailed invoice {invoice.invoice_number} to {recipient}',
            ip_address=_client_ip(request), path=request.path,
        )
        messages.success(request, f'Sent invoice {invoice.invoice_number} to {recipient}.')
    else:
        messages.error(request, 'Email send returned 0 recipients.')
    return redirect('psa:invoice_detail', pk=invoice.pk)


@login_required
@require_psa_enabled
def quote_detail(request, pk):
    org = get_request_organization(request)
    qs = Quote.objects.select_related('client_org', 'organization', 'created_by')
    if org is not None:
        qs = qs.filter(organization=org)
    item = get_object_or_404(qs, pk=pk)
    return render(request, 'psa/quote_detail.html', {
        'item': item,
        'line_items': item.line_items.all(),
    })


# ---------------------------------------------------------------------------
# Phase 10 — Client account view + Charges + Aging report
# ---------------------------------------------------------------------------


@login_required
@require_psa_enabled
def client_account(request, org_id):
    """Per-client account summary: balance, aging, invoices, charges,
    payments, unbilled time + expenses ready to invoice."""
    from .models import Charge, Invoice, Payment, get_psa_balance
    from core.models import Organization
    client = get_object_or_404(Organization, pk=org_id)

    org = get_request_organization(request)
    summary = get_psa_balance(client, msp_org=org)
    invoices = Invoice.objects.filter(client_org=client)
    charges = Charge.objects.filter(client_org=client)
    if org is not None:
        invoices = invoices.filter(organization=org)
        charges = charges.filter(organization=org)

    payments = Payment.objects.filter(invoice__client_org=client)
    if org is not None:
        payments = payments.filter(invoice__organization=org)

    # Unbilled work — time entries on this client's tickets that are
    # billable AND don't have an invoice line linked to them yet.
    unbilled_time = TicketTimeEntry.objects.filter(
        ticket__organization=client, is_billable=True,
    ).exclude(duration_minutes=0).select_related('ticket', 'user').order_by('-started_at')[:50]
    unbilled_expenses = TicketExpense.objects.filter(
        ticket__organization=client, is_billable=True,
    ).select_related('ticket', 'user').order_by('-incurred_on')[:50]

    return render(request, 'psa/client_account.html', {
        'client': client,
        'summary': summary,
        'invoices': invoices.select_related('client_org').order_by('-invoice_date')[:100],
        'charges': charges.order_by('-charge_date')[:100],
        'payments': payments.select_related('invoice').order_by('-paid_on')[:50],
        'unbilled_time': unbilled_time,
        'unbilled_expenses': unbilled_expenses,
    })


@login_required
@require_write
@require_psa_enabled
@require_http_methods(['POST'])
def charge_add(request, org_id):
    """Quick-add a one-off charge against a client's account."""
    from .models import Charge
    from core.models import Organization
    from datetime import date as _date
    org = get_request_organization(request)
    client = get_object_or_404(Organization, pk=org_id)

    description = (request.POST.get('description') or '').strip()
    if not description:
        messages.error(request, 'Description is required.')
        return redirect('psa:client_account', org_id=org_id)
    try:
        amount = float(request.POST.get('amount') or 0)
    except (TypeError, ValueError):
        amount = 0
    if amount <= 0:
        messages.error(request, 'Amount must be positive.')
        return redirect('psa:client_account', org_id=org_id)

    Charge.objects.create(
        organization=org or client,
        client_org=client,
        description=description[:300],
        amount=amount,
        currency=(request.POST.get('currency') or 'USD')[:8],
        charge_date=request.POST.get('charge_date') or _date.today(),
        is_credit=request.POST.get('is_credit') == 'on',
        is_recurring=request.POST.get('is_recurring') == 'on',
        recurrence=request.POST.get('recurrence') or 'once',
        notes=(request.POST.get('notes') or '').strip(),
        created_by=request.user,
    )
    AuditLog.log(
        user=request.user, action='create',
        organization=org or client,
        object_type='psa.Charge', object_id=0,
        object_repr=description,
        description=f'{"Credit" if request.POST.get("is_credit") == "on" else "Charge"} {amount:.2f} on {client.name}: {description}',
        ip_address=_client_ip(request), path=request.path,
    )
    messages.success(request, f'Recorded {"credit" if request.POST.get("is_credit") == "on" else "charge"} of {amount:.2f}.')
    return redirect('psa:client_account', org_id=org_id)


@login_required
@require_write
@require_psa_enabled
@require_http_methods(['POST'])
def charge_invoice(request, org_id):
    """Roll all uninvoiced charges + credits for this client into a new
    draft invoice. Marks each charge invoiced=True with invoice FK set."""
    from .models import Charge, Invoice, InvoiceLineItem
    from core.models import Organization
    from datetime import date as _date
    org = get_request_organization(request)
    client = get_object_or_404(Organization, pk=org_id)
    qs = Charge.objects.filter(client_org=client, invoiced=False)
    if org is not None:
        qs = qs.filter(organization=org)
    if not qs.exists():
        messages.info(request, 'No uninvoiced charges or credits to bill.')
        return redirect('psa:client_account', org_id=org_id)

    invoice = Invoice.objects.create(
        organization=org or client,
        client_org=client,
        title=f'Account charges through {_date.today():%Y-%m-%d}',
        invoice_date=_date.today(),
        created_by=request.user,
    )
    for i, c in enumerate(qs.order_by('charge_date')):
        # Credits come through as negative-priced line items.
        sign = -1 if c.is_credit else 1
        InvoiceLineItem.objects.create(
            invoice=invoice, sort_order=i,
            description=c.description[:300],
            quantity=1,
            unit_price=sign * float(c.amount),
            source='manual',
            source_id=str(c.pk),
        )
    qs.update(invoiced=True, invoice=invoice)
    invoice.recompute_totals()
    messages.success(request, f'Bundled {qs.count()} item(s) into invoice {invoice.invoice_number}.')
    return redirect('psa:invoice_detail', pk=invoice.pk)


@login_required
@require_psa_enabled
def aging_report(request):
    """Cross-client aging report. Sums each client's outstanding balance
    bucketed by 0-30 / 31-60 / 61-90 / 90+ days past due_date."""
    from .models import Invoice, get_psa_balance
    from core.models import Organization
    org = get_request_organization(request)

    # Find every client that has at least one non-void invoice
    client_ids = Invoice.objects.exclude(status='void').values_list('client_org', flat=True)
    if org is not None:
        client_ids = Invoice.objects.filter(organization=org).exclude(status='void').values_list('client_org', flat=True)
    client_ids = list(set(client_ids))
    rows = []
    totals = {'outstanding': 0, '0_30': 0, '31_60': 0, '61_90': 0, '90_plus': 0,
              'credit_total': 0, 'net_balance': 0}
    for client in Organization.objects.filter(pk__in=client_ids).order_by('name'):
        s = get_psa_balance(client, msp_org=org)
        if s['outstanding'] == 0 and s['credit_total'] == 0:
            continue
        rows.append({'client': client, 'summary': s})
        totals['outstanding'] += float(s['outstanding'])
        totals['credit_total'] += float(s['credit_total'])
        totals['net_balance'] += float(s['net_balance'])
        for k in ('0_30', '31_60', '61_90', '90_plus'):
            totals[k] += float(s['aging'][k])

    return render(request, 'psa/aging_report.html', {
        'rows': rows,
        'totals': totals,
    })


# ---------------------------------------------------------------------------
# Apply a Process workflow to a native PSA ticket
# ---------------------------------------------------------------------------


@login_required
@require_write
@require_psa_enabled
@require_http_methods(['GET', 'POST'])
def ticket_launch_workflow(request, ticket_number):
    """
    Pick a process from the catalog (organization-scoped + global) and
    launch a ProcessExecution against this ticket. The execution gets
    `native_psa_ticket=ticket` so the relationship is queryable from
    either side.
    """
    from processes.models import Process, ProcessExecution
    from django.db.models import Q

    org = get_request_organization(request)
    qs = Ticket.objects.all()
    if org is not None:
        qs = qs.filter(organization=org)
    ticket = get_object_or_404(qs, ticket_number=ticket_number)

    # Available processes — global + same-org as the ticket's client
    processes = Process.objects.filter(
        Q(is_global=True) | Q(organization=ticket.organization),
        is_active=True,
    ).order_by('name')

    if request.method == 'POST':
        try:
            process = processes.get(pk=request.POST.get('process') or 0)
        except (Process.DoesNotExist, ValueError):
            messages.error(request, 'Pick a valid workflow.')
            return redirect('psa:ticket_detail', ticket_number=ticket_number)

        execution = ProcessExecution.objects.create(
            process=process,
            organization=ticket.organization,
            assigned_to=request.user,
            started_by=request.user,
            started_at=timezone.now(),
            status='in_progress',
            native_psa_ticket=ticket,
            notes=(request.POST.get('notes') or '').strip()[:5000],
        )

        # Drop a system note on the ticket so timeline shows the launch
        TicketComment.objects.create(
            ticket=ticket, body=f'Launched workflow: **{process.name}** (execution #{execution.pk})',
            is_internal=True, is_system=True,
            source='workflow',
        )
        AuditLog.log(
            user=request.user, action='create',
            organization=ticket.organization,
            object_type='processes.ProcessExecution', object_id=execution.pk,
            object_repr=f'{process.name} on {ticket.ticket_number}',
            description=f'Launched workflow "{process.name}" against {ticket.ticket_number}',
            ip_address=_client_ip(request), path=request.path,
        )
        messages.success(request, f'Launched "{process.name}".')
        try:
            return redirect('processes:execution_detail', pk=execution.pk)
        except Exception:
            return redirect('psa:ticket_detail', ticket_number=ticket_number)

    return render(request, 'psa/ticket_launch_workflow.html', {
        'ticket': ticket,
        'processes': processes,
    })


# ---------------------------------------------------------------------------
# Invite a client portal user — creates User + Membership + emails a set-password link
# ---------------------------------------------------------------------------


@login_required
@require_admin
@require_psa_enabled
@require_http_methods(['GET', 'POST'])
def portal_invite(request, org_id):
    """
    Send a portal invite to a client user. Creates a Django User (with
    an unusable password), a Membership in the client org, and emails
    a one-time tokenized set-password link.
    """
    from django.contrib.auth.models import User
    from django.contrib.auth.tokens import default_token_generator
    from django.utils.encoding import force_bytes
    from django.utils.http import urlsafe_base64_encode
    from django.urls import reverse
    from django.core.mail import EmailMultiAlternatives
    from accounts.models import Membership, Role
    from core.models import Organization
    from psa.models import ClientPSASettings

    org = get_object_or_404(Organization, pk=org_id)

    if request.method == 'POST':
        email = (request.POST.get('email') or '').strip().lower()
        full_name = (request.POST.get('full_name') or '').strip()
        if not email or '@' not in email:
            messages.error(request, 'A valid email is required.')
            return redirect(request.path)

        # Ensure the org has portal_enabled — invite is meaningless otherwise.
        settings_row, _ = ClientPSASettings.objects.get_or_create(organization=org)
        if not settings_row.portal_enabled:
            settings_row.portal_enabled = True
            settings_row.save(update_fields=['portal_enabled', 'updated_at'])
            messages.info(request, f'Enabled the customer portal for {org.name}.')

        first, _, last = full_name.partition(' ')
        user, created = User.objects.get_or_create(
            email=email,
            defaults={'username': email[:150], 'first_name': first[:30],
                      'last_name': last[:150], 'is_active': True},
        )
        if created:
            user.set_unusable_password()
            user.save()
        # Force username sync if the User existed but had no email match
        if not user.username:
            user.username = email[:150]
            user.save(update_fields=['username'])

        is_org_admin = request.POST.get('is_org_admin') == 'on'
        Membership.objects.update_or_create(
            user=user, organization=org,
            defaults={'role': Role.READONLY, 'is_active': True,
                      'is_org_admin': is_org_admin,
                      'invited_by': request.user},
        )

        # Build one-time password-set link
        uid = urlsafe_base64_encode(force_bytes(user.pk))
        token = default_token_generator.make_token(user)
        path = reverse('accounts:portal_set_password',
                       kwargs={'uidb64': uid, 'token': token})
        link = request.build_absolute_uri(path)

        portal_url = request.build_absolute_uri('/portal/')
        subject = f'Your support portal account for {org.name}'
        text = (
            f'Hello{(" " + first) if first else ""},\n\n'
            f'You have been invited to access the support portal for {org.name}.\n\n'
            f'1. Click the link below to set your password:\n   {link}\n\n'
            f'2. After setting your password, sign in at:\n   {portal_url}\n\n'
            f'From there you can submit tickets, see updates from our support '
            f'team, and read knowledge-base articles we make available.\n\n'
            f'If you did not expect this invitation, you can safely ignore this email.\n'
        )
        try:
            msg = EmailMultiAlternatives(subject, text, to=[email])
            msg.send(fail_silently=False)
        except Exception as exc:
            messages.warning(request, f'User created but email send failed: {exc}. Invite link: {link}')
        else:
            messages.success(request, f'Invited {email} to the {org.name} portal.')

        AuditLog.log(
            user=request.user, action='create',
            organization=org,
            object_type='auth.User', object_id=user.pk,
            object_repr=f'portal user {email}',
            description=f'Invited {email} to {org.name} portal (membership=readonly)',
            ip_address=_client_ip(request), path=request.path,
        )
        return redirect('psa:client_account', org_id=org.pk)

    # GET — show the invite form
    return render(request, 'psa/portal_invite.html', {
        'org': org,
    })


# ---------------------------------------------------------------------------
# Dispatch board — drag-and-drop reassignment endpoint
# ---------------------------------------------------------------------------

@login_required
@require_write
@require_psa_enabled
@require_http_methods(['POST'])
def dispatch_assign(request):
    """
    JSON endpoint hit by the dispatch board's drag-and-drop handler.
    POST: ticket_number, assignee (user pk or 'unassigned').
    Defence-in-depth: re-checks the ticket is in scope for the user's
    current org filter.
    """
    from django.http import JsonResponse
    from django.contrib.auth.models import User
    org = get_request_organization(request)
    qs = Ticket.objects.all()
    if org is not None:
        qs = qs.filter(organization=org)
    tn = (request.POST.get('ticket_number') or '').strip()
    assignee = (request.POST.get('assignee') or '').strip()
    if not tn:
        return JsonResponse({'error': 'ticket_number required'}, status=400)
    try:
        ticket = qs.get(ticket_number=tn)
    except Ticket.DoesNotExist:
        return JsonResponse({'error': 'ticket not found'}, status=404)
    # Admin-level assignment check — staff/superuser, or org admin/owner of
    # the TICKET's org (not just the requester's currently-selected org).
    if not _can_assign(request, ticket.organization):
        return JsonResponse({'error': 'forbidden'}, status=403)
    old_assignee = ticket.assigned_to
    if assignee in ('', 'unassigned'):
        ticket.assigned_to = None
    else:
        try:
            target = User.objects.get(pk=int(assignee), is_active=True)
        except (User.DoesNotExist, ValueError):
            return JsonResponse({'error': 'invalid assignee'}, status=400)
        # Target must be staff/superuser or a member of the ticket's org.
        eligible_ids = set(
            _eligible_assignees(ticket.organization).values_list('id', flat=True)
        )
        if target.id not in eligible_ids:
            return JsonResponse({'error': 'assignee not eligible for this org'}, status=400)
        ticket.assigned_to = target
    ticket.save(update_fields=['assigned_to', 'updated_at'])
    AuditLog.log(
        user=request.user, action='update',
        organization=ticket.organization,
        object_type='psa.Ticket', object_id=ticket.pk,
        object_repr=ticket.ticket_number,
        description=f'Dispatch DnD: {ticket.ticket_number} reassigned '
                    f'from {old_assignee or "unassigned"} → {ticket.assigned_to or "unassigned"}',
        ip_address=_client_ip(request), path=request.path,
    )
    return JsonResponse({
        'ok': True,
        'ticket_number': ticket.ticket_number,
        'assigned_to': ticket.assigned_to.username if ticket.assigned_to else '',
        'assigned_to_id': ticket.assigned_to_id or 0,
    })


# ---------------------------------------------------------------------------
# Procurement (Phase 4.1) — Purchase Requisitions + Purchase Orders
# ---------------------------------------------------------------------------

from accounts.permission_utils import require_perm  # noqa: E402


def _procurement_save_lines(item, line_model, request):
    """Replace line_items on a PR/PO from POSTed lists, returning total."""
    descs = request.POST.getlist('li_description')
    skus = request.POST.getlist('li_sku')
    providers = request.POST.getlist('li_provider')
    qtys = request.POST.getlist('li_quantity')
    prices = request.POST.getlist('li_unit_price')
    if descs:
        item.line_items.all().delete()
        for i, d in enumerate(descs):
            if not (d or '').strip():
                continue
            try:
                qf = float(qtys[i] if i < len(qtys) and qtys[i] else 1)
                pf = float(prices[i] if i < len(prices) and prices[i] else 0)
            except (ValueError, IndexError):
                qf, pf = 1, 0
            kwargs = dict(
                sort_order=i,
                description=d.strip()[:300],
                sku=(skus[i] if i < len(skus) else '')[:80],
                distributor_provider=(providers[i] if i < len(providers) else '')[:40],
                quantity=qf, unit_price=pf,
            )
            if line_model.__name__ == 'PurchaseOrderLineItem':
                kwargs['po'] = item
            else:
                kwargs['requisition'] = item
            line_model.objects.create(**kwargs)


@login_required
@require_psa_enabled
@require_perm('procurement_view')
def requisition_list(request):
    from .models import PurchaseRequisition
    org = get_request_organization(request)
    qs = PurchaseRequisition.objects.select_related('client_org', 'requested_by', 'approver')
    if org is not None:
        qs = qs.filter(organization=org)
    elif not (request.user.is_superuser or getattr(request, 'is_staff_user', False)):
        qs = qs.none()
    status = request.GET.get('status')
    if status in {c[0] for c in PurchaseRequisition.STATUS_CHOICES}:
        qs = qs.filter(status=status)
    return render(request, 'psa/requisition_list.html', {
        'requisitions': qs.order_by('-requested_at')[:200],
        'status_filter': status or '',
    })


@login_required
@require_psa_enabled
@require_perm('procurement_view')
def requisition_detail(request, pk):
    from .models import PurchaseRequisition
    from accounts.permission_utils import user_has_perm
    org = get_request_organization(request)
    qs = PurchaseRequisition.objects.select_related(
        'client_org', 'organization', 'requested_by', 'approver',
        'source_ticket', 'source_project',
    )
    if org is not None:
        qs = qs.filter(organization=org)
    item = get_object_or_404(qs, pk=pk)
    return render(request, 'psa/requisition_detail.html', {
        'item': item,
        'line_items': item.line_items.all(),
        'can_approve': user_has_perm(request.user, 'procurement_approve_pr'),
        'can_create_po': user_has_perm(request.user, 'procurement_create_po'),
    })


@login_required
@require_psa_enabled
@require_perm('procurement_create_pr')
@require_http_methods(['GET', 'POST'])
def requisition_form(request, pk=None):
    from .models import PurchaseRequisition, PurchaseRequisitionLineItem
    from core.models import Organization
    if pk:
        item = get_object_or_404(PurchaseRequisition, pk=pk)
        org = item.organization
    else:
        item = None
        org = get_request_organization(request)

    client_orgs = Organization.objects.filter(is_active=True).order_by('name')

    def _render():
        return render(request, 'psa/requisition_form.html', {
            'item': item,
            'client_orgs': client_orgs,
            'status_choices': PurchaseRequisition.STATUS_CHOICES,
            'line_items': item.line_items.all() if item else [],
            'selected_org_id': item.organization_id if item else (org.pk if org else None),
        })

    if request.method == 'POST':
        posted_org_id = (request.POST.get('organization_id') or '').strip()
        if posted_org_id:
            try:
                item_org = Organization.objects.get(pk=int(posted_org_id), is_active=True)
            except (Organization.DoesNotExist, ValueError, TypeError):
                messages.error(request, 'Invalid organization.')
                return _render()
        else:
            item_org = org

        if item_org is None:
            messages.error(request, 'Please choose an MSP organization for this requisition.')
            return _render()

        title = (request.POST.get('title') or '').strip()
        if not title:
            messages.error(request, 'Title is required.')
            return _render()

        client_org = None
        client_org_id = (request.POST.get('client_org') or '').strip()
        if client_org_id:
            try:
                client_org = Organization.objects.get(pk=int(client_org_id))
            except (Organization.DoesNotExist, ValueError, TypeError):
                client_org = None

        if item is None:
            item = PurchaseRequisition(
                organization=item_org,
                title=title,
                requested_by=request.user,
            )
        item.organization = item_org
        item.client_org = client_org
        item.title = title
        item.description = (request.POST.get('description') or '').strip()
        item.notes = (request.POST.get('notes') or '').strip()
        # status only changeable if approve/reject — drafts stay draft from form
        if not pk:
            item.status = 'draft'
        try:
            item.tax_rate = request.POST.get('tax_rate') or 0
        except (TypeError, ValueError):
            item.tax_rate = 0
        item.currency = (request.POST.get('currency') or 'USD')[:8]
        item.save()

        _procurement_save_lines(item, PurchaseRequisitionLineItem, request)
        item.recompute_totals()
        item.save(update_fields=['subtotal', 'tax_amount', 'total', 'updated_at'])

        AuditLog.log(
            user=request.user, action='update' if pk else 'create',
            organization=item_org,
            object_type='psa.PurchaseRequisition', object_id=item.pk,
            object_repr=item.pr_number,
            description=f'{"Updated" if pk else "Created"} requisition {item.pr_number}',
            ip_address=_client_ip(request), path=request.path,
        )
        messages.success(request, f'Saved {item.pr_number}.')
        return redirect('psa:requisition_detail', pk=item.pk)

    return _render()


@login_required
@require_psa_enabled
@require_perm('procurement_create_pr')
@require_http_methods(['POST'])
def requisition_submit(request, pk):
    from .models import PurchaseRequisition
    org = get_request_organization(request)
    qs = PurchaseRequisition.objects.all()
    if org is not None:
        qs = qs.filter(organization=org)
    item = get_object_or_404(qs, pk=pk)
    if item.status != 'draft':
        messages.error(request, f'Cannot submit — already {item.get_status_display()}.')
        return redirect('psa:requisition_detail', pk=item.pk)
    item.status = 'submitted'
    item.save(update_fields=['status', 'updated_at'])
    AuditLog.log(
        user=request.user, action='update',
        organization=item.organization,
        object_type='psa.PurchaseRequisition', object_id=item.pk,
        object_repr=item.pr_number,
        description=f'Submitted requisition {item.pr_number} for approval',
        ip_address=_client_ip(request), path=request.path,
    )
    messages.success(request, f'Requisition {item.pr_number} submitted for approval.')
    return redirect('psa:requisition_detail', pk=item.pk)


@login_required
@require_psa_enabled
@require_perm('procurement_approve_pr')
@require_http_methods(['POST'])
def requisition_decide(request, pk):
    from .models import PurchaseRequisition
    org = get_request_organization(request)
    qs = PurchaseRequisition.objects.all()
    if org is not None:
        qs = qs.filter(organization=org)
    item = get_object_or_404(qs, pk=pk)
    if item.status != 'submitted':
        messages.error(request, f'Cannot decide — requisition is {item.get_status_display()}.')
        return redirect('psa:requisition_detail', pk=item.pk)

    decision = (request.POST.get('decision') or '').strip()
    note = (request.POST.get('decision_note') or '').strip()
    if decision == 'approve':
        item.status = 'approved'
    elif decision == 'reject':
        item.status = 'rejected'
    else:
        messages.error(request, 'Decision must be approve or reject.')
        return redirect('psa:requisition_detail', pk=item.pk)

    item.approver = request.user
    item.decided_at = timezone.now()
    item.decision_note = note
    item.save(update_fields=['status', 'approver', 'decided_at',
                             'decision_note', 'updated_at'])
    AuditLog.log(
        user=request.user, action='update',
        organization=item.organization,
        object_type='psa.PurchaseRequisition', object_id=item.pk,
        object_repr=item.pr_number,
        description=f'{decision.capitalize()}d requisition {item.pr_number}',
        ip_address=_client_ip(request), path=request.path,
    )
    messages.success(request, f'Requisition {item.pr_number} {item.get_status_display().lower()}.')
    return redirect('psa:requisition_detail', pk=item.pk)


@login_required
@require_psa_enabled
@require_perm('procurement_create_po')
@require_http_methods(['POST'])
def requisition_to_po(request, pk):
    """Convert an approved PR into a draft PO with the same line items."""
    from .models import (
        PurchaseRequisition, PurchaseOrder, PurchaseOrderLineItem,
    )
    org = get_request_organization(request)
    qs = PurchaseRequisition.objects.all()
    if org is not None:
        qs = qs.filter(organization=org)
    pr = get_object_or_404(qs, pk=pk)
    if pr.status != 'approved':
        messages.error(request, f'Cannot convert — requisition is {pr.get_status_display()}.')
        return redirect('psa:requisition_detail', pk=pr.pk)

    vendor_name = (request.POST.get('vendor_name') or '').strip() or 'Vendor TBD'
    vendor_email = (request.POST.get('vendor_email') or '').strip()

    po = PurchaseOrder.objects.create(
        organization=pr.organization,
        client_org=pr.client_org,
        requisition=pr,
        vendor_name=vendor_name[:200],
        vendor_email=vendor_email[:254],
        title=pr.title[:200],
        notes=pr.notes,
        tax_rate=pr.tax_rate,
        currency=pr.currency,
        created_by=request.user,
    )
    for li in pr.line_items.all():
        PurchaseOrderLineItem.objects.create(
            po=po,
            description=li.description,
            sku=li.sku,
            distributor_provider=li.distributor_provider,
            quantity=li.quantity,
            unit_price=li.unit_price,
            sort_order=li.sort_order,
        )
    po.recompute_totals()
    po.save(update_fields=['subtotal', 'tax_amount', 'total', 'updated_at'])

    pr.status = 'converted'
    pr.save(update_fields=['status', 'updated_at'])

    AuditLog.log(
        user=request.user, action='create',
        organization=po.organization,
        object_type='psa.PurchaseOrder', object_id=po.pk,
        object_repr=po.po_number,
        description=f'Converted {pr.pr_number} to PO {po.po_number}',
        ip_address=_client_ip(request), path=request.path,
    )
    messages.success(request, f'Created {po.po_number} from {pr.pr_number}.')
    return redirect('psa:po_detail', pk=po.pk)


# --- Purchase Orders ---


@login_required
@require_psa_enabled
@require_perm('procurement_view')
def po_list(request):
    from .models import PurchaseOrder
    org = get_request_organization(request)
    qs = PurchaseOrder.objects.select_related('client_org', 'created_by')
    if org is not None:
        qs = qs.filter(organization=org)
    elif not (request.user.is_superuser or getattr(request, 'is_staff_user', False)):
        qs = qs.none()
    status = request.GET.get('status')
    if status in {c[0] for c in PurchaseOrder.STATUS_CHOICES}:
        qs = qs.filter(status=status)
    return render(request, 'psa/po_list.html', {
        'pos': qs.order_by('-created_at')[:200],
        'status_filter': status or '',
    })


@login_required
@require_psa_enabled
@require_perm('procurement_view')
def po_detail(request, pk):
    from .models import PurchaseOrder
    from accounts.permission_utils import user_has_perm
    org = get_request_organization(request)
    qs = PurchaseOrder.objects.select_related(
        'client_org', 'organization', 'requisition', 'created_by',
    )
    if org is not None:
        qs = qs.filter(organization=org)
    item = get_object_or_404(qs, pk=pk)
    return render(request, 'psa/po_detail.html', {
        'item': item,
        'line_items': item.line_items.all(),
        'receipts': item.receipts.all().select_related('received_by').prefetch_related('lines__po_line'),
        'open_back_orders': item.back_orders.filter(status='open').select_related('po_line'),
        'can_edit_po': user_has_perm(request.user, 'procurement_create_po'),
        'can_send_po': user_has_perm(request.user, 'procurement_send_po'),
        'can_receive': user_has_perm(request.user, 'procurement_view'),
    })


@login_required
@require_psa_enabled
@require_perm('procurement_create_po')
@require_http_methods(['GET', 'POST'])
def po_form(request, pk=None):
    from .models import PurchaseOrder, PurchaseOrderLineItem
    from core.models import Organization
    if pk:
        item = get_object_or_404(PurchaseOrder, pk=pk)
        org = item.organization
    else:
        item = None
        org = get_request_organization(request)

    client_orgs = Organization.objects.filter(is_active=True).order_by('name')

    def _render():
        return render(request, 'psa/po_form.html', {
            'item': item,
            'client_orgs': client_orgs,
            'status_choices': PurchaseOrder.STATUS_CHOICES,
            'line_items': item.line_items.all() if item else [],
            'selected_org_id': item.organization_id if item else (org.pk if org else None),
        })

    if request.method == 'POST':
        posted_org_id = (request.POST.get('organization_id') or '').strip()
        if posted_org_id:
            try:
                item_org = Organization.objects.get(pk=int(posted_org_id), is_active=True)
            except (Organization.DoesNotExist, ValueError, TypeError):
                messages.error(request, 'Invalid organization.')
                return _render()
        else:
            item_org = org
        if item_org is None:
            messages.error(request, 'Please choose an MSP organization for this PO.')
            return _render()

        title = (request.POST.get('title') or '').strip()
        vendor_name = (request.POST.get('vendor_name') or '').strip()
        if not title or not vendor_name:
            messages.error(request, 'Title and vendor name are required.')
            return _render()

        client_org = None
        client_org_id = (request.POST.get('client_org') or '').strip()
        if client_org_id:
            try:
                client_org = Organization.objects.get(pk=int(client_org_id))
            except (Organization.DoesNotExist, ValueError, TypeError):
                client_org = None

        if item is None:
            item = PurchaseOrder(
                organization=item_org,
                created_by=request.user,
            )
        item.organization = item_org
        item.client_org = client_org
        item.title = title[:200]
        item.vendor_name = vendor_name[:200]
        item.vendor_email = (request.POST.get('vendor_email') or '').strip()[:254]
        item.vendor_phone = (request.POST.get('vendor_phone') or '').strip()[:40]
        item.vendor_address = (request.POST.get('vendor_address') or '').strip()
        item.notes = (request.POST.get('notes') or '').strip()
        item.status = request.POST.get('status') or 'draft'
        item.issue_date = request.POST.get('issue_date') or None
        item.expected_delivery_date = request.POST.get('expected_delivery_date') or None
        item.is_drop_ship = request.POST.get('is_drop_ship') == 'on'
        item.ship_to_name = (request.POST.get('ship_to_name') or '').strip()[:200]
        item.ship_to_address = (request.POST.get('ship_to_address') or '').strip()
        try:
            item.tax_rate = request.POST.get('tax_rate') or 0
        except (TypeError, ValueError):
            item.tax_rate = 0
        try:
            item.shipping_cost = request.POST.get('shipping_cost') or 0
        except (TypeError, ValueError):
            item.shipping_cost = 0
        item.currency = (request.POST.get('currency') or 'USD')[:8]
        item.save()

        _procurement_save_lines(item, PurchaseOrderLineItem, request)
        item.recompute_totals()
        item.save(update_fields=['subtotal', 'tax_amount', 'total', 'updated_at'])

        AuditLog.log(
            user=request.user, action='update' if pk else 'create',
            organization=item_org,
            object_type='psa.PurchaseOrder', object_id=item.pk,
            object_repr=item.po_number,
            description=f'{"Updated" if pk else "Created"} purchase order {item.po_number}',
            ip_address=_client_ip(request), path=request.path,
        )
        messages.success(request, f'Saved {item.po_number}.')
        return redirect('psa:po_detail', pk=item.pk)

    return _render()


@login_required
@require_psa_enabled
@require_perm('procurement_view')
def po_pdf(request, pk):
    from django.http import HttpResponse
    from .po_pdf import render_po_pdf
    from .models import PurchaseOrder
    org = get_request_organization(request)
    qs = PurchaseOrder.objects.all()
    if org is not None:
        qs = qs.filter(organization=org)
    po = get_object_or_404(
        qs.select_related('client_org', 'organization'), pk=pk,
    )
    pdf_bytes = render_po_pdf(po)
    resp = HttpResponse(pdf_bytes, content_type='application/pdf')
    disposition = 'attachment' if request.GET.get('download') else 'inline'
    resp['Content-Disposition'] = f'{disposition}; filename="{po.po_number}.pdf"'
    return resp


@login_required
@require_psa_enabled
@require_perm('procurement_send_po')
@require_http_methods(['POST'])
def po_send(request, pk):
    from .po_pdf import email_po
    from .models import PurchaseOrder
    org = get_request_organization(request)
    qs = PurchaseOrder.objects.all()
    if org is not None:
        qs = qs.filter(organization=org)
    po = get_object_or_404(qs.select_related('client_org', 'organization'), pk=pk)
    recipient = (request.POST.get('recipient') or po.vendor_email or '').strip()
    subject = (request.POST.get('subject') or '').strip()
    body = (request.POST.get('body') or '').strip()
    if not recipient or '@' not in recipient:
        messages.error(request, 'Provide a valid vendor recipient email.')
        return redirect('psa:po_detail', pk=po.pk)
    try:
        ok = email_po(po, recipient=recipient, subject=subject, body=body)
    except Exception as exc:
        messages.error(request, f'Email failed: {exc}')
        return redirect('psa:po_detail', pk=po.pk)
    if ok:
        if po.status == 'draft':
            po.status = 'sent'
            po.sent_at = timezone.now()
            po.save(update_fields=['status', 'sent_at', 'updated_at'])
        AuditLog.log(
            user=request.user, action='update',
            organization=po.organization,
            object_type='psa.PurchaseOrder', object_id=po.pk,
            object_repr=po.po_number,
            description=f'Emailed PO {po.po_number} to {recipient}',
            ip_address=_client_ip(request), path=request.path,
        )
        messages.success(request, f'Sent PO {po.po_number} to {recipient}.')
    else:
        messages.error(request, 'Email send returned 0 recipients.')
    return redirect('psa:po_detail', pk=po.pk)


# ---------------------------------------------------------------------------
# Procurement (Phase 4.2) — Receiving + back-orders + serial-number capture
# ---------------------------------------------------------------------------

def _recompute_po_status(po):
    """Set PO status to draft / partial / received based on line aggregates."""
    if po.status in ('cancelled', 'void', 'draft'):
        return
    total_qty = sum((l.quantity or 0) for l in po.line_items.all())
    received_qty = sum((l.received_quantity or 0) for l in po.line_items.all())
    if total_qty == 0:
        return
    if received_qty == 0:
        # Don't downgrade from sent/acknowledged
        return
    if received_qty < total_qty:
        po.status = 'partial'
    else:
        po.status = 'received'
        # Close any open back-orders
        po.back_orders.filter(status='open').update(
            status='filled', closed_at=timezone.now(),
        )
    po.save(update_fields=['status'])


def _maybe_create_assets_from_serials(line, serials, receipt, user):
    """When serial numbers are captured, optionally create assets.Asset rows
    so the inventory chain is complete. Skips silently if assets app or
    Asset model has incompatible signature."""
    if not serials:
        return
    try:
        from assets.models import Asset
    except Exception:
        return
    org = line.po.client_org or line.po.organization
    for sn in serials:
        # Skip duplicates
        if Asset.objects.filter(serial_number=sn).exists():
            continue
        try:
            Asset.objects.create(
                organization=org,
                name=f'{line.description} ({sn})'[:200],
                serial_number=sn,
                notes=f'Auto-created from PO {line.po.po_number} receipt #{receipt.pk}',
                # status default; let model defaults handle the rest
            )
        except Exception:
            # Some Asset models require asset_type or other FKs we can't infer.
            # In that case we just skip — the receipt + serial captures still
            # land in POReceiptLine.serial_numbers as the audit trail.
            continue


@login_required
@require_psa_enabled
@require_perm('procurement_view')
@require_http_methods(['GET', 'POST'])
def po_receive(request, pk):
    """
    GET: render receiving form for an open PO (one row per line item with
    a 'qty received' input, optional serial-numbers comma-separated
    textarea, carrier + tracking inputs).
    POST: create POReceipt + POReceiptLine rows, roll up
    PurchaseOrderLineItem.received_quantity, recompute PO status,
    create POBackOrder rows for shorted lines, optionally create
    assets.Asset rows from captured serial numbers, audit-log.
    """
    from .models import (
        PurchaseOrder, POReceipt, POReceiptLine, POBackOrder,
    )
    org = get_request_organization(request)
    qs = PurchaseOrder.objects.select_related('client_org', 'organization')
    if org is not None:
        qs = qs.filter(organization=org)
    po = get_object_or_404(qs, pk=pk)

    if request.method == 'POST':
        from decimal import Decimal
        from django.db import transaction

        carrier = (request.POST.get('carrier') or '').strip()[:80]
        tracking = (request.POST.get('tracking_number') or '').strip()[:120]
        notes = (request.POST.get('notes') or '').strip()
        is_drop_ship_confirmed = (request.POST.get('is_drop_ship_confirmed') == 'on')

        with transaction.atomic():
            receipt = POReceipt.objects.create(
                po=po, received_by=request.user,
                carrier=carrier, tracking_number=tracking,
                notes=notes, is_drop_ship_confirmed=is_drop_ship_confirmed,
            )
            for line in po.line_items.all():
                qty_str = (request.POST.get(f'qty_line_{line.pk}') or '').strip()
                if not qty_str:
                    continue
                try:
                    qty = Decimal(qty_str)
                except Exception:
                    continue
                if qty <= 0:
                    continue
                # Cap qty at remaining
                remaining = (line.quantity or Decimal('0')) - (line.received_quantity or Decimal('0'))
                if qty > remaining:
                    qty = remaining
                if qty <= 0:
                    continue

                serials_raw = (request.POST.get(f'serials_line_{line.pk}') or '').strip()
                serials = [s.strip() for s in serials_raw.replace('\n', ',').split(',') if s.strip()]

                POReceiptLine.objects.create(
                    receipt=receipt, po_line=line,
                    quantity_received=qty, serial_numbers=serials,
                )
                # Roll up cumulative
                line.received_quantity = (line.received_quantity or Decimal('0')) + qty
                line.save(update_fields=['received_quantity'])

                # Optional: auto-create Asset rows from serials
                _maybe_create_assets_from_serials(line, serials, receipt, request.user)

                # Back-order if still short
                still_short = (line.quantity or Decimal('0')) - (line.received_quantity or Decimal('0'))
                if still_short > 0:
                    POBackOrder.objects.create(
                        po=po, po_line=line, quantity_outstanding=still_short,
                    )

            # Recompute PO status
            _recompute_po_status(po)

            # If a full receipt occurred against any line that had a prior
            # open back-order, fill it.
            for line in po.line_items.all():
                remaining = (line.quantity or Decimal('0')) - (line.received_quantity or Decimal('0'))
                if remaining <= 0:
                    line.back_orders.filter(status='open').update(
                        status='filled', closed_at=timezone.now(),
                    )

            # Audit log
            try:
                AuditLog.log(
                    user=request.user, action='update',
                    organization=po.organization,
                    object_type='psa.PurchaseOrder',
                    object_id=po.pk,
                    object_repr=po.po_number,
                    description=f'Received against {po.po_number} (receipt #{receipt.pk})',
                    ip_address=_client_ip(request), path=request.path,
                )
            except Exception:
                pass

        messages.success(request, f'Receipt #{receipt.pk} recorded.')
        return redirect('psa:po_detail', pk=po.pk)

    return render(request, 'psa/po_receive_form.html', {
        'po': po,
        'line_items': po.line_items.all(),
        'prior_receipts': po.receipts.all().select_related('received_by').prefetch_related('lines__po_line'),
    })


@login_required
@require_psa_enabled
@require_perm('procurement_view')
def back_order_list(request):
    from .models import POBackOrder
    org = get_request_organization(request)
    status = (request.GET.get('status') or 'open').strip()
    qs = POBackOrder.objects.select_related('po', 'po_line', 'po__client_org', 'po__organization')
    if org is not None:
        qs = qs.filter(po__organization=org)
    elif not (request.user.is_superuser or getattr(request, 'is_staff_user', False)):
        qs = qs.none()
    if status != 'all':
        qs = qs.filter(status=status)
    from accounts.permission_utils import user_has_perm
    return render(request, 'psa/back_order_list.html', {
        'back_orders': qs.order_by('-created_at')[:300],
        'status_filter': status,
        'can_cancel': user_has_perm(request.user, 'procurement_create_po'),
    })


@login_required
@require_psa_enabled
@require_perm('procurement_create_po')
@require_http_methods(['POST'])
def back_order_cancel(request, pk):
    from .models import POBackOrder
    org = get_request_organization(request)
    qs = POBackOrder.objects.select_related('po')
    if org is not None:
        qs = qs.filter(po__organization=org)
    bo = get_object_or_404(qs, pk=pk)
    if bo.status == 'open':
        bo.status = 'cancelled'
        bo.closed_at = timezone.now()
        bo.save(update_fields=['status', 'closed_at'])
        try:
            AuditLog.log(
                user=request.user, action='update',
                organization=bo.po.organization,
                object_type='psa.POBackOrder', object_id=bo.pk,
                object_repr=str(bo),
                description=f'Cancelled back-order on {bo.po.po_number}',
                ip_address=_client_ip(request), path=request.path,
            )
        except Exception:
            pass
        messages.success(request, 'Back-order cancelled.')
    else:
        messages.info(request, 'Back-order is not open; nothing changed.')
    return redirect('psa:back_order_list')
