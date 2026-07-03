"""Tests for security hardening gates (auto-update, beta upstream, deployment audit)."""
from __future__ import annotations

from io import StringIO
from unittest import mock

from django.conf import settings as django_settings
from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.management import call_command
from django.test import Client, RequestFactory, TestCase, override_settings

from core.management.commands.run_scheduler import Command as SchedulerCommand
from core.updater import UpdateService
from core import views


TEST_MIDDLEWARE = [
    m for m in django_settings.MIDDLEWARE
    if 'Enforce2FAMiddleware' not in m and 'AxesMiddleware' not in m
]


@override_settings(MIDDLEWARE=TEST_MIDDLEWARE, SECURE_SSL_REDIRECT=False)
class HardeningGateTests(TestCase):
    """Verify opt-in gates for updates, polling, and beta forwarding."""

    @classmethod
    def setUpTestData(cls):
        cls.superuser = User.objects.create_user(
            'su', 'su@example.com', 'pw', is_superuser=True, is_staff=True,
        )

    def _login(self, client):
        client.force_login(self.superuser)
        session = client.session
        session['2fa_prompted'] = True
        session.save()

    @override_settings(AUTO_UPDATE_ENABLED=False)
    def test_apply_update_returns_403_when_disabled(self):
        client = Client(enforce_csrf_checks=False)
        self._login(client)
        response = client.post('/core/settings/updates/apply/')
        self.assertEqual(response.status_code, 403)
        data = response.json()
        self.assertIn('disabled', data['message'].lower())

    @override_settings(AUTO_UPDATE_ENABLED=False)
    def test_update_status_api_does_not_poll_github(self):
        cache.delete('system_update_check')
        client = Client()
        self._login(client)
        with mock.patch('core.views.UpdateService') as mock_svc:
            response = client.get('/core/api/update-status/')
        self.assertEqual(response.status_code, 200)
        mock_svc.assert_not_called()
        data = response.json()
        self.assertTrue(data.get('manual_check_required'))

    @override_settings(AUTO_UPDATE_ENABLED=False)
    def test_scheduler_run_update_check_skips(self):
        out = StringIO()
        cmd = SchedulerCommand()
        cmd.stdout = out
        cmd.run_update_check()
        self.assertIn('Skipping automatic update check', out.getvalue())

    @override_settings(BETA_UPSTREAM_URL='')
    def test_beta_signup_does_not_forward_when_upstream_blank(self):
        request = RequestFactory().post('/core/beta-test/', {
            'name': 'Test User',
            'google_account_email': 'test@example.com',
        })
        request.META['REMOTE_ADDR'] = '127.0.0.1'
        with mock.patch('core.views.requests.post') as post_mock, \
             mock.patch('django.core.mail.send_mail'):
            views.beta_test_signup(request)
        post_mock.assert_not_called()

    @override_settings(AUTO_UPDATE_ENABLED=False)
    def test_check_updates_apply_refuses_when_disabled(self):
        fake_info = {
            'update_available': True,
            'latest_version': '9.9.9',
            'current_version': '1.0.0',
        }
        out = StringIO()
        with mock.patch.object(UpdateService, 'check_for_updates', return_value=fake_info), \
             mock.patch.object(UpdateService, 'perform_update') as perform_mock:
            call_command('check_updates', '--apply', '--force', stdout=out)
        perform_mock.assert_not_called()
        self.assertIn('disabled', out.getvalue().lower())

    @override_settings(AUTO_UPDATE_ENABLED=True)
    def test_perform_update_runs_when_explicitly_enabled(self):
        updater = UpdateService()
        proc = mock.MagicMock()
        proc.stdout.readline.side_effect = ['done\n', '']
        proc.wait.return_value = None
        proc.returncode = 0
        fake_response = mock.MagicMock()
        fake_response.text = '#!/bin/bash\necho ok\n'
        fake_response.raise_for_status.return_value = None

        with mock.patch.object(updater, '_is_systemd_service', return_value=False), \
             mock.patch('core.updater.requests.get', return_value=fake_response), \
             mock.patch('core.updater.subprocess.Popen', return_value=proc), \
             mock.patch('builtins.open', mock.mock_open()):
            result = updater.perform_update(user=None, progress_tracker=None)

        self.assertTrue(result['success'])


@override_settings(
    DEBUG=False,
    SECRET_KEY='test-secret-key',
    APP_MASTER_KEY='dGVzdC1tYXN0ZXIta2V5LXRoaXJ0eS1ieXRlcw==',
    API_KEY_SECRET='api-secret-distinct-from-secret-key',
    AUTO_UPDATE_ENABLED=False,
    BETA_UPSTREAM_URL='',
    BETA_ADMIN_EMAIL='',
    HIBP_ENABLED=False,
    REQUIRE_2FA=True,
    SESSION_COOKIE_SECURE=True,
    CSRF_COOKIE_SECURE=True,
    ALLOWED_HOSTS=['docs.example.com'],
    CSRF_TRUSTED_ORIGINS=['https://docs.example.com'],
    PRIVATE_FILE_SERVER='nginx',
    DATABASES={
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': ':memory:',
        }
    },
    MIDDLEWARE=TEST_MIDDLEWARE,
)
class CheckSafeDeploymentCommandTests(TestCase):
    def test_all_pass_exits_zero(self):
        out = StringIO()
        call_command('check_safe_deployment', stdout=out)
        self.assertIn('PASS', out.getvalue())
        self.assertIn('All checks passed', out.getvalue())

    def test_debug_true_exits_nonzero(self):
        out = StringIO()
        err = StringIO()
        with override_settings(DEBUG=True):
            with self.assertRaises(SystemExit) as ctx:
                call_command('check_safe_deployment', stdout=out, stderr=err)
        self.assertEqual(ctx.exception.code, 1)
        self.assertIn('FAIL  DEBUG is False', out.getvalue())
