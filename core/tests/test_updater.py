"""Tests for the GUI updater (`core.updater.UpdateService`).

Focused on v3.17.284 (issue #128): when the update script exits non-zero,
the captured stdout/stderr lines must land in `AuditLog.extra_data.output_tail`
so superusers can diagnose without SSHing in.
"""
from __future__ import annotations

from unittest import mock

from django.test import TestCase

from audit.models import AuditLog
from core.updater import UpdateService


class UpdateServiceFailureCaptureTests(TestCase):
    """Phase / issue-128 (v3.17.284): persist failed-update output."""

    def _fake_response(self, script_text: str = '#!/bin/bash\necho hi\n'):
        r = mock.MagicMock()
        r.text = script_text
        r.raise_for_status.return_value = None
        return r

    def _fake_process(self, lines, returncode):
        proc = mock.MagicMock()
        # `iter(process.stdout.readline, '')` is what the updater uses
        # to consume output. A list iterator works as a stand-in.
        proc.stdout.readline.side_effect = list(lines) + ['']
        proc.wait.return_value = None
        proc.returncode = returncode
        return proc

    def test_failed_run_persists_output_tail_to_audit_log(self):
        updater = UpdateService()
        fake_lines = [
            'Step 1/5: Fetching latest code...\n',
            'Step 1/5: Code updated. New version: 3.17.999\n',
            'Step 2/5: Installing Python dependencies...\n',
            'Step 2/5: Core dependencies installed\n',
            'Step 3/5: Running database migrations...\n',
            'Traceback (most recent call last):\n',
            '  File "/srv/venv/bin/django-admin", line 8, in <module>\n',
            'django.db.utils.ProgrammingError: column "x" of relation does not exist\n',
            'ERROR: command failed at line 99: migrate (exit 1)\n',
        ]
        proc = self._fake_process(fake_lines, returncode=1)

        with mock.patch.object(updater, '_is_systemd_service', return_value=False), \
             mock.patch('core.updater.requests.get',
                         return_value=self._fake_response()), \
             mock.patch('core.updater.subprocess.Popen', return_value=proc):
            result = updater.perform_update(user=None, progress_tracker=None)

        self.assertFalse(result['success'])
        self.assertIn('exited with code 1', result['error'])

        # Audit row must exist + carry the full output tail
        row = AuditLog.objects.filter(action='system_update_failed').first()
        self.assertIsNotNone(row)
        tail = row.extra_data.get('output_tail', '')
        self.assertIn('ProgrammingError', tail)
        self.assertIn('Traceback', tail)
        # Steps that DID complete should be reflected
        self.assertIn('Step 2/5: Core dependencies installed', tail)

    def test_output_tail_capped_at_50kb(self):
        updater = UpdateService()
        # Generate ~120kb of fake output
        big_lines = [f'noisy line {i}: ' + ('x' * 200) + '\n' for i in range(600)]
        proc = self._fake_process(big_lines, returncode=1)

        with mock.patch.object(updater, '_is_systemd_service', return_value=False), \
             mock.patch('core.updater.requests.get',
                         return_value=self._fake_response()), \
             mock.patch('core.updater.subprocess.Popen', return_value=proc):
            updater.perform_update(user=None, progress_tracker=None)

        row = AuditLog.objects.filter(action='system_update_failed').first()
        self.assertIsNotNone(row)
        tail = row.extra_data.get('output_tail', '')
        self.assertLessEqual(len(tail), 50_000)

    def test_successful_run_does_not_create_failure_audit(self):
        updater = UpdateService()
        proc = self._fake_process(
            ['Step 1/5: Fetching latest code...\n',
             'Step 5/5: Scheduling restart...\n',
             'Update complete!\n'],
            returncode=0,
        )

        with mock.patch.object(updater, '_is_systemd_service', return_value=False), \
             mock.patch('core.updater.requests.get',
                         return_value=self._fake_response()), \
             mock.patch('core.updater.subprocess.Popen', return_value=proc):
            result = updater.perform_update(user=None, progress_tracker=None)

        self.assertTrue(result['success'])
        # Failed-update audit row must NOT exist for a successful run
        self.assertFalse(
            AuditLog.objects.filter(action='system_update_failed').exists()
        )
