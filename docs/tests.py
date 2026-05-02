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
