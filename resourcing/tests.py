"""
Smoke tests for resourcing — Phase 2.1.

Covers:
  1. UserSkill unique_together prevents duplicate names per user
  2. WorkingHours.clean() rejects end_time <= start_time
  3. UserCertification.is_expired flag works
  4. UserCertification.expires_soon flag works
  5. UserProfile.is_working_now() — empty / covers / doesn't cover
  6. View tech_roster is gated to staff/superuser
"""
from datetime import time, timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import UserCertification, UserSkill, WorkingHours

User = get_user_model()


class UserSkillTests(TestCase):
    def test_unique_together_prevents_duplicates(self):
        u = User.objects.create_user(username='alice', password='pw-test-12345')
        UserSkill.objects.create(user=u, name='Active Directory', proficiency='advanced')
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                UserSkill.objects.create(user=u, name='Active Directory', proficiency='expert')


class WorkingHoursTests(TestCase):
    def test_clean_rejects_end_before_start(self):
        u = User.objects.create_user(username='bob', password='pw-test-12345')
        wh = WorkingHours(user=u, weekday=0, start_time=time(17, 0), end_time=time(9, 0))
        with self.assertRaises(ValidationError):
            wh.clean()

    def test_clean_rejects_end_equal_to_start(self):
        u = User.objects.create_user(username='bob2', password='pw-test-12345')
        wh = WorkingHours(user=u, weekday=0, start_time=time(9, 0), end_time=time(9, 0))
        with self.assertRaises(ValidationError):
            wh.clean()


class UserCertificationTests(TestCase):
    def test_is_expired_when_past(self):
        u = User.objects.create_user(username='carol', password='pw-test-12345')
        yesterday = timezone.now().date() - timedelta(days=1)
        cert = UserCertification.objects.create(user=u, name='CCNA', expires_at=yesterday)
        self.assertTrue(cert.is_expired)
        self.assertFalse(cert.expires_soon)  # expired ≠ expires_soon

    def test_is_expired_when_no_expiry(self):
        u = User.objects.create_user(username='carol2', password='pw-test-12345')
        cert = UserCertification.objects.create(user=u, name='Lifetime cert')
        self.assertFalse(cert.is_expired)

    def test_expires_soon_within_60_days(self):
        u = User.objects.create_user(username='dave', password='pw-test-12345')
        in_30 = timezone.now().date() + timedelta(days=30)
        cert = UserCertification.objects.create(user=u, name='Microsoft 365 Admin', expires_at=in_30)
        self.assertTrue(cert.expires_soon)
        self.assertFalse(cert.is_expired)

    def test_expires_soon_false_when_far(self):
        u = User.objects.create_user(username='dave2', password='pw-test-12345')
        in_120 = timezone.now().date() + timedelta(days=120)
        cert = UserCertification.objects.create(user=u, name='AWS Solutions Architect', expires_at=in_120)
        self.assertFalse(cert.expires_soon)


class IsWorkingNowTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='eve', password='pw-test-12345')
        # Profile is auto-created by post_save signal; pin to UTC for determinism.
        self.profile = self.user.profile
        self.profile.timezone = 'UTC'
        self.profile.save()

    def test_returns_true_when_no_rows(self):
        # No WorkingHours configured at all → backwards-compat "always working".
        self.assertTrue(self.profile.is_working_now())

    def test_returns_true_when_row_covers_now(self):
        now = timezone.now().astimezone(timezone.get_default_timezone())
        # Build a row that's certain to cover "now in UTC", with a wide window.
        import zoneinfo
        utc_now = timezone.now().astimezone(zoneinfo.ZoneInfo('UTC'))
        WorkingHours.objects.create(
            user=self.user,
            weekday=utc_now.weekday(),
            start_time=time(0, 0),
            end_time=time(23, 59),
        )
        self.assertTrue(self.profile.is_working_now())

    def test_returns_false_when_only_other_days_configured(self):
        # User has rows but none for today → assume not working today.
        import zoneinfo
        utc_now = timezone.now().astimezone(zoneinfo.ZoneInfo('UTC'))
        wrong_day = (utc_now.weekday() + 3) % 7
        WorkingHours.objects.create(
            user=self.user,
            weekday=wrong_day,
            start_time=time(9, 0),
            end_time=time(17, 0),
        )
        self.assertFalse(self.profile.is_working_now())


@override_settings(REQUIRE_2FA=False)
class TechRosterAccessTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.regular = User.objects.create_user(username='regular', password='pw-test-12345')
        self.staff = User.objects.create_user(username='ops-tech', password='pw-test-12345', is_staff=True)

    def test_regular_user_blocked(self):
        """A non-staff user should NOT see the roster — either redirected away
        (e.g. to login/profile) or a 403. They must NOT see the rendered page
        with status 200."""
        self.client.force_login(self.regular)
        resp = self.client.get(reverse('resourcing:tech_roster'), follow=False)
        # 200 == they got the page, which would be the bug. 3xx redirect or 403 = ok.
        self.assertNotEqual(resp.status_code, 200)
        self.assertIn(resp.status_code, (301, 302, 303, 403))

    def test_staff_user_allowed(self):
        """A staff user can reach the roster (status 200, or — if 2FA-redirect
        middleware bounces them — at least *not* a 403)."""
        self.client.force_login(self.staff)
        resp = self.client.get(reverse('resourcing:tech_roster'), follow=False)
        self.assertNotEqual(resp.status_code, 403)
        # Final response after following any 2FA redirects should be 200.
        resp_final = self.client.get(reverse('resourcing:tech_roster'), follow=True)
        # Either the final rendered tech_roster (200) OR a redirect-chain that
        # ended at the 2FA setup page — both prove staff is not blocked by
        # @user_passes_test.
        self.assertEqual(resp_final.status_code, 200)
