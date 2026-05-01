"""
Outbound email helper — Phase 10.4.

When staff replies to a customer via a ticket, the outbound email must
carry RFC 5322 threading headers (``In-Reply-To`` + ``References``) so
the customer's mail client groups the reply with the original thread.
Without these headers each staff reply lands as an isolated message and
the customer sees a fragmented conversation.

This module provides a single entry point — ``send_threaded_reply`` —
that builds the message, sends it via Django's email backend, and
persists an ``EmailMessage(direction='out')`` row so the next inbound
reply can chain back via the existing 10.1 threading logic.

Callers (Phase 11 notification hooks, manual "Send as email" buttons,
mgmt commands) all funnel through here so threading is consistent.
"""
from __future__ import annotations

import email.utils
import logging
from typing import Iterable

from django.conf import settings
from django.core.mail import EmailMultiAlternatives

from psa.models import EmailMessage, Ticket, TicketComment

logger = logging.getLogger('psa.email_outbound')


def _make_message_id(ticket: Ticket) -> str:
    """
    Generate an RFC 5322 Message-ID for the outbound email. Uses Django's
    ``email.utils.make_msgid`` with a domain hint that ties the ID back
    to the ticket so future replies are diagnosable from the header alone.
    """
    domain = getattr(settings, 'PSA_OUTBOUND_MESSAGE_ID_DOMAIN', None)
    if not domain:
        domain = 'clientst0r.local'
    return email.utils.make_msgid(idstring=f'psa-{ticket.ticket_number}', domain=domain)


def send_threaded_reply(
    *,
    ticket: Ticket,
    comment: TicketComment | None,
    body_text: str,
    body_html: str = '',
    subject: str | None = None,
    to_emails: Iterable[str] | None = None,
    from_email: str | None = None,
) -> EmailMessage:
    """
    Send an outbound reply on ``ticket`` and persist the
    ``EmailMessage(direction='out')`` row. Returns the persisted row.

    Threading behavior:
      - The outbound message gets a fresh, server-generated Message-ID.
      - ``In-Reply-To`` is set to ``ticket.last_inbound_message_id`` when
        the ticket has captured an inbound (10.1+).
      - ``References`` is set to the same value (single-element chain is
        the spec-compliant minimum and matches what most clients write).

    The customer's reply to *this* outbound will set ``In-Reply-To`` to
    our generated Message-ID, which the 10.1 ``_thread_target`` lookup
    will resolve back to the same ticket — closing the round-trip.

    Args:
        ticket:        The ticket the reply attaches to.
        comment:       Optional TicketComment (for cross-linking; not
                       required — callers may send mail without an
                       associated comment).
        body_text:     Plain-text body. Required.
        body_html:     Optional HTML alternative. If provided, the
                       outbound message is sent as multipart/alternative.
        subject:       Override subject. Defaults to
                       ``Re: [<ticket_number>] <ticket.subject>``.
        to_emails:     Recipients. Falls back to the ticket's
                       ``requester_email`` when not provided.
        from_email:    Sender. Defaults to
                       ``settings.DEFAULT_FROM_EMAIL``.

    Returns:
        The persisted ``EmailMessage`` row (direction='out').
    """
    if not body_text:
        raise ValueError('body_text is required')

    recipients = list(to_emails) if to_emails else (
        [ticket.requester_email] if ticket.requester_email else []
    )
    if not recipients:
        raise ValueError(
            f'No recipients for ticket {ticket.ticket_number}; '
            'pass to_emails= or set ticket.requester_email.'
        )

    sender = from_email or getattr(settings, 'DEFAULT_FROM_EMAIL', 'noreply@localhost')

    # Build subject — wrap the ticket number in [...] so the existing
    # subject-regex fallback in psa_poll_email correlates legacy clients
    # that don't preserve threading headers.
    subj = subject or f'Re: [{ticket.ticket_number}] {ticket.subject}'

    message_id = _make_message_id(ticket)
    in_reply_to = (ticket.last_inbound_message_id or '').strip()

    extra_headers: dict[str, str] = {'Message-ID': message_id}
    if in_reply_to:
        extra_headers['In-Reply-To'] = in_reply_to
        extra_headers['References'] = in_reply_to

    msg = EmailMultiAlternatives(
        subject=subj[:998],
        body=body_text,
        from_email=sender,
        to=recipients,
        headers=extra_headers,
    )
    if body_html:
        msg.attach_alternative(body_html, 'text/html')

    msg.send(fail_silently=False)

    em = EmailMessage.objects.create(
        organization=ticket.organization,
        ticket=ticket,
        ingestion_config=None,
        direction='out',
        message_id=message_id,
        in_reply_to=in_reply_to,
        references=in_reply_to,
        from_email=sender[:320],
        to_emails=list(recipients)[:50],
        subject=subj[:998],
        headers_raw='\n'.join(f'{k}: {v}' for k, v in extra_headers.items())[:16000],
        body_text=body_text[:50000],
        body_html=body_html[:200000] if body_html else '',
    )
    logger.info(
        'sent outbound reply ticket=%s message_id=%s in_reply_to=%r recipients=%r',
        ticket.ticket_number, message_id, in_reply_to, recipients,
    )
    return em
