"""
Tests for accounts permission utilities + RoleTemplate boolean gates.

v3.17.145 — adds Reports + financials permission groups (RoleTemplate
booleans) and the `accounts.permission_utils.user_has_perm/require_perm`
helpers that mirror the v3.17.134 KB pattern.
"""
from django.conf import settings as django_settings
from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings


# Mirror the test middleware setup from psa.tests so we bypass the 2FA
# enforcement middleware and django-axes lockouts.
TEST_MIDDLEWARE = [
    m for m in django_settings.MIDDLEWARE
    if 'Enforce2FAMiddleware' not in m and 'AxesMiddleware' not in m
]


@override_settings(MIDDLEWARE=TEST_MIDDLEWARE, SECURE_SSL_REDIRECT=False)
class ReportsPermissionTests(TestCase):
    """v3.17.145: report views gated by RoleTemplate booleans."""

    @classmethod
    def setUpTestData(cls):
        cls.tech = User.objects.create_user('tech', 't@x.com', 'pw')
        # By default an Editor membership = reports_view_financial=False
        cls.admin = User.objects.create_user('admin', 'a@x.com', 'pw',
                                              is_superuser=True, is_staff=True)

    def test_user_has_perm_returns_false_for_anon(self):
        from accounts.permission_utils import user_has_perm
        from django.contrib.auth.models import AnonymousUser
        self.assertFalse(user_has_perm(AnonymousUser(), 'reports_view_financial'))

    def test_user_has_perm_returns_true_for_superuser(self):
        from accounts.permission_utils import user_has_perm
        self.assertTrue(user_has_perm(self.admin, 'reports_view_financial'))
        self.assertTrue(user_has_perm(self.admin, 'reports_view_capacity'))
        self.assertTrue(user_has_perm(self.admin, 'resourcing_manage_cost_rates'))

    def test_user_has_perm_returns_false_for_user_without_membership(self):
        from accounts.permission_utils import user_has_perm
        # Plain user — no memberships, no role_template — gets nothing.
        self.assertFalse(user_has_perm(self.tech, 'reports_view_financial'))
        self.assertFalse(user_has_perm(self.tech, 'reports_view_dashboards'))

    def test_tech_blocked_from_financial_report(self):
        self.client.force_login(self.tech)
        r = self.client.get('/reports/psa/profitability-by-client/')
        self.assertIn(r.status_code, [302, 403])

    def test_superuser_passes(self):
        self.client.force_login(self.admin)
        r = self.client.get('/reports/psa/profitability-by-client/')
        self.assertEqual(r.status_code, 200)

    def test_role_template_grant_unblocks_tech(self):
        from accounts.models import Membership, RoleTemplate
        from core.models import Organization
        org = Organization.objects.create(name='X', slug='perm-x')
        rt = RoleTemplate.objects.create(
            name='AccountingTech',
            reports_view_dashboards=True,
            reports_view_financial=True,
        )
        Membership.objects.create(
            user=self.tech, organization=org,
            role='editor', role_template=rt, is_active=True,
        )
        self.client.force_login(self.tech)
        r = self.client.get('/reports/psa/profitability-by-client/')
        self.assertEqual(r.status_code, 200)

    def test_capacity_report_gated_on_view_capacity(self):
        self.client.force_login(self.tech)
        r = self.client.get('/resourcing/capacity/')
        self.assertIn(r.status_code, [302, 403])

    def test_capacity_report_unblocked_with_role_template(self):
        from accounts.models import Membership, RoleTemplate
        from core.models import Organization
        org = Organization.objects.create(name='Y', slug='perm-y')
        rt = RoleTemplate.objects.create(
            name='ManagerLite',
            reports_view_capacity=True,
        )
        Membership.objects.create(
            user=self.tech, organization=org,
            role='editor', role_template=rt, is_active=True,
        )
        self.client.force_login(self.tech)
        r = self.client.get('/resourcing/capacity/')
        self.assertEqual(r.status_code, 200)

    def test_tech_roster_gated_on_view_team(self):
        self.client.force_login(self.tech)
        r = self.client.get('/resourcing/roster/')
        self.assertIn(r.status_code, [302, 403])

    def test_tech_roster_unblocked_with_role_template(self):
        from accounts.models import Membership, RoleTemplate
        from core.models import Organization
        org = Organization.objects.create(name='Z', slug='perm-z')
        rt = RoleTemplate.objects.create(
            name='OpsLead',
            resourcing_view_team=True,
        )
        Membership.objects.create(
            user=self.tech, organization=org,
            role='editor', role_template=rt, is_active=True,
        )
        self.client.force_login(self.tech)
        r = self.client.get('/resourcing/roster/')
        self.assertEqual(r.status_code, 200)

    def test_sla_trends_gated_on_view_sla(self):
        self.client.force_login(self.tech)
        r = self.client.get('/reports/psa/sla-trends/')
        self.assertIn(r.status_code, [302, 403])

    def test_owner_membership_grants_financial_via_simple_role_fallback(self):
        """Membership with `role=owner` and no role_template still grants
        the new flags via the SimpleNamespace fallback."""
        from accounts.models import Membership, Role
        from core.models import Organization
        org = Organization.objects.create(name='Owners', slug='perm-own')
        Membership.objects.create(
            user=self.tech, organization=org,
            role=Role.OWNER, role_template=None, is_active=True,
        )
        self.client.force_login(self.tech)
        r = self.client.get('/reports/psa/profitability-by-client/')
        self.assertEqual(r.status_code, 200)


@override_settings(MIDDLEWARE=TEST_MIDDLEWARE, SECURE_SSL_REDIRECT=False,
                   EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class MemberWelcomeEmailTests(TestCase):
    """v3.17.214: send a welcome email when a user is added to an org."""

    def setUp(self):
        from django.core import mail
        from accounts.models import Membership, Role
        from core.models import Organization
        mail.outbox = []
        self.org = Organization.objects.create(name='WelcomeOrg', slug='welcome-org')
        self.owner = User.objects.create_user('owner', 'owner@x.com', 'pw',
                                              is_staff=True, is_superuser=True)
        Membership.objects.create(
            user=self.owner, organization=self.org,
            role=Role.OWNER, is_active=True,
        )
        self.invitee = User.objects.create_user('newbie', 'newbie@x.com', 'pw',
                                                 first_name='New')

    def test_send_member_welcome_email_helper(self):
        from django.core import mail
        from accounts.models import Membership, Role
        from accounts.views import send_member_welcome_email
        m = Membership.objects.create(
            user=self.invitee, organization=self.org,
            role=Role.EDITOR, is_active=True,
            invited_by=self.owner,
        )
        sent = send_member_welcome_email(m, request=None)
        self.assertTrue(sent)
        self.assertEqual(len(mail.outbox), 1)
        msg = mail.outbox[0]
        self.assertIn('WelcomeOrg', msg.subject)
        self.assertEqual(msg.to, ['newbie@x.com'])
        self.assertIn('WelcomeOrg', msg.body)

    def test_helper_skips_when_user_has_no_email(self):
        from django.core import mail
        from accounts.models import Membership, Role
        from accounts.views import send_member_welcome_email
        u = User.objects.create_user('noemail', '', 'pw')
        m = Membership.objects.create(
            user=u, organization=self.org,
            role=Role.READONLY, is_active=True,
        )
        sent = send_member_welcome_email(m, request=None)
        self.assertFalse(sent)
        self.assertEqual(len(mail.outbox), 0)

    def test_member_add_view_sends_welcome(self):
        from django.core import mail
        self.client.force_login(self.owner)
        r = self.client.post(
            f'/accounts/organizations/{self.org.id}/members/add/',
            data={'email': 'newbie@x.com', 'role': 'editor'},
        )
        self.assertIn(r.status_code, [200, 302])
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('WelcomeOrg', mail.outbox[0].subject)
