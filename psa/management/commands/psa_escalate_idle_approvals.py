"""
v3.17.256 — Phase 20 v1: escalate idle PSAApprovals.

Run hourly / daily via cron. Finds PSAApproval rows with status=pending
that have been sitting longer than their `escalation_threshold_hours`
and haven't been escalated yet. Emails superusers a single digest with
the list, then stamps `escalated_at` so the same approval isn't
flagged on every run.

Usage:
    python manage.py psa_escalate_idle_approvals [--dry-run]
"""
from datetime import timedelta

from django.contrib.auth.models import User
from django.core.mail import send_mail
from django.core.management.base import BaseCommand
from django.db.models import F, Q
from django.utils import timezone

from psa.models import PSAApproval


class Command(BaseCommand):
    help = 'Email superusers a digest of PSAApprovals stuck pending past their threshold.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help="Don't send mail or stamp escalated_at; just print.",
        )

    def handle(self, *, dry_run=False, **kwargs):
        now = timezone.now()
        # Fetch pending, non-escalated, with a positive threshold.
        candidates = (PSAApproval.objects
                      .filter(status='pending', escalation_threshold_hours__gt=0)
                      .filter(escalated_at__isnull=True)
                      .select_related('organization', 'requested_by',
                                       'related_ticket'))

        # Filter to those whose age >= threshold (computed in Python because
        # the threshold varies per row).
        idle = []
        for a in candidates:
            elapsed = now - a.requested_at
            threshold = timedelta(hours=a.escalation_threshold_hours)
            if elapsed >= threshold:
                idle.append(a)

        if not idle:
            self.stdout.write(self.style.SUCCESS(
                f'Escalation run @ {now.isoformat()}: nothing past threshold.'
            ))
            return

        admin_emails = list(
            User.objects.filter(is_superuser=True, is_active=True)
                         .exclude(email='').values_list('email', flat=True)
        )
        if not admin_emails:
            self.stdout.write(self.style.WARNING(
                'No superuser emails on file — cannot escalate.'
            ))
            return

        subject = f'[PSA] {len(idle)} approval(s) past escalation threshold'
        lines = [
            f'{len(idle)} PSAApproval row(s) have been pending past their threshold:',
            '',
        ]
        for a in idle:
            elapsed_h = int((now - a.requested_at).total_seconds() / 3600)
            requester = a.requested_by.username if a.requested_by_id else 'unknown'
            org = a.organization.name if a.organization_id else 'no org'
            ticket = (f' (ticket {a.related_ticket.ticket_number})'
                      if a.related_ticket_id else '')
            lines.append(
                f'  - #{a.pk} {a.get_kind_display()} from {requester} '
                f'for {org}{ticket} — pending {elapsed_h}h '
                f'(threshold {a.escalation_threshold_hours}h)'
            )
        lines += [
            '',
            'Open /psa/approvals/ to decide them or extend their threshold.',
        ]
        body = '\n'.join(lines)

        if dry_run:
            self.stdout.write(self.style.WARNING(
                f'[DRY RUN] {len(idle)} approvals → {len(admin_emails)} admins'
            ))
            return

        try:
            send_mail(subject, body, None, admin_emails, fail_silently=False)
        except Exception as exc:
            self.stdout.write(self.style.ERROR(f'send failed: {exc}'))
            return
        PSAApproval.objects.filter(
            pk__in=[a.pk for a in idle],
        ).update(escalated_at=now)
        self.stdout.write(self.style.SUCCESS(
            f'Escalation run @ {now.isoformat()}: '
            f'{len(idle)} approvals digested to {len(admin_emails)} admins.'
        ))
