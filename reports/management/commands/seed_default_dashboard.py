"""Seed a default global dashboard with a sensible mix of widgets so
new installs land on something useful instead of an empty page."""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Create the default global dashboard with starter widgets'

    def handle(self, *args, **opts):
        from reports.models import Dashboard, DashboardWidget
        # Idempotent: skip if already created
        dash, created = Dashboard.objects.get_or_create(
            name='MSP Overview',
            defaults={
                'description': 'Default dashboard with revenue, ticket, and tech KPIs.',
                'is_global': True, 'is_default': True,
            },
        )
        if not created and dash.widgets.exists():
            self.stdout.write('Default dashboard already populated; skipping.')
            return

        widgets = [
            ('Revenue (30d)', 'metric', 'revenue_this_period', {'days': 30}),
            ('Open Tickets', 'metric', 'open_tickets_count', {}),
            ('SLA Overdue', 'metric', 'overdue_tickets_count', {}),
            ('Unbilled Hours at Risk', 'metric', 'unbilled_hours', {}),
            ('Active Techs', 'metric', 'active_techs', {}),
            ('Avg Time-to-Resolve', 'metric', 'avg_resolution_hours', {}),
            ('Revenue Trend (30d)', 'chart_bar', 'revenue_trend_30d', {}),
            ('Tickets Opened (30d)', 'chart_line', 'tickets_opened_30d', {}),
            ('Billable vs Non-billable', 'chart_pie', 'hours_split_pie', {}),
            ('Top Clients by Revenue', 'table', 'top_clients_by_revenue', {'days': 30, 'limit': 5}),
            ('Open Tickets by Priority', 'table', 'tickets_by_priority', {}),
            ('My Assigned Tickets', 'table', 'my_assigned_tickets', {}),
        ]
        for i, (title, wt, ds, params) in enumerate(widgets):
            DashboardWidget.objects.create(
                dashboard=dash, title=title, widget_type=wt,
                data_source=ds, query_params=params,
                position={'x': (i % 4) * 3, 'y': (i // 4) * 2, 'w': 3, 'h': 2},
            )
        self.stdout.write(self.style.SUCCESS(
            f'Seeded {len(widgets)} widgets onto "{dash.name}".'
        ))
