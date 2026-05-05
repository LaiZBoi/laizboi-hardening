"""
Phase 15 v13 (v3.17.298): subscription lifecycle advancement.

Daily cron. Two jobs:
  1. **Auto-resume** — contracts with `paused_until` <= today get
     `paused_at` cleared.
  2. **Cancel-at-period-end** — contracts with `cancel_at_period_end=True`
     whose next_billing_date is past get transitioned to `status='cancelled'`.

Both are idempotent — already-resumed and already-cancelled contracts
are filtered out at the SQL level.
"""
from __future__ import annotations

from datetime import date

from django.core.management.base import BaseCommand

from psa.models import Contract


class Command(BaseCommand):
    help = 'Advance Contract subscription lifecycle (auto-resume + cancel-at-period-end).'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        dry = options['dry_run']
        today = date.today()
        resumed = 0
        cancelled = 0

        # Auto-resume
        resume_qs = Contract.objects.filter(
            paused_at__isnull=False,
            paused_until__isnull=False,
            paused_until__lte=today,
        )
        for c in resume_qs:
            if dry:
                self.stdout.write(f'[dry] would resume {c.name}')
            else:
                c.resume()
                resumed += 1
                self.stdout.write(self.style.SUCCESS(
                    f'Resumed {c.name}'))

        # Cancel-at-period-end
        cancel_qs = Contract.objects.filter(
            cancel_at_period_end=True,
            status='active',
            next_billing_date__isnull=False,
            next_billing_date__lt=today,
        )
        for c in cancel_qs:
            if dry:
                self.stdout.write(f'[dry] would cancel {c.name}')
            else:
                c.status = 'cancelled'
                c.cancel_at_period_end = False
                c.save(update_fields=['status', 'cancel_at_period_end',
                                        'updated_at'])
                cancelled += 1
                self.stdout.write(self.style.SUCCESS(
                    f'Cancelled {c.name} (period ended)'))

        self.stdout.write(self.style.SUCCESS(
            f'{"[dry] " if dry else ""}{resumed} resumed; {cancelled} cancelled.'
        ))
