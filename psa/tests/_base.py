"""
Shared test helpers for the PSA test suite.

These get imported by every test module in this package. The legacy
single-file `psa/tests.py` (5,465 lines) was split into this package in
v3.17.192 — these helpers used to live at the top of that file.
"""
from datetime import timedelta

from django.conf import settings as django_settings
from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.utils import timezone


# Tests bypass the project-wide 2FA enforcement middleware so we can exercise
# the views directly. Same list every PSA test module uses.
TEST_MIDDLEWARE = [
    m for m in django_settings.MIDDLEWARE
    if 'Enforce2FAMiddleware' not in m and 'AxesMiddleware' not in m
]


def _setup_seed():
    """Seed PSA defaults — queues, statuses, priorities, types."""
    call_command('psa_seed_defaults', verbosity=0)


def _enable_psa_global():
    """Flip the system-wide PSA feature flag on."""
    from core.models import SystemSetting
    s = SystemSetting.get_settings()
    s.psa_enabled = True
    s.save()


def _enable_psa_for(org):
    """Flip the per-client PSA feature flag on for a given org."""
    from psa.models import ClientPSASettings
    cps, _ = ClientPSASettings.objects.get_or_create(organization=org)
    cps.enabled = True
    cps.save()
    return cps
