"""
Baseline test coverage for the docs/ app.

Knowledge base + Diagrams. KB articles surface to clients via the
portal — bug here can leak internal docs externally OR break the
slug routing on customer-visible URLs. Every other app links here
(PSA→KB-link, processes→linked_document, vault→linked_document).

Coverage areas:
  * `Document.save()` slug auto-generation; version snapshots on
    update.
  * `DocumentCategory` slug auto-generation.
  * `Diagram.save()` slug auto-generation.
  * Tenant-isolation contract via `organization` FK.
  * `is_global` for cross-tenant KB articles.
"""
from __future__ import annotations

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from core.models import Organization
from docs.models import (
    Diagram,
    Document,
    DocumentCategory,
    DocumentVersion,
)


class DocumentCategoryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organization.objects.create(name='DocCo', slug='doc-co')

    def test_slug_auto_generated_from_name(self):
        cat = DocumentCategory.objects.create(
            organization=self.org, name='Network Documentation',
        )
        self.assertEqual(cat.slug, 'network-documentation')

    def test_explicit_slug_preserved(self):
        cat = DocumentCategory.objects.create(
            organization=self.org, name='X', slug='custom-slug',
        )
        self.assertEqual(cat.slug, 'custom-slug')

    def test_str_includes_name(self):
        cat = DocumentCategory.objects.create(
            organization=self.org, name='Onboarding',
        )
        self.assertIn('Onboarding', str(cat))


class DocumentSlugTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organization.objects.create(name='SlugCo', slug='slug-co')
        cls.user = User.objects.create_user('doc-user', email='d@x.com', password='pw')

    def test_slug_auto_generated_from_title_on_create(self):
        d = Document.objects.create(
            organization=self.org, title='How to Reboot the Server',
            body='step 1: ...', created_by=self.user,
        )
        self.assertEqual(d.slug, 'how-to-reboot-the-server')

    def test_explicit_slug_preserved(self):
        d = Document.objects.create(
            organization=self.org, title='X', slug='custom-doc-slug',
            body='b', created_by=self.user,
        )
        self.assertEqual(d.slug, 'custom-doc-slug')

    def test_str_returns_title(self):
        d = Document.objects.create(
            organization=self.org, title='My Doc',
            body='', created_by=self.user,
        )
        # Document.__str__ returns title (line 111-112 in models.py).
        self.assertIn('My Doc', str(d))


class DocumentVersionSnapshotTests(TestCase):
    """`Document._create_version` snapshots the previous body/title BEFORE
    a save when the document already exists. Bug here = no audit trail
    of edits, customers can't roll back."""

    @classmethod
    def setUpTestData(cls):
        cls.org = Organization.objects.create(name='VerCo', slug='ver-co')
        cls.user = User.objects.create_user('ver-user', email='v@x.com', password='pw')

    def test_no_versions_on_initial_create(self):
        d = Document.objects.create(
            organization=self.org, title='Doc', body='v1',
            created_by=self.user,
        )
        self.assertEqual(d.versions.count(), 0)

    def test_version_recorded_on_first_edit(self):
        d = Document.objects.create(
            organization=self.org, title='Doc', body='v1',
            created_by=self.user, last_modified_by=self.user,
        )
        d.title = 'Doc-renamed'
        d.body = 'v2'
        d.save()
        # The pre-save snapshot recorded the v1 state.
        self.assertEqual(d.versions.count(), 1)
        version = d.versions.first()
        self.assertEqual(version.title, 'Doc')
        self.assertEqual(version.body, 'v1')
        self.assertEqual(version.version_number, 1)

    def test_version_numbers_increment_on_each_edit(self):
        d = Document.objects.create(
            organization=self.org, title='Doc', body='v1',
            created_by=self.user, last_modified_by=self.user,
        )
        for i in range(2, 5):
            d.body = f'v{i}'
            d.save()
        # We made 3 edits → 3 version snapshots, numbered 1..3.
        nums = sorted(d.versions.values_list('version_number', flat=True))
        self.assertEqual(nums, [1, 2, 3])


class DiagramSlugTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organization.objects.create(name='DiagCo', slug='diag-co')
        cls.user = User.objects.create_user('diag-user', email='dg@x.com', password='pw')

    def test_slug_auto_generated_from_title(self):
        d = Diagram.objects.create(
            organization=self.org, title='Network Topology',
            created_by=self.user,
        )
        self.assertEqual(d.slug, 'network-topology')

    def test_str_returns_title(self):
        d = Diagram.objects.create(
            organization=self.org, title='Rack Layout',
            created_by=self.user,
        )
        self.assertIn('Rack Layout', str(d))


class GlobalKBVisibilityTests(TestCase):
    """Documents with `is_global=True` are visible across tenants. This
    is the cross-tenant KB story; querysets in views need to OR
    (organization=current OR is_global=True)."""

    @classmethod
    def setUpTestData(cls):
        cls.org_a = Organization.objects.create(name='KBA', slug='kba')
        cls.org_b = Organization.objects.create(name='KBB', slug='kbb')
        cls.user = User.objects.create_user('kb-user', email='kb@x.com', password='pw')

    def test_global_doc_can_have_no_organization(self):
        # is_global docs may be org-scoped (MSP-internal) OR fully global
        # (organization=None). The model permits both — confirm a
        # null-org global doc round-trips.
        d = Document.objects.create(
            organization=None, title='Global FAQ', body='b',
            is_global=True, created_by=self.user,
        )
        self.assertIsNone(d.organization)
        self.assertTrue(d.is_global)

    def test_org_scoped_doc_with_global_flag(self):
        # An MSP-internal doc with is_global=True is visible to staff
        # across tenants but tied to the MSP org for ownership/audit.
        d = Document.objects.create(
            organization=self.org_a, title='MSP runbook', body='b',
            is_global=True, created_by=self.user,
        )
        self.assertEqual(d.organization, self.org_a)
        self.assertTrue(d.is_global)


# ---------------------------------------------------------------------------
# Phase 22 v1 — KB review reminders + mark-reviewed (v3.17.245)
# ---------------------------------------------------------------------------

from datetime import timedelta as _td

from django.conf import settings as django_settings
from django.test import Client, override_settings


_TEST_MIDDLEWARE = [
    m for m in django_settings.MIDDLEWARE
    if 'Enforce2FAMiddleware' not in m and 'AxesMiddleware' not in m
]


@override_settings(MIDDLEWARE=_TEST_MIDDLEWARE, SECURE_SSL_REDIRECT=False)
class KBReviewQueueTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        from core.models import Organization
        cls.org = Organization.objects.create(name='KBReviewCo', slug='kb-rev-co')
        cls.owner = User.objects.create_user('kb-owner', 'kbo@x.com', 'pw')
        cls.staff = User.objects.create_user('kb-staff', 'kbs@x.com', 'pw',
                                              is_staff=True, is_superuser=True)
        # An article owned by `owner`, last reviewed 200 days ago — overdue.
        cls.overdue = Document.objects.create(
            organization=cls.org, title='Overdue article', body='...',
            is_published=True, owner=cls.owner,
            review_interval_days=90,
            last_reviewed_at=timezone.now() - _td(days=200),
        )
        # An article last reviewed 5 days ago — due in (90-5) = 85d. Current.
        cls.current = Document.objects.create(
            organization=cls.org, title='Current article', body='...',
            is_published=True, owner=cls.owner,
            review_interval_days=90,
            last_reviewed_at=timezone.now() - _td(days=5),
        )
        # 86 days ago → due_soon (within 7 days of due).
        cls.due_soon = Document.objects.create(
            organization=cls.org, title='Due soon article', body='...',
            is_published=True, owner=cls.owner,
            review_interval_days=90,
            last_reviewed_at=timezone.now() - _td(days=86),
        )
        # review_interval_days=0 → never review.
        cls.no_review = Document.objects.create(
            organization=cls.org, title='No review article', body='...',
            is_published=True, owner=cls.owner,
            review_interval_days=0,
        )

    def _login(self, c, user):
        c.force_login(user)
        s = c.session
        s['2fa_prompted'] = True
        s.save()

    def test_review_status_classifies_correctly(self):
        self.assertEqual(self.overdue.review_status, 'overdue')
        self.assertEqual(self.current.review_status, 'current')
        self.assertEqual(self.due_soon.review_status, 'due_soon')
        self.assertEqual(self.no_review.review_status, 'no_review')

    def test_is_review_overdue_property(self):
        self.assertTrue(self.overdue.is_review_overdue)
        self.assertFalse(self.current.is_review_overdue)
        self.assertFalse(self.due_soon.is_review_overdue)
        self.assertFalse(self.no_review.is_review_overdue)

    def test_mark_reviewed_resets_clock(self):
        before = self.overdue.last_reviewed_at
        self.overdue.mark_reviewed(user=self.staff)
        self.overdue.refresh_from_db()
        self.assertGreater(self.overdue.last_reviewed_at, before)
        self.assertFalse(self.overdue.is_review_overdue)

    def test_review_queue_for_owner_lists_overdue_and_due_soon(self):
        c = Client()
        self._login(c, self.owner)
        r = c.get('/docs/review-queue/')
        self.assertEqual(r.status_code, 200)
        ctx = r.context
        overdue_titles = [d.title for d in ctx['overdue']]
        due_soon_titles = [d.title for d in ctx['due_soon']]
        self.assertIn('Overdue article', overdue_titles)
        self.assertIn('Due soon article', due_soon_titles)
        self.assertNotIn('Current article', overdue_titles + due_soon_titles)
        self.assertNotIn('No review article', overdue_titles + due_soon_titles)

    def test_mark_reviewed_view_works_for_owner(self):
        c = Client()
        self._login(c, self.owner)
        before = self.overdue.last_reviewed_at
        c.post(f'/docs/{self.overdue.slug}/mark-reviewed/')
        self.overdue.refresh_from_db()
        self.assertGreater(self.overdue.last_reviewed_at, before)

    def test_mark_reviewed_view_blocked_for_non_owner_non_staff(self):
        peer = User.objects.create_user('peer', 'p@x.com', 'pw')
        c = Client()
        self._login(c, peer)
        before = self.overdue.last_reviewed_at
        c.post(f'/docs/{self.overdue.slug}/mark-reviewed/')
        self.overdue.refresh_from_db()
        self.assertEqual(self.overdue.last_reviewed_at, before)


@override_settings(MIDDLEWARE=_TEST_MIDDLEWARE, SECURE_SSL_REDIRECT=False,
                   EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class KBReviewReminderCommandTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        from core.models import Organization
        cls.org = Organization.objects.create(name='ReminderCo', slug='kb-rem-co')
        cls.owner = User.objects.create_user('rem-owner', 'remo@x.com', 'pw')
        Document.objects.create(
            organization=cls.org, title='Stale 1', body='...',
            is_published=True, owner=cls.owner,
            review_interval_days=30,
            last_reviewed_at=timezone.now() - _td(days=120),
        )
        Document.objects.create(
            organization=cls.org, title='Stale 2', body='...',
            is_published=True, owner=cls.owner,
            review_interval_days=30,
            last_reviewed_at=timezone.now() - _td(days=90),
        )
        # Current — should NOT trigger
        Document.objects.create(
            organization=cls.org, title='Fresh', body='...',
            is_published=True, owner=cls.owner,
            review_interval_days=30,
            last_reviewed_at=timezone.now() - _td(days=5),
        )

    def test_command_sends_one_digest_per_owner(self):
        from django.core import mail
        from django.core.management import call_command
        mail.outbox = []
        call_command('kb_review_reminders', verbosity=0)
        self.assertEqual(len(mail.outbox), 1)
        msg = mail.outbox[0]
        self.assertEqual(msg.to, ['remo@x.com'])
        self.assertIn('2 article', msg.subject)
        self.assertIn('Stale 1', msg.body)
        self.assertIn('Stale 2', msg.body)
        self.assertNotIn('Fresh', msg.body)

    def test_dry_run_does_not_send(self):
        from django.core import mail
        from django.core.management import call_command
        mail.outbox = []
        call_command('kb_review_reminders', '--dry-run', verbosity=0)
        self.assertEqual(len(mail.outbox), 0)
