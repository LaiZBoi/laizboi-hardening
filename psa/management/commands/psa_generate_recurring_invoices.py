"""
Phase 15 v1 (v3.17.291): cron-driven generator for recurring invoices.

For each active Contract whose `billing_frequency != 'none'` and whose
`next_billing_date <= today`, creates a draft Invoice for the period
and advances `next_billing_date` by one cycle. Idempotent — already-
billed periods don't get re-billed because the cron advances the date.

Wire to a daily systemd timer.
"""
from __future__ import annotations

from datetime import date

from django.core.management.base import BaseCommand
from django.utils import timezone

from psa.models import Contract


class Command(BaseCommand):
    help = 'Spawn draft Invoices from Contracts on their billing cadence.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument('--max-per-contract', type=int, default=12,
                            help='Cap catch-up cycles per contract (default 12).')

    def handle(self, *args, **options):
        dry = options['dry_run']
        cap = options['max_per_contract']
        today = date.today()

        # Phase 15 v13: skip paused contracts (paused_at non-null)
        qs = Contract.objects.filter(
            status='active',
            next_billing_date__lte=today,
            paused_at__isnull=True,
        ).exclude(billing_frequency='none')

        spawned = 0
        for contract in qs:
            cycles = 0
            while (contract.next_billing_date
                   and contract.next_billing_date <= today
                   and cycles < cap):
                if dry:
                    self.stdout.write(
                        f'[dry] would invoice {contract.name} for '
                        f'period {contract.next_billing_date}'
                    )
                    nxt = Contract._advance_billing(contract.next_billing_date,
                                                     contract.billing_frequency)
                    contract.next_billing_date = nxt
                else:
                    inv = contract.generate_invoice(
                        on_date=contract.next_billing_date,
                    )
                    if inv is None:
                        # Disabled or zero-amount; bail out of this contract
                        break
                    spawned += 1
                    self.stdout.write(self.style.SUCCESS(
                        f'Generated {inv.invoice_number} for '
                        f'{contract.name} (period {contract.next_billing_date})'
                    ))
                    nxt = Contract._advance_billing(contract.next_billing_date,
                                                     contract.billing_frequency)
                    contract.last_billed_at = today
                    contract.next_billing_date = nxt
                    contract.save(update_fields=[
                        'last_billed_at', 'next_billing_date', 'updated_at',
                    ])
                cycles += 1

        self.stdout.write(self.style.SUCCESS(
            f'{"[dry] " if dry else ""}Processed {qs.count()} contract(s); '
            f'{spawned} invoice(s) generated.'
        ))
