from django.urls import path

from . import views

app_name = 'psa'

urlpatterns = [
    path('', views.ticket_list, name='ticket_list'),
    path('new/', views.ticket_create, name='ticket_create'),
    path('settings/client/', views.client_settings_view, name='client_settings'),
    path('t/<str:ticket_number>/context/', views.ticket_vault_context, name='ticket_vault_context'),
    path('t/<str:ticket_number>/', views.ticket_detail, name='ticket_detail'),
]
