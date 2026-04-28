from django.urls import path

from . import views

app_name = 'psa'

urlpatterns = [
    path('', views.ticket_list, name='ticket_list'),
    path('new/', views.ticket_create, name='ticket_create'),
    path('settings/', views.psa_global_settings_view, name='settings'),
    # Legacy per-client URL — redirects to the new global page.
    path('settings/client/', views.client_settings_view, name='client_settings'),
    path('t/<str:ticket_number>/context/', views.ticket_vault_context, name='ticket_vault_context'),
    path('t/<str:ticket_number>/comment/', views.ticket_post_comment, name='ticket_post_comment'),
    path('t/<str:ticket_number>/attach/', views.ticket_attach, name='ticket_attach'),
    path('t/<str:ticket_number>/action/', views.ticket_quick_action, name='ticket_quick_action'),
    path('t/<str:ticket_number>/', views.ticket_detail, name='ticket_detail'),
]
