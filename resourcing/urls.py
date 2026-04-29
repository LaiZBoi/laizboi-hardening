from django.urls import path

from . import views

app_name = 'resourcing'

urlpatterns = [
    # My profile (current user manages own skills/certs/hours)
    path('me/', views.my_resourcing, name='my_resourcing'),

    # Skills CRUD (user adds/edits their own; superuser can edit anyone's via ?user=<id>)
    path('skills/add/', views.skill_add, name='skill_add'),
    path('skills/<int:pk>/edit/', views.skill_edit, name='skill_edit'),
    path('skills/<int:pk>/delete/', views.skill_delete, name='skill_delete'),

    # Certifications CRUD
    path('certifications/add/', views.cert_add, name='cert_add'),
    path('certifications/<int:pk>/edit/', views.cert_edit, name='cert_edit'),
    path('certifications/<int:pk>/delete/', views.cert_delete, name='cert_delete'),

    # Working hours CRUD
    path('hours/add/', views.hours_add, name='hours_add'),
    path('hours/<int:pk>/edit/', views.hours_edit, name='hours_edit'),
    path('hours/<int:pk>/delete/', views.hours_delete, name='hours_delete'),

    # Staff: roster / coverage view
    path('roster/', views.tech_roster, name='tech_roster'),

    # Holidays (admin)
    path('holidays/', views.holiday_list, name='holiday_list'),
    path('holidays/add/', views.holiday_add, name='holiday_add'),
    path('holidays/<int:pk>/edit/', views.holiday_edit, name='holiday_edit'),
    path('holidays/<int:pk>/delete/', views.holiday_delete, name='holiday_delete'),

    # Leave requests
    path('leave/', views.my_leave, name='my_leave'),
    path('leave/add/', views.leave_request_add, name='leave_request_add'),
    path('leave/<int:pk>/cancel/', views.leave_request_cancel, name='leave_request_cancel'),
    path('leave/approvals/', views.leave_approvals, name='leave_approvals'),
    path('leave/<int:pk>/decide/', views.leave_decide, name='leave_decide'),

    # Billable target
    path('billable-target/', views.my_billable_target, name='my_billable_target'),
    path('billable-target/<int:user_id>/edit/', views.billable_target_edit, name='billable_target_edit'),
]
