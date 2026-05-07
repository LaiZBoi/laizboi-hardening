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
    SecurityIncident,
    SecurityIncidentEvent,
    SecurityVendorConnection,
    SIEMWebhookEndpoint,
    _correlate_alert_to_incident,
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


# ---------------------------------------------------------------------------
# Phase 23 v3.17.338 — SecurityIncident + timeline correlation
# ---------------------------------------------------------------------------

class SecurityIncidentCorrelationTests(TestCase):
    def setUp(self):
        self.org = _make_org()
        self.conn = _make_conn(self.org)

    def _make_alert(self, ext, severity='high', asset='workstation-1'):
        return SecurityAlert.objects.create(
            connection=self.conn, organization=self.org,
            external_id=ext, severity=severity,
            title=f'{severity} on {asset}', asset_hint=asset,
        )

    def test_first_alert_opens_incident(self):
        a1 = self._make_alert('a-1')
        inc = _correlate_alert_to_incident(a1)
        self.assertIsNotNone(inc.pk)
        self.assertEqual(inc.organization, self.org)
        self.assertEqual(inc.severity, 'high')
        self.assertEqual(inc.asset_hint, 'workstation-1')
        self.assertIn(a1, inc.alerts.all())
        # Timeline event for opening
        self.assertEqual(inc.events.filter(kind='opened').count(), 1)

    def test_second_matching_alert_attaches_to_same_incident(self):
        a1 = self._make_alert('a-1')
        inc1 = _correlate_alert_to_incident(a1)
        a2 = self._make_alert('a-2')
        inc2 = _correlate_alert_to_incident(a2)
        self.assertEqual(inc1.pk, inc2.pk)
        self.assertEqual(inc1.alerts.count(), 2)
        self.assertEqual(inc1.events.filter(kind='alert_added').count(), 1)

    def test_different_severity_opens_new_incident(self):
        a1 = self._make_alert('a-1', severity='high')
        inc1 = _correlate_alert_to_incident(a1)
        a2 = self._make_alert('a-2', severity='critical')
        inc2 = _correlate_alert_to_incident(a2)
        self.assertNotEqual(inc1.pk, inc2.pk)

    def test_resolved_incident_does_not_correlate(self):
        a1 = self._make_alert('a-1')
        inc1 = _correlate_alert_to_incident(a1)
        inc1.status = 'resolved'
        inc1.save(update_fields=['status'])
        a2 = self._make_alert('a-2')
        inc2 = _correlate_alert_to_incident(a2)
        self.assertNotEqual(inc1.pk, inc2.pk)

    def test_add_event_helper(self):
        a1 = self._make_alert('a-1')
        inc = _correlate_alert_to_incident(a1)
        ev = inc.add_event(kind='note', message='analyst comment')
        self.assertEqual(ev.kind, 'note')
        self.assertEqual(ev.message, 'analyst comment')
        self.assertEqual(ev.incident, inc)


_TEST_STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
}


@override_settings(
    MIDDLEWARE=TEST_MIDDLEWARE, SECURE_SSL_REDIRECT=False,
    STORAGES=_TEST_STORAGES,
)
class SecurityIncidentViewTests(TestCase):
    def setUp(self):
        self.org = _make_org()
        self.user = User.objects.create_user(
            username='analyst', password='pw', is_superuser=True, is_staff=True,
        )
        from accounts.models import Membership
        Membership.objects.create(
            user=self.user, organization=self.org, is_active=True,
        )
        self.incident = SecurityIncident.objects.create(
            organization=self.org, title='Test incident', severity='high',
        )

    def test_incident_detail_view(self):
        c = Client()
        c.force_login(self.user)
        r = c.get(f'/security/incidents/{self.incident.pk}/')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Test incident')

    def test_incident_decide_acknowledge(self):
        c = Client()
        c.force_login(self.user)
        r = c.post(
            f'/security/incidents/{self.incident.pk}/decide/',
            data={'decision': 'acknowledge'},
        )
        self.assertEqual(r.status_code, 302)
        self.incident.refresh_from_db()
        self.assertEqual(self.incident.status, 'investigating')
        self.assertIsNotNone(self.incident.acknowledged_at)
        self.assertEqual(self.incident.events.filter(kind='acknowledged').count(), 1)


# ---------------------------------------------------------------------------
# Phase 23 v3.17.339 — Exposure scoring
# ---------------------------------------------------------------------------

class ExposureScoringTests(TestCase):
    def setUp(self):
        self.org = _make_org()
        self.conn = _make_conn(self.org)

    def test_zero_for_clean_org(self):
        from security_alerts.exposure import compute_exposure_score
        self.assertEqual(compute_exposure_score(self.org), 0)

    def test_severity_weights_increase_score(self):
        from security_alerts.exposure import compute_exposure_score
        # One critical alert → weight 25.
        SecurityAlert.objects.create(
            connection=self.conn, organization=self.org,
            external_id='sev-c', severity='critical', title='c',
        )
        score_after_critical = compute_exposure_score(self.org)
        self.assertGreaterEqual(score_after_critical, 25)
        # Adding a low alert raises it further.
        SecurityAlert.objects.create(
            connection=self.conn, organization=self.org,
            external_id='sev-l', severity='low', title='l',
        )
        score_after_low = compute_exposure_score(self.org)
        self.assertGreater(score_after_low, score_after_critical)

    def test_resolved_alerts_dont_count(self):
        from security_alerts.exposure import compute_exposure_score
        SecurityAlert.objects.create(
            connection=self.conn, organization=self.org,
            external_id='r-1', severity='critical', title='resolved',
            status='resolved',
        )
        self.assertEqual(compute_exposure_score(self.org), 0)

    def test_recompute_for_org_persists(self):
        from security_alerts.exposure import recompute_for_org
        SecurityAlert.objects.create(
            connection=self.conn, organization=self.org,
            external_id='p-1', severity='high', title='h',
        )
        score = recompute_for_org(self.org)
        self.assertGreater(score, 0)
        self.org.refresh_from_db()
        self.assertEqual(self.org.exposure_score, score)
        self.assertIsNotNone(self.org.exposure_score_updated_at)

    def test_management_command_updates_all_orgs(self):
        from io import StringIO
        SecurityAlert.objects.create(
            connection=self.conn, organization=self.org,
            external_id='m-1', severity='medium', title='m',
        )
        out = StringIO()
        call_command('recompute_exposure_scores', stdout=out)
        self.org.refresh_from_db()
        self.assertGreater(self.org.exposure_score, 0)


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
