"""
Run a lightweight distributor health probe across all active connections.

Distributor adapters are pull-on-demand — the catalog and pricing endpoints
are called by the UI when needed, and order placement is human-initiated.
This command's job is to record connection health into
DistributorConnection.last_sync_status / last_sync_at so the list page
shows a green/red dot, and to fail loudly in cron if a distributor breaks.
"""
from __future__ import annotations

from django.core.management.base import BaseCommand
from django.utils import timezone

from integrations.models import DistributorConnection
from integrations.providers.distributors import get_distributor_provider


class Command(BaseCommand):
    help = 'Probe distributor connections (test_connection) and update sync status.'

    def add_arguments(self, parser):
        parser.add_argument('--connection-id', type=int,
                            help='Limit run to a single connection.')

    def handle(self, *args, **options):
        qs = DistributorConnection.objects.filter(is_active=True)
        if options.get('connection_id'):
            qs = qs.filter(pk=options['connection_id'])

        if not qs.exists():
            self.stdout.write(self.style.WARNING('No active distributor connections.'))
            return

        for conn in qs:
            provider = get_distributor_provider(conn)
            if provider is None:
                conn.last_sync_status = 'error'
                conn.last_error = 'No provider class registered'
                conn.last_sync_at = timezone.now()
                conn.save(update_fields=['last_sync_status', 'last_error', 'last_sync_at'])
                self.stdout.write(self.style.ERROR(f'{conn.name}: no provider'))
                continue

            try:
                ok = provider.test_connection()
            except Exception as exc:
                conn.last_sync_status = 'error'
                conn.last_error = str(exc)[:500]
                conn.last_sync_at = timezone.now()
                conn.save(update_fields=['last_sync_status', 'last_error', 'last_sync_at'])
                self.stdout.write(self.style.ERROR(f'{conn.name}: {exc}'))
                continue

            conn.last_sync_status = 'ok' if ok else 'error'
            conn.last_error = '' if ok else 'test_connection returned False'
            conn.last_sync_at = timezone.now()
            conn.save(update_fields=['last_sync_status', 'last_error', 'last_sync_at'])
            self.stdout.write(self.style.SUCCESS(f'{conn.name}: {"ok" if ok else "fail"}'))
