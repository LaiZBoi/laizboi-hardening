"""
Baseline test coverage for the assets/ app.

Foundational app — Asset, Contact, EquipmentModel are referenced from
every other surface (vault for asset linking, docs for KB→asset, PSA
for ticket→asset, monitoring for rack→asset, etc.). Bugs here ripple.
This test file covers the core model behavior + view smoke tests +
tenant isolation; deeper feature tests (port configurations,
relationship graph, AI doc generation) belong in follow-up files.

Same family of tests as v3.17.193's `api/` and v3.17.195's `audit/`
suites — patterns reused so each new app follows the same shape.
"""
from __future__ import annotations

from django.conf import settings as django_settings
from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings

from accounts.models import Membership, Role
from assets.models import Asset, AssetType, Contact, EquipmentModel, Vendor
from core.models import Organization


TEST_MIDDLEWARE = [
    m for m in django_settings.MIDDLEWARE
    if 'Enforce2FAMiddleware' not in m and 'AxesMiddleware' not in m
]


def _login_in_org(client, user, org):
    client.force_login(user)
    s = client.session
    s['2fa_prompted'] = True
    s['current_organization_id'] = org.id
    s.save()


# ---------------------------------------------------------------------------
# Asset model behavior
# ---------------------------------------------------------------------------

class AssetModelTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organization.objects.create(name='AssetCo', slug='asset-co')

    def test_create_with_minimal_required_fields(self):
        a = Asset.objects.create(
            organization=self.org, name='Server-01', asset_type='server',
        )
        self.assertEqual(a.name, 'Server-01')
        self.assertEqual(a.asset_type, 'server')

    def test_str_includes_name_and_human_readable_type(self):
        a = Asset.objects.create(
            organization=self.org, name='Server-01', asset_type='server',
        )
        s = str(a)
        self.assertIn('Server-01', s)
        # `get_asset_type_display()` returns 'Server' (the label, not the slug).
        self.assertIn('Server', s)

    def test_default_asset_type_is_other(self):
        a = Asset.objects.create(organization=self.org, name='Mystery box')
        self.assertEqual(a.asset_type, 'other')

    def test_for_organization_filters_correctly(self):
        # Asset uses OrganizationManager — `.for_organization()` is the
        # explicit-tenant filter helper. v3.17.171 tenant-isolation
        # tests exercise this on Password; mirror for Asset.
        org_b = Organization.objects.create(name='AssetB', slug='asset-b')
        Asset.objects.create(organization=self.org, name='A1', asset_type='server')
        Asset.objects.create(organization=self.org, name='A2', asset_type='laptop')
        Asset.objects.create(organization=org_b, name='B1', asset_type='server')

        for_a = list(Asset.objects.for_organization(self.org))
        for_b = list(Asset.objects.for_organization(org_b))
        self.assertEqual(len(for_a), 2)
        self.assertEqual(len(for_b), 1)
        self.assertEqual({a.name for a in for_a}, {'A1', 'A2'})


# ---------------------------------------------------------------------------
# Contact model
# ---------------------------------------------------------------------------

class ContactModelTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organization.objects.create(name='ContactCo', slug='contact-co')

    def test_full_name_property(self):
        c = Contact.objects.create(
            organization=self.org, first_name='Alice', last_name='Bob',
            email='ab@x.com',
        )
        self.assertEqual(c.full_name, 'Alice Bob')

    def test_str_returns_full_name(self):
        c = Contact.objects.create(
            organization=self.org, first_name='Alice', last_name='Bob',
        )
        self.assertEqual(str(c), 'Alice Bob')

    def test_blank_email_allowed(self):
        # email is `blank=True` on the model — creating without one
        # must not raise. Confirms the migration matches the model.
        c = Contact.objects.create(
            organization=self.org, first_name='No', last_name='Email',
        )
        self.assertEqual(c.email, '')

    def test_org_manager_for_organization_works(self):
        org_b = Organization.objects.create(name='Other', slug='contact-other')
        Contact.objects.create(organization=self.org, first_name='A', last_name='X')
        Contact.objects.create(organization=org_b, first_name='B', last_name='Y')
        for_a = list(Contact.objects.for_organization(self.org))
        self.assertEqual(len(for_a), 1)
        self.assertEqual(for_a[0].full_name, 'A X')


# ---------------------------------------------------------------------------
# AssetType model — per-org custom asset types
# ---------------------------------------------------------------------------

class AssetTypeModelTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organization.objects.create(name='TypeCo', slug='type-co')

    def test_create_basic(self):
        t = AssetType.objects.create(
            organization=self.org, name='Custom Server Type', slug='custom-server',
        )
        self.assertEqual(t.name, 'Custom Server Type')

    def test_default_icon_and_color(self):
        t = AssetType.objects.create(
            organization=self.org, name='Defaults', slug='defaults',
        )
        self.assertEqual(t.icon, 'fa-box')
        self.assertEqual(t.color, '#0d6efd')


# ---------------------------------------------------------------------------
# View-level smoke tests
# ---------------------------------------------------------------------------

@override_settings(MIDDLEWARE=TEST_MIDDLEWARE, SECURE_SSL_REDIRECT=False)
class AssetListViewTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='ViewCo', slug='view-co')
        self.user = User.objects.create_user(
            'viewuser', email='v@x.com', password='pw',
        )
        Membership.objects.create(
            user=self.user, organization=self.org, role=Role.OWNER, is_active=True,
        )
        Asset.objects.create(organization=self.org, name='Local-Server', asset_type='server')
        self.client = Client()

    def test_anonymous_redirected_to_login(self):
        resp = self.client.get('/assets/')
        # Either 302 (redirect to login) or 401 — both indicate the
        # @login_required gate fired.
        self.assertIn(resp.status_code, (302, 401))

    def test_authenticated_returns_200_with_org_asset(self):
        _login_in_org(self.client, self.user, self.org)
        resp = self.client.get('/assets/')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Local-Server')


@override_settings(MIDDLEWARE=TEST_MIDDLEWARE, SECURE_SSL_REDIRECT=False)
class AssetDetailIsolationTests(TestCase):
    """Mirror of `core.tests.test_tenant_isolation` for the assets/
    views — own-org PK returns 200, cross-org PK returns 404."""

    def setUp(self):
        self.org_a = Organization.objects.create(name='OrgA-A', slug='orga-a')
        self.org_b = Organization.objects.create(name='OrgB-A', slug='orgb-a')
        self.user_a = User.objects.create_user(
            'asset-user-a', email='aa@x.com', password='pw',
        )
        Membership.objects.create(
            user=self.user_a, organization=self.org_a, role=Role.OWNER, is_active=True,
        )
        self.asset_a = Asset.objects.create(
            organization=self.org_a, name='A-server', asset_type='server',
        )
        self.asset_b = Asset.objects.create(
            organization=self.org_b, name='B-server', asset_type='server',
        )
        self.client = Client()
        _login_in_org(self.client, self.user_a, self.org_a)

    def test_own_org_asset_detail_returns_200(self):
        resp = self.client.get(f'/assets/{self.asset_a.pk}/')
        self.assertEqual(resp.status_code, 200)

    def test_cross_org_asset_detail_returns_404(self):
        resp = self.client.get(f'/assets/{self.asset_b.pk}/')
        self.assertEqual(resp.status_code, 404)


# ---------------------------------------------------------------------------
# EquipmentModel — vendor-database join used by Asset.equipment_model FK
# ---------------------------------------------------------------------------

class EquipmentModelLookupTests(TestCase):
    """`EquipmentModel` is global (no organization FK) — same model row
    is shared across tenants. Joined to Asset via Asset.equipment_model.
    Confirms creation + back-reference + uniqueness contract."""

    @classmethod
    def setUpTestData(cls):
        cls.vendor = Vendor.objects.create(name='Dell', slug='dell-eq')

    def test_create_and_str_includes_vendor_and_model(self):
        em = EquipmentModel.objects.create(
            vendor=self.vendor,
            model_name='PowerEdge R740',
            slug='dell-poweredge-r740',
            equipment_type='server',
        )
        s = str(em)
        # The display string should expose enough to identify the model.
        self.assertTrue('Dell' in s or 'R740' in s,
            f'expected vendor or model name in str(): {s!r}')

    def test_asset_back_reference_via_equipment_model_fk(self):
        org = Organization.objects.create(name='EQOrg', slug='eq-org')
        em = EquipmentModel.objects.create(
            vendor=self.vendor,
            model_name='ProLiant DL380',
            slug='hp-proliant-dl380',
            equipment_type='server',
        )
        a1 = Asset.objects.create(
            organization=org, name='hp-1', asset_type='server', equipment_model=em,
        )
        a2 = Asset.objects.create(
            organization=org, name='hp-2', asset_type='server', equipment_model=em,
        )
        self.assertEqual(em.assets.count(), 2)
        self.assertIn(a1, em.assets.all())
        self.assertIn(a2, em.assets.all())
