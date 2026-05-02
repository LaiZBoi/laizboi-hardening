"""
Phase 12 v2 (v3.17.232): Portal Announcements.

A portal announcement is a banner posted by an MSP admin (or a client
admin when granted) that's visible to all portal users of a given org
until it expires or is dismissed.
"""
from django.conf import settings
from django.db import models
from django.utils import timezone


class PortalAnnouncement(models.Model):
    SEVERITY_CHOICES = [
        ('info', 'Info'),
        ('success', 'Success'),
        ('warning', 'Warning'),
        ('danger', 'Critical'),
    ]

    organization = models.ForeignKey(
        'core.Organization', on_delete=models.CASCADE,
        related_name='portal_announcements',
        help_text='Client org whose portal users should see this banner.',
    )
    title = models.CharField(max_length=200)
    body = models.TextField(
        blank=True,
        help_text='Plain-text body. Rendered with newlines preserved.',
    )
    severity = models.CharField(
        max_length=20, choices=SEVERITY_CHOICES, default='info',
        help_text='Visual treatment — info (blue), success (green), warning (yellow), danger (red).',
    )
    is_active = models.BooleanField(
        default=True,
        help_text='Inactive announcements are hidden from the portal regardless of expiry.',
    )
    expires_at = models.DateTimeField(
        null=True, blank=True,
        help_text='If set, announcement is hidden after this time. Leave blank for no expiry.',
    )
    is_dismissable = models.BooleanField(
        default=True,
        help_text='When true, portal users can hide the banner for themselves '
                  '(stored per-session). Critical announcements should set this to False.',
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='portal_announcements_created',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'portal_announcements'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['organization', 'is_active', '-created_at']),
            models.Index(fields=['expires_at']),
        ]

    def __str__(self):
        return f'[{self.severity.upper()}] {self.title} ({self.organization.name})'

    @property
    def is_expired(self) -> bool:
        return bool(self.expires_at and self.expires_at <= timezone.now())

    @classmethod
    def active_for_org(cls, organization):
        """Visible announcements for `organization` right now."""
        now = timezone.now()
        return cls.objects.filter(
            organization=organization, is_active=True,
        ).exclude(expires_at__lte=now).order_by('-created_at')
