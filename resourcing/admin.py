from django.contrib import admin

from .models import UserSkill, UserCertification, WorkingHours


@admin.register(UserSkill)
class UserSkillAdmin(admin.ModelAdmin):
    list_display = ('user', 'name', 'proficiency', 'years_experience', 'updated_at')
    list_filter = ('proficiency',)
    search_fields = ('user__username', 'user__first_name', 'user__last_name', 'name')
    autocomplete_fields = ('user',)
    list_editable = ('proficiency',)
    ordering = ('user', 'name')


@admin.register(UserCertification)
class UserCertificationAdmin(admin.ModelAdmin):
    list_display = ('user', 'name', 'issuer', 'issued_at', 'expires_at', 'is_expired')
    list_filter = ('issuer',)
    search_fields = (
        'user__username', 'user__first_name', 'user__last_name',
        'name', 'issuer', 'credential_id',
    )
    autocomplete_fields = ('user',)
    date_hierarchy = 'issued_at'
    readonly_fields = ('created_at', 'updated_at')

    @admin.display(boolean=True, description='Expired?')
    def is_expired(self, obj):
        return obj.is_expired


@admin.register(WorkingHours)
class WorkingHoursAdmin(admin.ModelAdmin):
    list_display = ('user', 'weekday', 'start_time', 'end_time', 'is_active')
    list_filter = ('weekday', 'is_active')
    search_fields = ('user__username', 'user__first_name', 'user__last_name')
    autocomplete_fields = ('user',)
    list_editable = ('is_active',)
