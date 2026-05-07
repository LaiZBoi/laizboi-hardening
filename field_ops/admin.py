"""
Admin registration for field_ops models.
"""
from django.contrib import admin

from .models import (
    ClientSiteGeofence,
    MobileDevice,
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


@admin.register(MobileDevice)
class MobileDeviceAdmin(admin.ModelAdmin):
    list_display = ('user', 'platform', 'name', 'last_seen_at', 'revoked')
    list_filter = ('platform', 'revoked')
    search_fields = ('user__username', 'name', 'device_id')
    raw_id_fields = ('user', 'token')
    readonly_fields = ('device_id', 'created_at')
