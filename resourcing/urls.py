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
]
