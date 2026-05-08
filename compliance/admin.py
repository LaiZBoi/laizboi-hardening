from django.contrib import admin

from .models import (
    ComplianceCategory, ComplianceCheckItem, ComplianceFramework,
    OrganizationCompliance, OrganizationComplianceItem,
    RecertificationReminder,
)


@admin.register(ComplianceFramework)
class ComplianceFrameworkAdmin(admin.ModelAdmin):
    list_display = ('name', 'version', 'slug', 'active',
                    'recertification_default_days')
    list_filter = ('active',)
    search_fields = ('name', 'slug')
    prepopulated_fields = {'slug': ('name',)}


@admin.register(ComplianceCategory)
class ComplianceCategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'framework', 'order', 'slug')
    list_filter = ('framework',)
    search_fields = ('name', 'slug')


@admin.register(ComplianceCheckItem)
class ComplianceCheckItemAdmin(admin.ModelAdmin):
    list_display = ('name', 'category', 'order', 'slug')
    list_filter = ('category__framework', 'category')
    search_fields = ('name', 'description', 'slug')


class OrganizationComplianceItemInline(admin.TabularInline):
    model = OrganizationComplianceItem
    extra = 0
    readonly_fields = ('item', 'last_reviewed_at', 'last_reviewed_by')
    fields = ('item', 'status', 'evidence_link', 'notes',
              'last_reviewed_at', 'last_reviewed_by')


@admin.register(OrganizationCompliance)
class OrganizationComplianceAdmin(admin.ModelAdmin):
    list_display = ('organization', 'framework', 'enrolled_at',
                    'last_recertified_at', 'recertification_emails_enabled')
    list_filter = ('framework', 'recertification_emails_enabled')
    search_fields = ('organization__name', 'framework__name')
    inlines = [OrganizationComplianceItemInline]


@admin.register(OrganizationComplianceItem)
class OrganizationComplianceItemAdmin(admin.ModelAdmin):
    list_display = ('org_compliance', 'item', 'status', 'last_reviewed_at')
    list_filter = ('status', 'org_compliance__framework')
    search_fields = ('item__name', 'notes')


@admin.register(RecertificationReminder)
class RecertificationReminderAdmin(admin.ModelAdmin):
    list_display = ('org_compliance', 'recipient_email', 'sent_at')
    list_filter = ('sent_at',)
    search_fields = ('recipient_email',)
    readonly_fields = ('sent_at',)
