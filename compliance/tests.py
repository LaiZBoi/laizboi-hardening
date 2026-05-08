"""Tests for Phase 39 compliance evidence packs."""
from django.conf import settings as django_settings
from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings


TEST_MIDDLEWARE = [
    m for m in django_settings.MIDDLEWARE
    if 'Enforce2FAMiddleware' not in m and 'AxesMiddleware' not in m
]


@override_settings(MIDDLEWARE=TEST_MIDDLEWARE, SECURE_SSL_REDIRECT=False)
class EvidencePackTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        from accounts.models import Membership, Role
        from core.models import Organization
        cls.org_a = Organization.objects.create(name='OrgA', slug='cep-a')
        cls.org_b = Organization.objects.create(name='OrgB', slug='cep-b')
        cls.owner_a = User.objects.create_user('cep-owner-a', 'a@x.com', 'pw',
                                                first_name='AOwner')
        cls.owner_b = User.objects.create_user('cep-owner-b', 'b@x.com', 'pw',
                                                first_name='BOwner')
        cls.staff = User.objects.create_user('cep-staff', 's@x.com', 'pw',
                                              is_superuser=True, is_staff=True)
        Membership.objects.create(user=cls.owner_a, organization=cls.org_a,
                                  role=Role.OWNER, is_active=True)
        Membership.objects.create(user=cls.owner_b, organization=cls.org_b,
                                  role=Role.OWNER, is_active=True)

    def _login(self, c, user, org=None):
        c.force_login(user)
        s = c.session
        s['2fa_prompted'] = True
        if org is not None:
            s['current_organization_id'] = org.id
        s.save()

    def test_owner_gets_200_html_with_org_name(self):
        c = Client()
        self._login(c, self.owner_a, self.org_a)
        r = c.get(f'/compliance/organizations/{self.org_a.id}/evidence-pack/')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'OrgA')
        self.assertContains(r, 'Two-Factor Authentication Status')
        self.assertContains(r, 'User Access Report')
        self.assertContains(r, 'Password Access History')
        self.assertContains(r, 'Asset Inventory')
        self.assertContains(r, 'Ticket / SLA History')
        # v3.17.226 — Phase 39 v2 sections
        self.assertContains(r, 'SSL / Domain Expiration')
        self.assertContains(r, 'Uptime Evidence')
        self.assertContains(r, 'Vulnerability Summary')
        self.assertContains(r, 'Backup Evidence')

    def test_owner_gets_200_zip(self):
        c = Client()
        self._login(c, self.owner_a, self.org_a)
        r = c.get(f'/compliance/organizations/{self.org_a.id}/evidence-pack/?format=zip')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r['Content-Type'], 'application/zip')
        self.assertIn('attachment', r['Content-Disposition'])
        import io
        import json
        import zipfile
        zf = zipfile.ZipFile(io.BytesIO(r.content))
        names = zf.namelist()
        self.assertIn('manifest.json', names)
        for section in ('two_factor', 'user_access', 'password_history',
                        'asset_inventory', 'ticket_sla',
                        'ssl_domain', 'uptime', 'vulnerabilities', 'backups'):
            self.assertIn(f'{section}.csv', names)
        manifest = json.loads(zf.read('manifest.json').decode('utf-8'))
        self.assertEqual(manifest['organization'], 'OrgA')
        self.assertEqual(len(manifest['sections']), 9)

    def test_other_org_owner_gets_404(self):
        c = Client()
        self._login(c, self.owner_b, self.org_b)
        r = c.get(f'/compliance/organizations/{self.org_a.id}/evidence-pack/')
        self.assertEqual(r.status_code, 404)

    def test_anonymous_redirected_to_login(self):
        c = Client()
        r = c.get(f'/compliance/organizations/{self.org_a.id}/evidence-pack/')
        self.assertIn(r.status_code, [302, 401])

    def test_superuser_can_access_any_org(self):
        c = Client()
        self._login(c, self.staff, self.org_b)
        r = c.get(f'/compliance/organizations/{self.org_a.id}/evidence-pack/')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'OrgA')

    def test_missing_org_returns_404(self):
        c = Client()
        self._login(c, self.staff)
        r = c.get('/compliance/organizations/99999/evidence-pack/')
        self.assertEqual(r.status_code, 404)

    def test_tenant_scope_user_access(self):
        # OrgB has owner_b; that user must NOT show in OrgA's pack.
        c = Client()
        self._login(c, self.owner_a, self.org_a)
        r = c.get(f'/compliance/organizations/{self.org_a.id}/evidence-pack/')
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, 'cep-owner-b')

    def test_tenant_scope_assets(self):
        from assets.models import Asset
        Asset.objects.create(organization=self.org_b, name='OrgBSerialUnique',
                             asset_type='other', serial_number='B-SECRET-SN-12345')
        c = Client()
        self._login(c, self.owner_a, self.org_a)
        r = c.get(f'/compliance/organizations/{self.org_a.id}/evidence-pack/')
        self.assertNotContains(r, 'B-SECRET-SN-12345')

    def test_tenant_scope_password_history(self):
        from audit.models import AuditLog
        AuditLog.log(
            user=self.owner_b, action='read',
            organization=self.org_b,
            object_type='password', object_id=99,
            object_repr='Cross-tenant secret name',
            description='reveal',
        )
        c = Client()
        self._login(c, self.owner_a, self.org_a)
        r = c.get(f'/compliance/organizations/{self.org_a.id}/evidence-pack/')
        self.assertNotContains(r, 'Cross-tenant secret name')

    def test_v226_sections_tenant_scoped(self):
        # v3.17.226: SSL/uptime + vuln sections are tenant-scoped.
        # Create OrgB-only WebsiteMonitor + SecurityAlert; verify they
        # do not leak into OrgA's pack.
        from monitoring.models import WebsiteMonitor
        from security_alerts.models import SecurityAlert, SecurityVendorConnection
        WebsiteMonitor.objects.create(
            organization=self.org_b, name='OrgB-Monitor-XYZ',
            url='https://orgb.example.com',
        )
        conn = SecurityVendorConnection.objects.create(
            organization=self.org_b, name='OrgB Vendor',
            provider='huntress', category='edr',
            base_url='https://example.invalid/', is_active=True,
        )
        SecurityAlert.objects.create(
            connection=conn, organization=self.org_b, client_org=self.org_b,
            external_id='orgb-vuln-1',
            title='OrgB-only-CVE-99999',
            severity='critical', status='new',
        )
        c = Client()
        self._login(c, self.owner_a, self.org_a)
        r = c.get(f'/compliance/organizations/{self.org_a.id}/evidence-pack/')
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, 'OrgB-Monitor-XYZ')
        self.assertNotContains(r, 'OrgB-only-CVE-99999')

    def test_audit_log_written_on_generate(self):
        from audit.models import AuditLog
        before = AuditLog.objects.filter(
            organization=self.org_a,
            object_type='compliance.EvidencePack',
        ).count()
        c = Client()
        self._login(c, self.owner_a, self.org_a)
        r = c.get(f'/compliance/organizations/{self.org_a.id}/evidence-pack/')
        self.assertEqual(r.status_code, 200)
        after = AuditLog.objects.filter(
            organization=self.org_a,
            object_type='compliance.EvidencePack',
        ).count()
        self.assertEqual(after, before + 1)


class ComplianceFrameworkModelTests(TestCase):
    def test_framework_create_and_str(self):
        from compliance.models import ComplianceFramework
        f = ComplianceFramework.objects.create(
            slug='pci-dss-v4', name='PCI-DSS', version='v4.0',
            description='Payment Card Industry Data Security Standard',
        )
        self.assertEqual(str(f), 'PCI-DSS v4.0')
        self.assertEqual(f.recertification_default_days, 365)

    def test_category_and_item_chain(self):
        from compliance.models import (
            ComplianceFramework, ComplianceCategory, ComplianceCheckItem,
        )
        f = ComplianceFramework.objects.create(slug='hipaa', name='HIPAA')
        cat = ComplianceCategory.objects.create(
            framework=f, slug='admin', name='Administrative Safeguards', order=1,
        )
        item = ComplianceCheckItem.objects.create(
            category=cat, slug='164-308-a-1', name='Security Management Process',
            description='Policies and procedures to prevent, detect, contain, and correct security violations.',
            evidence_hint='Risk assessment, sanction policy, info system activity review.',
            order=1,
        )
        self.assertEqual(item.category.framework, f)
        self.assertIn('Administrative', str(item))


class OrganizationComplianceModelTests(TestCase):
    def setUp(self):
        from core.models import Organization
        from compliance.models import (
            ComplianceFramework, ComplianceCategory, ComplianceCheckItem,
        )
        self.org = Organization.objects.create(name='Test Co')
        self.fw = ComplianceFramework.objects.create(slug='pci', name='PCI-DSS')
        cat = ComplianceCategory.objects.create(
            framework=self.fw, slug='r1', name='Network', order=1,
        )
        self.item1 = ComplianceCheckItem.objects.create(
            category=cat, slug='1-1', name='Item 1', order=1,
        )
        self.item2 = ComplianceCheckItem.objects.create(
            category=cat, slug='1-2', name='Item 2', order=2,
        )

    def test_org_enrollment_and_status_counts(self):
        from compliance.models import (
            OrganizationCompliance, OrganizationComplianceItem,
        )
        oc = OrganizationCompliance.objects.create(
            organization=self.org, framework=self.fw,
        )
        OrganizationComplianceItem.objects.create(
            org_compliance=oc, item=self.item1, status='compliant',
        )
        OrganizationComplianceItem.objects.create(
            org_compliance=oc, item=self.item2, status='unanswered',
        )
        counts = oc.status_counts()
        self.assertEqual(counts['total'], 2)
        self.assertEqual(counts['compliant'], 1)
        self.assertEqual(counts['unanswered'], 1)
        self.assertEqual(oc.percent_compliant(), 50)

    def test_recertification_due_date(self):
        from compliance.models import OrganizationCompliance
        oc = OrganizationCompliance.objects.create(
            organization=self.org, framework=self.fw,
            recertification_interval_days=30,
        )
        # Days until recert: ~30 (just enrolled, last_recertified is None
        # so it uses enrolled_at).
        self.assertGreaterEqual(oc.days_until_recertification, 29)
        self.assertLessEqual(oc.days_until_recertification, 30)

    def test_unique_per_org_per_framework(self):
        from compliance.models import OrganizationCompliance
        OrganizationCompliance.objects.create(
            organization=self.org, framework=self.fw,
        )
        with self.assertRaises(Exception):
            OrganizationCompliance.objects.create(
                organization=self.org, framework=self.fw,
            )
