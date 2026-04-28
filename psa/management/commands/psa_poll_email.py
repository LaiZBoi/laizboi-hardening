"""
IMAP poller — converts inbound email to PSA tickets.

Designed for cron (every 5 minutes is the default poll_interval). For
each active EmailIngestionConfig:
  1. Connect via IMAP (SSL by default).
  2. Fetch UNREAD messages from the configured folder.
  3. For each message:
       - If the subject contains a ticket number matching subject_ticket_pattern
         AND that ticket exists in the same organization, append the email
         body as a public TicketComment from the requester.
       - Otherwise create a new Ticket with source='email', requester_email
         and requester_name from From:.
  4. Mark the message as Seen.

Errors per config are stored on the config row; one bad config doesn't
break the others.
"""
from __future__ import annotations

import email
import imaplib
import logging
import re
from email.header import decode_header
from email.utils import parseaddr

from django.core.management.base import BaseCommand
from django.utils import timezone

from psa.models import (
    EmailIngestionConfig, Ticket, TicketComment, TicketStatus,
)


logger = logging.getLogger('psa.email_ingest')


def _decode(s) -> str:
    if not s:
        return ''
    if isinstance(s, bytes):
        try:
            return s.decode('utf-8', errors='replace')
        except Exception:
            return str(s)
    return str(s)


def _decode_header(s: str) -> str:
    if not s:
        return ''
    try:
        parts = decode_header(s)
        decoded = []
        for text, charset in parts:
            if isinstance(text, bytes):
                decoded.append(text.decode(charset or 'utf-8', errors='replace'))
            else:
                decoded.append(text)
        return ''.join(decoded)
    except Exception:
        return _decode(s)


def _extract_text_body(msg) -> str:
    """Return the best-effort text body of an email Message."""
    if msg.is_multipart():
        # Prefer text/plain, fall back to text/html stripped to text.
        for part in msg.walk():
            ctype = part.get_content_type()
            disp = str(part.get('Content-Disposition') or '')
            if ctype == 'text/plain' and 'attachment' not in disp.lower():
                payload = part.get_payload(decode=True) or b''
                charset = part.get_content_charset() or 'utf-8'
                return payload.decode(charset, errors='replace')
        for part in msg.walk():
            ctype = part.get_content_type()
            if ctype == 'text/html':
                payload = part.get_payload(decode=True) or b''
                charset = part.get_content_charset() or 'utf-8'
                html = payload.decode(charset, errors='replace')
                return re.sub(r'<[^>]+>', '', html)  # crude tag strip
        return ''
    payload = msg.get_payload(decode=True) or b''
    charset = msg.get_content_charset() or 'utf-8'
    return payload.decode(charset, errors='replace')


class Command(BaseCommand):
    help = 'Poll IMAP mailboxes for active EmailIngestionConfig rows and create tickets.'

    def add_arguments(self, parser):
        parser.add_argument('--config-id', type=int)

    def handle(self, *args, **options):
        qs = EmailIngestionConfig.objects.filter(is_active=True)
        if options.get('config_id'):
            qs = qs.filter(pk=options['config_id'])

        if not qs.exists():
            self.stdout.write(self.style.WARNING('No active email-ingestion configs.'))
            return

        new_status = TicketStatus.objects.filter(slug='new').first()
        if new_status is None:
            self.stdout.write(self.style.ERROR('Run psa_seed_defaults first — no "new" TicketStatus.'))
            return

        for config in qs.select_related('default_queue', 'default_priority',
                                        'default_type', 'organization'):
            try:
                created, replied = self._poll_one(config, new_status)
            except Exception as exc:
                config.last_poll_status = 'error'
                config.last_error = str(exc)[:1000]
                config.last_poll_at = timezone.now()
                config.save(update_fields=['last_poll_status', 'last_error', 'last_poll_at'])
                self.stdout.write(self.style.ERROR(f'{config.name}: {exc}'))
                logger.exception('email poll failed for config %s', config.pk)
                continue

            config.last_poll_status = 'ok'
            config.last_error = ''
            config.last_poll_at = timezone.now()
            config.save(update_fields=['last_poll_status', 'last_error', 'last_poll_at'])
            self.stdout.write(self.style.SUCCESS(
                f'{config.name}: created {created} new + {replied} reply comment(s)'
            ))

    def _poll_one(self, config, new_status):
        password = config.get_password()
        if not password:
            raise RuntimeError('No password configured')

        if config.use_ssl:
            mail = imaplib.IMAP4_SSL(config.imap_host, config.imap_port)
        else:
            mail = imaplib.IMAP4(config.imap_host, config.imap_port)
        mail.login(config.username, password)
        try:
            mail.select(config.folder)
            typ, data = mail.search(None, 'UNSEEN')
            if typ != 'OK':
                return 0, 0
            ids = (data[0] or b'').split()

            ticket_pattern = re.compile(config.subject_ticket_pattern)
            created_count = 0
            replied_count = 0

            for msg_id in ids:
                typ, msg_data = mail.fetch(msg_id, '(RFC822)')
                if typ != 'OK' or not msg_data or not msg_data[0]:
                    continue
                raw = msg_data[0][1]
                msg = email.message_from_bytes(raw)

                subject = _decode_header(msg.get('Subject', '')).strip()[:300]
                from_name, from_email = parseaddr(_decode_header(msg.get('From', '')))
                body = _extract_text_body(msg).strip()[:50000]

                # Reply detection — if subject matches a ticket number we own
                m = ticket_pattern.search(subject)
                target = None
                if m:
                    target = Ticket.objects.filter(
                        ticket_number=m.group(0),
                        organization=config.organization,
                    ).first()

                if target is not None:
                    TicketComment.objects.create(
                        ticket=target,
                        body=body or '(empty email body)',
                        is_internal=False,
                        is_system=False,
                        author_name=from_name or from_email or 'email',
                        author_email=from_email or '',
                        source='email',
                    )
                    replied_count += 1
                else:
                    Ticket.objects.create(
                        organization=config.organization,
                        subject=subject or '(no subject)',
                        description=body,
                        queue=config.default_queue,
                        priority=config.default_priority,
                        ticket_type=config.default_type,
                        status=new_status,
                        source='email',
                        visibility='client',
                        client_can_view=True,
                        requester_name=from_name[:200] if from_name else '',
                        requester_email=from_email[:254] if from_email else '',
                    )
                    created_count += 1

                mail.store(msg_id, '+FLAGS', '\\Seen')

            return created_count, replied_count
        finally:
            try:
                mail.logout()
            except Exception:
                pass
