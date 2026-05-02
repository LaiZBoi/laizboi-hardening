"""
Phase 12 v1 (v3.17.231): CSAT survey emailer.

Called from the post-save signal when a ticket's status transitions to a
terminal state. Best-effort — failures log but never raise so the ticket
save flow stays unaffected.
"""
from __future__ import annotations

import logging

from django.core.mail import EmailMultiAlternatives
from django.urls import reverse


logger = logging.getLogger('psa.csat')


def maybe_send_csat(ticket, *, base_url=''):
    """
    Create a CSAT survey for the ticket if one doesn't exist yet, then
    email the requester. Returns the survey row (created or existing) or
    None when CSAT is disabled / no recipient is on file.

    Idempotent — re-firing on the same closed ticket returns the existing
    survey without sending a second email.
    """
    from core.models import SystemSetting
    from .models import TicketCSATSurvey

    try:
        settings = SystemSetting.get_settings()
    except Exception:
        return None
    if not getattr(settings, 'psa_csat_enabled', False):
        return None

    recipient = (ticket.requester_email or '').strip()
    if not recipient:
        return None

    survey, created = TicketCSATSurvey.objects.get_or_create(
        ticket=ticket,
        defaults={
            'organization': ticket.organization,
            'recipient_email': recipient,
        },
    )
    if not created:
        return survey

    try:
        path = reverse('psa:csat_respond', kwargs={'token': survey.token})
    except Exception:
        return survey
    link = f'{base_url}{path}' if base_url else path

    subject = f'How did we do? Quick rating for {ticket.ticket_number}'
    org_name = ticket.organization.name
    body = (
        f'Hi{(" " + (ticket.requester_name or "")) if ticket.requester_name else ""},\n\n'
        f'Your support request {ticket.ticket_number} ("{ticket.subject}") was just '
        f'marked resolved by the team at {org_name}.\n\n'
        f'How did we do? It takes one click — pick a star from 1 (very '
        f'dissatisfied) to 5 (very satisfied):\n\n'
        f'  {link}\n\n'
        f'Your feedback goes straight to the team handling this kind of '
        f'work. Optional comment is appreciated but not required.\n\n'
        f'Thanks,\n'
        f'{org_name}\n'
    )
    try:
        msg = EmailMultiAlternatives(subject, body, to=[recipient])
        msg.send(fail_silently=False)
    except Exception as exc:
        logger.warning('CSAT email failed for ticket %s: %s', ticket.pk, exc)
    return survey
