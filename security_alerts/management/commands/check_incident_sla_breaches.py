"""
Phase 23 v3.17.340 — walk every open SecurityIncident and append
`sla_breach` timeline events for any SLA target that has been crossed
without being met. Idempotent.
"""
from __future__ import annotations

from django.core.management.base import BaseCommand

from security_alerts.models import SecurityIncident, evaluate_incident_breaches


class Command(BaseCommand):
    help = 'Check open security incidents for SLA breaches and record timeline events.'

    def add_arguments(self, parser):
        parser.add_argument('--org-id', type=int, default=None)

    def handle(self, *args, **options):
        qs = SecurityIncident.objects.filter(
            status__in=['open', 'investigating', 'contained'],
        )
        if options.get('org_id'):
            qs = qs.filter(organization_id=options['org_id'])

        n_checked = 0
        n_breaches = 0
        for inc in qs.iterator():
            n_checked += 1
            new = evaluate_incident_breaches(inc)
            if new:
                n_breaches += len(new)
                self.stdout.write(
                    f'incident {inc.pk} — recorded breaches: {", ".join(new)}'
                )
        self.stdout.write(self.style.SUCCESS(
            f'Checked {n_checked} open incident(s); recorded {n_breaches} breach event(s).'
        ))
