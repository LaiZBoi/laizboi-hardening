"""
Phase 17 v9 (v3.17.308): scheduled baseline capture for monitored
assets. For every Asset with `config_monitored=True`, snapshot the
current state into a new `AssetBaseline` row. Useful for "give me a
weekly snapshot of every server so I can audit drift over time."

The previous baseline's `is_current` flag is cleared automatically
by `Asset.capture_baseline()`. Old baselines are kept for history.
Wire to a daily/weekly systemd timer.
"""
from __future__ import annotations

from django.core.management.base import BaseCommand

from assets.models import Asset


class Command(BaseCommand):
    help = 'Capture AssetBaselines for every config_monitored=True asset.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument('--label', type=str, default='scheduled',
                            help='Label applied to each new baseline.')

    def handle(self, *args, **options):
        dry = options['dry_run']
        label = options['label']

        qs = Asset.objects.filter(config_monitored=True)
        captured = 0
        for a in qs:
            if dry:
                self.stdout.write(f'[dry] would snapshot {a.name}')
            else:
                a.capture_baseline(label=label)
                captured += 1
                self.stdout.write(self.style.SUCCESS(
                    f'Captured baseline for {a.name}'))

        self.stdout.write(self.style.SUCCESS(
            f'{"[dry] " if dry else ""}{captured} baselines captured.'
        ))
