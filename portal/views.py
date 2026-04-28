"""
Customer portal views.

Hard rules:
  * Tickets visible: organization belongs to the user via Membership AND
    visibility != 'staff' AND client_can_view=True (when staff visibility flag).
    Internal-only comments and attachments are filtered out at queryset time.
  * Per-client opt-in: ClientPSASettings.portal_enabled must be True for
    the user's organization.
  * No staff/MSP nav, no internal-staff routes — the portal_base.html
    template provides a stripped layout.
"""
from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from accounts.models import Membership
from audit.models import AuditLog
from psa.feature_flags import is_psa_enabled, is_psa_enabled_for_client
from psa.models import (
    ClientPSASettings, Queue, Ticket, TicketAttachment,
    TicketComment, TicketPriority, TicketStatus, TicketType,
)


def _portal_membership(request):
    """Return the active Membership for the request's user OR None.
    Restricted to users with at least one active membership in an org
    that has portal_enabled=True. Superusers are NOT auto-granted —
    the portal is for clients, period.
    """
    user = request.user
    if not user.is_authenticated:
        return None
    qs = Membership.objects.filter(user=user, is_active=True).select_related('organization')
    for m in qs:
        try:
            settings = m.organization.psa_settings
            if settings.portal_enabled:
                return m
        except ClientPSASettings.DoesNotExist:
            continue
    return None


def portal_required(view_func):
    """Decorator: 404 if PSA is off globally OR the user has no portal-enabled org."""
    from functools import wraps

    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('/account/login/?next=' + request.path)
        if not is_psa_enabled():
            raise Http404('Portal is unavailable')
        m = _portal_membership(request)
        if m is None:
            raise Http404('Portal is not enabled for your account')
        request.portal_membership = m
        return view_func(request, *args, **kwargs)

    return wrapped


@portal_required
def ticket_list(request):
    """Tickets owned by the user's portal organization, client-visible only."""
    m = request.portal_membership
    qs = Ticket.objects.filter(organization=m.organization).select_related(
        'status', 'priority', 'queue', 'ticket_type',
    )
    qs = qs.filter(client_can_view=True)
    status = request.GET.get('status')
    if status == 'open':
        qs = qs.filter(status__is_terminal=False)
    elif status == 'closed':
        qs = qs.filter(status__is_terminal=True)
    return render(request, 'portal/ticket_list.html', {
        'tickets': qs.order_by('-created_at')[:200],
        'status_filter': status or '',
        'organization': m.organization,
    })


@portal_required
def ticket_detail(request, ticket_number):
    m = request.portal_membership
    ticket = get_object_or_404(
        Ticket.objects.filter(organization=m.organization, client_can_view=True),
        ticket_number=ticket_number,
    )
    # Public comments only — no internals, no staff system notes if they
    # mention vault/internal (they don't, but is_internal filters anyway).
    comments = ticket.comments.filter(is_internal=False).order_by('created_at')
    attachments = ticket.attachments.filter(is_internal=False)
    return render(request, 'portal/ticket_detail.html', {
        'ticket': ticket,
        'comments': comments,
        'attachments': attachments,
        'organization': m.organization,
    })


@portal_required
@require_http_methods(['POST'])
def post_reply(request, ticket_number):
    m = request.portal_membership
    ticket = get_object_or_404(
        Ticket.objects.filter(organization=m.organization, client_can_view=True),
        ticket_number=ticket_number,
    )
    body = (request.POST.get('body') or '').strip()
    if not body:
        messages.error(request, 'Reply cannot be empty.')
        return redirect('portal:ticket_detail', ticket_number=ticket_number)
    if len(body) > 50000:
        body = body[:50000]

    TicketComment.objects.create(
        ticket=ticket, author=request.user, body=body,
        is_internal=False, source='portal',
        author_name=request.user.get_full_name() or request.user.username,
        author_email=request.user.email or '',
    )
    ticket.last_client_response_at = timezone.now()
    ticket.save(update_fields=['last_client_response_at'])
    messages.success(request, 'Reply added.')
    return redirect('portal:ticket_detail', ticket_number=ticket_number)


@portal_required
@require_http_methods(['GET', 'POST'])
def ticket_create(request):
    """Client-side ticket submission."""
    m = request.portal_membership

    if request.method == 'POST':
        subject = (request.POST.get('subject') or '').strip()[:300]
        description = (request.POST.get('description') or '').strip()[:50000]
        if not subject:
            messages.error(request, 'Subject is required.')
            return redirect(request.path)

        # Use the client's default queue/priority/type — fall back to any.
        queue = Queue.objects.filter(is_active=True).first()
        priority = TicketPriority.objects.order_by('sort_order').first()
        ticket_type = TicketType.objects.filter(is_active=True).first()
        new_status = TicketStatus.objects.filter(slug='new').first()
        if not (queue and priority and ticket_type and new_status):
            messages.error(request, 'PSA is not configured. Contact support.')
            return redirect('portal:ticket_list')

        ticket = Ticket.objects.create(
            organization=m.organization,
            subject=subject, description=description,
            queue=queue, priority=priority, ticket_type=ticket_type,
            status=new_status, source='portal',
            visibility='client', client_can_view=True,
            requester_name=request.user.get_full_name() or request.user.username,
            requester_email=request.user.email or '',
            created_by=request.user,
        )
        AuditLog.log(
            user=request.user, action='create',
            organization=m.organization,
            object_type='psa.Ticket', object_id=ticket.pk,
            object_repr=ticket.ticket_number,
            description=f'Portal ticket {ticket.ticket_number}: {subject}',
            path=request.path,
        )
        messages.success(request, f'Ticket {ticket.ticket_number} created.')
        return redirect('portal:ticket_detail', ticket_number=ticket.ticket_number)

    return render(request, 'portal/ticket_create.html', {
        'organization': m.organization,
    })
