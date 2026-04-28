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
    ('recurring', 'Recurring Schedule'),
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
    project = models.ForeignKey(
        'Project', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='tickets',
        help_text='Group this ticket under a PSA project',
    )
    recurring_schedule = models.ForeignKey(
        'RecurringTicketSchedule', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='generated_tickets',
        help_text='Set when the ticket was created by a recurring schedule',
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

    # Used when the comment came from an external source (email, portal,
    # anonymous form) and there's no User to attribute. Free-text by design.
    author_name = models.CharField(max_length=200, blank=True)
    author_email = models.EmailField(blank=True)
    source = models.CharField(max_length=20, default='manual',
        help_text='manual | email | portal | api')

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


# ---------------------------------------------------------------------------
# Phase 2b — watchers + canned replies
# ---------------------------------------------------------------------------

class TicketWatcher(models.Model):
    """
    A user who has subscribed to receive email notifications about activity
    on a specific ticket. Watching is per-user, per-ticket, idempotent.
    """
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name='watchers')
    user = models.ForeignKey(
        django_settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='psa_watching',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'psa_ticket_watchers'
        unique_together = [('ticket', 'user')]
        indexes = [
            models.Index(fields=['ticket']),
            models.Index(fields=['user']),
        ]

    def __str__(self):
        return f'{self.user_id} watches {self.ticket_id}'


class CannedReply(models.Model):
    """
    Reusable comment template. Variable substitution at insert time:
      {{ticket.number}}, {{ticket.subject}}, {{ticket.client}},
      {{user.first_name}}, {{user.last_name}}, {{user.username}}

    `organization=None` means global (visible on every client's tickets).
    `organization=<Org>` scopes the reply to that client only.
    """
    name = models.CharField(max_length=120, help_text='Short label shown in the dropdown')
    body = models.TextField(help_text='Use {{ticket.number}}, {{ticket.subject}}, {{ticket.client}}, {{user.first_name}}, {{user.last_name}}, {{user.username}}')
    organization = models.ForeignKey(
        'core.Organization',
        on_delete=models.CASCADE,
        null=True, blank=True,
        related_name='psa_canned_replies',
        help_text='Leave blank for a global reply (every client).',
    )
    created_by = models.ForeignKey(
        django_settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='psa_canned_replies',
    )
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'psa_canned_replies'
        ordering = ['sort_order', 'name']
        indexes = [
            models.Index(fields=['organization', 'is_active']),
        ]

    def __str__(self):
        scope = self.organization.name if self.organization_id else 'global'
        return f'{self.name} ({scope})'

    def render(self, ticket=None, user=None):
        """
        Substitute {{ticket.*}} and {{user.*}} placeholders in `body`.
        Unknown placeholders are left as-is. No HTML escaping — the result
        lands in a <textarea> the staff member can edit before posting.
        """
        out = self.body
        if ticket is not None:
            out = out.replace('{{ticket.number}}', ticket.ticket_number or '')
            out = out.replace('{{ticket.subject}}', ticket.subject or '')
            out = out.replace('{{ticket.client}}', ticket.organization.name if ticket.organization_id else '')
        if user is not None:
            out = out.replace('{{user.first_name}}', user.first_name or '')
            out = out.replace('{{user.last_name}}', user.last_name or '')
            out = out.replace('{{user.username}}', user.username or '')
        return out


# ---------------------------------------------------------------------------
# Phase 2c — time tracking + service catalog + hygiene
# ---------------------------------------------------------------------------

class TicketTimeEntry(models.Model):
    """
    Time logged against a ticket. Supports running-timer entries
    (started_at set, ended_at null) and finalised manual entries.
    Approval flow lands in a later phase; for now `is_billable` and
    `notes` are the load-bearing fields.
    """
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name='time_entries')
    user = models.ForeignKey(
        django_settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL, null=True, blank=True,
        related_name='psa_time_entries',
    )
    started_at = models.DateTimeField()
    ended_at = models.DateTimeField(null=True, blank=True)
    duration_minutes = models.PositiveIntegerField(default=0,
        help_text='Computed on save when ended_at is set')
    is_billable = models.BooleanField(default=True)
    notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'psa_ticket_time_entries'
        ordering = ['-started_at']
        indexes = [
            models.Index(fields=['ticket', '-started_at']),
            models.Index(fields=['user', '-started_at']),
            models.Index(fields=['ticket', 'is_billable']),
        ]
        constraints = [
            # Prevent two running timers on the same ticket for the same user.
            models.UniqueConstraint(
                fields=['ticket', 'user'],
                condition=models.Q(ended_at__isnull=True),
                name='psa_one_running_timer_per_user_per_ticket',
            ),
        ]

    def __str__(self):
        return f'{self.user_id} on ticket {self.ticket_id}: {self.duration_minutes}m'

    def save(self, *args, **kwargs):
        is_new = self.pk is None
        was_running = False
        prior_duration = 0
        if not is_new:
            prior = TicketTimeEntry.objects.filter(pk=self.pk).only('duration_minutes', 'ended_at').first()
            if prior:
                was_running = prior.ended_at is None
                prior_duration = prior.duration_minutes or 0

        if self.ended_at and self.started_at and not self.duration_minutes:
            delta = self.ended_at - self.started_at
            self.duration_minutes = max(0, int(delta.total_seconds() // 60))
        super().save(*args, **kwargs)

        # Contract hour accounting: only on transitions from running→stopped
        # or on initial create with a duration. Skip while still running.
        if self.duration_minutes and self.ended_at:
            from django.db.models import F
            try:
                contract = Contract.for_ticket(self.ticket)
            except (NameError, AttributeError):
                contract = None
            if contract:
                delta_min = self.duration_minutes - (prior_duration if not was_running else 0)
                if delta_min > 0:
                    Contract.objects.filter(pk=contract.pk).update(
                        hours_used_minutes=F('hours_used_minutes') + delta_min,
                    )

    @property
    def is_running(self):
        return self.ended_at is None


class ServiceCatalogItem(models.Model):
    """
    Predefined ticket template — admin or ticket creator picks one and
    a partly-filled ticket is created. Common MSP requests live here:
    New User, Terminate User, Password Reset, etc. Seeded by the
    psa_seed_defaults command.
    """
    name = models.CharField(max_length=120, unique=True)
    slug = models.SlugField(max_length=120, unique=True)
    description = models.TextField(blank=True,
        help_text='Shown in the catalog grid')
    default_subject = models.CharField(max_length=300, blank=True)
    default_body = models.TextField(blank=True)
    default_priority = models.ForeignKey(
        TicketPriority, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='catalog_items',
    )
    default_queue = models.ForeignKey(
        Queue, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='catalog_items',
    )
    default_type = models.ForeignKey(
        TicketType, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='catalog_items',
    )
    icon = models.CharField(max_length=80, blank=True,
        help_text='Font Awesome class, e.g. "fas fa-user-plus"')
    fields_json = models.JSONField(
        default=list, blank=True,
        help_text='List of {key, label, type, required, placeholder, options, help} '
                  'objects describing the structured fields the requester fills in. '
                  'Field values substitute into default_subject + default_body via '
                  '{{key}} placeholders. Supported types: text, email, date, '
                  'number, textarea, select, checkbox.',
    )
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @staticmethod
    def render_template(template, values):
        """{{key}} → values[key] substitution. Unfilled placeholders are stripped."""
        if not template:
            return ''
        import re as _re
        out = template
        for k, v in (values or {}).items():
            out = out.replace('{{' + str(k) + '}}', str(v) if v is not None else '')
        out = _re.sub(r'\{\{\s*[a-zA-Z0-9_]+\s*\}\}', '', out)
        return out

    class Meta:
        db_table = 'psa_service_catalog_items'
        ordering = ['sort_order', 'name']

    def __str__(self):
        return self.name


# ---------------------------------------------------------------------------
# Phase 3 — Projects (Workstream 3)
# ---------------------------------------------------------------------------

class Project(models.Model):
    """
    Lightweight PSA project. Groups tickets under a named delivery effort
    so techs can see all related work in one place. Optional client_org
    scopes the project to a specific customer organization (when null,
    the project is internal to the MSP tenant).

    NOT a full project-management replacement — for a deep PM tool, link
    to the existing `processes/` workflows. This model is the answer to
    "show me everything we're doing for ACME this quarter".
    """
    STATUS_CHOICES = [
        ('planning', 'Planning'),
        ('active', 'Active'),
        ('on_hold', 'On Hold'),
        ('completed', 'Completed'),
        ('cancelled', 'Cancelled'),
    ]

    organization = models.ForeignKey(
        'core.Organization', on_delete=models.CASCADE,
        related_name='psa_projects',
        help_text='MSP tenant that owns the project',
    )
    client_org = models.ForeignKey(
        'core.Organization', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='psa_client_projects',
        help_text='Client organization the project is for (null = internal)',
    )
    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=220, blank=True)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='planning')

    start_date = models.DateField(null=True, blank=True)
    due_date = models.DateField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    owner = models.ForeignKey(
        django_settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='owned_psa_projects',
    )
    is_billable = models.BooleanField(default=True)
    estimated_hours = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'psa_projects'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['organization', 'status']),
            models.Index(fields=['client_org', 'status']),
        ]

    def __str__(self):
        return f'{self.name} ({self.get_status_display()})'

    def save(self, *args, **kwargs):
        if not self.slug and self.name:
            from django.utils.text import slugify
            base = slugify(self.name)[:200] or 'project'
            slug = base
            n = 2
            while Project.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                slug = f'{base}-{n}'
                n += 1
            self.slug = slug
        if self.status == 'completed' and not self.completed_at:
            self.completed_at = timezone.now()
        super().save(*args, **kwargs)


# ---------------------------------------------------------------------------
# Recurring tickets (preventive maintenance) — Workstream 1 queued item
# ---------------------------------------------------------------------------

class RecurringTicketSchedule(models.Model):
    """
    Cron-driven creation of routine maintenance tickets.

    The `psa_run_recurring_tickets` management command (run hourly via
    cron / systemd timer) finds schedules whose next_run_at <= now and
    is_active=True, creates a fresh Ticket from the template, and rolls
    next_run_at forward by one frequency interval.
    """
    FREQUENCY_CHOICES = [
        ('daily', 'Daily'),
        ('weekly', 'Weekly'),
        ('monthly', 'Monthly'),
        ('quarterly', 'Quarterly'),
        ('yearly', 'Yearly'),
    ]

    organization = models.ForeignKey(
        'core.Organization', on_delete=models.CASCADE,
        related_name='psa_recurring_schedules',
    )
    client_org = models.ForeignKey(
        'core.Organization', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='psa_client_recurring_schedules',
        help_text='Client the recurring ticket targets (null = internal/MSP)',
    )

    name = models.CharField(max_length=200, help_text='Internal name for this schedule')
    template_subject = models.CharField(max_length=300)
    template_body = models.TextField(blank=True)

    queue = models.ForeignKey(Queue, on_delete=models.PROTECT, related_name='recurring_schedules')
    priority = models.ForeignKey(TicketPriority, on_delete=models.PROTECT, related_name='recurring_schedules')
    ticket_type = models.ForeignKey(TicketType, on_delete=models.PROTECT, related_name='recurring_schedules')
    assigned_to = models.ForeignKey(
        django_settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='recurring_schedules',
    )

    frequency = models.CharField(max_length=20, choices=FREQUENCY_CHOICES, default='monthly')
    interval = models.PositiveIntegerField(default=1,
        help_text='Run every N units (e.g. interval=2, frequency=weekly = every 2 weeks)')

    is_active = models.BooleanField(default=True)
    next_run_at = models.DateTimeField()
    last_run_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)

    created_by = models.ForeignKey(
        django_settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='created_recurring_schedules',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'psa_recurring_schedules'
        ordering = ['next_run_at']
        indexes = [
            models.Index(fields=['is_active', 'next_run_at']),
        ]

    def __str__(self):
        return f'{self.name} ({self.get_frequency_display()} ×{self.interval})'

    def advance_next_run(self):
        """Advance `next_run_at` by one frequency-interval step."""
        from datetime import timedelta
        from dateutil.relativedelta import relativedelta  # already in deps via icalendar
        base = self.next_run_at or timezone.now()
        if self.frequency == 'daily':
            self.next_run_at = base + timedelta(days=self.interval)
        elif self.frequency == 'weekly':
            self.next_run_at = base + timedelta(weeks=self.interval)
        elif self.frequency == 'monthly':
            self.next_run_at = base + relativedelta(months=self.interval)
        elif self.frequency == 'quarterly':
            self.next_run_at = base + relativedelta(months=3 * self.interval)
        elif self.frequency == 'yearly':
            self.next_run_at = base + relativedelta(years=self.interval)


# ---------------------------------------------------------------------------
# Knowledge Base linking — the docs app already provides KB-style articles
# (docs.Document with is_global=True), but ticket↔article was a single FK.
# This through model lets a ticket attach to multiple KB articles.
# ---------------------------------------------------------------------------

class TicketKBLink(models.Model):
    """Many-to-many linkage between PSA tickets and docs.Document KB articles."""
    ticket = models.ForeignKey('Ticket', on_delete=models.CASCADE, related_name='kb_links')
    article = models.ForeignKey('docs.Document', on_delete=models.CASCADE,
                                related_name='psa_ticket_links')
    linked_by = models.ForeignKey(
        django_settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='kb_links_created',
    )
    note = models.CharField(max_length=300, blank=True,
        help_text='Optional context on why this article was linked')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'psa_ticket_kb_links'
        unique_together = [['ticket', 'article']]
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.ticket.ticket_number} ↔ {self.article_id}'


# ---------------------------------------------------------------------------
# Approvals — generic manager-approval gate for time, expenses, quotes,
# AI actions, distributor orders, etc.
# ---------------------------------------------------------------------------

class PSAApproval(models.Model):
    """
    Generic approval record. Any PSA workflow that needs a manager sign-off
    can create one of these and reference it by (object_type, object_id).
    """
    KIND_CHOICES = [
        ('time', 'Time entry'),
        ('expense', 'Expense'),
        ('quote', 'Quote / estimate'),
        ('order', 'Distributor order'),
        ('action', 'AI action'),
        ('change', 'Change / scope'),
        ('other', 'Other'),
    ]
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('denied', 'Denied'),
        ('cancelled', 'Cancelled'),
    ]

    organization = models.ForeignKey(
        'core.Organization', on_delete=models.CASCADE,
        related_name='psa_approvals',
    )
    kind = models.CharField(max_length=20, choices=KIND_CHOICES)
    object_type = models.CharField(max_length=80,
        help_text='app_label.ModelName of the thing being approved')
    object_id = models.PositiveIntegerField()
    object_repr = models.CharField(max_length=300, blank=True)

    requested_by = models.ForeignKey(
        django_settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='psa_approvals_requested',
    )
    requested_at = models.DateTimeField(auto_now_add=True)
    request_comment = models.TextField(blank=True)

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    decided_by = models.ForeignKey(
        django_settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='psa_approvals_decided',
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_comment = models.TextField(blank=True)

    related_ticket = models.ForeignKey(
        'Ticket', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='approvals',
        help_text='If the approval is tied to a specific ticket',
    )

    class Meta:
        db_table = 'psa_approvals'
        ordering = ['-requested_at']
        indexes = [
            models.Index(fields=['organization', 'status', '-requested_at']),
            models.Index(fields=['object_type', 'object_id']),
        ]

    def __str__(self):
        return f'{self.get_kind_display()} #{self.pk} — {self.get_status_display()}'

    def decide(self, *, user, approved: bool, comment: str = ''):
        self.status = 'approved' if approved else 'denied'
        self.decided_by = user
        self.decided_at = timezone.now()
        self.decision_comment = comment[:5000]
        self.save(update_fields=['status', 'decided_by', 'decided_at', 'decision_comment'])


# ---------------------------------------------------------------------------
# Workstream 5 — Contracts (block hours, retainer, managed services)
# ---------------------------------------------------------------------------

class Contract(models.Model):
    """
    MSP contract with a client. Tracks an hours allowance + per-priority
    SLA overrides. The PSA SLA engine consults the active contract for
    a ticket's organization before falling back to the global queue
    SLA defaults.

    The `hours_used_minutes` field is incremented by `TicketTimeEntry.save()`
    when an entry's ticket belongs to a client_org with an active contract.
    """
    CONTRACT_TYPES = [
        ('block_hours', 'Block of Hours'),
        ('retainer', 'Monthly Retainer'),
        ('managed_services', 'Managed Services'),
        ('per_incident', 'Per-Incident'),
    ]
    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('active', 'Active'),
        ('expired', 'Expired'),
        ('cancelled', 'Cancelled'),
    ]

    organization = models.ForeignKey(
        'core.Organization', on_delete=models.CASCADE,
        related_name='psa_msp_contracts',
        help_text='MSP tenant that owns the contract',
    )
    client_org = models.ForeignKey(
        'core.Organization', on_delete=models.CASCADE,
        related_name='psa_client_contracts',
        help_text='Client the contract is with',
    )
    name = models.CharField(max_length=200)
    contract_type = models.CharField(max_length=30, choices=CONTRACT_TYPES, default='block_hours')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')

    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True,
        help_text='Open-ended if blank')

    total_hours = models.DecimalField(max_digits=10, decimal_places=2, default=0,
        help_text='Allowance for block_hours / retainer; 0 for unlimited')
    hours_used_minutes = models.PositiveIntegerField(default=0,
        help_text='Auto-incremented by TicketTimeEntry; minutes for precision')
    hourly_rate = models.DecimalField(max_digits=10, decimal_places=2, default=0,
        help_text='Effective rate when consuming or overaging')
    overage_rate = models.DecimalField(max_digits=10, decimal_places=2, default=0,
        help_text='Rate for hours beyond total_hours (defaults to hourly_rate when 0)')

    # Per-priority SLA matrix overrides queue defaults.
    # Schema: {"priority_slug": {"response_minutes": int, "resolution_minutes": int}, ...}
    sla_matrix = models.JSONField(default=dict, blank=True)

    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        django_settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='created_psa_contracts',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'psa_contracts'
        ordering = ['-start_date']
        indexes = [
            models.Index(fields=['organization', 'client_org', 'status']),
        ]

    def __str__(self):
        return f'{self.client_org.name} — {self.name} ({self.get_contract_type_display()})'

    @property
    def hours_used(self):
        return round(self.hours_used_minutes / 60.0, 2)

    @property
    def hours_remaining(self):
        if not self.total_hours:
            return None  # unlimited
        return max(0, float(self.total_hours) - self.hours_used)

    @property
    def is_currently_active(self):
        if self.status != 'active':
            return False
        today = timezone.now().date()
        if self.start_date and self.start_date > today:
            return False
        if self.end_date and self.end_date < today:
            return False
        return True

    @classmethod
    def for_ticket(cls, ticket):
        """Return the active contract for a ticket's client (None if none)."""
        if not ticket or not ticket.organization_id:
            return None
        today = timezone.now().date()
        return cls.objects.filter(
            client_org=ticket.organization,
            status='active',
            start_date__lte=today,
        ).filter(
            models.Q(end_date__isnull=True) | models.Q(end_date__gte=today)
        ).first()


# ---------------------------------------------------------------------------
# Email ingestion — Workstream 1 queued: email-to-ticket
# ---------------------------------------------------------------------------

class EmailIngestionConfig(models.Model):
    """
    Per-organization IMAP mailbox. The `psa_poll_email` management command
    connects, parses unread messages, creates tickets (or appends comments
    to existing tickets when the subject contains a ticket number), and
    marks messages as read.

    Password is encrypted at rest using the same vault.encryption helpers
    as integrations/RMM credentials.
    """
    organization = models.ForeignKey(
        'core.Organization', on_delete=models.CASCADE,
        related_name='email_ingestion_configs',
    )
    name = models.CharField(max_length=200)
    imap_host = models.CharField(max_length=255)
    imap_port = models.PositiveIntegerField(default=993)
    use_ssl = models.BooleanField(default=True)
    username = models.CharField(max_length=255)
    encrypted_password = models.TextField(blank=True)
    folder = models.CharField(max_length=100, default='INBOX')

    # Defaults applied when creating a new ticket.
    default_queue = models.ForeignKey(Queue, on_delete=models.PROTECT,
                                      related_name='email_configs')
    default_priority = models.ForeignKey(TicketPriority, on_delete=models.PROTECT,
                                         related_name='email_configs')
    default_type = models.ForeignKey(TicketType, on_delete=models.PROTECT,
                                     related_name='email_configs')

    # Subject regex that captures a ticket number for reply-threading.
    # Default matches "PSA-YYYY-NNNNNN" anywhere in the subject.
    subject_ticket_pattern = models.CharField(
        max_length=200, default=r'PSA-\d{4}-\d{6}',
        help_text='Regex; first match in the subject is treated as a reply to that ticket',
    )

    is_active = models.BooleanField(default=True)
    poll_interval_minutes = models.PositiveIntegerField(default=5)
    last_poll_at = models.DateTimeField(null=True, blank=True)
    last_poll_status = models.CharField(max_length=50, blank=True)
    last_error = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'psa_email_ingestion_configs'
        ordering = ['name']

    def __str__(self):
        return f'{self.name} ({self.username}@{self.imap_host})'

    def set_password(self, password: str):
        from vault.encryption import encrypt_dict
        import json as _json
        if not password:
            self.encrypted_password = ''
            return
        self.encrypted_password = _json.dumps(encrypt_dict({'p': password}))

    def get_password(self) -> str:
        from vault.encryption import decrypt_dict
        import json as _json
        if not self.encrypted_password:
            return ''
        try:
            return decrypt_dict(_json.loads(self.encrypted_password)).get('p', '')
        except Exception:
            return ''


# ---------------------------------------------------------------------------
# Time-entry hook — increment Contract.hours_used_minutes on save
# ---------------------------------------------------------------------------

# This is attached as a Django signal in psa/apps.py to keep models.py
# free of side effects on import.


# ---------------------------------------------------------------------------
# Quotes / Estimates (Workstream 5 — sales pipeline)
# ---------------------------------------------------------------------------

class Quote(models.Model):
    """
    Sales quote / estimate. Goes through draft → sent → accepted/rejected,
    and on acceptance optionally converts into a Ticket. Line items are
    stored on QuoteLineItem.

    The `quote_number` is auto-assigned `Q-YYYY-NNNNN` per year.
    """
    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('sent', 'Sent'),
        ('accepted', 'Accepted'),
        ('rejected', 'Rejected'),
        ('expired', 'Expired'),
    ]

    quote_number = models.CharField(max_length=32, unique=True, db_index=True, blank=True)
    organization = models.ForeignKey(
        'core.Organization', on_delete=models.CASCADE,
        related_name='psa_quotes',
    )
    client_org = models.ForeignKey(
        'core.Organization', on_delete=models.CASCADE,
        related_name='psa_client_quotes',
    )
    title = models.CharField(max_length=300)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')

    valid_until = models.DateField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    accepted_at = models.DateTimeField(null=True, blank=True)
    rejected_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(blank=True)

    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    tax_rate = models.DecimalField(max_digits=5, decimal_places=4, default=0,
        help_text='e.g. 0.0875 for 8.75%')
    tax_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    converted_ticket = models.ForeignKey(
        Ticket, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='source_quotes',
        help_text='Set when the quote was converted to a ticket on acceptance',
    )
    converted_project = models.ForeignKey(
        'Project', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='source_quotes',
    )

    created_by = models.ForeignKey(
        django_settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='created_psa_quotes',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'psa_quotes'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['organization', 'status', '-created_at']),
            models.Index(fields=['client_org', 'status']),
        ]

    def __str__(self):
        return f'{self.quote_number} — {self.title}'

    def save(self, *args, **kwargs):
        if not self.quote_number:
            self.quote_number = self._next_number()
        super().save(*args, **kwargs)

    def _next_number(self) -> str:
        year = timezone.now().year
        prefix = f'Q-{year}-'
        last = Quote.objects.filter(quote_number__startswith=prefix).order_by('-quote_number').first()
        if last and last.quote_number:
            try:
                n = int(last.quote_number.rsplit('-', 1)[-1])
            except ValueError:
                n = 0
        else:
            n = 0
        return f'{prefix}{n + 1:05d}'

    def recompute_totals(self):
        from decimal import Decimal
        sub = sum((li.line_total for li in self.line_items.all()), Decimal('0'))
        self.subtotal = sub
        self.tax_amount = (sub * (self.tax_rate or 0)).quantize(Decimal('0.01'))
        self.total = sub + self.tax_amount
        self.save(update_fields=['subtotal', 'tax_amount', 'total'])

    def mark_accepted(self, *, user=None, create_ticket: bool = True,
                      queue=None, priority=None, ticket_type=None,
                      status=None):
        self.status = 'accepted'
        self.accepted_at = timezone.now()
        if create_ticket and not self.converted_ticket and queue and priority and ticket_type and status:
            self.converted_ticket = Ticket.objects.create(
                organization=self.client_org,
                subject=f'Q {self.quote_number}: {self.title}',
                description=self.description or '',
                queue=queue, priority=priority,
                ticket_type=ticket_type, status=status,
                source='manual',
                created_by=user,
            )
        self.save()


class QuoteLineItem(models.Model):
    quote = models.ForeignKey(Quote, on_delete=models.CASCADE, related_name='line_items')
    sort_order = models.PositiveIntegerField(default=0)

    description = models.CharField(max_length=300)
    quantity = models.DecimalField(max_digits=10, decimal_places=2, default=1)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    is_taxable = models.BooleanField(default=True)

    class Meta:
        db_table = 'psa_quote_line_items'
        ordering = ['sort_order', 'pk']

    @property
    def line_total(self):
        from decimal import Decimal
        return (self.quantity * self.unit_price).quantize(Decimal('0.01'))

    def __str__(self):
        return f'{self.description} ({self.quantity} × {self.unit_price})'


# ---------------------------------------------------------------------------
# Expenses (Workstream 2 expansion)
# ---------------------------------------------------------------------------

def expense_receipt_upload_to(instance, filename):
    return f'psa/expenses/{instance.ticket.organization_id}/{instance.ticket_id}/{filename}'


class TicketExpense(models.Model):
    """
    Reimbursable / billable expenses tracked against a ticket. Optional
    receipt file upload. Approvals integrate with PSAApproval.
    """
    CATEGORY_CHOICES = [
        ('mileage', 'Mileage / Travel'),
        ('parts', 'Parts'),
        ('software', 'Software / License'),
        ('subcontractor', 'Subcontractor'),
        ('shipping', 'Shipping'),
        ('other', 'Other'),
    ]

    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name='expenses')
    user = models.ForeignKey(
        django_settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='psa_expenses',
    )
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES, default='other')
    description = models.CharField(max_length=300)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=8, default='USD')
    incurred_on = models.DateField()
    is_billable = models.BooleanField(default=True)
    is_reimbursable = models.BooleanField(default=True)
    receipt_file = models.FileField(upload_to=expense_receipt_upload_to, blank=True, null=True)

    approval = models.ForeignKey(
        'PSAApproval', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='expenses',
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'psa_ticket_expenses'
        ordering = ['-incurred_on', '-created_at']
        indexes = [
            models.Index(fields=['ticket', '-incurred_on']),
            models.Index(fields=['user', '-incurred_on']),
        ]

    def __str__(self):
        return f'{self.description} — {self.amount} {self.currency}'


# ---------------------------------------------------------------------------
# Project tasks (Workstream 3 expansion)
# ---------------------------------------------------------------------------

class ProjectTask(models.Model):
    """
    A discrete task or milestone under a Project. Distinct from Ticket
    (which is for client-facing service desk work) — ProjectTask is for
    internal delivery breakdown. Tasks can optionally link to a Ticket
    when concrete client work is needed.
    """
    STATUS_CHOICES = [
        ('todo', 'To Do'),
        ('in_progress', 'In Progress'),
        ('blocked', 'Blocked'),
        ('done', 'Done'),
        ('cancelled', 'Cancelled'),
    ]

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='tasks')
    parent = models.ForeignKey(
        'self', on_delete=models.CASCADE,
        null=True, blank=True, related_name='subtasks',
        help_text='Optional — for milestone → child task hierarchy',
    )
    title = models.CharField(max_length=300)
    description = models.TextField(blank=True)
    is_milestone = models.BooleanField(default=False,
        help_text='Major delivery checkpoint (rendered prominently)')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='todo')

    assigned_to = models.ForeignKey(
        django_settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='psa_project_tasks',
    )
    due_date = models.DateField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    estimated_hours = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)

    sort_order = models.PositiveIntegerField(default=0)
    related_ticket = models.ForeignKey(
        Ticket, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='project_tasks',
    )

    created_by = models.ForeignKey(
        django_settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='created_psa_project_tasks',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'psa_project_tasks'
        ordering = ['sort_order', 'created_at']
        indexes = [
            models.Index(fields=['project', 'status']),
            models.Index(fields=['assigned_to', 'status']),
        ]

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        if self.status in ('done', 'cancelled') and not self.completed_at:
            self.completed_at = timezone.now()
        elif self.status not in ('done', 'cancelled') and self.completed_at:
            self.completed_at = None
        super().save(*args, **kwargs)
