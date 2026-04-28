from django.urls import path

from . import views

app_name = 'psa'

urlpatterns = [
    path('', views.ticket_list, name='ticket_list'),
    path('new/', views.ticket_create, name='ticket_create'),
    path('settings/', views.psa_global_settings_view, name='settings'),
    # Legacy per-client URL — redirects to the new global page.
    path('settings/client/', views.client_settings_view, name='client_settings'),
    # Canned replies (Phase 2b)
    path('canned/', views.canned_reply_list, name='canned_reply_list'),
    path('canned/new/', views.canned_reply_create, name='canned_reply_create'),
    path('canned/<int:pk>/edit/', views.canned_reply_edit, name='canned_reply_edit'),
    # Service catalog (Phase 2c)
    path('catalog/', views.service_catalog, name='service_catalog'),
    path('catalog/new/', views.service_catalog_form, name='service_catalog_create'),
    path('catalog/<int:pk>/edit/', views.service_catalog_form, name='service_catalog_edit'),
    # Time entries
    path('t/<str:ticket_number>/timer/start/', views.timer_start, name='timer_start'),
    path('t/<str:ticket_number>/timer/stop/', views.timer_stop, name='timer_stop'),
    path('t/<str:ticket_number>/time/manual/', views.time_entry_manual, name='time_entry_manual'),
    # Per-ticket
    path('t/<str:ticket_number>/context/', views.ticket_vault_context, name='ticket_vault_context'),
    path('t/<str:ticket_number>/comment/', views.ticket_post_comment, name='ticket_post_comment'),
    path('t/<str:ticket_number>/attach/', views.ticket_attach, name='ticket_attach'),
    path('t/<str:ticket_number>/action/', views.ticket_quick_action, name='ticket_quick_action'),
    path('t/<str:ticket_number>/watch/', views.ticket_watch_toggle, name='ticket_watch_toggle'),
    path('t/<str:ticket_number>/', views.ticket_detail, name='ticket_detail'),
]
