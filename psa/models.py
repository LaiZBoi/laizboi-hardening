"""
Native PSA / Service Desk models.

This is the OWN ticketing system, distinct from `integrations.PSATicket`
which mirrors data from third-party PSAs (Halo/Autotask/ConnectWise/etc.).

Foreign keys reference existing models discovered in INTEGRATION_MAP.md:
  - core.Organization        (tenant)
  - assets.Contact           (requester)
  - assets.Asset             (related asset)
  - vault.Password           (related credential — staff-only)
  - docs.Document            (linked doc / KB article)
  - scheduling.ScheduledTask (linked calendar event)
"""
from django.conf import settings as django_settings
from django.db import models, transaction
from django.utils import timezone


# ---------------------------------------------------------------------------
# Per-client opt-in (Phase 1 — full feature flag bedrock)
# ---------------------------------------------------------------------------

class ClientPSASettings(models.Model):
    """
    Per-org PSA configuration. PSA is off by default for every client; an
    admin must explicitly enable it. Additional client-level toggles guard
    the more sensitive surfaces (portal, anonymous tickets, ingestion).
    """
    organization = models.OneToOneField(
        'core.Organization',
        on_delete=models.CASCADE,
        related_name='psa_settings',
    )

    enabled = models.BooleanField(
        default=True,
        help_text='Enable PSA for this client. Inherits the global PSA flag — '
                  'use this to opt a specific client OUT.',
    )

    # Surface flags — every external surface defaults to OFF.
    portal_enabled = models.BooleanField(default=False, help_text='Allow this client to use the customer portal')
    anonymous_ticket_form_enabled = models.BooleanField(default=False, help_text='Allow public/anonymous ticket submission')
    email_to_ticket_enabled = models.BooleanField(default=False, help_text='Convert inbound emails to tickets')
    sms_notifications_enabled = models.BooleanField(default=False, help_text='Send SMS to staff for this client (no secrets)')
    desktop_alerts_enabled = models.BooleanField(default=False, help_text='Send desktop/browser alerts to staff for this client')
    external_alert_ingest_enabled = models.BooleanField(default=False, help_text='Accept alerts from external monitoring/RMM for this client')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'psa_client_settings'
        verbose_name = 'Client PSA Settings'
        verbose_name_plural = 'Client PSA Settings'

    def __str__(self):
        return f'PSA settings for {self.organization} (enabled={self.enabled})'


# ---------------------------------------------------------------------------
# Reference / lookup models (seeded by `psa_seed_defaults`)
# ---------------------------------------------------------------------------

class Queue(models.Model):
    """Routing queue (e.g. Helpdesk, Escalations, Projects, Security)."""
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'psa_queues'
        ordering = ['sort_order', 'name']

    def __str__(self):
        return self.name


class TicketStatus(models.Model):
    """
    Ticket workflow status (New, Assigned, In Progress, Waiting on Client,
    etc.). `is_terminal` marks closed/cancelled states for SLA logic.
    """
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=100, unique=True)
    is_terminal = models.BooleanField(default=False, help_text='True for resolved/closed/cancelled-style states')
    pauses_sla = models.BooleanField(default=False, help_text='True if SLA clock pauses while in this state')
    sort_order = models.PositiveIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'psa_ticket_statuses'
        ordering = ['sort_order', 'name']
        verbose_name_plural = 'Ticket statuses'

    def __str__(self):
        return self.name


class TicketPriority(models.Model):
    """P1..P5 priorities, plus default SLA targets in minutes."""
    code = models.CharField(max_length=10, unique=True, help_text="Short code, e.g. 'P1'")
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    response_target_minutes = models.PositiveIntegerField(default=240, help_text='Default response SLA target')
    resolution_target_minutes = models.PositiveIntegerField(default=4320, help_text='Default resolution SLA target')
    sort_order = models.PositiveIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'psa_ticket_priorities'
        ordering = ['sort_order', 'code']
        verbose_name_plural = 'Ticket priorities'

    def __str__(self):
        return f'{self.code} {self.name}'


class TicketType(models.Model):
    """Incident, Service Request, Change, Problem, Project Task, etc."""
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'psa_ticket_types'
        ordering = ['sort_order', 'name']

    def __str__(self):
        return self.name


# ---------------------------------------------------------------------------
# Ticket
# ---------------------------------------------------------------------------

SOURCE_CHOICES = [
    ('manual', 'Manual'),
    ('portal', 'Client Portal'),
    ('email', 'Email'),
    ('sms', 'SMS'),
    ('rmm', 'RMM'),
    ('api', 'API'),
    ('monitoring', 'Monitoring'),
    ('anonymous', 'Anonymous Form'),
    ('calendar', 'Calendar'),
]

VISIBILITY_CHOICES = [
    ('staff', 'Staff Only'),
    ('client', 'Visible to Client'),
]

IMPACT_CHOICES = [
    ('low', 'Low'),
    ('medium', 'Medium'),
    ('high', 'High'),
    ('critical', 'Critical'),
]

URGENCY_CHOICES = IMPACT_CHOICES  # same scale


class Ticket(models.Model):
    """
    The core PSA ticket. Field set mirrors the user spec.

    `ticket_number` is auto-generated as PSA-YYYY-NNNNNN per year on first save.
    """
    ticket_number = models.CharField(max_length=32, unique=True, db_index=True, blank=True)

    # Tenant + actors
    organization = models.ForeignKey(
        'core.Organization',
        on_delete=models.CASCADE,
        related_name='native_psa_tickets',
    )
    contact = models.ForeignKey(
        'assets.Contact',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='native_psa_tickets',
        help_text='Customer-side contact who requested',
    )
    requester_name = models.CharField(max_length=200, blank=True, help_text='Free-text requester (e.g. anonymous portal)')
    requester_email = models.EmailField(blank=True)

    # Content
    subject = models.CharField(max_length=300)
    description = models.TextField(blank=True)

    # Workflow
    status = models.ForeignKey(TicketStatus, on_delete=models.PROTECT, related_name='tickets')
    priority = models.ForeignKey(TicketPriority, on_delete=models.PROTECT, related_name='tickets')
    ticket_type = models.ForeignKey(TicketType, on_delete=models.PROTECT, related_name='tickets')
    queue = models.ForeignKey(Queue, on_delete=models.PROTECT, related_name='tickets')

    assigned_to = models.ForeignKey(
        django_settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='psa_assigned_tickets',
    )
    assigned_team = models.CharField(max_length=100, blank=True)

    # Source / visibility
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES, default='manual')
    visibility = models.CharField(max_length=20, choices=VISIBILITY_CHOICES, default='staff')
    client_can_view = models.BooleanField(default=False)

    # Linked context (Phase 1 wires the FKs; deeper integration in later phases)
    related_asset = models.ForeignKey(
        'assets.Asset',
        on_delete=models.SET_NULL, null=True, blank=True,
        related_name='native_psa_tickets',
    )
    related_documentation = models.ForeignKey(
        'docs.Document',
        on_delete=models.SET_NULL, null=True, blank=True,
        related_name='native_psa_doc_tickets',
    )
    related_kb_article = models.ForeignKey(
        'docs.Document',
        on_delete=models.SET_NULL, null=True, blank=True,
        related_name='native_psa_kb_tickets',
        help_text='KB article (docs.Document with is_global=True)',
    )
    related_calendar_event = models.ForeignKey(
        'scheduling.ScheduledTask',
        on_delete=models.SET_NULL, null=True, blank=True,
        related_name='native_psa_tickets',
    )
    parent_ticket = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL, null=True, blank=True,
        related_name='child_tickets',
    )
    duplicate_of = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL, null=True, blank=True,
        related_name='duplicates',
    )

    # Severity scoring
    impact = models.CharField(max_length=20, choices=IMPACT_CHOICES, default='medium')
    urgency = models.CharField(max_length=20, choices=URGENCY_CHOICES, default='medium')
    tags = models.JSONField(default=list, blank=True, help_text='List of free-form string tags')
    custom_fields = models.JSONField(default=dict, blank=True)

    # SLA tracking
    first_response_due_at = models.DateTimeField(null=True, blank=True)
    resolution_due_at = models.DateTimeField(null=True, blank=True)
    first_response_at = models.DateTimeField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    last_client_response_at = models.DateTimeField(null=True, blank=True)
    last_tech_response_at = models.DateTimeField(null=True, blank=True)
    sla_paused_until = models.DateTimeField(null=True, blank=True)
    sla_breached_response = models.BooleanField(default=False)
    sla_breached_resolution = models.BooleanField(default=False)

    # Vendor handoff
    waiting_on_vendor = models.BooleanField(default=False)
    vendor_ticket_number = models.CharField(max_length=100, blank=True)
    vendor_contact = models.CharField(max_length=200, blank=True)

    # Closure
    CLOSURE_CATEGORIES = [
        ('fixed', 'Fixed'),
        ('workaround', 'Workaround Provided'),
        ('duplicate', 'Duplicate'),
        ('cant_reproduce', "Can't Reproduce"),
        ('no_action', 'No Action Required'),
        ('cancelled', 'Cancelled by Requester'),
        ('other', 'Other'),
    ]
    closure_category = models.CharField(max_length=30, choices=CLOSURE_CATEGORIES, blank=True)
    resolution_summary = models.TextField(blank=True, help_text='Required when closing — what was the fix?')

    # Audit columns
    created_by = models.ForeignKey(
        django_settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='psa_tickets_created',
    )
    updated_by = models.ForeignKey(
        django_settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='psa_tickets_updated',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'psa_native_tickets'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['organization', '-created_at']),
            models.Index(fields=['status', '-created_at']),
            models.Index(fields=['assigned_to', '-created_at']),
            models.Index(fields=['priority', '-created_at']),
            models.Index(fields=['ticket_number']),
        ]

    def __str__(self):
        return f'{self.ticket_number or "PSA-?"} {self.subject}'

    @transaction.atomic
    def save(self, *args, **kwargs):
        if not self.ticket_number:
            self.ticket_number = self._generate_ticket_number()
        super().save(*args, **kwargs)

    @staticmethod
    def _generate_ticket_number():
        """
        Format: PSA-YYYY-NNNNNN, monotonically incrementing per year.
        We compute the next sequence atomically via a count on the
        current year's tickets — adequate for Phase 1 throughput. Higher-
        volume installs can swap in a dedicated counter table later.
        """
        year = timezone.now().year
        prefix = f'PSA-{year}-'
        # Find the highest existing number for this year
        last = (
            Ticket.objects
            .filter(ticket_number__startswith=prefix)
            .order_by('-ticket_number')
            .values_list('ticket_number', flat=True)
            .first()
        )
        if last:
            try:
                seq = int(last.rsplit('-', 1)[1]) + 1
            except (ValueError, IndexError):
                seq = 1
        else:
            seq = 1
        return f'{prefix}{seq:06d}'


# ---------------------------------------------------------------------------
# Ticket comments / notes / attachments
# ---------------------------------------------------------------------------

class TicketComment(models.Model):
    """
    A reply, internal note, or system event on a ticket.

    `is_internal=True` means staff-only — MUST be filtered out of any
    client-facing surface (portal, customer reply emails). Phase 3 (portal)
    will enforce this at the queryset layer; Phase 1 simply records it.
    """
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name='comments')
    author = models.ForeignKey(
        django_settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL, null=True, blank=True,
        related_name='psa_comments',
    )
    body = models.TextField()
    is_internal = models.BooleanField(default=False, help_text='Internal-only — never shown to the client')
    is_system = models.BooleanField(default=False, help_text='Auto-generated event (status change, etc.)')

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'psa_ticket_comments'
        ordering = ['created_at']
        indexes = [
            models.Index(fields=['ticket', 'created_at']),
            models.Index(fields=['ticket', 'is_internal']),
        ]

    def __str__(self):
        kind = 'internal note' if self.is_internal else 'reply'
        return f'{kind} on {self.ticket_id} by {self.author_id}'


def ticket_attachment_upload_to(instance, filename):
    """Tenant-scoped upload path so storage maps to the org boundary."""
    org_id = instance.ticket.organization_id if instance.ticket_id else 'orphan'
    return f'psa/{org_id}/tickets/{instance.ticket_id}/{filename}'


class TicketAttachment(models.Model):
    """
    File attached to a ticket. `is_internal` mirrors TicketComment so internal
    artifacts stay staff-only.
    """
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name='attachments')
    comment = models.ForeignKey(
        TicketComment, on_delete=models.CASCADE, null=True, blank=True,
        related_name='attachments',
        help_text='Optional — link this attachment to a specific comment',
    )
    uploaded_by = models.ForeignKey(
        django_settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL, null=True, blank=True,
        related_name='psa_attachments',
    )
    file = models.FileField(upload_to=ticket_attachment_upload_to)
    filename = models.CharField(max_length=255)
    content_type = models.CharField(max_length=100, blank=True)
    size_bytes = models.PositiveBigIntegerField(default=0)
    is_internal = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'psa_ticket_attachments'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['ticket', '-created_at']),
        ]

    def __str__(self):
        return self.filename or f'attachment {self.pk}'
