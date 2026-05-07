"""
Admin registration for field_ops models.
"""
from django.contrib import admin

from .models import (
    AutoTimePreference,
    ClientSiteGeofence,
    LocationRetentionPolicy,
    MobileDevice,
    PendingAutoTime,
    TechnicianLocation,
    TimeclockEntry,
)


@admin.register(TechnicianLocation)
class TechnicianLocationAdmin(admin.ModelAdmin):
    list_display = ('tech', 'lat', 'lon', 'accuracy', 'timestamp', 'source', 'retention_until')
    list_filter = ('source',)
    search_fields = ('tech__username', 'tech__email')
    date_hierarchy = 'timestamp'
    readonly_fields = ('tech', 'lat', 'lon', 'accuracy', 'timestamp', 'source', 'retention_until')


@admin.register(ClientSiteGeofence)
class ClientSiteGeofenceAdmin(admin.ModelAdmin):
    list_display = ('name', 'organization', 'kind', 'radius_meters', 'active')
    list_filter = ('kind', 'active')
    search_fields = ('name', 'organization__name')


@admin.register(TimeclockEntry)
class TimeclockEntryAdmin(admin.ModelAdmin):
    list_display = ('tech', 'organization', 'ticket', 'clocked_in_at', 'clocked_out_at', 'source')
    list_filter = ('source', 'organization')
    search_fields = ('tech__username', 'tech__email', 'notes')
    date_hierarchy = 'clocked_in_at'
    raw_id_fields = ('tech', 'organization', 'location', 'ticket', 'project', 'derived_time_entry')


@admin.register(AutoTimePreference)
class AutoTimePreferenceAdmin(admin.ModelAdmin):
    list_display = ('user', 'mode', 'updated_at')
    list_filter = ('mode',)
    search_fields = ('user__username',)


@admin.register(PendingAutoTime)
class PendingAutoTimeAdmin(admin.ModelAdmin):
    list_display = ('user', 'organization', 'geofence', 'entered_at', 'confirmed_at', 'cancelled_at')
    list_filter = ('organization',)
    search_fields = ('user__username',)
    raw_id_fields = ('user', 'organization', 'geofence', 'suggested_ticket')


@admin.register(LocationRetentionPolicy)
class LocationRetentionPolicyAdmin(admin.ModelAdmin):
    list_display = ('organization', 'retention_days', 'apply_to_geofence_only', 'updated_at')
    list_filter = ('apply_to_geofence_only',)
    search_fields = ('organization__name',)


@admin.register(MobileDevice)
class MobileDeviceAdmin(admin.ModelAdmin):
    list_display = ('user', 'platform', 'name', 'last_seen_at', 'revoked')
    list_filter = ('platform', 'revoked')
    search_fields = ('user__username', 'name', 'device_id')
    raw_id_fields = ('user', 'token')
    readonly_fields = ('device_id', 'created_at')
