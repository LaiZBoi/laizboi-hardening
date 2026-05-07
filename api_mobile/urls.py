"""
Mobile API URL config — `/api/mobile/v1/`.
"""
from django.urls import path

from . import views_auth, views_assets, views_dashboard

app_name = 'api_mobile'

urlpatterns = [
    # Auth (v3.17.346)
    path('auth/login/', views_auth.login_view, name='login'),
    path('auth/mfa/', views_auth.mfa_view, name='mfa'),
    path('auth/logout/', views_auth.logout_view, name='logout'),
    path('auth/me/', views_auth.me_view, name='me'),
    path('auth/refresh/', views_auth.refresh_view, name='refresh'),

    # Dashboard + organizations (v3.17.347)
    path('dashboard/', views_dashboard.dashboard_view, name='dashboard'),
    path('organizations/', views_dashboard.organization_list_view, name='org_list'),
    path('organizations/<int:pk>/', views_dashboard.organization_detail_view, name='org_detail'),

    # Assets (v3.17.348)
    path('assets/', views_assets.asset_list_view, name='asset_list'),
    path('assets/<int:pk>/', views_assets.asset_detail_view, name='asset_detail'),
]
