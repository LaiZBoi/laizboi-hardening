"""
crm/models.py — CRM sales pipeline.

Lead: pre-qualification — somebody who *might* become a customer.
Opportunity: a deal in flight, scoped to a specific Organization.
Campaign: a marketing/outreach effort that produces leads.
"""
from decimal import Decimal
from django.conf import settings as django_settings
from django.db import models


class Campaign(models.Model):
    """A marketing/outreach effort. Leads + Opportunities can attribute
    to one. Used for cost-per-lead + ROI reports (Phase 5.2)."""
    organization = models.ForeignKey(
        'core.Organization', on_delete=models.CASCADE,
        related_name='crm_campaigns',
    )
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    channel = models.CharField(
        max_length=40, blank=True,
        choices=[
            ('email', 'Email'), ('cold_call', 'Cold Call'),
            ('referral', 'Referral'), ('event', 'Event'),
            ('social', 'Social Media'), ('paid_ads', 'Paid Ads'),
            ('content', 'Content / SEO'), ('partner', 'Partner'),
            ('other', 'Other'),
        ],
    )
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    budget = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        django_settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='+',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'crm_campaigns'
        ordering = ['-created_at']
        indexes = [models.Index(fields=['organization', 'is_active'])]

    def __str__(self):
        return self.name


class Lead(models.Model):
    """
    A potential customer who hasn't been qualified yet. Once qualified,
    convert into an Organization + Opportunity.
    """
    STATUS_CHOICES = [
        ('new', 'New'),
        ('contacted', 'Contacted'),
        ('qualified', 'Qualified'),
        ('disqualified', 'Disqualified'),
        ('converted', 'Converted'),
    ]
    organization = models.ForeignKey(
        'core.Organization', on_delete=models.CASCADE,
        related_name='crm_leads',
        help_text='MSP tenant that owns this lead.',
    )
    company_name = models.CharField(max_length=200)
    contact_first_name = models.CharField(max_length=80, blank=True)
    contact_last_name = models.CharField(max_length=80, blank=True)
    contact_email = models.EmailField(blank=True)
    contact_phone = models.CharField(max_length=40, blank=True)
    contact_title = models.CharField(max_length=120, blank=True)
    website = models.URLField(blank=True)
    industry = models.CharField(max_length=80, blank=True)
    employee_count = models.PositiveIntegerField(null=True, blank=True)
    estimated_value = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='new')
    source = models.CharField(max_length=40, blank=True,
        help_text='Free-form source label (web form, referral name, etc.)')
    campaign = models.ForeignKey(
        Campaign, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='leads',
    )

    assigned_to = models.ForeignKey(
        django_settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='+',
    )
    notes = models.TextField(blank=True)

    # Conversion outcomes
    converted_to_org = models.ForeignKey(
        'core.Organization', on_delete=models.SET_NULL,
        related_name='+', null=True, blank=True,
    )
    converted_to_opportunity = models.ForeignKey(
        'crm.Opportunity', on_delete=models.SET_NULL,
        related_name='+', null=True, blank=True,
    )

    # Phase 5.2: lead scoring (auto-computed in save())
    score = models.PositiveSmallIntegerField(default=0,
        help_text='0-100 auto-computed by the lead scoring service. Higher = hotter.')

    created_by = models.ForeignKey(
        django_settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='created_leads',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'crm_leads'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['organization', 'status', '-created_at']),
            models.Index(fields=['assigned_to', 'status']),
            models.Index(fields=['campaign']),
        ]

    def __str__(self):
        return f'{self.company_name} ({self.get_status_display()})'

    @property
    def contact_full_name(self):
        return ' '.join(filter(None, [self.contact_first_name, self.contact_last_name]))

    def save(self, *args, **kwargs):
        """Auto-score on each save unless explicitly skipped via skip_score=True."""
        if not kwargs.pop('skip_score', False):
            from .services import score_lead
            try:
                self.score = score_lead(self)
            except Exception:
                pass
        super().save(*args, **kwargs)


class Opportunity(models.Model):
    """
    A deal in flight against an existing Organization (the prospect or
    a customer with new business). Moves through pipeline stages.
    """
    STAGE_CHOICES = [
        ('discovery', 'Discovery'),
        ('qualified', 'Qualified'),
        ('proposal', 'Proposal'),
        ('negotiation', 'Negotiation'),
        ('closed_won', 'Closed Won'),
        ('closed_lost', 'Closed Lost'),
    ]
    organization = models.ForeignKey(
        'core.Organization', on_delete=models.CASCADE,
        related_name='crm_opportunities_msp',
        help_text='MSP tenant that owns the opportunity.',
    )
    client_org = models.ForeignKey(
        'core.Organization', on_delete=models.CASCADE,
        related_name='crm_opportunities_client',
        help_text='Prospect or customer the deal is with.',
    )
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    stage = models.CharField(max_length=20, choices=STAGE_CHOICES, default='discovery')
    estimated_value = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    probability_pct = models.PositiveSmallIntegerField(default=20,
        help_text='Subjective close probability 0-100. Default 20%.')
    expected_close_date = models.DateField(null=True, blank=True)
    actual_close_date = models.DateField(null=True, blank=True)
    lost_reason = models.CharField(max_length=200, blank=True)

    source_lead = models.ForeignKey(
        Lead, on_delete=models.SET_NULL, related_name='opportunities',
        null=True, blank=True,
    )
    campaign = models.ForeignKey(
        Campaign, on_delete=models.SET_NULL, related_name='opportunities',
        null=True, blank=True,
    )
    assigned_to = models.ForeignKey(
        django_settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='crm_opportunities_owned',
    )
    quote = models.ForeignKey(
        'psa.Quote', on_delete=models.SET_NULL, related_name='opportunities',
        null=True, blank=True,
    )

    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        django_settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='+',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'crm_opportunities'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['organization', 'stage', '-created_at']),
            models.Index(fields=['client_org', 'stage']),
            models.Index(fields=['assigned_to', 'stage']),
        ]
        verbose_name_plural = 'Opportunities'

    def __str__(self):
        return f'{self.name} — {self.get_stage_display()}'

    @property
    def is_open(self):
        return self.stage not in ('closed_won', 'closed_lost')

    @property
    def weighted_value(self):
        from decimal import Decimal
        return (self.estimated_value or Decimal('0')) * Decimal(self.probability_pct) / Decimal('100')


class CommissionRule(models.Model):
    """
    Per-tenant rule that decides how to compute commission on a closed-won
    Opportunity. Multiple rules can match — the highest-priority active
    rule wins (deterministic via `priority` int).
    """
    organization = models.ForeignKey(
        'core.Organization', on_delete=models.CASCADE,
        related_name='crm_commission_rules',
    )
    name = models.CharField(max_length=120)
    is_active = models.BooleanField(default=True)
    priority = models.PositiveIntegerField(default=100,
        help_text='Lower number = higher priority (matches first).')

    # Match clauses — all must match (empty = match-all)
    applies_to_user = models.ForeignKey(
        django_settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='+',
        help_text='Restrict to a specific tech/sales user. Blank = applies to anyone.',
    )
    min_value = models.DecimalField(max_digits=12, decimal_places=2, default=0,
        help_text='Only opportunities with estimated_value >= this. 0 = no floor.')

    # Computation
    rate_pct = models.DecimalField(max_digits=5, decimal_places=2, default=0,
        help_text='Commission as a % of opportunity estimated_value.')
    flat_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0,
        help_text='Fixed bonus on top of % (or instead, when rate_pct=0).')

    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'crm_commission_rules'
        ordering = ['priority', 'name']
        indexes = [models.Index(fields=['organization', 'is_active', 'priority'])]

    def __str__(self):
        return f'{self.name} ({self.rate_pct}% + ${self.flat_amount})'

    def matches(self, opportunity):
        if not self.is_active:
            return False
        if self.applies_to_user_id and opportunity.assigned_to_id != self.applies_to_user_id:
            return False
        if self.min_value and (opportunity.estimated_value or 0) < self.min_value:
            return False
        return True

    def compute(self, opportunity):
        from decimal import Decimal
        ev = opportunity.estimated_value or Decimal('0')
        return (ev * Decimal(self.rate_pct) / Decimal('100')) + (self.flat_amount or Decimal('0'))


class Commission(models.Model):
    """
    Commission earned by a user on a closed-won Opportunity. Created by
    the engine when an Opportunity transitions to closed_won. One row
    per (opportunity, user) — typically just the assigned_to.

    Status pipeline: pending → approved → paid; or cancelled.
    """
    STATUS_CHOICES = [
        ('pending', 'Pending Review'),
        ('approved', 'Approved'),
        ('paid', 'Paid'),
        ('cancelled', 'Cancelled'),
    ]
    opportunity = models.ForeignKey(
        'crm.Opportunity', on_delete=models.CASCADE,
        related_name='commissions',
    )
    user = models.ForeignKey(
        django_settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='crm_commissions',
    )
    rule = models.ForeignKey(
        CommissionRule, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='commissions',
        help_text='Which rule produced this commission (audit).',
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')

    earned_at = models.DateTimeField(auto_now_add=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(
        django_settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='+',
    )
    paid_at = models.DateTimeField(null=True, blank=True)
    paid_reference = models.CharField(max_length=80, blank=True,
        help_text='Payroll reference / batch ID.')
    notes = models.TextField(blank=True)

    class Meta:
        db_table = 'crm_commissions'
        ordering = ['-earned_at']
        unique_together = [['opportunity', 'user']]
        indexes = [
            models.Index(fields=['user', 'status']),
            models.Index(fields=['opportunity']),
        ]

    def __str__(self):
        return f'{self.user.username}: ${self.amount} ({self.get_status_display()})'
