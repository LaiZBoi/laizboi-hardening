"""
URL configuration for Service Vehicles
"""
from django.urls import path
from . import views

app_name = 'vehicles'

urlpatterns = [
    # Dashboard
    path('', views.vehicles_dashboard, name='vehicles_dashboard'),

    # Vehicle CRUD
    path('vehicles/', views.vehicle_list, name='vehicle_list'),
    path('vehicles/create/', views.vehicle_create, name='vehicle_create'),
    path('vehicles/<int:pk>/', views.vehicle_detail, name='vehicle_detail'),
    path('vehicles/<int:pk>/edit/', views.vehicle_edit, name='vehicle_edit'),
    path('vehicles/<int:pk>/delete/', views.vehicle_delete, name='vehicle_delete'),

    # Inventory sections
    path('inventory/global/', views.inventory_global_list, name='inventory_global_list'),
    path('inventory/by-vehicle/', views.inventory_by_vehicle, name='inventory_by_vehicle'),
    path('inventory/shop/', views.shop_inventory_list, name='shop_inventory_list'),
    path('inventory/shop/create/', views.shop_inventory_create, name='shop_inventory_create'),
    path('inventory/shop/<int:pk>/edit/', views.shop_inventory_edit, name='shop_inventory_edit'),
    path('inventory/shop/<int:pk>/delete/', views.shop_inventory_delete, name='shop_inventory_delete'),

    # Vehicle inventory items
    path('vehicles/<int:vehicle_id>/inventory/create/', views.inventory_item_create, name='inventory_item_create'),
    path('inventory/<int:pk>/edit/', views.inventory_item_edit, name='inventory_item_edit'),
    path('inventory/<int:pk>/delete/', views.inventory_item_delete, name='inventory_item_delete'),

    # Take Inventory (QR Scanning Mode)
    path('vehicles/<int:vehicle_id>/take-inventory/', views.take_inventory, name='take_inventory'),
    path('inventory/scan-update/<str:qr_code>/', views.inventory_scan_update, name='inventory_scan_update'),
    path('vehicles/<int:vehicle_id>/end-inventory/', views.end_inventory_session, name='end_inventory_session'),

    # Damage Reports
    path('vehicles/<int:vehicle_id>/damage/create/', views.damage_report_create, name='damage_report_create'),
    path('damage/<int:pk>/edit/', views.damage_report_edit, name='damage_report_edit'),
    path('damage/<int:pk>/delete/', views.damage_report_delete, name='damage_report_delete'),

    # Maintenance
    path('vehicles/<int:vehicle_id>/maintenance/create/', views.maintenance_record_create, name='maintenance_record_create'),
    path('maintenance/<int:pk>/edit/', views.maintenance_record_edit, name='maintenance_record_edit'),
    path('maintenance/<int:pk>/delete/', views.maintenance_record_delete, name='maintenance_record_delete'),

    # Fuel Logs
    path('vehicles/<int:vehicle_id>/fuel/create/', views.fuel_log_create, name='fuel_log_create'),
    path('fuel/<int:pk>/edit/', views.fuel_log_edit, name='fuel_log_edit'),
    path('fuel/<int:pk>/delete/', views.fuel_log_delete, name='fuel_log_delete'),

    # Assignments
    path('vehicles/<int:vehicle_id>/assign/', views.assignment_create, name='assignment_create'),
    path('assignments/<int:pk>/end/', views.assignment_end, name='assignment_end'),

    # QR Codes & Mobile Scanner
    path('inventory/<int:pk>/qr-image/', views.inventory_qr_image, name='inventory_qr_image'),
    path('vehicles/<int:vehicle_id>/inventory/print-qr/', views.inventory_print_qr_codes, name='inventory_print_qr_codes'),
    path('scan/<str:qr_code>/', views.inventory_scan, name='inventory_scan'),
    path('inventory/<int:pk>/quick-update/', views.inventory_quick_update, name='inventory_quick_update'),

    # Service Alerts
    path('service-alerts/', views.service_alert_list, name='service_alert_list'),
    path('service-alerts/<int:pk>/acknowledge/', views.service_alert_acknowledge, name='service_alert_acknowledge'),

    # Service Schedules
    path('vehicles/<int:vehicle_id>/schedules/create/', views.service_schedule_create, name='service_schedule_create'),
    path('schedules/<int:pk>/edit/', views.service_schedule_edit, name='service_schedule_edit'),
    path('schedules/<int:pk>/delete/', views.service_schedule_delete, name='service_schedule_delete'),

    # Service Providers
    path('vehicles/<int:vehicle_id>/providers/create/', views.service_provider_create, name='service_provider_create'),
    path('providers/<int:pk>/edit/', views.service_provider_edit, name='service_provider_edit'),
    path('providers/<int:pk>/delete/', views.service_provider_delete, name='service_provider_delete'),
]
