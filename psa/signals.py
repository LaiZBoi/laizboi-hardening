"""
PSA signal handlers - fire WorkflowRule engine on ticket / comment events,
plus tech notifications when a ticket gets assigned or scheduled.

Kept minimal: a status change is detected via a pre_save snapshot of the
old status_id, then post_save dispatches the appropriate trigger. Each
handler swallows engine errors so workflow misconfiguration never blocks
ticket save flow.
"""
from __future__ import annotations

import logging

from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from .models import Ticket, TicketComment


logger = logging.getLogger('psa.signals')


# ticket_id -> {status_id, assigned_to_id, resolution_due_at} captured pre_save
_PRIOR_TICKET = {}


@receiver(pre_save, sender=Ticket)
def _capture_prior_status(sender, instance, **kwargs):
    if instance.pk:
        try:
            prev = Ticket.objects.only(
                'status_id', 'assigned_to_id', 'resolution_due_at',
            ).get(pk=instance.pk)
            _PRIOR_TICKET[instance.pk] = {
                'status_id': prev.status_id,
                'assigned_to_id': prev.assigned_to_id,
                'resolution_due_at': prev.resolution_due_at,
            }
        except Ticket.DoesNotExist:
            _PRIOR_TICKET[instance.pk] = {
                'status_id': None,
                'assigned_to_id': None,
                'resolution_due_at': None,
            }
    else:
        _PRIOR_TICKET[instance.pk] = {
            'status_id': None,
            'assigned_to_id': None,
            'resolution_due_at': None,
        }


@receiver(post_save, sender=Ticket)
def _fire_ticket_workflow(sender, instance, created, **kwargs):
    prior = _PRIOR_TICKET.pop(instance.pk, {
        'status_id': None,
        'assigned_to_id': None,
        'resolution_due_at': None,
    })
    try:
        from .workflow_engine import fire
        if created:
            fire('ticket_created', instance)
        else:
            prior_status_id = prior.get('status_id')
            if prior_status_id is not None and prior_status_id != instance.status_id:
                fire('status_changed', instance, prior_status=prior_status_id)
            fire('ticket_updated', instance)
    except Exception:
        logger.exception('PSA ticket workflow signal failed')

    # Tech notifications - never block save flow
    try:
        from .notifications import notify_tech_assigned, notify_tech_scheduled
        by_user = getattr(instance, 'updated_by', None) or getattr(instance, 'created_by', None)
        # Assignment changed (including new ticket with an assignee)
        if instance.assigned_to_id and instance.assigned_to_id != prior.get('assigned_to_id'):
            notify_tech_assigned(instance, by_user=by_user)
        # Schedule changed (resolution_due_at set or changed) - only when assigned
        if (
            instance.assigned_to_id
            and instance.resolution_due_at
            and instance.resolution_due_at != prior.get('resolution_due_at')
        ):
            notify_tech_scheduled(
                instance,
                by_user=getattr(instance, 'updated_by', None),
                prior_due_date=prior.get('resolution_due_at'),
            )
    except Exception:
        logger.exception('PSA tech notification signal failed')

    # Phase 12 v1 (v3.17.231): CSAT email when ticket transitions to a
    # terminal status. Idempotent — TicketCSATSurvey is OneToOne, so a
    # survey only ever gets created (and emailed) on the first transition.
    try:
        prior_status_id = prior.get('status_id')
        moved_status = (prior_status_id is not None
                        and prior_status_id != instance.status_id)
        if (moved_status
                and instance.status_id
                and getattr(instance.status, 'is_terminal', False)):
            from .csat import maybe_send_csat
            maybe_send_csat(instance)
    except Exception:
        logger.exception('PSA CSAT survey hook failed')


@receiver(post_save, sender=TicketComment)
def _fire_comment_workflow(sender, instance, created, **kwargs):
    if not created:
        return
    try:
        from .workflow_engine import fire
        fire('comment_added', instance.ticket)
    except Exception:
        logger.exception('PSA comment workflow signal failed')


# ---------------------------------------------------------------------------
# Phase 7 -- two-way outsourcing sync (outbound side)
# ---------------------------------------------------------------------------

@receiver(post_save, sender=TicketComment)
def _fan_out_comment_to_partners(sender, instance, created, **kwargs):
    """When a comment lands on a ticket with active TicketShare rows,
    fire an HMAC-signed POST to each partner's webhook. Failures are
    logged but never block the comment save flow.

    Comments authored by the partner (source='partner') are skipped to
    avoid loops. Internal-only notes are skipped -- partners only see
    public-visible comments.
    """
    if not created:
        return
    if getattr(instance, 'source', '') == 'partner':
        return
    if instance.is_internal:
        return
    try:
        import time as _time

        from .views import _phase7_post_to_partner

        active_shares = instance.ticket.shares.filter(status__in=['accepted']).select_related('partner_org')
        for share in active_shares:
            try:
                payload = {
                    'event': 'comment',
                    'ticket_number': instance.ticket.ticket_number,
                    'share_pk': share.pk,
                    'payload': {
                        'body': instance.body or '',
                        'author': (
                            instance.author.get_username() if instance.author_id
                            else (instance.author_name or 'system')
                        ),
                        'author_email': instance.author_email or (
                            instance.author.email if instance.author_id else ''
                        ),
                        'is_internal': False,
                    },
                    'ts': int(_time.time()),
                }
                _phase7_post_to_partner(share, payload)
            except Exception:
                logger.exception('Outbound partner comment fan-out failed for share %s', share.pk)
    except Exception:
        logger.exception('Outbound partner comment fan-out top-level failed')


@receiver(post_save, sender=Ticket)
def _fan_out_status_to_partners(sender, instance, created, **kwargs):
    """When a Ticket status changes, fire an HMAC-signed POST to each
    partner with an active share. Fan-out is best-effort -- failures
    log but never block ticket save.

    Reuses the prior-status snapshot captured by `_capture_prior_status`.
    Note: that handler pops the snapshot in `_fire_ticket_workflow`; we
    must run BEFORE it -- since signal order is registration order, we
    instead recompute by checking what the workflow engine logs. To keep
    the fan-out independent of receiver ordering, we re-fetch the prior
    status via a separate snapshot dict.
    """
    if created:
        return
    try:
        prior = _PRIOR_STATUS_FOR_PARTNERS.pop(instance.pk, None)
        if prior is None or prior == instance.status_id:
            return
        active_shares = instance.shares.filter(status__in=['accepted']).select_related('partner_org')
        if not active_shares.exists():
            return
        import time as _time

        from .views import _phase7_post_to_partner

        status_slug = instance.status.slug if instance.status_id else ''
        for share in active_shares:
            try:
                payload = {
                    'event': 'status',
                    'ticket_number': instance.ticket_number,
                    'share_pk': share.pk,
                    'payload': {
                        'status': status_slug,
                        'status_name': (instance.status.name if instance.status_id else ''),
                    },
                    'ts': int(_time.time()),
                }
                _phase7_post_to_partner(share, payload)
            except Exception:
                logger.exception('Outbound partner status fan-out failed for share %s', share.pk)
    except Exception:
        logger.exception('Outbound partner status fan-out top-level failed')


# Separate snapshot dict so the fan-out handler is independent of
# `_fire_ticket_workflow` consuming `_PRIOR_TICKET`.
_PRIOR_STATUS_FOR_PARTNERS = {}


@receiver(pre_save, sender=Ticket)
def _capture_prior_status_for_partners(sender, instance, **kwargs):
    if not instance.pk:
        return
    try:
        prev = Ticket.objects.only('status_id').get(pk=instance.pk)
        _PRIOR_STATUS_FOR_PARTNERS[instance.pk] = prev.status_id
    except Ticket.DoesNotExist:
        _PRIOR_STATUS_FOR_PARTNERS[instance.pk] = None


@receiver(post_save, sender=Ticket)
def _auto_create_change_request(sender, instance, created, **kwargs):
    """When a Ticket of type 'change' is created, auto-spawn a draft
    ChangeRequest. The ticket_type slug 'change' is the link contract."""
    if not created:
        return
    try:
        if instance.ticket_type and instance.ticket_type.slug == 'change':
            from .models import ChangeRequest
            ChangeRequest.objects.get_or_create(
                ticket=instance,
                defaults={'organization': instance.organization},
            )
    except Exception:
        logger.exception('PSA change request auto-create signal failed')
