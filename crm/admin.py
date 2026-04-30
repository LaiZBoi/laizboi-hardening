from django.contrib import admin

from .models import Campaign, Commission, CommissionRule, Lead, Opportunity


@admin.register(Campaign)
class CampaignAdmin(admin.ModelAdmin):
    list_display = ('name', 'organization', 'channel', 'budget', 'is_active', 'created_at')
    list_filter = ('is_active', 'channel', 'organization')
    search_fields = ('name', 'description')
    date_hierarchy = 'created_at'
    readonly_fields = ('created_at', 'updated_at')


@admin.register(Lead)
class LeadAdmin(admin.ModelAdmin):
    list_display = (
        'company_name', 'contact_email', 'status',
        'estimated_value', 'campaign', 'assigned_to', 'created_at',
    )
    list_filter = ('status', 'organization', 'campaign')
    search_fields = (
        'company_name', 'contact_first_name', 'contact_last_name',
        'contact_email', 'contact_phone', 'industry',
    )
    autocomplete_fields = ('campaign', 'assigned_to', 'created_by')
    readonly_fields = ('created_at', 'updated_at')


@admin.register(Opportunity)
class OpportunityAdmin(admin.ModelAdmin):
    list_display = (
        'name', 'client_org', 'stage', 'estimated_value',
        'probability_pct', 'expected_close_date', 'assigned_to',
    )
    list_filter = ('stage', 'organization', 'client_org')
    search_fields = ('name', 'description')
    autocomplete_fields = (
        'campaign', 'assigned_to', 'created_by',
    )
    raw_id_fields = ('client_org', 'organization', 'source_lead', 'quote')
    readonly_fields = ('created_at', 'updated_at')


@admin.register(CommissionRule)
class CommissionRuleAdmin(admin.ModelAdmin):
    list_display = (
        'name', 'organization', 'is_active', 'priority',
        'applies_to_user', 'min_value', 'rate_pct', 'flat_amount',
    )
    list_filter = ('is_active', 'organization')
    search_fields = ('name', 'notes')
    autocomplete_fields = ('applies_to_user',)
    readonly_fields = ('created_at', 'updated_at')


@admin.register(Commission)
class CommissionAdmin(admin.ModelAdmin):
    list_display = (
        'opportunity', 'user', 'amount', 'status',
        'rule', 'earned_at', 'paid_at',
    )
    list_filter = ('status',)
    search_fields = ('user__username', 'paid_reference')
    autocomplete_fields = ('user', 'rule', 'approved_by')
    raw_id_fields = ('opportunity',)
    readonly_fields = ('earned_at',)
