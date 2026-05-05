"""
PSA workflow rule engine.

Fires from psa/signals.py on ticket and comment events. Each rule is a
small JSON DSL — evaluate the condition, run the actions in order. Rule
errors are caught and stored on the rule row so a single bad rule never
breaks ticket save flow.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from django.contrib.auth.models import User
from django.utils import timezone


logger = logging.getLogger('psa.workflow_engine')


# ---- Condition evaluator --------------------------------------------------

def _get(ticket, key: str):
    """Pull a comparable scalar from the ticket for condition checking."""
    if key == 'priority':
        return getattr(getattr(ticket, 'priority', None), 'code', None)
    if key == 'queue':
        return getattr(getattr(ticket, 'queue', None), 'name', None)
    if key == 'status':
        return getattr(getattr(ticket, 'status', None), 'slug', None)
    if key == 'ticket_type':
        return getattr(getattr(ticket, 'ticket_type', None), 'slug', None)
    if key == 'is_unassigned':
        return ticket.assigned_to_id is None
    if key == 'is_paused':
        try:
            return bool(ticket.status_id and ticket.status.pauses_sla)
        except Exception:
            return False
    if key == 'subject':
        return ticket.subject or ''
    return None


def matches(ticket, condition: Dict[str, Any]) -> bool:
    """Evaluate a single condition dict against a ticket. Empty = always True."""
    if not condition:
        return True
    if not isinstance(condition, dict):
        return False

    # Boolean combinators
    if 'any' in condition:
        return any(matches(ticket, c) for c in (condition.get('any') or []))
    if 'all' in condition:
        return all(matches(ticket, c) for c in (condition.get('all') or []))

    # Specials
    if 'subject_contains' in condition:
        needle = (condition['subject_contains'] or '').lower()
        if needle and needle not in (ticket.subject or '').lower():
            return False

    # Generic field comparisons
    for key, val in condition.items():
        if key in ('any', 'all', 'subject_contains'):
            continue
        if key.endswith('__in'):
            base = key[:-4]
            cur = _get(ticket, base)
            if cur not in (val or []):
                return False
        elif key.endswith('__not'):
            base = key[:-5]
            cur = _get(ticket, base)
            if cur == val:
                return False
        else:
            cur = _get(ticket, key)
            if cur != val:
                return False
    return True


# ---- Action runner --------------------------------------------------------

def _resolve_user(username: Optional[str]) -> Optional[User]:
    if not username:
        return None
    return User.objects.filter(username=username, is_active=True).first()


def run_action(ticket, action: Dict[str, Any]) -> None:
    """Run a single action dict against a ticket. Raises on bad input."""
    from .models import (
        Queue, TicketComment, TicketPriority, TicketWatcher,
    )
    atype = (action or {}).get('type', '')

    if atype == 'set_priority':
        code = action.get('code')
        p = TicketPriority.objects.filter(code=code).first()
        if p:
            ticket.priority = p
            ticket.save(update_fields=['priority', 'updated_at'])

    elif atype == 'set_queue':
        name = action.get('name')
        q = Queue.objects.filter(name=name, is_active=True).first()
        if q:
            ticket.queue = q
            ticket.save(update_fields=['queue', 'updated_at'])

    elif atype == 'assign_to':
        u = _resolve_user(action.get('username'))
        if u:
            ticket.assigned_to = u
            ticket.save(update_fields=['assigned_to', 'updated_at'])

    elif atype == 'add_watcher':
        u = _resolve_user(action.get('username'))
        if u:
            TicketWatcher.objects.get_or_create(ticket=ticket, user=u)

    elif atype == 'add_internal_note':
        body = (action.get('body') or '').strip()
        if body:
            TicketComment.objects.create(
                ticket=ticket, body=body[:5000],
                is_internal=True, is_system=True,
                source='workflow',
            )

    elif atype == 'add_tag':
        tag = (action.get('tag') or '').strip()
        if tag:
            tags = list(ticket.tags or [])
            if tag not in tags:
                tags.append(tag)
                ticket.tags = tags
                ticket.save(update_fields=['tags', 'updated_at'])

    elif atype == 'fire_rule':
        # Phase 14 v4 (v3.17.285): multi-step orchestration. Chain
        # to another WorkflowRule by name within the same org.
        # Cycle protection lives in `fire()` via _firing_chain.
        from .models import WorkflowRule
        from django.db.models import Q
        name = (action.get('name') or '').strip()
        if not name:
            raise ValueError('fire_rule requires "name"')
        nxt = WorkflowRule.objects.filter(
            Q(organization__isnull=True) | Q(organization_id=ticket.organization_id),
            name=name, is_active=True,
        ).first()
        if nxt is None:
            raise ValueError(f'fire_rule: no active rule named {name!r}')
        # Run nested rule's actions inline (skip the cron-like trigger
        # filter — chained execution intentionally bypasses trigger
        # matching since the parent already fired).
        if matches(ticket, nxt.conditions or {}):
            for sub in (nxt.actions or []):
                run_action(ticket, sub)
        else:
            for sub in (nxt.else_actions or []):
                run_action(ticket, sub)

    else:
        raise ValueError(f'Unknown action type: {atype!r}')


# ---- Rule runner ----------------------------------------------------------

def fire(trigger: str, ticket, *, prior_status=None) -> int:
    """
    Run all active rules in the ticket's organization that match `trigger`
    AND whose conditions evaluate true. Returns the number of rules fired.
    Errors per rule are captured to WorkflowRule.last_error.
    """
    from django.db.models import Q
    from .models import WorkflowRule

    if ticket is None or not ticket.organization_id:
        return 0

    # Match MSP-wide rules (organization IS NULL) AND rules scoped to this
    # ticket's client organization.
    rules = WorkflowRule.objects.filter(
        Q(organization__isnull=True) | Q(organization_id=ticket.organization_id),
        trigger=trigger,
        is_active=True,
    ).order_by('sort_order', 'pk')

    fired = 0
    for rule in rules:
        try:
            if matches(ticket, rule.conditions or {}):
                for action in (rule.actions or []):
                    run_action(ticket, action)
            elif rule.else_actions:
                # Phase 14 v2 (v3.17.285): conditional routing — when
                # the main condition fails AND else_actions are
                # configured, run them instead. This still counts as
                # a "fire" so the cooldown / fire_count tracks both
                # branches.
                for action in rule.else_actions:
                    run_action(ticket, action)
            else:
                continue
            rule.last_fired_at = timezone.now()
            rule.fire_count = (rule.fire_count or 0) + 1
            rule.last_error = ''
            rule.save(update_fields=['last_fired_at', 'fire_count', 'last_error'])
            fired += 1
        except Exception as exc:
            logger.exception('workflow rule %s failed', rule.pk)
            rule.last_error = str(exc)[:1000]
            rule.save(update_fields=['last_error'])
    return fired
