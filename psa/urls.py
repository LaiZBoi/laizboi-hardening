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
    path('t/<str:ticket_number>/merge/', views.ticket_merge, name='ticket_merge'),
    path('t/<str:ticket_number>/kb-link/', views.ticket_kb_link, name='ticket_kb_link'),
    path('t/<str:ticket_number>/kb-unlink/<int:link_pk>/', views.ticket_kb_unlink, name='ticket_kb_unlink'),
    path('t/<str:ticket_number>/workflow/launch/', views.ticket_launch_workflow, name='ticket_launch_workflow'),
    path('t/<str:ticket_number>/', views.ticket_detail, name='ticket_detail'),
    # Projects (Workstream 3)
    path('projects/', views.project_list, name='project_list'),
    path('projects/new/', views.project_form, name='project_create'),
    path('projects/<int:pk>/', views.project_detail, name='project_detail'),
    path('projects/<int:pk>/edit/', views.project_form, name='project_edit'),
    path('projects/<int:pk>/task/add/', views.project_task_add, name='project_task_add'),
    path('project-task/<int:task_pk>/update/', views.project_task_update, name='project_task_update'),
    path('project-task/<int:task_pk>/delete/', views.project_task_delete, name='project_task_delete'),
    # Recurring tickets (preventive maintenance)
    path('recurring/', views.recurring_list, name='recurring_list'),
    path('recurring/new/', views.recurring_form, name='recurring_create'),
    path('recurring/<int:pk>/edit/', views.recurring_form, name='recurring_edit'),
    # Knowledge Base browser
    path('kb/', views.kb_browse, name='kb_browse'),
    # Approvals
    path('approvals/', views.approval_list, name='approval_list'),
    path('approvals/<int:pk>/decide/', views.approval_decide, name='approval_decide'),
    # Contracts (Workstream 5)
    path('contracts/', views.contract_list, name='contract_list'),
    path('contracts/new/', views.contract_form, name='contract_create'),
    path('contracts/<int:pk>/', views.contract_detail, name='contract_detail'),
    path('contracts/<int:pk>/edit/', views.contract_form, name='contract_edit'),
    # Email ingestion
    path('email-configs/', views.email_config_list, name='email_config_list'),
    path('email-configs/new/', views.email_config_form, name='email_config_create'),
    path('email-configs/<int:pk>/edit/', views.email_config_form, name='email_config_edit'),
    # Quotes / Estimates
    path('quotes/', views.quote_list, name='quote_list'),
    path('quotes/new/', views.quote_form, name='quote_create'),
    path('quotes/<int:pk>/', views.quote_detail, name='quote_detail'),
    path('quotes/<int:pk>/edit/', views.quote_form, name='quote_edit'),
    path('quotes/<int:pk>/accept/', views.quote_accept, name='quote_accept'),
    # Expenses (per-ticket)
    path('t/<str:ticket_number>/expense/add/', views.ticket_expense_add, name='ticket_expense_add'),
    # Workflow rules (Workstream 9)
    path('rules/', views.workflow_rule_list, name='workflow_rule_list'),
    path('rules/new/', views.workflow_rule_form, name='workflow_rule_create'),
    path('rules/<int:pk>/edit/', views.workflow_rule_form, name='workflow_rule_edit'),
    path('rules/<int:pk>/delete/', views.workflow_rule_delete, name='workflow_rule_delete'),
    # Dispatch board
    path('dispatch/', views.dispatch_board, name='dispatch_board'),
    path('dispatch/assign/', views.dispatch_assign, name='dispatch_assign'),
    # Invoices + payments (Workstream 5 billing)
    path('invoices/', views.invoice_list, name='invoice_list'),
    path('invoices/new/', views.invoice_form, name='invoice_create'),
    path('invoices/<int:pk>/', views.invoice_detail, name='invoice_detail'),
    path('invoices/<int:pk>/edit/', views.invoice_form, name='invoice_edit'),
    path('invoices/<int:pk>/payment/', views.payment_add, name='payment_add'),
    path('invoices/<int:pk>/push/', views.invoice_push_to_accounting, name='invoice_push_to_accounting'),
    path('invoices/<int:pk>/pdf/', views.invoice_pdf, name='invoice_pdf'),
    path('invoices/<int:pk>/email/', views.invoice_email, name='invoice_email'),
    path('invoices/from-ticket/<str:ticket_number>/', views.invoice_from_ticket, name='invoice_from_ticket'),
    # Quote PDF + email
    path('quotes/<int:pk>/pdf/', views.quote_pdf, name='quote_pdf'),
    path('quotes/<int:pk>/email/', views.quote_email, name='quote_email'),
    # Client account + charges + aging
    path('clients/<int:org_id>/account/', views.client_account, name='client_account'),
    path('clients/<int:org_id>/charge/', views.charge_add, name='charge_add'),
    path('clients/<int:org_id>/charge/invoice/', views.charge_invoice, name='charge_invoice'),
    path('clients/<int:org_id>/invite-portal/', views.portal_invite, name='portal_invite'),
    path('aging/', views.aging_report, name='aging_report'),
]
