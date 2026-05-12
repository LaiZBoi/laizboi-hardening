"""
Mobile API tickets endpoints (v3.17.349).
"""
from __future__ import annotations

from django.db.models import Q
from django.utils import timezone
from rest_framework.authentication import TokenAuthentication
from rest_framework.decorators import (
    api_view, authentication_classes, permission_classes,
)
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .scoping import accessible_org_ids


def _serialize_ticket(t, *, detail=False):
    out = {
        'id': t.id,
        'ticket_number': t.ticket_number,
        'subject': t.subject,
        'status': t.status.name if t.status_id else None,
        'status_id': t.status_id,
        'priority': t.priority.code if t.priority_id else None,
        'priority_id': t.priority_id,
        'organization_id': t.organization_id,
        'assigned_to_id': t.assigned_to_id,
        'created_at': t.created_at.isoformat() if t.created_at else None,
    }
    if detail:
        out.update({
            'description': t.description,
            'requester_name': t.requester_name,
            'requester_email': t.requester_email,
            'is_terminal': t.status.is_terminal if t.status_id else False,
            'comments': [
                {
                    'id': c.id,
                    'body': c.body,
                    'is_internal': c.is_internal,
                    'is_system': c.is_system,
                    'author_id': c.author_id,
                    'author_name': c.author_name,
                    'created_at': c.created_at.isoformat() if c.created_at else None,
                }
                for c in t.comments.order_by('created_at')
            ],
        })
    return out


@api_view(['GET', 'POST'])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def ticket_list_view(request):
    """
    GET  /api/mobile/v1/tickets/?status=&priority=&assigned_to_me=true&organization_id=&search=&page=
    POST /api/mobile/v1/tickets/  (create)
    """
    from psa.models import Ticket, TicketStatus, TicketPriority, TicketType, Queue

    org_ids = accessible_org_ids(request.user)

    if request.method == 'POST':
        data = request.data or {}
        org_id = data.get('organization_id')
        try:
            org_id = int(org_id) if org_id is not None else None
        except (TypeError, ValueError):
            return Response({'detail': 'organization_id must be an integer'}, status=400)
        if not org_id or org_id not in org_ids:
            return Response({'detail': 'organization_id required + must be accessible'}, status=403)

        subject = (data.get('subject') or '').strip()
        if not subject:
            return Response({'detail': 'subject is required'}, status=400)

        # Pick reasonable defaults
        status_obj = (
            TicketStatus.objects.filter(slug='new').first()
            or TicketStatus.objects.first()
        )
        priority_obj = (
            TicketPriority.objects.filter(code='P3').first()
            or TicketPriority.objects.first()
        )
        type_obj = TicketType.objects.first()
        queue_obj = Queue.objects.first()
        if not all([status_obj, priority_obj, type_obj, queue_obj]):
            return Response(
                {'detail': 'PSA seed data missing — cannot create ticket'},
                status=409,
            )

        ticket = Ticket.objects.create(
            organization_id=org_id,
            subject=subject,
            description=data.get('description', ''),
            status=status_obj,
            priority=priority_obj,
            ticket_type=type_obj,
            queue=queue_obj,
            source='api',
            requester_name=data.get('requester_name', ''),
            requester_email=data.get('requester_email', ''),
        )
        return Response(_serialize_ticket(ticket, detail=True), status=201)

    # GET
    qs = Ticket.objects.select_related('status', 'priority').filter(
        organization_id__in=org_ids,
    )

    status_filter = request.query_params.get('status')
    if status_filter:
        if status_filter == 'open':
            qs = qs.filter(status__is_terminal=False)
        elif status_filter == 'closed':
            qs = qs.filter(status__is_terminal=True)
        else:
            qs = qs.filter(status__slug=status_filter)

    priority_filter = request.query_params.get('priority')
    if priority_filter:
        # v3.17.474 (bug from dashboard critical tile) — mobile dashboard
        # sends ?priority=critical but TicketPriority rows use P1..P4
        # codes, so the previous filter returned zero. Accept friendly
        # labels and map them to codes; fall back to literal match for
        # raw P-codes / priority names.
        label_to_code = {
            'critical': 'P1', 'urgent': 'P1',
            'high':     'P2',
            'medium':   'P3', 'normal': 'P3',
            'low':      'P4',
        }
        raw = priority_filter.strip().lower()
        code = label_to_code.get(raw, raw.upper() if len(raw) <= 4 else raw)
        qs = qs.filter(
            Q(priority__code__iexact=code)
            | Q(priority__name__iexact=raw)
        )

    if request.query_params.get('assigned_to_me') == 'true':
        qs = qs.filter(assigned_to=request.user)

    org_filter = request.query_params.get('organization_id')
    if org_filter:
        try:
            oid = int(org_filter)
            if oid in org_ids:
                qs = qs.filter(organization_id=oid)
            else:
                qs = qs.none()
        except ValueError:
            pass

    search = (request.query_params.get('search') or '').strip()
    if search:
        qs = qs.filter(
            Q(subject__icontains=search)
            | Q(ticket_number__icontains=search)
            | Q(description__icontains=search)
        )

    qs = qs.order_by('-created_at')

    try:
        page = max(int(request.query_params.get('page', 1)), 1)
    except ValueError:
        page = 1
    page_size = 50
    start = (page - 1) * page_size
    total = qs.count()
    rows = qs[start:start + page_size]

    return Response({
        'count': total,
        'page': page,
        'page_size': page_size,
        'results': [_serialize_ticket(t) for t in rows],
    })


@api_view(['GET', 'PATCH'])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def ticket_detail_view(request, pk: int):
    """
    GET   /api/mobile/v1/tickets/<id>/      — detail with comments
    PATCH /api/mobile/v1/tickets/<id>/      — partial update (status / priority / assignee)
    """
    from psa.models import Ticket, TicketStatus, TicketPriority

    org_ids = accessible_org_ids(request.user)
    try:
        ticket = Ticket.objects.select_related('status', 'priority').get(
            pk=pk, organization_id__in=org_ids,
        )
    except Ticket.DoesNotExist:
        return Response({'detail': 'Not found'}, status=404)

    if request.method == 'GET':
        return Response(_serialize_ticket(ticket, detail=True))

    # PATCH
    data = request.data or {}

    # v3.17.450: also accept `status` and `priority` as friendly strings.
    # The mobile UI surfaces statuses as `'open'` / `'in_progress'` /
    # `'closed'` and priorities as `'low'` / `'medium'` / `'high'` /
    # `'critical'`. Without these branches the mobile PATCH silently
    # no-op'd because only `status_id` / `priority_id` keys were honored.
    if 'status_id' in data:
        try:
            ticket.status = TicketStatus.objects.get(pk=int(data['status_id']))
        except (TicketStatus.DoesNotExist, ValueError, TypeError):
            return Response({'detail': 'invalid status_id'}, status=400)
    elif 'status' in data:
        raw = (str(data['status']) or '').strip().lower()
        # Mobile sends `in_progress` and friends; DB slugs use dashes.
        slug = raw.replace('_', '-')
        match = (TicketStatus.objects.filter(slug=slug).first()
                 or TicketStatus.objects.filter(slug__iexact=raw).first()
                 or TicketStatus.objects.filter(name__iexact=raw).first())
        if match is None:
            return Response({'detail': f'unknown status: {raw}'}, status=400)
        ticket.status = match
    if 'priority_id' in data:
        try:
            ticket.priority = TicketPriority.objects.get(pk=int(data['priority_id']))
        except (TicketPriority.DoesNotExist, ValueError, TypeError):
            return Response({'detail': 'invalid priority_id'}, status=400)
    elif 'priority' in data:
        raw = (str(data['priority']) or '').strip().lower()
        # Mobile labels → P-codes. Falls through to direct code/name match
        # for backends that already use 'P1' / 'urgent' / etc.
        label_to_code = {
            'critical': 'P1', 'urgent': 'P1',
            'high':     'P2',
            'medium':   'P3', 'normal': 'P3',
            'low':      'P4',
        }
        candidate_codes = [label_to_code[raw]] if raw in label_to_code else [raw.upper(), raw]
        match = None
        for code in candidate_codes:
            match = TicketPriority.objects.filter(code__iexact=code).first()
            if match:
                break
        if match is None:
            match = TicketPriority.objects.filter(name__iexact=raw).first()
        if match is None:
            return Response({'detail': f'unknown priority: {raw}'}, status=400)
        ticket.priority = match
    if 'assigned_to_id' in data:
        from django.contrib.auth.models import User
        v = data['assigned_to_id']
        if v in (None, '', 0):
            ticket.assigned_to = None
        else:
            try:
                ticket.assigned_to = User.objects.get(pk=int(v))
            except (User.DoesNotExist, ValueError, TypeError):
                return Response({'detail': 'invalid assigned_to_id'}, status=400)
    ticket.save()
    return Response(_serialize_ticket(ticket, detail=True))


@api_view(['POST'])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def ticket_comment_view(request, pk: int):
    """POST /api/mobile/v1/tickets/<id>/comments/ — add a comment."""
    from psa.models import Ticket, TicketComment

    org_ids = accessible_org_ids(request.user)
    try:
        ticket = Ticket.objects.get(pk=pk, organization_id__in=org_ids)
    except Ticket.DoesNotExist:
        return Response({'detail': 'Not found'}, status=404)

    body = (request.data.get('body') or '').strip()
    if not body:
        return Response({'detail': 'body is required'}, status=400)

    is_internal = bool(request.data.get('is_internal', False))
    comment = TicketComment.objects.create(
        ticket=ticket,
        author=request.user,
        body=body,
        is_internal=is_internal,
        source='api',
    )
    return Response({
        'id': comment.id,
        'body': comment.body,
        'is_internal': comment.is_internal,
        'author_id': comment.author_id,
        'created_at': comment.created_at.isoformat() if comment.created_at else None,
    }, status=201)


@api_view(['GET', 'POST'])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def ticket_time_view(request, pk: int):
    """
    GET  /api/mobile/v1/tickets/<id>/time/  — list time entries on this ticket
    POST /api/mobile/v1/tickets/<id>/time/  — log time on this ticket (v3.17.454)

    POST body (one of two shapes):
      * Manual entry: `{duration_minutes, notes?, is_billable?, started_at?}`
        — `started_at` defaults to (now - duration). `ended_at` set to now.
      * Running timer: `{started_at, ended_at?, notes?, is_billable?}`
        — if `ended_at` omitted, server computes from `started_at` and now.
    """
    from psa.models import Ticket, TicketTimeEntry
    from django.utils import timezone
    from datetime import datetime, timedelta

    org_ids = accessible_org_ids(request.user)
    try:
        ticket = Ticket.objects.get(pk=pk, organization_id__in=org_ids)
    except Ticket.DoesNotExist:
        return Response({'detail': 'Not found'}, status=404)

    if request.method == 'GET':
        entries = (TicketTimeEntry.objects
                   .filter(ticket=ticket)
                   .order_by('-started_at')[:50])
        return Response({
            'count': entries.count() if hasattr(entries, 'count')
                     else len(list(entries)),
            'results': [_serialize_time_entry(t) for t in entries],
        })

    # POST
    data = request.data or {}

    def _parse_dt(raw):
        if raw is None:
            return None
        try:
            parsed = datetime.fromisoformat(str(raw).replace('Z', '+00:00'))
        except (ValueError, TypeError):
            return None
        if timezone.is_naive(parsed):
            parsed = timezone.make_aware(parsed, timezone.utc)
        return parsed

    started_at = _parse_dt(data.get('started_at'))
    ended_at = _parse_dt(data.get('ended_at'))

    duration_minutes = data.get('duration_minutes')
    if duration_minutes is not None:
        try:
            duration_minutes = max(0, int(duration_minutes))
        except (TypeError, ValueError):
            return Response({'detail': 'invalid duration_minutes'}, status=400)

    if duration_minutes is None and started_at is None:
        return Response(
            {'detail': 'provide either duration_minutes or started_at'},
            status=400,
        )

    now = timezone.now()
    if started_at is None:
        started_at = now - timedelta(minutes=duration_minutes or 0)
    if ended_at is None:
        if duration_minutes is not None:
            ended_at = started_at + timedelta(minutes=duration_minutes)
        else:
            ended_at = now
    if duration_minutes is None:
        duration_minutes = max(0, int((ended_at - started_at).total_seconds() // 60))

    is_billable = bool(data.get('is_billable', True))
    notes = (data.get('notes') or '').strip()[:2000]

    entry = TicketTimeEntry.objects.create(
        ticket=ticket,
        user=request.user,
        started_at=started_at,
        ended_at=ended_at,
        duration_minutes=duration_minutes,
        is_billable=is_billable,
        notes=notes,
    )
    return Response(_serialize_time_entry(entry), status=201)


def _serialize_time_entry(t):
    return {
        'id': t.id,
        'ticket_id': t.ticket_id,
        'user_id': t.user_id,
        'started_at': t.started_at.isoformat() if t.started_at else None,
        'ended_at': t.ended_at.isoformat() if t.ended_at else None,
        'duration_minutes': t.duration_minutes,
        'is_billable': t.is_billable,
        'notes': t.notes,
    }
