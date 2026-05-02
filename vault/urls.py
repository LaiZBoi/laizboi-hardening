"""
Vault URL configuration
"""
from django.urls import path
from . import views

app_name = 'vault'

urlpatterns = [
    path('', views.password_list, name='password_list'),
    path('datatables/', views.password_list_datatables, name='password_list_datatables'),
    path('create/', views.password_create, name='password_create'),
    path('<int:pk>/', views.password_detail, name='password_detail'),
    path('<int:pk>/edit/', views.password_edit, name='password_edit'),
    path('<int:pk>/delete/', views.password_delete, name='password_delete'),
    path('<int:pk>/reveal/', views.password_reveal, name='password_reveal'),
    # Phase 37 (v3.17.241) — Vault approval & break-glass workflow.
    path('<int:pk>/request-reveal/', views.password_request_reveal, name='password_request_reveal'),
    path('<int:pk>/break-glass/', views.password_break_glass, name='password_break_glass'),
    path('reveal-requests/', views.vault_reveal_request_list, name='reveal_request_list'),
    path('reveal-requests/<int:pk>/decide/', views.vault_reveal_request_decide, name='reveal_request_decide'),
    path('<int:pk>/test-breach/', views.password_test_breach, name='password_test_breach'),
    path('<int:pk>/otp/', views.generate_otp_api, name='generate_otp'),
    path('<int:pk>/qrcode/', views.password_qrcode, name='password_qrcode'),

    # Utility APIs
    path('api/generate/', views.generate_password_api, name='generate_password_api'),
    path('api/strength/', views.check_password_strength_api, name='check_strength_api'),

    # Personal Vault (encrypted notes)
    path('personal/', views.personal_vault_list, name='personal_vault_list'),
    path('personal/create/', views.personal_vault_create, name='personal_vault_create'),
    path('personal/<int:pk>/', views.personal_vault_detail, name='personal_vault_detail'),
    path('personal/<int:pk>/edit/', views.personal_vault_edit, name='personal_vault_edit'),
    path('personal/<int:pk>/delete/', views.personal_vault_delete, name='personal_vault_delete'),

    # Access rules — GeoIP / IP / time-of-day gates (v3.17.163)
    path('access-rules/', views.access_rule_list, name='access_rule_list'),
    path('access-rules/new/', views.access_rule_form, name='access_rule_create'),
    path('access-rules/<int:pk>/edit/', views.access_rule_form, name='access_rule_edit'),
    path('access-rules/<int:pk>/delete/', views.access_rule_delete, name='access_rule_delete'),
]
