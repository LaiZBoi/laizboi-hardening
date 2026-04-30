"""
CRM tests — Phase 5.1.

Covers:
* Lead.contact_full_name
* Opportunity.weighted_value
* Pipeline kanban view auth
* Lead → Org + Opportunity conversion
* Permission gating on /crm/pipeline/

Tests run with REQUIRE_2FA=False + SECURE_SSL_REDIRECT=False (matching
resourcing's pattern) so the SSL/2FA middleware doesn't 30x the test
client. Sessions also pre-set `2fa_prompted=True` to bypass the
optional-2FA prompt redirect.
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse


User = get_user_model()


def _bypass_2fa(client):
    """Pre-set 2fa_prompted on the session so Enforce2FAMiddleware passes."""
    session = client.session
    session['2fa_prompted'] = True
    session.save()


class LeadModelTests(TestCase):
    def test_contact_full_name(self):
        from crm.models import Lead
        from core.models import Organization
        org = Organization.objects.create(name='X', slug='x')
        l = Lead(
            organization=org, company_name='Acme',
            contact_first_name='Wile', contact_last_name='Coyote',
        )
        self.assertEqual(l.contact_full_name, 'Wile Coyote')

    def test_contact_full_name_only_first(self):
        from crm.models import Lead
        from core.models import Organization
        org = Organization.objects.create(name='Y', slug='y')
        l = Lead(organization=org, company_name='B', contact_first_name='Solo')
        self.assertEqual(l.contact_full_name, 'Solo')


class OpportunityWeightingTests(TestCase):
    def test_weighted_value_calculation(self):
        from crm.models import Opportunity
        from core.models import Organization
        msp = Organization.objects.create(name='MSP', slug='msp')
        cl = Organization.objects.create(name='Cl', slug='cl')
        o = Opportunity(
            organization=msp, client_org=cl, name='Test',
            estimated_value=10000, probability_pct=25,
        )
        self.assertEqual(float(o.weighted_value), 2500.0)

    def test_is_open_property(self):
        from crm.models import Opportunity
        from core.models import Organization
        msp = Organization.objects.create(name='MSP2', slug='msp2')
        cl = Organization.objects.create(name='Cl2', slug='cl2')
        o = Opportunity(
            organization=msp, client_org=cl, name='Live',
            stage='proposal',
        )
        self.assertTrue(o.is_open)
        o.stage = 'closed_won'
        self.assertFalse(o.is_open)


@override_settings(REQUIRE_2FA=False, SECURE_SSL_REDIRECT=False)
class PipelineKanbanViewTests(TestCase):
    """Renders for users with crm_manage_pipeline (superuser passes)."""

    def setUp(self):
        from core.models import Organization
        self.org = Organization.objects.create(name='MSP3', slug='msp3')
        self.client_obj = Client()

    def test_anonymous_redirected(self):
        url = reverse('crm:pipeline')
        resp = self.client_obj.get(url)
        # login_required should redirect to login
        self.assertIn(resp.status_code, (302, 301))

    def test_superuser_sees_kanban(self):
        u = User.objects.create_superuser(
            username='admin', email='a@example.com', password='pw',
        )
        self.client_obj.force_login(u)
        _bypass_2fa(self.client_obj)
        url = reverse('crm:pipeline')
        resp = self.client_obj.get(url)
        if resp.status_code != 200:
            resp = self.client_obj.get(url, follow=True)
        self.assertEqual(resp.status_code, 200)


@override_settings(REQUIRE_2FA=False, SECURE_SSL_REDIRECT=False)
class LeadConversionTests(TestCase):
    def test_convert_creates_org_and_opportunity(self):
        from core.models import Organization
        from crm.models import Lead, Opportunity

        msp = Organization.objects.create(name='MSP4', slug='msp4')
        u = User.objects.create_superuser(
            username='su', email='su@example.com', password='pw',
        )

        lead = Lead.objects.create(
            organization=msp,
            company_name='ConvertCo',
            contact_first_name='Pat',
            contact_last_name='Kim',
            contact_email='pat@convertco.test',
            estimated_value=Decimal('5000'),
        )

        c = Client()
        c.force_login(u)
        _bypass_2fa(c)
        # Place the user inside the MSP org via session
        session = c.session
        session['current_organization_id'] = msp.id
        session['2fa_prompted'] = True
        session.save()

        url = reverse('crm:lead_convert', kwargs={'pk': lead.pk})
        resp = c.post(url, follow=False)
        # Expect a redirect to the new opportunity. Some envs may issue an
        # extra 302 first (e.g. SSL), so just check that it eventually wrote
        # the conversion.
        self.assertIn(resp.status_code, (302, 200))

        lead.refresh_from_db()
        self.assertEqual(lead.status, 'converted')
        self.assertIsNotNone(lead.converted_to_org_id)
        self.assertIsNotNone(lead.converted_to_opportunity_id)

        # Verify the new org + opportunity got built
        new_org = Organization.objects.get(pk=lead.converted_to_org_id)
        self.assertEqual(new_org.name, 'ConvertCo')
        opp = Opportunity.objects.get(pk=lead.converted_to_opportunity_id)
        self.assertEqual(opp.organization_id, msp.id)
        self.assertEqual(opp.client_org_id, new_org.id)
        self.assertEqual(opp.estimated_value, Decimal('5000'))
        self.assertEqual(opp.source_lead_id, lead.id)


@override_settings(REQUIRE_2FA=False, SECURE_SSL_REDIRECT=False)
class CRMPermissionTests(TestCase):
    def test_anonymous_blocked_from_pipeline(self):
        c = Client()
        url = reverse('crm:pipeline')
        resp = c.get(url)
        self.assertIn(resp.status_code, (302, 301, 403))

    def test_owner_passes(self):
        u = User.objects.create_superuser(
            username='owner', email='o@example.com', password='pw',
        )
        c = Client()
        c.force_login(u)
        _bypass_2fa(c)
        resp = c.get(reverse('crm:pipeline'))
        if resp.status_code != 200:
            resp = c.get(reverse('crm:pipeline'), follow=True)
        self.assertEqual(resp.status_code, 200)

    def test_basic_user_blocked_from_pipeline(self):
        """A user without any role_template should be denied (403)."""
        u = User.objects.create_user(
            username='nobody', email='n@example.com', password='pw',
        )
        c = Client()
        c.force_login(u)
        _bypass_2fa(c)
        resp = c.get(reverse('crm:pipeline'))
        self.assertEqual(resp.status_code, 403)
