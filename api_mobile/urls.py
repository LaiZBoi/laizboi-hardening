"""
Mobile API URL config — `/api/mobile/v1/`.
"""
from django.urls import path

from . import views_auth

app_name = 'api_mobile'

urlpatterns = [
    # Auth (v3.17.346)
    path('auth/login/', views_auth.login_view, name='login'),
    path('auth/mfa/', views_auth.mfa_view, name='mfa'),
    path('auth/logout/', views_auth.logout_view, name='logout'),
    path('auth/me/', views_auth.me_view, name='me'),
    path('auth/refresh/', views_auth.refresh_view, name='refresh'),
]
