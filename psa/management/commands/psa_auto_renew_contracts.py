"""
Auto-renew contracts whose end_date has just passed and have auto_renew=True.
Run nightly via cron. Idempotent — guards against duplicate renewals via
parent_contract linkage.
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone


class Command(BaseCommand):
    help = 'Auto-renew expired contracts that have auto_renew=True. Applies rollover.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='List what would happen, do nothing')

    def handle(self, *args, **opts):
        from psa.models import Contract
        from dateutil.relativedelta import relativedelta

        today = timezone.now().date()
        # Candidates: status active, auto_renew, end_date passed, no child renewal yet
        candidates = Contract.objects.filter(
            status='active',
            auto_renew=True,
            end_date__lt=today,
            end_date__isnull=False,
            renewals__isnull=True,  # no renewal child created yet
        ).distinct()

        renewed = 0
        for c in candidates:
            # Compute rollover
            rolled_minutes = 0
            if c.rollover_percent and c.total_hours:
                base_minutes = int(float(c.total_hours) * 60)
                unused = max(0, base_minutes - (c.hours_used_minutes or 0))
                rolled_minutes = int(unused * float(c.rollover_percent) / 100)

            new_start = c.end_date + timedelta(days=1)
            new_end = new_start + relativedelta(months=c.auto_renew_period_months or 12) - timedelta(days=1)
            rollover_expires_at = None
            if rolled_minutes and c.rollover_expiry_days:
                rollover_expires_at = new_start + timedelta(days=c.rollover_expiry_days)

            self.stdout.write(
                f'Renewing {c.id} {c.name}: {new_start} -> {new_end}, '
                f'rollover_minutes={rolled_minutes}'
            )
            if opts['dry_run']:
                continue

            with transaction.atomic():
                # Old contract: mark expired
                c.status = 'expired'
                c.save(update_fields=['status'])

                # New contract: copy + bump dates + reset usage + apply rollover
                new_c = Contract.objects.create(
                    organization=c.organization,
                    client_org=c.client_org,
                    name=c.name,
                    contract_type=c.contract_type,
                    status='active',
                    start_date=new_start,
                    end_date=new_end,
                    total_hours=c.total_hours,
                    hours_used_minutes=0,
                    hourly_rate=c.hourly_rate,
                    overage_rate=c.overage_rate,
                    rollover_percent=c.rollover_percent,
                    rollover_expiry_days=c.rollover_expiry_days,
                    rolled_over_minutes=rolled_minutes,
                    rollover_expires_at=rollover_expires_at,
                    auto_renew=c.auto_renew,
                    auto_renew_period_months=c.auto_renew_period_months,
                    proration_enabled=c.proration_enabled,
                    billable_role_codes=c.billable_role_codes,
                    excluded_role_codes=c.excluded_role_codes,
                    parent_contract=c,
                    sla_matrix=c.sla_matrix,
                    notes=c.notes,
                )
                # Copy bundle items
                for b in c.bundle_items.all():
                    b.pk = None
                    b.contract = new_c
                    b.save()

                renewed += 1

        self.stdout.write(self.style.SUCCESS(
            f'{"Would renew" if opts["dry_run"] else "Renewed"} {renewed} '
            f'contract{"s" if renewed != 1 else ""}'
        ))
