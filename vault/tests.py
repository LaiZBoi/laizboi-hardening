"""
Tests for vault.access_rules decision engine (v3.17.163).
"""
from datetime import time, datetime, timezone as dt_timezone
from unittest.mock import patch

from django.conf import settings as django_settings
from django.contrib.auth.models import User
from django.test import Client, RequestFactory, TestCase, override_settings
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


# ---------------------------------------------------------------------------
# v3.17.181 — password mutation audit logging.
# Confirms password_edit / password_delete emit AuditLog rows on success
# AND failure. Reads were already audited; mutations were not.
# ---------------------------------------------------------------------------

@override_settings(MIDDLEWARE=TEST_MIDDLEWARE, SECURE_SSL_REDIRECT=False)
class PasswordMutationAuditTests(TestCase):
    def setUp(self):
        from accounts.models import Membership, Role
        from audit.models import AuditLog
        from django.test import Client
        self.AuditLog = AuditLog
        self.org = Organization.objects.create(name='AuditCo', slug='auditco')
        self.user = User.objects.create_user('mutuser', password='pw', email='m@x.com')
        Membership.objects.create(
            user=self.user, organization=self.org,
            role=Role.OWNER, is_active=True,
        )
        self.password = Password.objects.create(
            organization=self.org, title='target', username='svc',
            password_type='server',
        )
        self.password.set_password('s3cret')
        self.password.save()

        self.client = Client()
        self.client.force_login(self.user)
        session = self.client.session
        session['2fa_prompted'] = True
        session['current_organization_id'] = self.org.id
        session.save()

    def test_password_edit_success_logs_update(self):
        url = f'/vault/{self.password.pk}/edit/'
        # Use the form's actual fields — title is required, password_type
        # must be one of the model choices.
        response = self.client.post(url, {
            'title': 'target-renamed',
            'username': 'svc',
            'password_type': 'server',
            'organization': self.org.pk,
        })
        # Either 302 (redirect on success) or 200 (form re-render); we just
        # care that an audit row landed.
        self.assertIn(response.status_code, (200, 302))
        log = (self.AuditLog.objects
               .filter(action='update', object_type='password',
                       object_id=self.password.pk)
               .order_by('-timestamp')
               .first())
        self.assertIsNotNone(log, 'expected an audit row for password update')

    def test_password_delete_logs_with_title_preserved(self):
        url = f'/vault/{self.password.pk}/delete/'
        deleted_pk = self.password.pk
        title = self.password.title
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        # `AuditLoggingMiddleware` also writes a generic URL-pattern row;
        # after delete it can't resolve the model and falls back to
        # `'password #N'`. Filter to *our* view-level row by its title.
        log = (self.AuditLog.objects
               .filter(action='delete', object_type='password',
                       object_id=deleted_pk, object_repr=title)
               .order_by('-timestamp')
               .first())
        self.assertIsNotNone(log,
            'expected the view-level audit row carrying the original title')
        self.assertTrue(log.success)
        self.assertIn('deleted', log.description.lower())


# ---------------------------------------------------------------------------
# Phase 37 — Vault Approval & Break-Glass Workflow (v3.17.241)
# ---------------------------------------------------------------------------

@override_settings(MIDDLEWARE=TEST_MIDDLEWARE, SECURE_SSL_REDIRECT=False)
class VaultApprovalAndBreakGlassTests(TestCase):
    """Phase 37 v1: per-credential reveal approval + emergency break-glass."""

    def setUp(self):
        from accounts.models import Membership, Role
        from core.models import Organization
        from vault.models import Password
        self.org = Organization.objects.create(name='AppRevCo', slug='appr-co')
        self.user = User.objects.create_user('appr-user', 'a@x.com', 'pw')
        Membership.objects.create(user=self.user, organization=self.org,
                                   role=Role.OWNER, is_active=True)
        self.admin = User.objects.create_user('appr-admin', 'admin@x.com', 'pw',
                                                is_superuser=True, is_staff=True)
        Membership.objects.create(user=self.admin, organization=self.org,
                                   role=Role.OWNER, is_active=True)
        self.password = Password.objects.create(
            organization=self.org, title='Locked credential',
            requires_reveal_approval=True,
        )
        self.password.set_password('s3cret-value')
        self.password.save()

    def _login(self, c, user):
        c.force_login(user)
        s = c.session
        s['2fa_prompted'] = True
        s['current_organization_id'] = self.org.id
        s.save()

    def test_reveal_blocked_without_approval(self):
        c = Client()
        self._login(c, self.user)
        r = c.post(f'/vault/{self.password.pk}/reveal/')
        self.assertEqual(r.status_code, 403)
        body = r.json()
        self.assertTrue(body.get('requires_approval'))

    def test_request_reveal_creates_pending_row(self):
        from vault.models import VaultRevealRequest
        c = Client()
        self._login(c, self.user)
        r = c.post(f'/vault/{self.password.pk}/request-reveal/', data={
            'justification': 'Need to reset prod database',
        })
        self.assertEqual(r.status_code, 200)
        self.assertEqual(VaultRevealRequest.objects.count(), 1)
        req = VaultRevealRequest.objects.first()
        self.assertEqual(req.status, 'pending')
        self.assertEqual(req.requester_id, self.user.id)
        self.assertFalse(req.is_break_glass)

    def test_request_reveal_rejects_empty_justification(self):
        from vault.models import VaultRevealRequest
        c = Client()
        self._login(c, self.user)
        r = c.post(f'/vault/{self.password.pk}/request-reveal/', data={
            'justification': '',
        })
        self.assertEqual(r.status_code, 400)
        self.assertEqual(VaultRevealRequest.objects.count(), 0)

    def test_admin_approve_unlocks_reveal(self):
        from vault.models import VaultRevealRequest
        # User requests
        c_user = Client()
        self._login(c_user, self.user)
        c_user.post(f'/vault/{self.password.pk}/request-reveal/',
                    data={'justification': 'standard maintenance'})
        req = VaultRevealRequest.objects.first()

        # Admin approves
        c_admin = Client()
        self._login(c_admin, self.admin)
        ar = c_admin.post(f'/vault/reveal-requests/{req.pk}/decide/', data={
            'decision': 'approve', 'notes': 'OK proceed',
        })
        self.assertEqual(ar.status_code, 200)
        req.refresh_from_db()
        self.assertEqual(req.status, 'approved')
        self.assertEqual(req.decided_by_id, self.admin.id)
        self.assertIsNotNone(req.expires_at)

        # User can now reveal
        rr = c_user.post(f'/vault/{self.password.pk}/reveal/')
        self.assertEqual(rr.status_code, 200)
        body = rr.json()
        self.assertEqual(body['password'], 's3cret-value')

        # And the approval is marked single-use (revealed_at set).
        req.refresh_from_db()
        self.assertIsNotNone(req.revealed_at)

        # A second reveal needs a fresh request — gate fires again.
        rr2 = c_user.post(f'/vault/{self.password.pk}/reveal/')
        self.assertEqual(rr2.status_code, 403)

    def test_admin_deny_keeps_block(self):
        from vault.models import VaultRevealRequest
        c_user = Client()
        self._login(c_user, self.user)
        c_user.post(f'/vault/{self.password.pk}/request-reveal/',
                    data={'justification': 'curious'})
        req = VaultRevealRequest.objects.first()
        c_admin = Client()
        self._login(c_admin, self.admin)
        c_admin.post(f'/vault/reveal-requests/{req.pk}/decide/', data={
            'decision': 'deny', 'notes': 'no business need',
        })
        req.refresh_from_db()
        self.assertEqual(req.status, 'denied')
        rr = c_user.post(f'/vault/{self.password.pk}/reveal/')
        self.assertEqual(rr.status_code, 403)

    def test_break_glass_bypasses_approval_with_long_justification(self):
        from vault.models import VaultRevealRequest
        c = Client()
        self._login(c, self.user)
        r = c.post(f'/vault/{self.password.pk}/break-glass/', data={
            'justification': 'Production is down at 3am, on-call has been paging me for an hour.',
        })
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertTrue(body['is_break_glass'])
        self.assertEqual(body['status'], 'approved')
        # Reveal works immediately
        rr = c.post(f'/vault/{self.password.pk}/reveal/')
        self.assertEqual(rr.status_code, 200)
        self.assertEqual(rr.json()['password'], 's3cret-value')

    def test_break_glass_requires_long_justification(self):
        from vault.models import VaultRevealRequest
        c = Client()
        self._login(c, self.user)
        r = c.post(f'/vault/{self.password.pk}/break-glass/', data={
            'justification': 'too short',
        })
        self.assertEqual(r.status_code, 400)
        self.assertEqual(VaultRevealRequest.objects.count(), 0)

    def test_password_without_flag_skips_gate(self):
        from vault.models import Password
        unguarded = Password.objects.create(
            organization=self.org, title='Free reveal',
            requires_reveal_approval=False,
        )
        unguarded.set_password('open-secret')
        unguarded.save()
        c = Client()
        self._login(c, self.user)
        rr = c.post(f'/vault/{unguarded.pk}/reveal/')
        self.assertEqual(rr.status_code, 200)
        self.assertEqual(rr.json()['password'], 'open-secret')

    def test_non_staff_cannot_decide_request(self):
        from vault.models import VaultRevealRequest
        c_user = Client()
        self._login(c_user, self.user)
        c_user.post(f'/vault/{self.password.pk}/request-reveal/',
                    data={'justification': 'reason'})
        req = VaultRevealRequest.objects.first()

        # Different non-staff user tries to decide.
        from accounts.models import Membership, Role
        peer = User.objects.create_user('peer', 'p@x.com', 'pw')
        Membership.objects.create(user=peer, organization=self.org,
                                   role=Role.OWNER, is_active=True)
        c_peer = Client()
        self._login(c_peer, peer)
        r = c_peer.post(f'/vault/reveal-requests/{req.pk}/decide/', data={
            'decision': 'approve',
        })
        self.assertEqual(r.status_code, 403)
        req.refresh_from_db()
        self.assertEqual(req.status, 'pending')

    def test_reveal_request_list_renders_for_staff(self):
        c = Client()
        self._login(c, self.admin)
        r = c.get('/vault/reveal-requests/')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Vault Reveal Approvals')


@override_settings(MIDDLEWARE=TEST_MIDDLEWARE, SECURE_SSL_REDIRECT=False)
class VaultClientLevelApprovalTests(TestCase):
    """v3.17.247 — Phase 37 v2: client-org admin can approve reveals for
    their own org, closing the "Optional client-level vault approval
    rules" sub-bullet."""

    def setUp(self):
        from accounts.models import Membership, Role
        from core.models import Organization
        from vault.models import Password
        self.client_org = Organization.objects.create(name='ClientCo', slug='cl-co')
        self.other_org = Organization.objects.create(name='OtherCo', slug='cl-other')
        # Client-org admin: a regular user who's been flagged is_org_admin
        # for THE CLIENT ORG. NOT MSP staff/superuser.
        self.client_admin = User.objects.create_user('cl-admin', 'a@x.com', 'pw')
        Membership.objects.create(
            user=self.client_admin, organization=self.client_org,
            role=Role.OWNER, is_active=True, is_org_admin=True,
        )
        # Regular client user: needs to reveal a password owned by client_org.
        self.client_user = User.objects.create_user('cl-user', 'u@x.com', 'pw')
        Membership.objects.create(
            user=self.client_user, organization=self.client_org,
            role=Role.OWNER, is_active=True,
        )
        # Outsider: same admin role on a DIFFERENT org. Must NOT be able
        # to approve reveals for client_org's passwords.
        self.outsider_admin = User.objects.create_user('out-admin', 'o@x.com', 'pw')
        Membership.objects.create(
            user=self.outsider_admin, organization=self.other_org,
            role=Role.OWNER, is_active=True, is_org_admin=True,
        )

        self.password = Password.objects.create(
            organization=self.client_org, title='Client locked credential',
            requires_reveal_approval=True,
        )
        self.password.set_password('client-secret')
        self.password.save()

    def _login(self, c, user, org):
        c.force_login(user)
        s = c.session
        s['2fa_prompted'] = True
        s['current_organization_id'] = org.id
        s.save()

    def test_client_org_admin_can_approve_their_orgs_reveal(self):
        from vault.models import VaultRevealRequest
        # Client user requests
        c_user = Client()
        self._login(c_user, self.client_user, self.client_org)
        c_user.post(f'/vault/{self.password.pk}/request-reveal/',
                    data={'justification': 'Production access needed'})
        req = VaultRevealRequest.objects.first()

        # Client-org admin (NOT MSP staff) approves
        c_admin = Client()
        self._login(c_admin, self.client_admin, self.client_org)
        r = c_admin.post(f'/vault/reveal-requests/{req.pk}/decide/', data={
            'decision': 'approve',
        })
        self.assertEqual(r.status_code, 200)
        req.refresh_from_db()
        self.assertEqual(req.status, 'approved')
        self.assertEqual(req.decided_by_id, self.client_admin.id)

    def test_outsider_org_admin_cannot_approve(self):
        from vault.models import VaultRevealRequest
        c_user = Client()
        self._login(c_user, self.client_user, self.client_org)
        c_user.post(f'/vault/{self.password.pk}/request-reveal/',
                    data={'justification': 'standard'})
        req = VaultRevealRequest.objects.first()

        c_outsider = Client()
        self._login(c_outsider, self.outsider_admin, self.other_org)
        r = c_outsider.post(f'/vault/reveal-requests/{req.pk}/decide/', data={
            'decision': 'approve',
        })
        self.assertEqual(r.status_code, 403)
        req.refresh_from_db()
        self.assertEqual(req.status, 'pending')

    def test_requester_cannot_self_approve_even_as_org_admin(self):
        # Make the requester also an org admin — they STILL can't approve
        # their own request (defense in depth against insider misuse).
        from accounts.models import Membership
        from vault.models import VaultRevealRequest
        Membership.objects.filter(
            user=self.client_user, organization=self.client_org,
        ).update(is_org_admin=True)
        c_user = Client()
        self._login(c_user, self.client_user, self.client_org)
        c_user.post(f'/vault/{self.password.pk}/request-reveal/',
                    data={'justification': 'self-approval test'})
        req = VaultRevealRequest.objects.first()
        # Same user attempts to decide
        r = c_user.post(f'/vault/reveal-requests/{req.pk}/decide/', data={
            'decision': 'approve',
        })
        self.assertEqual(r.status_code, 403)
        req.refresh_from_db()
        self.assertEqual(req.status, 'pending')

    def test_regular_member_without_org_admin_flag_blocked(self):
        from vault.models import VaultRevealRequest
        c_user = Client()
        self._login(c_user, self.client_user, self.client_org)
        c_user.post(f'/vault/{self.password.pk}/request-reveal/',
                    data={'justification': 'reason'})
        req = VaultRevealRequest.objects.first()

        # Another regular member (no is_org_admin) tries to approve.
        from accounts.models import Membership, Role
        peer = User.objects.create_user('peer', 'p@x.com', 'pw')
        Membership.objects.create(
            user=peer, organization=self.client_org,
            role=Role.OWNER, is_active=True,  # is_org_admin defaults False
        )
        c_peer = Client()
        self._login(c_peer, peer, self.client_org)
        r = c_peer.post(f'/vault/reveal-requests/{req.pk}/decide/', data={
            'decision': 'approve',
        })
        self.assertEqual(r.status_code, 403)
        req.refresh_from_db()
        self.assertEqual(req.status, 'pending')


# ===========================================================================
# Phase 28 — Browser extension API
# ===========================================================================


@override_settings(MIDDLEWARE=TEST_MIDDLEWARE, SECURE_SSL_REDIRECT=False)
class WebExtensionAuthTokenModelTests(TestCase):
    """v3.17.327 — token issue/expiry/revoke."""

    @classmethod
    def setUpTestData(cls):
        cls.org = Organization.objects.create(name='ExtCo', slug='ext-co-tok')
        cls.user = User.objects.create_user('ext_user', 'e@x.com', 'pw')

    def test_issue_returns_secret_string_and_row(self):
        from vault.models import WebExtensionAuthToken
        secret, row = WebExtensionAuthToken.issue(
            user=self.user, label='Chrome on Mac',
        )
        self.assertEqual(row.token, secret)
        self.assertGreater(len(secret), 30)
        self.assertEqual(row.label, 'Chrome on Mac')
        self.assertTrue(row.is_active)

    def test_revoke_makes_token_inactive(self):
        from vault.models import WebExtensionAuthToken
        _, row = WebExtensionAuthToken.issue(user=self.user)
        row.revoke()
        self.assertFalse(row.is_active)
        self.assertIsNotNone(row.revoked_at)

    def test_expired_token_inactive(self):
        from datetime import timedelta
        from vault.models import WebExtensionAuthToken
        _, row = WebExtensionAuthToken.issue(user=self.user, ttl_days=1)
        row.expires_at = timezone.now() - timedelta(seconds=1)
        row.save(update_fields=['expires_at'])
        self.assertFalse(row.is_active)


@override_settings(MIDDLEWARE=TEST_MIDDLEWARE, SECURE_SSL_REDIRECT=False)
class ExtensionAuthDecoratorTests(TestCase):
    """v3.17.327 — extension_auth_required decorator."""

    @classmethod
    def setUpTestData(cls):
        cls.org = Organization.objects.create(name='ExtCo', slug='ext-co-dec')
        cls.user = User.objects.create_user('ext_user2', 'e2@x.com', 'pw')

    def setUp(self):
        from vault.models import WebExtensionAuthToken
        self.secret, self.row = WebExtensionAuthToken.issue(
            user=self.user, organization=self.org, label='Chrome',
        )
        self.rf = RequestFactory()

    def _wrap_passthrough(self):
        from vault.extension_auth import extension_auth_required

        @extension_auth_required
        def _view(request):
            from django.http import JsonResponse as JR
            return JR({
                'user': request.user.username,
                'org': request.current_organization.id if request.current_organization else None,
            })
        return _view

    def test_valid_token_resolves_user_and_org(self):
        view = self._wrap_passthrough()
        req = self.rf.get('/api/vault/extension/x/',
                          HTTP_AUTHORIZATION=f'Bearer {self.secret}')
        resp = view(req)
        self.assertEqual(resp.status_code, 200)
        import json as _json
        data = _json.loads(resp.content)
        self.assertEqual(data['user'], 'ext_user2')
        self.assertEqual(data['org'], self.org.id)

    def test_missing_header_401(self):
        view = self._wrap_passthrough()
        req = self.rf.get('/api/vault/extension/x/')
        resp = view(req)
        self.assertEqual(resp.status_code, 401)

    def test_revoked_token_401(self):
        self.row.revoke()
        view = self._wrap_passthrough()
        req = self.rf.get('/api/vault/extension/x/',
                          HTTP_AUTHORIZATION=f'Bearer {self.secret}')
        resp = view(req)
        self.assertEqual(resp.status_code, 401)

    def test_expired_token_401(self):
        from datetime import timedelta
        self.row.expires_at = timezone.now() - timedelta(seconds=1)
        self.row.save(update_fields=['expires_at'])
        view = self._wrap_passthrough()
        req = self.rf.get('/api/vault/extension/x/',
                          HTTP_AUTHORIZATION=f'Bearer {self.secret}')
        resp = view(req)
        self.assertEqual(resp.status_code, 401)

    def test_org_id_header_overrides_token_pin(self):
        other_org = Organization.objects.create(name='OtherCo', slug='ext-co-other')
        # Give the user membership in the other org so access is allowed.
        from accounts.models import Membership, Role
        Membership.objects.create(
            user=self.user, organization=other_org, role=Role.READONLY, is_active=True,
        )
        view = self._wrap_passthrough()
        req = self.rf.get('/api/vault/extension/x/',
                          HTTP_AUTHORIZATION=f'Bearer {self.secret}',
                          HTTP_X_ORGANIZATION_ID=str(other_org.id))
        resp = view(req)
        self.assertEqual(resp.status_code, 200)
        import json as _json
        data = _json.loads(resp.content)
        self.assertEqual(data['org'], other_org.id)


@override_settings(MIDDLEWARE=TEST_MIDDLEWARE, SECURE_SSL_REDIRECT=False)
class ExtensionTokenLifecycleEndpointTests(TestCase):
    """v3.17.327 — token issue / list / revoke endpoints (session-authed)."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user('ext_user3', 'e3@x.com', 'pw')

    def setUp(self):
        self.c = Client()
        self.c.force_login(self.user)

    def test_issue_returns_token_once(self):
        r = self.c.post('/vault/api/extension/tokens/issue/',
                        data={'label': 'Firefox'})
        self.assertEqual(r.status_code, 201)
        body = r.json()
        self.assertIn('token', body)
        self.assertEqual(body['label'], 'Firefox')

    def test_list_excludes_secret(self):
        # Issue first, then list.
        self.c.post('/vault/api/extension/tokens/issue/', data={'label': 'A'})
        r = self.c.get('/vault/api/extension/tokens/')
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(len(data['tokens']), 1)
        self.assertNotIn('token', data['tokens'][0])

    def test_revoke_marks_revoked(self):
        r1 = self.c.post('/vault/api/extension/tokens/issue/', data={})
        token_id = r1.json()['id']
        r2 = self.c.delete(f'/vault/api/extension/tokens/{token_id}/revoke/')
        self.assertEqual(r2.status_code, 200)
        self.assertTrue(r2.json()['revoked'])

    def test_other_user_cannot_revoke(self):
        r1 = self.c.post('/vault/api/extension/tokens/issue/', data={})
        token_id = r1.json()['id']
        intruder = User.objects.create_user('intruder', 'i@x.com', 'pw')
        c2 = Client()
        c2.force_login(intruder)
        r2 = c2.delete(f'/vault/api/extension/tokens/{token_id}/revoke/')
        self.assertEqual(r2.status_code, 403)
