"""
Signal handlers that turn server-side state changes into mobile pushes
(v3.17.463). Currently:

  * Ticket assignment change → push to the new assignee.

Registered in `api_mobile.apps.ApiMobileConfig.ready()`.
"""
from __future__ import annotations

import logging

from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

logger = logging.getLogger('api_mobile.signals')

# Sentinel set on the instance during pre_save so post_save knows what
# changed. Keeps the diff scoped to this single request.
_PRESAVE_ATTR = '_api_mobile_prev_assigned_to_id'


def _register_ticket_signals():
    try:
        from psa.models import Ticket
    except Exception:  # PSA may be uninstalled
        return

    @receiver(pre_save, sender=Ticket, dispatch_uid='api_mobile_ticket_presave_v1')
    def _ticket_presave(sender, instance, **kwargs):
        if instance.pk:
            try:
                prev = Ticket.objects.filter(pk=instance.pk).only('assigned_to_id').first()
                setattr(instance, _PRESAVE_ATTR, prev.assigned_to_id if prev else None)
            except Exception:
                setattr(instance, _PRESAVE_ATTR, None)
        else:
            setattr(instance, _PRESAVE_ATTR, None)

    @receiver(post_save, sender=Ticket, dispatch_uid='api_mobile_ticket_postsave_v1')
    def _ticket_postsave(sender, instance, created, **kwargs):
        from .push import send_push_to_user
        prev_assignee = getattr(instance, _PRESAVE_ATTR, None)
        new_assignee_id = instance.assigned_to_id
        if not new_assignee_id:
            return
        # Fire when newly assigned OR when reassigned to someone different
        if created or (prev_assignee != new_assignee_id):
            try:
                user = instance.assigned_to
                ticket_num = instance.ticket_number or f'#{instance.pk}'
                subject = (instance.subject or 'Ticket')[:80]
                send_push_to_user(
                    user,
                    title=f'Assigned: {ticket_num}',
                    body=subject,
                    data={
                        'kind': 'ticket',
                        'ticket_id': instance.pk,
                        'route': f'/tickets/{instance.pk}',
                    },
                )
            except Exception as e:  # noqa: BLE001
                logger.exception('ticket assignment push failed: %s', e)


def register():
    """Called from AppConfig.ready(). Idempotent — Django signals dedupe
    on `dispatch_uid`."""
    _register_ticket_signals()
