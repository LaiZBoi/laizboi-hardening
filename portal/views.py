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
from django.http import Http404, JsonResponse
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


def _active_announcements(request, organization):
    """v3.17.232: announcements visible to the requester for this org.
    Filters out session-dismissed IDs so each portal user only sees
    each dismissable banner once (until they clear their session).
    """
    from .models import PortalAnnouncement
    dismissed = set(request.session.get('portal_dismissed_announcements') or [])
    return [
        a for a in PortalAnnouncement.active_for_org(organization)
        if a.pk not in dismissed
    ]


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
        'announcements': _active_announcements(request, m.organization),
    })


@portal_required
@require_http_methods(['POST'])
def announcement_dismiss(request, pk):
    """v3.17.232: portal user dismisses an announcement for the rest of
    the session. Only works on `is_dismissable=True` announcements; the
    flag is enforced server-side in case the client lies.
    """
    from .models import PortalAnnouncement
    m = request.portal_membership
    try:
        ann = PortalAnnouncement.objects.get(pk=pk, organization=m.organization)
    except PortalAnnouncement.DoesNotExist:
        return JsonResponse({'error': 'not found'}, status=404)
    if not ann.is_dismissable:
        return JsonResponse({'error': 'not dismissable'}, status=400)
    dismissed = list(request.session.get('portal_dismissed_announcements') or [])
    if pk not in dismissed:
        dismissed.append(pk)
        request.session['portal_dismissed_announcements'] = dismissed
        request.session.modified = True
    return JsonResponse({'ok': True})


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


# ---------------------------------------------------------------------------
# Customer-facing quote signing — opaque token URL, no portal login required
# ---------------------------------------------------------------------------

import json as _json_qs

from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_http_methods


def _client_ip_qs(request):
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR')
    if forwarded:
        return forwarded.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')


@require_http_methods(['GET', 'POST'])
def quote_sign(request, token):
    """
    Public sign-and-accept page. The token is on `Quote.customer_token`
    and is generated server-side; only the customer with the link can
    sign. POSTing a signature flips the quote to 'accepted', creates the
    QuoteSignature record, and runs Quote.mark_accepted (which optionally
    creates a ticket).
    """
    from psa.models import (
        Queue, Quote, QuoteSignature,
        TicketPriority, TicketStatus, TicketType,
    )

    quote = get_object_or_404(Quote.objects.select_related('client_org'),
                              customer_token=token)

    if request.method == 'POST':
        if quote.status in ('accepted', 'rejected'):
            return render(request, 'portal/quote_sign.html', {
                'quote': quote,
                'organization': quote.client_org,
                'already': True,
            })

        signed_by_name = (request.POST.get('signed_by_name') or '').strip()[:200]
        signed_by_email = (request.POST.get('signed_by_email') or '').strip()[:254]
        signed_by_title = (request.POST.get('signed_by_title') or '').strip()[:200]
        signature_data = (request.POST.get('signature_data') or '').strip()

        if not signed_by_name or not signed_by_email or not signature_data.startswith('data:image/'):
            messages.error(request, 'Please complete the form and draw your signature.')
            return redirect(request.path)
        if len(signature_data) > 200_000:
            messages.error(request, 'Signature image is too large.')
            return redirect(request.path)

        QuoteSignature.objects.update_or_create(
            quote=quote,
            defaults={
                'signed_by_name': signed_by_name,
                'signed_by_email': signed_by_email,
                'signed_by_title': signed_by_title,
                'signature_data': signature_data,
                'ip_address': _client_ip_qs(request),
                'user_agent': (request.META.get('HTTP_USER_AGENT') or '')[:400],
            },
        )

        # Flip status + optionally create ticket
        queue = Queue.objects.filter(is_active=True).first()
        priority = TicketPriority.objects.order_by('sort_order').first()
        ttype = TicketType.objects.filter(is_active=True).first()
        new_status = TicketStatus.objects.filter(slug='new').first()
        try:
            quote.mark_accepted(
                user=None, create_ticket=True,
                queue=queue, priority=priority,
                ticket_type=ttype, status=new_status,
            )
        except Exception:
            quote.status = 'accepted'
            quote.save()

        try:
            from audit.models import AuditLog
            AuditLog.log(
                user=None, action='update',
                organization=quote.organization,
                object_type='psa.Quote', object_id=quote.pk,
                object_repr=quote.quote_number,
                description=f'Quote {quote.quote_number} signed by {signed_by_name} <{signed_by_email}> from IP {_client_ip_qs(request)}',
                path=request.path,
                extra_data={'signed_by_email': signed_by_email,
                            'signed_by_name': signed_by_name},
            )
        except Exception:
            pass

        return render(request, 'portal/quote_sign.html', {
            'quote': quote,
            'organization': quote.client_org,
            'just_signed': True,
        })

    return render(request, 'portal/quote_sign.html', {
        'quote': quote,
        'organization': quote.client_org,
        'line_items': quote.line_items.all(),
    })


# ---------------------------------------------------------------------------
# Customer portal KB — articles staff have marked is_client_visible=True
# ---------------------------------------------------------------------------

@portal_required
def kb_list(request):
    """List KB articles available to the requesting client.

    Visible: documents where is_client_visible=True AND is_published=True
    AND (is_global=True OR organization == client's org).
    """
    from django.db.models import Q
    from docs.models import Document
    m = request.portal_membership
    q = (request.GET.get('q') or '').strip()
    qs = Document.objects.filter(
        is_client_visible=True, is_published=True, is_archived=False,
    ).filter(
        Q(is_global=True) | Q(organization=m.organization)
    )
    if q:
        qs = qs.filter(Q(title__icontains=q) | Q(body__icontains=q))
    return render(request, 'portal/kb_list.html', {
        'articles': qs.order_by('-updated_at')[:200],
        'query': q,
        'organization': m.organization,
    })


@portal_required
def kb_detail(request, slug):
    """Show a single KB article. Same visibility rule as kb_list."""
    from django.db.models import Q
    from docs.models import Document
    m = request.portal_membership
    qs = Document.objects.filter(
        is_client_visible=True, is_published=True, is_archived=False,
    ).filter(
        Q(is_global=True) | Q(organization=m.organization)
    )
    article = get_object_or_404(qs, slug=slug)
    return render(request, 'portal/kb_detail.html', {
        'article': article,
        'organization': m.organization,
    })


# ---------------------------------------------------------------------------
# Customer portal — Vault access (Password reveal + Org-admin management)
# ---------------------------------------------------------------------------

@portal_required
def vault_list(request):
    """List passwords this portal user is allowed to see."""
    from vault.models import Password
    m = request.portal_membership
    qs = Password.objects.filter(
        organization=m.organization,
        is_personal=False,
        client_visible=True,
    ).exclude(client_access_mode='none')
    # Filter to actually-visible: cheap way is iterate with the helper.
    visible = [p for p in qs.order_by('title')[:1000] if p.visible_to_portal_user(request.user)]
    return render(request, 'portal/vault_list.html', {
        'passwords': visible,
        'organization': m.organization,
        'is_org_admin': bool(m.is_org_admin),
    })


@portal_required
@require_http_methods(['POST'])
def vault_reveal(request, pk):
    """Audit-logged password reveal — returns the plaintext as JSON."""
    from django.http import JsonResponse
    from vault.models import Password
    from audit.models import AuditLog
    m = request.portal_membership
    pwd = get_object_or_404(Password, pk=pk, organization=m.organization,
                            is_personal=False, client_visible=True)
    if not pwd.visible_to_portal_user(request.user):
        return JsonResponse({'error': 'Not authorised.'}, status=403)
    try:
        plaintext = pwd.password
    except Exception as exc:
        return JsonResponse({'error': f'decrypt failed: {exc}'}, status=500)
    try:
        AuditLog.log(
            user=request.user, action='view',
            organization=pwd.organization,
            object_type='vault.Password', object_id=pwd.pk,
            object_repr=pwd.title,
            description=f'Portal reveal of {pwd.title} by {request.user.email}',
            path=request.path,
        )
    except Exception:
        pass
    return JsonResponse({'password': plaintext, 'title': pwd.title,
                         'username': pwd.username})


# --- Org Admin management UI -----------------------------------------------

def _require_org_admin(view_func):
    """Decorator: portal_required + must have is_org_admin on the membership."""
    from functools import wraps

    @wraps(view_func)
    @portal_required
    def wrapped(request, *args, **kwargs):
        if not request.portal_membership.is_org_admin:
            raise Http404()
        return view_func(request, *args, **kwargs)

    return wrapped


@_require_org_admin
def org_admin_vault(request):
    """
    Org admin landing — list every password in this org that's in
    `org_admin_managed` mode plus a count of how many users currently
    have access. Click a row to manage individual users.
    """
    from vault.models import Password
    m = request.portal_membership
    items = (Password.objects
             .filter(organization=m.organization, is_personal=False,
                     client_visible=True, client_access_mode='org_admin_managed')
             .prefetch_related('client_allowed_users')
             .order_by('title'))
    return render(request, 'portal/org_admin_vault.html', {
        'items': items,
        'organization': m.organization,
    })


@_require_org_admin
@require_http_methods(['GET', 'POST'])
def org_admin_vault_item(request, pk):
    """Per-item access management — add/remove individual users."""
    from accounts.models import Membership
    from vault.models import Password
    m = request.portal_membership
    pwd = get_object_or_404(
        Password, pk=pk, organization=m.organization,
        is_personal=False, client_visible=True,
        client_access_mode='org_admin_managed',
    )
    if request.method == 'POST':
        # Replace allowed_users with the posted set (only members of the org).
        org_member_ids = set(Membership.objects.filter(
            organization=m.organization, is_active=True,
        ).values_list('user_id', flat=True))
        try:
            posted = {int(x) for x in request.POST.getlist('allowed_user_ids')}
        except ValueError:
            posted = set()
        clean = posted & org_member_ids
        pwd.client_allowed_users.set(list(clean))

        try:
            from audit.models import AuditLog
            AuditLog.log(
                user=request.user, action='update',
                organization=pwd.organization,
                object_type='vault.Password', object_id=pwd.pk,
                object_repr=pwd.title,
                description=f'Org admin {request.user.email} updated access list '
                            f'for {pwd.title} (now {len(clean)} users)',
                path=request.path,
                extra_data={'allowed_user_ids': sorted(clean)},
            )
        except Exception:
            pass
        messages.success(request, f'Updated access for "{pwd.title}".')
        return redirect('portal:org_admin_vault')

    # Prefetch every active org member with their grant state
    members = (Membership.objects
               .filter(organization=m.organization, is_active=True)
               .select_related('user')
               .order_by('user__username'))
    granted_ids = set(pwd.client_allowed_users.values_list('pk', flat=True))
    rows = [
        {'user': mb.user, 'granted': mb.user_id in granted_ids,
         'is_self': mb.user_id == request.user.pk}
        for mb in members
    ]
    return render(request, 'portal/org_admin_vault_item.html', {
        'pwd': pwd,
        'rows': rows,
        'organization': m.organization,
    })
