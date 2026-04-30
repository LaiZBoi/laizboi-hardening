"""
URL Configuration for Reports and Analytics
"""
from django.urls import path
from . import views

app_name = 'reports'

urlpatterns = [
    # Dashboard and Analytics
    path('', views.reports_home, name='home'),
    path('dashboards/', views.dashboard_list, name='dashboard_list'),
    path('dashboards/<int:pk>/', views.dashboard_detail, name='dashboard_detail'),
    path('dashboards/create/', views.dashboard_create, name='dashboard_create'),
    path('dashboards/<int:pk>/edit/', views.dashboard_edit, name='dashboard_edit'),
    path('dashboards/<int:pk>/delete/', views.dashboard_delete, name='dashboard_delete'),

    # Dashboard widgets (v3.17.142)
    path('dashboards/<int:dashboard_pk>/widgets/add/',
         views.dashboard_widget_add, name='dashboard_widget_add'),
    path('widgets/<int:pk>/edit/',
         views.dashboard_widget_edit, name='dashboard_widget_edit'),
    path('widgets/<int:pk>/delete/',
         views.dashboard_widget_delete, name='dashboard_widget_delete'),

    # Report Templates
    path('templates/', views.template_list, name='template_list'),
    path('templates/<int:pk>/', views.template_detail, name='template_detail'),
    path('templates/create/', views.template_create, name='template_create'),
    path('templates/<int:pk>/edit/', views.template_edit, name='template_edit'),
    path('templates/<int:pk>/delete/', views.template_delete, name='template_delete'),
    path('templates/<int:pk>/generate/', views.generate_report, name='generate_report'),

    # Generated Reports
    path('generated/', views.generated_list, name='generated_list'),
    path('generated/<int:pk>/', views.generated_detail, name='generated_detail'),
    path('generated/<int:pk>/download/', views.generated_download, name='generated_download'),
    path('generated/<int:pk>/delete/', views.generated_delete, name='generated_delete'),

    # Scheduled Reports
    path('scheduled/', views.scheduled_list, name='scheduled_list'),
    path('scheduled/create/', views.scheduled_create, name='scheduled_create'),
    path('scheduled/<int:pk>/edit/', views.scheduled_edit, name='scheduled_edit'),
    path('scheduled/<int:pk>/delete/', views.scheduled_delete, name='scheduled_delete'),
    path('scheduled/<int:pk>/toggle/', views.scheduled_toggle, name='scheduled_toggle'),

    # Analytics
    path('analytics/', views.analytics_overview, name='analytics_overview'),
    path('analytics/events/', views.analytics_events, name='analytics_events'),

    # PSA reports (Workstream 6)
    path('psa/', views.psa_reports_list, name='psa_reports_list'),
    # Phase 3.1 — canonical profitability report. Must come BEFORE the
    # catch-all `<str:report_type>/` so the literal slug routes here.
    path('psa/profitability-by-client/', views.psa_profitability_by_client,
         name='psa_profitability_by_client'),
    # Phase 3.2 — Profitability pivots (tech / contract / project)
    path('psa/profitability-by-tech/', views.psa_profitability_by_tech,
         name='psa_profitability_by_tech'),
    path('psa/profitability-by-contract/', views.psa_profitability_by_contract,
         name='psa_profitability_by_contract'),
    path('psa/profitability-by-project/', views.psa_profitability_by_project,
         name='psa_profitability_by_project'),
    # Phase 3.3 — Effective hourly rate + Revenue leakage
    path('psa/effective-hourly-rate/', views.psa_effective_hourly_rate,
         name='psa_effective_hourly_rate'),
    path('psa/revenue-leakage/', views.psa_revenue_leakage,
         name='psa_revenue_leakage'),
    # Phase 3.4 — SLA trends + Margin analytics by service line
    path('psa/sla-trends/', views.psa_sla_trends, name='psa_sla_trends'),
    path('psa/margin-analytics/', views.psa_margin_analytics,
         name='psa_margin_analytics'),
    # Phase 3.6 wave A — Wallboard + Executive scorecard
    path('wallboard/', views.wallboard, name='wallboard'),
    path('wallboard/data/', views.wallboard_data, name='wallboard_data'),
    path('exec-scorecard/', views.exec_scorecard, name='exec_scorecard'),
    # Phase 3.6 wave B — Client-health score (v3.17.147)
    path('psa/client-health/', views.psa_client_health, name='psa_client_health'),
    # Phase 5.2 — CRM sales funnel
    path('crm/sales-funnel/', views.crm_sales_funnel, name='crm_sales_funnel'),
    path('psa/<str:report_type>/', views.psa_report_run, name='psa_report_run'),
]
