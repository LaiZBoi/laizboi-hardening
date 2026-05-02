"""Tests for portal app — Phase 12 v2 portal announcements."""
from datetime import timedelta

from django.conf import settings as django_settings
from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings
from django.utils import timezone


TEST_MIDDLEWARE = [
    m for m in django_settings.MIDDLEWARE
    if 'Enforce2FAMiddleware' not in m and 'AxesMiddleware' not in m
]


@override_settings(MIDDLEWARE=TEST_MIDDLEWARE, SECURE_SSL_REDIRECT=False)
class PortalAnnouncementTests(TestCase):
    """v3.17.232: portal announcements rendered to portal users."""

    @classmethod
    def setUpTestData(cls):
        from accounts.models import Membership, Role
        from core.models import Organization, SystemSetting
        from portal.models import PortalAnnouncement
        from psa.models import ClientPSASettings

        s = SystemSetting.get_settings()
        s.psa_enabled = True
        s.save()
        cls.org = Organization.objects.create(name='PortalCo', slug='portal-co')
        cls.other_org = Organization.objects.create(name='OtherCo', slug='portal-other')
        ClientPSASettings.objects.create(organization=cls.org, portal_enabled=True)
        ClientPSASettings.objects.create(organization=cls.other_org, portal_enabled=True)

        cls.user = User.objects.create_user('portal-user', 'p@x.com', 'pw')
        Membership.objects.create(user=cls.user, organization=cls.org,
                                   role=Role.READONLY, is_active=True)
        cls.outsider = User.objects.create_user('portal-outsider', 'o@x.com', 'pw')
        Membership.objects.create(user=cls.outsider, organization=cls.other_org,
                                   role=Role.READONLY, is_active=True)

        cls.active = PortalAnnouncement.objects.create(
            organization=cls.org, title='Maintenance window', body='Saturday 2am',
            severity='warning',
        )
        cls.inactive = PortalAnnouncement.objects.create(
            organization=cls.org, title='Old draft', body='Hidden',
            severity='info', is_active=False,
        )
        cls.expired = PortalAnnouncement.objects.create(
            organization=cls.org, title='Past notice', body='gone',
            severity='info',
            expires_at=timezone.now() - timedelta(days=1),
        )
        cls.other_org_ann = PortalAnnouncement.objects.create(
            organization=cls.other_org, title='OtherCo only', body='secret',
            severity='info',
        )
        cls.critical = PortalAnnouncement.objects.create(
            organization=cls.org, title='Critical breach', body='all hands',
            severity='danger', is_dismissable=False,
        )

    def _login(self, c, user):
        c.force_login(user)
        s = c.session
        s['2fa_prompted'] = True
        s.save()

    def test_active_for_org_filters_inactive_and_expired(self):
        from portal.models import PortalAnnouncement
        active = list(PortalAnnouncement.active_for_org(self.org).values_list('title', flat=True))
        self.assertIn('Maintenance window', active)
        self.assertIn('Critical breach', active)
        self.assertNotIn('Old draft', active)
        self.assertNotIn('Past notice', active)

    def test_portal_home_shows_active_announcement(self):
        c = Client()
        self._login(c, self.user)
        r = c.get('/portal/')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Maintenance window')
        self.assertContains(r, 'Critical breach')
        self.assertNotContains(r, 'Old draft')
        self.assertNotContains(r, 'Past notice')

    def test_portal_home_does_not_leak_other_orgs_announcement(self):
        c = Client()
        self._login(c, self.user)
        r = c.get('/portal/')
        self.assertNotContains(r, 'OtherCo only')

    def test_dismiss_endpoint_filters_announcement_from_subsequent_renders(self):
        c = Client()
        self._login(c, self.user)
        r = c.post(f'/portal/announcement/{self.active.pk}/dismiss/')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['ok'], True)
        # Subsequent home render should not include the dismissed one,
        # but should still include the non-dismissable critical one.
        r = c.get('/portal/')
        self.assertNotContains(r, 'Maintenance window')
        self.assertContains(r, 'Critical breach')

    def test_dismiss_endpoint_rejects_non_dismissable(self):
        c = Client()
        self._login(c, self.user)
        r = c.post(f'/portal/announcement/{self.critical.pk}/dismiss/')
        self.assertEqual(r.status_code, 400)

    def test_dismiss_endpoint_404s_cross_org(self):
        c = Client()
        self._login(c, self.user)
        # The user can't dismiss OtherCo's announcement.
        r = c.post(f'/portal/announcement/{self.other_org_ann.pk}/dismiss/')
        self.assertEqual(r.status_code, 404)

    def test_critical_announcement_has_no_dismiss_button(self):
        c = Client()
        self._login(c, self.user)
        r = c.get('/portal/')
        body = r.content.decode('utf-8')
        # The critical announcement is rendered, but its alert should NOT
        # contain the dismiss-button class.
        self.assertIn('Critical breach', body)
        # Find the critical announcement's div (data-announcement-id) and
        # verify there's no js-dismiss-announcement inside it.
        import re
        match = re.search(
            r'data-announcement-id="' + str(self.critical.pk) + r'"(.*?)</div>',
            body, re.DOTALL,
        )
        self.assertIsNotNone(match)
        self.assertNotIn('js-dismiss-announcement', match.group(1))
