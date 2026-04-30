from django.urls import path
from . import views

app_name = 'crm'

urlpatterns = [
    path('', views.crm_home, name='home'),

    # Leads
    path('leads/', views.lead_list, name='lead_list'),
    path('leads/new/', views.lead_form, name='lead_create'),
    path('leads/<int:pk>/', views.lead_detail, name='lead_detail'),
    path('leads/<int:pk>/edit/', views.lead_form, name='lead_edit'),
    path('leads/<int:pk>/convert/', views.lead_convert, name='lead_convert'),
    path('leads/<int:pk>/disqualify/', views.lead_disqualify, name='lead_disqualify'),

    # Pipeline (Kanban)
    path('pipeline/', views.pipeline_kanban, name='pipeline'),
    path('opportunities/', views.opportunity_list, name='opportunity_list'),
    path('opportunities/new/', views.opportunity_form, name='opportunity_create'),
    path('opportunities/<int:pk>/', views.opportunity_detail, name='opportunity_detail'),
    path('opportunities/<int:pk>/edit/', views.opportunity_form, name='opportunity_edit'),
    path('opportunities/<int:pk>/stage/', views.opportunity_set_stage, name='opportunity_set_stage'),
    path('opportunities/<int:pk>/to-quote/', views.opportunity_to_quote, name='opportunity_to_quote'),

    # Campaigns
    path('campaigns/', views.campaign_list, name='campaign_list'),
    path('campaigns/new/', views.campaign_form, name='campaign_create'),
    path('campaigns/<int:pk>/', views.campaign_detail, name='campaign_detail'),
    path('campaigns/<int:pk>/edit/', views.campaign_form, name='campaign_edit'),
]
