"""
PSA AI Assist — models.

`AISuggestion` is the proposal (drafted by the model, may or may not be
applied). `AIActionLog` is the structured outcome record once a
suggestion is approved + executed. `AIUsageBucket` tracks token spend
per (org, day) and (user, day) for cost guardrails.

All models scoped to `core.Organization`. Cross-tenant access is
prevented at the queryset layer in views; defence-in-depth checks at
the service layer too.
"""
from django.conf import settings as django_settings
from django.db import models


KIND_CHOICES = [
    ('reply', 'Reply'),
    ('action', 'Action'),
    ('triage', 'Triage Guidance'),
]
REVIEW_STATES = [
    ('draft', 'Drafted by AI — awaiting tech review'),
    ('pending_review', 'Awaiting senior approval'),
    ('approved', 'Approved (and applied)'),
    ('rejected', 'Rejected by reviewer'),
    ('expired', 'Expired before action'),
    ('superseded', 'Replaced by a newer suggestion'),
    ('failed', 'Generation failed'),
    ('blocked', 'Blocked by safety filter'),
]
RISK_LEVELS = [('low', 'Low'), ('medium', 'Medium'), ('high', 'High')]


class AISuggestion(models.Model):
    """A single AI proposal attached to a PSA ticket."""
    organization = models.ForeignKey(
        'core.Organization', on_delete=models.CASCADE, related_name='ai_suggestions'
    )
    # Either native_ticket or psa_ticket is set (never both, never neither).
    native_ticket = models.ForeignKey(
        'psa.Ticket', null=True, blank=True,
        on_delete=models.CASCADE, related_name='ai_suggestions',
    )
    psa_ticket = models.ForeignKey(
        'integrations.PSATicket', null=True, blank=True,
        on_delete=models.CASCADE, related_name='ai_suggestions',
    )

    kind = models.CharField(max_length=20, choices=KIND_CHOICES)
    risk_level = models.CharField(max_length=20, choices=RISK_LEVELS, default='medium')
    review_state = models.CharField(max_length=20, choices=REVIEW_STATES, default='draft')

    # Generation metadata
    model_name = models.CharField(max_length=100)
    model_version = models.CharField(max_length=50, blank=True)
    confidence = models.DecimalField(max_digits=4, decimal_places=2, default=0)
    prompt_version = models.CharField(max_length=50, blank=True)

    # Reply content (when kind='reply')
    suggested_body = models.TextField(blank=True)
    final_body = models.TextField(blank=True, help_text='What was actually sent (after edits)')

    # Action payload (when kind='action')
    action_type = models.CharField(max_length=50, blank=True)
    action_payload = models.JSONField(default=dict, blank=True)

    # Context audit — what we fed the model. Vault data is forbidden here;
    # the no-secrets test verifies it. Stored for replay/diagnose only.
    context_snapshot = models.JSONField(default=dict, blank=True)

    # Token accounting — for cost guardrails + per-user rate-limit.
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    requested_by = models.ForeignKey(
        django_settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='+',
    )

    # Review trail
    created_at = models.DateTimeField(auto_now_add=True)
    requested_review_at = models.DateTimeField(null=True, blank=True)
    requested_review_by = models.ForeignKey(
        django_settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='+',
    )
    reviewer = models.ForeignKey(
        django_settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='+',
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewer_note = models.TextField(blank=True)

    class Meta:
        db_table = 'psa_ai_suggestions'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['organization', 'review_state', '-created_at']),
            models.Index(fields=['native_ticket', '-created_at']),
            models.Index(fields=['psa_ticket', '-created_at']),
            models.Index(fields=['requested_by', '-created_at']),
        ]

    def __str__(self):
        ref = self.native_ticket_id or self.psa_ticket_id or '?'
        return f'AISuggestion {self.kind}/{self.review_state} on ticket {ref}'

    def ticket_obj(self):
        return self.native_ticket or self.psa_ticket


class AIActionLog(models.Model):
    """Append-only outcome record for an applied AISuggestion."""
    suggestion = models.ForeignKey(
        AISuggestion, on_delete=models.CASCADE, related_name='action_logs'
    )
    organization = models.ForeignKey(
        'core.Organization', on_delete=models.CASCADE, related_name='ai_action_logs'
    )
    actor = models.ForeignKey(
        django_settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='+',
    )
    applied_at = models.DateTimeField(auto_now_add=True)
    success = models.BooleanField()
    error = models.TextField(blank=True)
    diff = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = 'psa_ai_action_logs'
        ordering = ['-applied_at']


class AIUsageBucket(models.Model):
    """
    Daily token spend per scope. One row per (organization, day) and
    one per (user, day). Used for cost ceilings — checked BEFORE every
    generation. Updated atomically via F() expressions.
    """
    SCOPE_CHOICES = [('org', 'Organization'), ('user', 'User')]
    scope = models.CharField(max_length=10, choices=SCOPE_CHOICES)
    organization = models.ForeignKey(
        'core.Organization', null=True, blank=True,
        on_delete=models.CASCADE, related_name='ai_usage_buckets',
    )
    user = models.ForeignKey(
        django_settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.CASCADE, related_name='ai_usage_buckets',
    )
    day = models.DateField(db_index=True)
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    generations = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = 'psa_ai_usage_buckets'
        unique_together = [
            ('scope', 'organization', 'user', 'day'),
        ]
        indexes = [
            models.Index(fields=['scope', 'day']),
            models.Index(fields=['organization', 'day']),
            models.Index(fields=['user', 'day']),
        ]

    @property
    def total_tokens(self):
        return self.input_tokens + self.output_tokens
