"""
Phase 23 v3.17.339 — recompute Organization.exposure_score for every
active org. Designed to run on a regular cadence (e.g. hourly cron).
"""
from __future__ import annotations

from django.core.management.base import BaseCommand

from core.models import Organization
from security_alerts.exposure import recompute_for_org


class Command(BaseCommand):
    help = 'Recompute the cached security exposure score for every active organization.'

    def add_arguments(self, parser):
        parser.add_argument('--org-id', type=int, default=None,
                            help='Only recompute for this org id (default: all active).')
        parser.add_argument('--dry-run', action='store_true',
                            help='Compute + print without writing back to DB.')

    def handle(self, *args, **options):
        qs = Organization.objects.filter(is_active=True)
        if options.get('org_id'):
            qs = qs.filter(pk=options['org_id'])
        dry = options.get('dry_run')
        updated = 0
        for org in qs:
            if dry:
                from security_alerts.exposure import compute_exposure_score
                score = compute_exposure_score(org)
                self.stdout.write(f'[dry] {org.name}: would set exposure_score={score}')
            else:
                score = recompute_for_org(org)
                self.stdout.write(f'{org.name}: exposure_score={score}')
                updated += 1
        self.stdout.write(self.style.SUCCESS(
            f'{"[dry] " if dry else ""}done — {updated} org(s) updated.'
        ))
