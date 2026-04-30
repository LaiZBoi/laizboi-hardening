"""
Tests for vault.access_rules decision engine (v3.17.163).
"""
from datetime import time, datetime, timezone as dt_timezone
from unittest.mock import patch

from django.conf import settings as django_settings
from django.contrib.auth.models import User
from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone

from core.models import Organization
from vault.models import Password, VaultAccessRule


# Mirror the test middleware setup used elsewhere so we bypass the 2FA
# enforcement middleware and django-axes lockouts.
TEST_MIDDLEWARE = [
    m for m in django_settings.MIDDLEWARE
    if 'Enforce2FAMiddleware' not in m and 'AxesMiddleware' not in m
]


def _make_request(rf, ip='8.8.8.8', user=None):
    req = rf.get('/vault/1/', REMOTE_ADDR=ip)
    if user is not None:
        req.user = user
    return req


@override_settings(MIDDLEWARE=TEST_MIDDLEWARE, SECURE_SSL_REDIRECT=False)
class VaultAccessRuleEngineTests(TestCase):
    """v3.17.163 — DENY-wins-then-priority access engine."""

    @classmethod
    def setUpTestData(cls):
        cls.org = Organization.objects.create(name='Acme', slug='acme-rules')
        cls.org_b = Organization.objects.create(name='OtherCo', slug='other-rules')
        cls.alice = User.objects.create_user('alice', 'a@x.com', 'pw')
        cls.bob = User.objects.create_user('bob', 'b@x.com', 'pw')
        cls.password = Password.objects.create(
            organization=cls.org,
            title='Email login',
            encrypted_password='dummy',
        )
        cls.password_b = Password.objects.create(
            organization=cls.org,
            title='Other thing',
            encrypted_password='dummy',
        )

    def setUp(self):
        self.rf = RequestFactory()

    def test_no_rules_means_allow(self):
        from vault.access_rules import evaluate
        req = _make_request(self.rf, user=self.alice)
        result = evaluate(self.password, self.alice, req)
        self.assertTrue(result['allowed'])
        self.assertIsNone(result['matched_rule_id'])

    @patch('vault.access_rules._country_for_ip', return_value='CN')
    def test_deny_country_blocks(self, _mock_cc):
        VaultAccessRule.objects.create(
            organization=self.org,
            name='Block China',
            scope='organization',
            effect='deny',
            blocked_countries=['CN'],
        )
        from vault.access_rules import evaluate
        req = _make_request(self.rf, user=self.alice)
        result = evaluate(self.password, self.alice, req)
        self.assertFalse(result['allowed'])
        self.assertIn('Denied', result['reason'])

    @patch('vault.access_rules._country_for_ip', return_value='CN')
    def test_deny_wins_over_allow(self, _mock_cc):
        VaultAccessRule.objects.create(
            organization=self.org,
            name='Allow all',
            scope='organization',
            effect='allow',
            priority=10,
        )
        VaultAccessRule.objects.create(
            organization=self.org,
            name='Deny CN',
            scope='organization',
            effect='deny',
            priority=200,
            blocked_countries=['CN'],
        )
        from vault.access_rules import evaluate
        req = _make_request(self.rf, user=self.alice)
        result = evaluate(self.password, self.alice, req)
        self.assertFalse(result['allowed'])
        self.assertIn('Deny CN', result['reason'])

    def test_time_window_outside_business_hours(self):
        VaultAccessRule.objects.create(
            organization=self.org,
            name='Business hours only',
            scope='organization',
            effect='allow',
            allowed_hour_start=time(9, 0),
            allowed_hour_end=time(17, 0),
            timezone='UTC',
        )
        from vault.access_rules import evaluate
        req = _make_request(self.rf, user=self.alice)
        # Force "now" to 22:00 UTC -> outside business hours -> conservative deny.
        fake_now = datetime(2026, 4, 30, 22, 0, tzinfo=dt_timezone.utc)
        with patch('django.utils.timezone.now', return_value=fake_now):
            with patch('vault.access_rules._country_for_ip', return_value=None):
                result = evaluate(self.password, self.alice, req)
        self.assertFalse(result['allowed'])
        self.assertIn('No matching ALLOW rule', result['reason'])

    @patch('vault.access_rules._country_for_ip', return_value=None)
    def test_cidr_allowlist(self, _mock_cc):
        VaultAccessRule.objects.create(
            organization=self.org,
            name='Office only',
            scope='organization',
            effect='allow',
            allowed_cidrs=['10.0.0.0/8'],
        )
        from vault.access_rules import evaluate
        # Request from 8.8.8.8 -> not in 10.0.0.0/8 -> conservative deny
        req = _make_request(self.rf, ip='8.8.8.8', user=self.alice)
        result = evaluate(self.password, self.alice, req)
        self.assertFalse(result['allowed'])
        # Request from 10.0.0.5 -> in 10.0.0.0/8 -> allow
        req2 = _make_request(self.rf, ip='10.0.0.5', user=self.alice)
        result2 = evaluate(self.password, self.alice, req2)
        self.assertTrue(result2['allowed'])

    @patch('vault.access_rules._country_for_ip', return_value='CN')
    def test_user_scoped_rule(self, _mock_cc):
        # A rule that targets alice only must not affect bob's request.
        VaultAccessRule.objects.create(
            organization=self.org,
            name='No CN for alice',
            scope='user',
            target_user=self.alice,
            effect='deny',
            blocked_countries=['CN'],
        )
        from vault.access_rules import evaluate
        # Alice from CN -> denied
        result_alice = evaluate(
            self.password, self.alice, _make_request(self.rf, user=self.alice),
        )
        self.assertFalse(result_alice['allowed'])
        # Bob from CN -> rule does not apply -> default ALLOW (no rules for bob)
        result_bob = evaluate(
            self.password, self.bob, _make_request(self.rf, user=self.bob),
        )
        self.assertTrue(result_bob['allowed'])

    @patch('vault.access_rules._country_for_ip', return_value='CN')
    def test_item_scoped_rule(self, _mock_cc):
        # A rule that targets one password must not affect other passwords.
        VaultAccessRule.objects.create(
            organization=self.org,
            name='No CN for this password',
            scope='item',
            target_password=self.password,
            effect='deny',
            blocked_countries=['CN'],
        )
        from vault.access_rules import evaluate
        result_target = evaluate(
            self.password, self.alice, _make_request(self.rf, user=self.alice),
        )
        self.assertFalse(result_target['allowed'])
        result_other = evaluate(
            self.password_b, self.alice, _make_request(self.rf, user=self.alice),
        )
        self.assertTrue(result_other['allowed'])
