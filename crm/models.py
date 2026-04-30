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
