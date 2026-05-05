"""
Baseline test coverage for the api/ app (audit punch-list / Phase 7 polish).

Before this file existed the externally-exposed REST API had zero
regression coverage. The v3.17.171 fix to `AuditLog.objects.create(... details=...)`
shipped silently — a real production bug that had been crashing every
successful `/api/passwords/<id>/` retrieve since the initial commit.
That class of bug is exactly what these tests are designed to catch.

Coverage areas:
  * `IsAuthenticated` gate — anonymous requests get 401/403.
  * `OrganizationScopedViewSet` — list and detail filter by current org.
  * Cross-tenant isolation — org A's user cannot see / read org B's rows
    even when they know the row's primary key.
  * Audit-log side effects on the password-specific actions
    (`retrieve`, `reveal`, `otp`) write the right rows.
  * The custom `reveal` and `otp` actions return the expected payload.

The tests use Django's session auth (force_login) to dodge the
django-axes auth backend; same pattern used by the tenant-isolation
tests in `core.tests.test_tenant_isolation` (rebuilt v3.17.171).
"""
from __future__ import annotations

from django.conf import settings as django_settings
from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings

from accounts.models import Membership, Role
from assets.models import Asset
from audit.models import AuditLog
from core.models import Organization
from vault.models import Password


TEST_MIDDLEWARE = [
    m for m in django_settings.MIDDLEWARE
    if 'Enforce2FAMiddleware' not in m and 'AxesMiddleware' not in m
]


def _login_in_org(client, user, org):
    """Force-login + pin the org on the test session. Mirrors the helper
    used in core.tests.test_tenant_isolation (v3.17.171 rebuild)."""
    client.force_login(user)
    s = client.session
    s['2fa_prompted'] = True
    s['current_organization_id'] = org.id
    s.save()


@override_settings(MIDDLEWARE=TEST_MIDDLEWARE, SECURE_SSL_REDIRECT=False)
class APIAuthGateTests(TestCase):
    """The DRF default permission class is `IsAuthenticated`. Anonymous
    requests must not reach any list endpoint."""

    def test_anonymous_passwords_list_blocked(self):
        c = Client()
        resp = c.get('/api/passwords/')
        self.assertIn(resp.status_code, (401, 403))

    def test_anonymous_assets_list_blocked(self):
        c = Client()
        resp = c.get('/api/assets/')
        self.assertIn(resp.status_code, (401, 403))


@override_settings(MIDDLEWARE=TEST_MIDDLEWARE, SECURE_SSL_REDIRECT=False)
class OrganizationScopedListFilteringTests(TestCase):
    """`OrganizationScopedViewSet.get_queryset` filters every list endpoint
    by `request.current_organization`. Org A's user must see only org A's
    rows."""

    def setUp(self):
        self.org_a = Organization.objects.create(name='OrgA-API', slug='orga-api')
        self.org_b = Organization.objects.create(name='OrgB-API', slug='orgb-api')

        self.user_a = User.objects.create_user('apiuser-a', password='pw', email='a@x.com')
        Membership.objects.create(
            user=self.user_a, organization=self.org_a, role=Role.OWNER, is_active=True,
        )

        # Two assets in each org so we can confirm filtering by count.
        for i in range(2):
            Asset.objects.create(
                organization=self.org_a, name=f'A-asset-{i}', asset_type='server',
            )
            Asset.objects.create(
                organization=self.org_b, name=f'B-asset-{i}', asset_type='server',
            )

        self.client = Client()
        _login_in_org(self.client, self.user_a, self.org_a)

    def test_assets_list_filters_to_user_org(self):
        resp = self.client.get('/api/assets/')
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        rows = data['results'] if isinstance(data, dict) and 'results' in data else data
        self.assertEqual(len(rows), 2)
        for row in rows:
            self.assertTrue(row['name'].startswith('A-asset-'))

    def test_search_finds_by_mac_address(self):
        """Phase 21 v3/v4 (v3.17.318): scanning a MAC barcode → search."""
        Asset.objects.create(
            organization=self.org_a, name='switch-01', asset_type='switch',
            mac_address='00:11:22:33:44:55',
        )
        resp = self.client.get('/api/assets/?search=00:11:22:33:44:55')
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        rows = data['results'] if isinstance(data, dict) and 'results' in data else data
        names = [r['name'] for r in rows]
        self.assertIn('switch-01', names)

    def test_search_finds_by_ip_address(self):
        """Phase 21 v3/v4 (v3.17.318): scanning an IP barcode → search."""
        Asset.objects.create(
            organization=self.org_a, name='router-99', asset_type='router',
            ip_address='10.99.0.1',
        )
        resp = self.client.get('/api/assets/?search=10.99.0.1')
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        rows = data['results'] if isinstance(data, dict) and 'results' in data else data
        names = [r['name'] for r in rows]
        self.assertIn('router-99', names)

    def test_search_still_finds_by_serial(self):
        """Sanity: pre-v3.17.318 search-by-serial still works."""
        Asset.objects.create(
            organization=self.org_a, name='sn-asset', asset_type='server',
            serial_number='ABC-1234',
        )
        resp = self.client.get('/api/assets/?search=ABC-1234')
        rows = resp.json()
        rows = rows.get('results', rows) if isinstance(rows, dict) else rows
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['name'], 'sn-asset')


@override_settings(MIDDLEWARE=TEST_MIDDLEWARE, SECURE_SSL_REDIRECT=False)
class CrossTenantDetailIsolationTests(TestCase):
    """A row's primary key being known doesn't grant access. Org A's user
    GET on org B's password row returns 404, not 200."""

    def setUp(self):
        self.org_a = Organization.objects.create(name='OrgA-x', slug='orga-x')
        self.org_b = Organization.objects.create(name='OrgB-x', slug='orgb-x')

        self.user_a = User.objects.create_user('xuser-a', password='pw', email='ax@x.com')
        Membership.objects.create(
            user=self.user_a, organization=self.org_a, role=Role.OWNER, is_active=True,
        )

        self.password_b = Password.objects.create(
            organization=self.org_b, title='Org B secret',
            username='svc', password_type='server',
        )
        self.password_a = Password.objects.create(
            organization=self.org_a, title='Org A own',
            username='svc', password_type='server',
        )

        self.client = Client()
        _login_in_org(self.client, self.user_a, self.org_a)

    def test_cross_org_detail_returns_404(self):
        resp = self.client.get(f'/api/passwords/{self.password_b.id}/')
        self.assertEqual(resp.status_code, 404)

    def test_own_org_detail_returns_200(self):
        resp = self.client.get(f'/api/passwords/{self.password_a.id}/')
        self.assertEqual(resp.status_code, 200)


@override_settings(MIDDLEWARE=TEST_MIDDLEWARE, SECURE_SSL_REDIRECT=False)
class PasswordEndpointAuditTrailTests(TestCase):
    """The password-specific actions (`retrieve`, `reveal`, `otp`) write
    AuditLog rows. The v3.17.171 fix that triggered this whole test
    file was an `AuditLog.objects.create(... details=...)` bug — these
    tests guard against regressing it."""

    def setUp(self):
        self.org = Organization.objects.create(name='AuditAPI', slug='audit-api')
        self.user = User.objects.create_user('audituser', password='pw', email='au@x.com')
        Membership.objects.create(
            user=self.user, organization=self.org, role=Role.OWNER, is_active=True,
        )
        self.password = Password.objects.create(
            organization=self.org, title='auditable',
            username='svc', password_type='server',
        )
        self.password.set_password('s3cret')
        self.password.save()
        self.client = Client()
        _login_in_org(self.client, self.user, self.org)

    def test_retrieve_writes_read_audit_row(self):
        resp = self.client.get(f'/api/passwords/{self.password.id}/')
        self.assertEqual(resp.status_code, 200)
        log = (AuditLog.objects
               .filter(action='read', object_type='password',
                       object_id=self.password.id)
               .order_by('-timestamp').first())
        self.assertIsNotNone(log, 'expected an audit row from retrieve')
        self.assertIn('via API', log.description)
        self.assertEqual(log.organization_id, self.org.id)
        self.assertEqual(log.user_id, self.user.id)

    def test_reveal_returns_password_and_writes_audit(self):
        resp = self.client.post(f'/api/passwords/{self.password.id}/reveal/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['password'], 's3cret')
        log = (AuditLog.objects
               .filter(action='reveal', object_type='password',
                       object_id=self.password.id)
               .order_by('-timestamp').first())
        self.assertIsNotNone(log)
        self.assertIn('revealed via API', log.description)

    def test_retrieve_does_not_crash_on_audit_log_create(self):
        """Regression guard for the v3.17.171 bug: AuditLog.objects.create
        was being called with ``details=...`` (the wrong kwarg name) and
        raised TypeError on every successful retrieve. Now that the kwarg
        is ``description=...``, the request must return 200 cleanly."""
        resp = self.client.get(f'/api/passwords/{self.password.id}/')
        self.assertEqual(resp.status_code, 200)


@override_settings(MIDDLEWARE=TEST_MIDDLEWARE, SECURE_SSL_REDIRECT=False)
class PasswordOTPEndpointTests(TestCase):
    """The OTP action returns 400 for non-OTP password types and 200
    when the entry has an OTP secret."""

    def setUp(self):
        self.org = Organization.objects.create(name='OtpAPI', slug='otp-api')
        self.user = User.objects.create_user('otpuser', password='pw', email='ou@x.com')
        Membership.objects.create(
            user=self.user, organization=self.org, role=Role.OWNER, is_active=True,
        )
        self.client = Client()
        _login_in_org(self.client, self.user, self.org)

    def test_otp_on_non_otp_entry_returns_400(self):
        non_otp = Password.objects.create(
            organization=self.org, title='regular',
            username='svc', password_type='server',
        )
        resp = self.client.get(f'/api/passwords/{non_otp.id}/otp/')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('Not an OTP entry', resp.json().get('error', ''))


@override_settings(MIDDLEWARE=TEST_MIDDLEWARE, SECURE_SSL_REDIRECT=False)
class OrganizationViewSetTests(TestCase):
    """`OrganizationViewSet` is a `ReadOnlyModelViewSet` — POST / PUT /
    DELETE must be rejected even by the org owner. List should return
    the orgs the user is a member of."""

    def setUp(self):
        self.org = Organization.objects.create(name='ReadOnlyOrg', slug='ro-org')
        self.user = User.objects.create_user('rouser', password='pw', email='ro@x.com')
        Membership.objects.create(
            user=self.user, organization=self.org, role=Role.OWNER, is_active=True,
        )
        self.client = Client()
        _login_in_org(self.client, self.user, self.org)

    def test_post_to_organizations_endpoint_rejected(self):
        resp = self.client.post('/api/organizations/', {'name': 'Hijack', 'slug': 'hijack'})
        # ReadOnlyModelViewSet returns 405 Method Not Allowed for writes.
        self.assertEqual(resp.status_code, 405)

    def test_get_organizations_list_returns_200(self):
        resp = self.client.get('/api/organizations/')
        self.assertEqual(resp.status_code, 200)
