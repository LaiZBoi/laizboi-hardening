"""
field_ops URLs — Phase 8 Timeclock + privacy.

Mounted at `/field-ops/` in `config/urls.py`. The timeclock dashboard
also gets a top-level `/timeclock/` mount for discoverability.
"""
from django.urls import path

from . import views

app_name = 'field_ops'

urlpatterns = [
    # Sub-phase 8.3 (v3.17.413)
    path('timeclock/', views.timeclock_dashboard, name='timeclock_dashboard'),
    path(
        'timeclock/payroll-export.csv',
        views.timeclock_payroll_export, name='timeclock_payroll_export',
    ),
]
