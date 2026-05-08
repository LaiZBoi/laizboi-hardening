"""Compliance frameworks + per-org attestation (Phase 41)."""
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone


class ComplianceFramework(models.Model):
    """A compliance framework like PCI-DSS or HIPAA. Seeded by mgmt cmds."""

    slug = models.SlugField(unique=True, max_length=64)
    name = models.CharField(max_length=120)
    version = models.CharField(max_length=32, blank=True)
    description = models.TextField(blank=True)
    recertification_default_days = models.PositiveIntegerField(default=365)
    active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f'{self.name} {self.version}'.strip()


class ComplianceCategory(models.Model):
    """Group of controls within a framework (e.g. PCI Requirement 1)."""

    framework = models.ForeignKey(
        ComplianceFramework, on_delete=models.CASCADE, related_name='categories'
    )
    slug = models.SlugField(max_length=64)
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['framework', 'order', 'slug']
        unique_together = [('framework', 'slug')]
        verbose_name_plural = 'Compliance categories'

    def __str__(self):
        return f'{self.framework.name} / {self.name}'


class ComplianceCheckItem(models.Model):
    """An individual control / requirement within a category."""

    category = models.ForeignKey(
        ComplianceCategory, on_delete=models.CASCADE, related_name='items'
    )
    slug = models.SlugField(max_length=64)
    name = models.CharField(max_length=200)
    description = models.TextField(
        help_text='What the control requires (verbatim where possible).',
        blank=True,
    )
    evidence_hint = models.TextField(
        help_text='What evidence the auditor typically expects.',
        blank=True,
    )
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['category', 'order', 'slug']
        unique_together = [('category', 'slug')]

    def __str__(self):
        return f'{self.category.name} :: {self.name}'


class OrganizationCompliance(models.Model):
    """Per-org enrollment in a framework."""

    organization = models.ForeignKey(
        'core.Organization', on_delete=models.CASCADE,
        related_name='compliance_enrollments',
    )
    framework = models.ForeignKey(
        ComplianceFramework, on_delete=models.PROTECT,
        related_name='org_enrollments',
    )
    enrolled_at = models.DateTimeField(auto_now_add=True)
    enrolled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='+',
    )
    recertification_interval_days = models.PositiveIntegerField(default=365)
    recertification_emails_enabled = models.BooleanField(default=True)
    notify_email = models.EmailField(
        blank=True,
        help_text='If set, recertification reminders go here. '
                  'Otherwise the org\'s primary admin email.',
    )
    last_recertified_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ['organization', 'framework']
        unique_together = [('organization', 'framework')]
        verbose_name_plural = 'Organization compliance enrollments'

    def __str__(self):
        return f'{self.organization} :: {self.framework}'

    @property
    def recertification_due_at(self):
        """When the next recertification reminder is due."""
        if self.last_recertified_at is None:
            return self.enrolled_at + timedelta(days=self.recertification_interval_days)
        return self.last_recertified_at + timedelta(days=self.recertification_interval_days)

    @property
    def days_until_recertification(self):
        delta = self.recertification_due_at - timezone.now()
        return delta.days

    def status_counts(self):
        """Count of items by status — for progress bars."""
        counts = {s: 0 for s, _ in OrganizationComplianceItem.STATUS_CHOICES}
        counts['total'] = 0
        for item in self.item_attestations.all():
            counts[item.status] = counts.get(item.status, 0) + 1
            counts['total'] += 1
        return counts

    def percent_compliant(self):
        c = self.status_counts()
        if not c['total']:
            return 0
        return int(round(100 * c.get('compliant', 0) / c['total']))


class OrganizationComplianceItem(models.Model):
    """Per-org attestation for one control item."""

    STATUS_CHOICES = [
        ('unanswered', 'Unanswered'),
        ('compliant', 'Compliant'),
        ('partial', 'Partial'),
        ('non_compliant', 'Non-compliant'),
        ('not_applicable', 'Not applicable'),
    ]

    org_compliance = models.ForeignKey(
        OrganizationCompliance, on_delete=models.CASCADE,
        related_name='item_attestations',
    )
    item = models.ForeignKey(
        ComplianceCheckItem, on_delete=models.CASCADE,
        related_name='org_attestations',
    )
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default='unanswered',
    )
    notes = models.TextField(blank=True)
    evidence_link = models.URLField(blank=True)
    last_reviewed_at = models.DateTimeField(null=True, blank=True)
    last_reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='+',
    )

    class Meta:
        ordering = ['org_compliance', 'item']
        unique_together = [('org_compliance', 'item')]

    def __str__(self):
        return f'{self.org_compliance.organization} :: {self.item.slug} = {self.status}'


class RecertificationReminder(models.Model):
    """Audit row: a recertification email we sent."""

    org_compliance = models.ForeignKey(
        OrganizationCompliance, on_delete=models.CASCADE,
        related_name='reminders',
    )
    sent_at = models.DateTimeField(auto_now_add=True)
    recipient_email = models.EmailField()
    message_id = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ['-sent_at']

    def __str__(self):
        return f'Reminder to {self.recipient_email} at {self.sent_at:%Y-%m-%d}'
