"""
Phase 14 v13 (v3.17.290): **OPTIONAL AI** workflow suggestion engine.

A heuristic suggester today: scans recent ticket history for repeating
patterns and emits `WorkflowSuggestion` rows. The model + accept()
plumbing is shared, so an LLM-driven implementation can swap in later
without changing the UI / accept flow.

Gated by `SystemSetting.psa_ai_enabled`.
"""
from __future__ import annotations

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import Count
from django.utils import timezone

from core.models import SystemSetting
from psa.models import Ticket, WorkflowSuggestion


class Command(BaseCommand):
    help = 'Scan recent ticket patterns and generate workflow rule suggestions.'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=30,
                            help='Window for pattern detection (default 30).')
        parser.add_argument('--min-count', type=int, default=5,
                            help='Pattern must appear at least N times.')
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        ss = SystemSetting.get_settings()
        if not getattr(ss, 'psa_ai_enabled', False):
            self.stdout.write(self.style.WARNING(
                'psa_ai_enabled is OFF — no suggestions generated.'))
            return

        days = options['days']
        min_count = options['min_count']
        dry = options['dry_run']
        cutoff = timezone.now() - timedelta(days=days)

        suggestions = []

        # Pattern 1: many tickets sharing the same priority all routed to
        # the same tech → suggest a skill-match auto-assign rule.
        rows = (Ticket.objects
                .filter(created_at__gte=cutoff,
                        assigned_to__isnull=False,
                        priority__isnull=False)
                .values('priority__code', 'assigned_to__username')
                .annotate(n=Count('id'))
                .filter(n__gte=min_count)
                .order_by('-n'))
        for row in rows[:20]:
            summary = (
                f'{row["n"]} tickets at priority {row["priority__code"]} in '
                f'the last {days} days were assigned to '
                f'{row["assigned_to__username"]} — consider auto-assigning.'
            )
            payload = {
                'name': f'Auto-route P{row["priority__code"][1:]} to {row["assigned_to__username"]}',
                'trigger': 'ticket_created',
                'conditions': {'priority': row['priority__code']},
                'actions': [{
                    'type': 'assign_to',
                    'username': row['assigned_to__username'],
                }],
                'description': 'Suggested by workflow analyzer',
            }
            suggestions.append(('priority-route', summary, payload))

        # Pattern 2: many tickets sharing a tag → suggest tagging on
        # subject keyword if a strong correlation exists.
        # (Simple heuristic; real LLM suggester can do much better.)
        from collections import Counter
        tag_counter = Counter()
        for tags in (Ticket.objects.filter(created_at__gte=cutoff)
                     .values_list('tags', flat=True)):
            for tag in (tags or []):
                tag_counter[tag] += 1
        for tag, n in tag_counter.most_common(10):
            if n < min_count:
                continue
            summary = f'Tag "{tag}" applied to {n} tickets in {days}d — review for automation.'
            payload = {
                'name': f'Inspect tag "{tag}" automation',
                'trigger': 'ticket_created',
                'conditions': {},
                'actions': [],
                'description': f'High-frequency tag candidate (n={n})',
            }
            suggestions.append(('tag-frequency', summary, payload))

        # Persist
        created = 0
        for kind, summary, payload in suggestions:
            # De-dup against pending suggestions with same payload name
            already = WorkflowSuggestion.objects.filter(
                status='pending',
                summary=summary,
            ).first()
            if already:
                continue
            if dry:
                self.stdout.write(f'[dry] would suggest: {summary}')
                continue
            WorkflowSuggestion.objects.create(
                summary=summary,
                rationale=f'Heuristic kind={kind}',
                suggested_payload=payload,
            )
            created += 1

        self.stdout.write(self.style.SUCCESS(
            f'{"[dry] " if dry else ""}{len(suggestions)} pattern(s) found; '
            f'{created} new suggestion(s) recorded.'
        ))
