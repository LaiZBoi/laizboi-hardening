"""Customer portal URLs — kept off the main staff /psa/ path for clarity."""
from django.urls import path

from . import views

app_name = 'portal'

urlpatterns = [
    path('', views.ticket_list, name='ticket_list'),
    # v3.17.232 — Phase 12 portal announcements (per-session dismissal).
    path('announcement/<int:pk>/dismiss/',
         views.announcement_dismiss, name='announcement_dismiss'),
    path('new/', views.ticket_create, name='ticket_create'),
    path('t/<str:ticket_number>/', views.ticket_detail, name='ticket_detail'),
    path('t/<str:ticket_number>/reply/', views.post_reply, name='post_reply'),
    # Customer-facing quote signing — public, opaque token, no login required.
    path('quote/<str:token>/sign/', views.quote_sign, name='quote_sign'),
    # Knowledge base for portal users
    path('kb/', views.kb_list, name='kb_list'),
    path('kb/<slug:slug>/', views.kb_detail, name='kb_detail'),
    # Vault access for portal users
    path('vault/', views.vault_list, name='vault_list'),
    path('vault/<int:pk>/reveal/', views.vault_reveal, name='vault_reveal'),
    # Org admin — manage which users in this org see which org-admin-managed items
    path('vault/admin/', views.org_admin_vault, name='org_admin_vault'),
    path('vault/admin/<int:pk>/', views.org_admin_vault_item, name='org_admin_vault_item'),
]
