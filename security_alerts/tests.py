"""
Phase 9 — Security alert ingestion tests.

Cover model-level dedupe + MTTA math, the auto-ticket rule engine
(severity-min, suppression window, P1 mapping), and the webhook
receiver auth path.
"""
from datetime import date, datetime, timedelta, timezone as dt_timezone
from unittest.mock import patch

from django.conf import settings as django_settings
from django.contrib.auth.models import User
from django.core.management import call_command
from django.db import IntegrityError
from django.test import Client, TestCase, override_settings
from django.utils import timezone


# Tests bypass the project-wide 2FA enforcement middleware.
TEST_MIDDLEWARE = [
    m for m in django_settings.MIDDLEWARE
    if 'Enforce2FAMiddleware' not in m and 'AxesMiddleware' not in m
]


from core.models import Organization
from psa.models import Queue, Ticket, TicketPriority, TicketStatus, TicketType
from security_alerts.models import (
    SecurityAlert,
    SecurityAlertRule,
    SecurityVendorConnection,
    SIEMWebhookEndpoint,
)


def _seed_psa():
    Queue.objects.get_or_create(slug='helpdesk', defaults={'name': 'Helpdesk', 'is_active': True})
    for code, name in [('P1', 'Critical'), ('P2', 'High'), ('P3', 'Medium'),
                       ('P4', 'Low'), ('P5', 'Info')]:
        TicketPriority.objects.get_or_create(code=code, defaults={'name': name})
    TicketStatus.objects.get_or_create(slug='new', defaults={'name': 'New'})
    TicketType.objects.get_or_create(slug='incident', defaults={'name': 'Incident'})


def _make_org(name='ACME', slug='acme'):
    return Organization.objects.create(name=name, slug=slug)


def _make_conn(org, **kwargs):
    return SecurityVendorConnection.objects.create(
        organization=org, name='Test conn',
        provider='defender', category='edr',
        **kwargs,
    )


class SecurityAlertModelTests(TestCase):
    def test_unique_connection_external_id(self):
        org = _make_org()
        conn = _make_conn(org)
        SecurityAlert.objects.create(
            connection=conn, organization=org, external_id='abc-1',
            severity='high', title='one',
        )
        with self.assertRaises(IntegrityError):
            SecurityAlert.objects.create(
                connection=conn, organization=org, external_id='abc-1',
                severity='high', title='dup',
            )

    def test_acknowledge_minutes(self):
        org = _make_org()
        conn = _make_conn(org)
        alert = SecurityAlert.objects.create(
            connection=conn, organization=org, external_id='ack-1',
            severity='medium', title='ack me',
        )
        # seen_at is auto_now_add. Set acknowledged_at = seen_at + 30min.
        alert.acknowledged_at = alert.seen_at + timedelta(minutes=30)
        alert.save(update_fields=['acknowledged_at'])
        self.assertEqual(alert.acknowledge_minutes, 30)
        # When unacked, returns None.
        unacked = SecurityAlert.objects.create(
            connection=conn, organization=org, external_id='ack-2',
            severity='low', title='still open',
        )
        self.assertIsNone(unacked.acknowledge_minutes)


class AutoTicketRuleTests(TestCase):
    def setUp(self):
        _seed_psa()
        self.org = _make_org()
        self.conn = _make_conn(self.org)

    def _make_alert(self, severity, ext='evt-1'):
        return SecurityAlert.objects.create(
            connection=self.conn, organization=self.org,
            external_id=ext, severity=severity, title=f'{severity} alert',
        )

    def test_severity_min_match(self):
        SecurityAlertRule.objects.create(
            organization=self.org, name='only-high+',
            match_severity_min='high',
        )
        from security_alerts.adapters.base import _maybe_auto_ticket

        # Low — no match.
        low = self._make_alert('low', ext='evt-low')
        _maybe_auto_ticket(low)
        low.refresh_from_db()
        self.assertIsNone(low.auto_ticket)

        # High — matches.
        high = self._make_alert('high', ext='evt-high')
        _maybe_auto_ticket(high)
        high.refresh_from_db()
        self.assertIsNotNone(high.auto_ticket)

    def test_critical_alert_creates_ticket_with_p1(self):
        SecurityAlertRule.objects.create(
            organization=self.org, name='all',
            match_severity_min='info',
        )
        from security_alerts.adapters.base import _maybe_auto_ticket
        crit = self._make_alert('critical', ext='evt-crit')
        _maybe_auto_ticket(crit)
        crit.refresh_from_db()
        self.assertIsNotNone(crit.auto_ticket)
        self.assertEqual(crit.auto_ticket.priority.code, 'P1')

    def test_suppression_window_blocks_ticket(self):
        # Build a suppression window spanning the current hour.
        now_hr = timezone.now().hour
        SecurityAlertRule.objects.create(
            organization=self.org, name='off-hours',
            match_severity_min='info',
            suppress_start_hour=now_hr, suppress_end_hour=now_hr,
        )
        from security_alerts.adapters.base import _maybe_auto_ticket
        a = self._make_alert('critical', ext='evt-suppress')
        _maybe_auto_ticket(a)
        a.refresh_from_db()
        self.assertIsNone(a.auto_ticket)


@override_settings(MIDDLEWARE=TEST_MIDDLEWARE, SECURE_SSL_REDIRECT=False)
class WebhookReceiverTests(TestCase):
    def setUp(self):
        _seed_psa()
        self.org = _make_org()
        self.conn = _make_conn(self.org)

    def test_invalid_token_returns_404(self):
        c = Client()
        r = c.post('/security/webhook/bogus-token/', data='{}',
                   content_type='application/json')
        self.assertEqual(r.status_code, 404)

    def test_invalid_hmac_returns_403(self):
        c = Client()
        r = c.post(
            f'/security/webhook/{self.conn.webhook_token}/',
            data='{"id":"1","title":"x"}',
            content_type='application/json',
            HTTP_X_CST0R_SIGNATURE='deadbeef-not-valid',
        )
        self.assertEqual(r.status_code, 403)

    def test_valid_unsigned_post_persists_alert(self):
        # No signature header — accepted (auth via token only).
        c = Client()
        r = c.post(
            f'/security/webhook/{self.conn.webhook_token}/',
            data='{"id":"evt-77","title":"web hook test","severity":"high"}',
            content_type='application/json',
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['ok'], True)
        self.assertEqual(SecurityAlert.objects.filter(external_id='evt-77').count(), 1)


# ---------------------------------------------------------------------------
# Phase 23 v3.17.337 — SIEM webhook adapter
# ---------------------------------------------------------------------------

class SIEMParserTests(TestCase):
    def test_parse_cef_basic_line(self):
        from security_alerts.siem import parse_cef_line
        line = (
            'CEF:0|TestVendor|TestProduct|1.0|sig-1234|Sample Login Failure|7|'
            'src=10.0.0.1 dvchost=workstation-3 msg=Failed login attempt rt=1700000000'
        )
        out = parse_cef_line(line)
        self.assertIsNotNone(out)
        self.assertEqual(out['title'], 'Sample Login Failure')
        self.assertEqual(out['severity'], 'high')
        self.assertEqual(out['asset_hint'], 'workstation-3')
        self.assertIn('Failed login attempt', out['description'])
        self.assertEqual(out['raw_payload']['vendor'], 'TestVendor')

    def test_parse_cef_returns_none_on_non_cef(self):
        from security_alerts.siem import parse_cef_line
        self.assertIsNone(parse_cef_line('not a cef line'))
        self.assertIsNone(parse_cef_line(''))

    def test_severity_bucket_numeric_and_string(self):
        from security_alerts.siem import _bucket_severity
        self.assertEqual(_bucket_severity(0), 'low')
        self.assertEqual(_bucket_severity(5), 'medium')
        self.assertEqual(_bucket_severity(8), 'high')
        self.assertEqual(_bucket_severity(10), 'critical')
        self.assertEqual(_bucket_severity('critical'), 'critical')
        self.assertEqual(_bucket_severity('CRITICAL'), 'critical')
        self.assertEqual(_bucket_severity('low'), 'low')
        self.assertEqual(_bucket_severity(None), 'medium')


@override_settings(MIDDLEWARE=TEST_MIDDLEWARE, SECURE_SSL_REDIRECT=False)
class SIEMWebhookReceiverTests(TestCase):
    def setUp(self):
        _seed_psa()
        self.org = _make_org()
        self.endpoint = SIEMWebhookEndpoint.objects.create(
            organization=self.org, name='Splunk-feed',
            expected_format='cef', default_severity='medium',
        )

    def test_unknown_token_returns_404(self):
        c = Client()
        r = c.post('/security/siem/webhook/no-such-token/', data='CEF:0|x|y|1|1|t|5|',
                   content_type='text/plain')
        self.assertEqual(r.status_code, 404)

    def test_invalid_hmac_returns_403(self):
        import hmac, hashlib
        c = Client()
        body = b'CEF:0|Vendor|Prod|1|sig-1|test|5|src=1.2.3.4'
        r = c.post(
            f'/security/siem/webhook/{self.endpoint.token}/',
            data=body, content_type='text/plain',
            HTTP_X_CST0R_SIGNATURE='deadbeef-not-valid',
        )
        self.assertEqual(r.status_code, 403)

    def test_cef_ingestion_creates_security_alert(self):
        c = Client()
        body = (
            'CEF:0|Splunk|Enterprise|9.0|firewall-1234|Suspicious Login|9|'
            'src=10.0.0.5 dvchost=mail-server msg=multiple failed logins'
        )
        r = c.post(
            f'/security/siem/webhook/{self.endpoint.token}/',
            data=body, content_type='text/plain',
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['imported'], 1)
        alert = SecurityAlert.objects.get(siem_endpoint=self.endpoint)
        self.assertEqual(alert.title, 'Suspicious Login')
        self.assertEqual(alert.severity, 'critical')
        self.assertEqual(alert.asset_hint, 'mail-server')

    def test_dedupe_on_repeat_ingestion(self):
        c = Client()
        body = (
            'CEF:0|Vendor|Prod|1.0|sig-dedupe|Repeat Event|6|'
            'externalId=EVT-7777 dvchost=db-host'
        )
        c.post(f'/security/siem/webhook/{self.endpoint.token}/',
               data=body, content_type='text/plain')
        c.post(f'/security/siem/webhook/{self.endpoint.token}/',
               data=body, content_type='text/plain')
        # Should be one row, not two.
        self.assertEqual(SecurityAlert.objects.filter(siem_endpoint=self.endpoint).count(), 1)

    def test_valid_hmac_accepts_request(self):
        import hmac, hashlib
        body = b'CEF:0|Vendor|Prod|1.0|sig-2|HMAC-protected|7|src=10.0.0.9'
        sig = hmac.new(self.endpoint.hmac_secret.encode('utf-8'), body, hashlib.sha256).hexdigest()
        c = Client()
        r = c.post(
            f'/security/siem/webhook/{self.endpoint.token}/',
            data=body, content_type='text/plain',
            HTTP_X_CST0R_SIGNATURE=sig,
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['imported'], 1)

    def test_require_hmac_rejects_missing_signature(self):
        self.endpoint.require_hmac = True
        self.endpoint.save(update_fields=['require_hmac'])
        c = Client()
        r = c.post(
            f'/security/siem/webhook/{self.endpoint.token}/',
            data='CEF:0|v|p|1|x|t|5|', content_type='text/plain',
        )
        self.assertEqual(r.status_code, 403)

    def test_json_payload_ingestion(self):
        self.endpoint.expected_format = 'json'
        self.endpoint.save(update_fields=['expected_format'])
        c = Client()
        body = '{"id": "abc-9", "title": "JSON event", "severity": "high", "host": "web-01"}'
        r = c.post(
            f'/security/siem/webhook/{self.endpoint.token}/',
            data=body, content_type='application/json',
        )
        self.assertEqual(r.status_code, 200)
        alert = SecurityAlert.objects.get(siem_endpoint=self.endpoint, external_id='abc-9')
        self.assertEqual(alert.severity, 'high')
        self.assertEqual(alert.asset_hint, 'web-01')


class MTTAQueryTests(TestCase):
    def test_unacked_alerts_counted(self):
        from reports.queries import security_alert_mtta
        org = _make_org()
        conn = _make_conn(org)
        SecurityAlert.objects.create(
            connection=conn, organization=org, external_id='m-1',
            severity='high', title='unacked',
        )
        a2 = SecurityAlert.objects.create(
            connection=conn, organization=org, external_id='m-2',
            severity='high', title='acked',
        )
        a2.acknowledged_at = a2.seen_at + timedelta(minutes=15)
        a2.save(update_fields=['acknowledged_at'])

        today = date.today()
        rows = security_alert_mtta(today, today)
        # All in same client_id+vendor bucket.
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row['count'], 2)
        self.assertEqual(row['unack_count'], 1)
        self.assertEqual(row['avg_mtta_minutes'], 15)
