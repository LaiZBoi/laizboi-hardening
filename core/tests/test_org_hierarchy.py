"""Tests for Phase 18 — Multi-Location Client Hierarchy (v3.17.240)."""
from django.test import TestCase

from core.models import Organization
from core.utils import descendant_org_ids


class OrgHierarchyHelperTests(TestCase):
    """`descendant_org_ids` walks the parent chain correctly."""

    @classmethod
    def setUpTestData(cls):
        cls.acme = Organization.objects.create(name='ACME Corp', slug='acme')
        cls.east = Organization.objects.create(
            name='ACME East', slug='acme-east', parent=cls.acme,
        )
        cls.west = Organization.objects.create(
            name='ACME West', slug='acme-west', parent=cls.acme,
        )
        cls.east_a = Organization.objects.create(
            name='ACME East — Boston', slug='acme-east-bos', parent=cls.east,
        )
        cls.unrelated = Organization.objects.create(name='Other Co', slug='other')

    def test_descendant_ids_for_top_level_includes_all_children(self):
        ids = descendant_org_ids(self.acme)
        self.assertIn(self.acme.pk, ids)
        self.assertIn(self.east.pk, ids)
        self.assertIn(self.west.pk, ids)
        self.assertIn(self.east_a.pk, ids)
        self.assertNotIn(self.unrelated.pk, ids)

    def test_descendant_ids_for_leaf_returns_only_self(self):
        ids = descendant_org_ids(self.east_a)
        self.assertEqual(ids, {self.east_a.pk})

    def test_descendant_ids_handles_none(self):
        self.assertEqual(descendant_org_ids(None), set())

    def test_ancestors_property_walks_up(self):
        chain = [a.pk for a in self.east_a.ancestors]
        self.assertEqual(chain, [self.east.pk, self.acme.pk])

    def test_breadcrumb_label_renders_chain(self):
        self.assertEqual(
            self.east_a.breadcrumb_label,
            'ACME Corp → ACME East → ACME East — Boston',
        )


class OrgHierarchyManagerTests(TestCase):
    """`OrganizationManager.for_organization()` follows parent chain."""

    @classmethod
    def setUpTestData(cls):
        cls.parent = Organization.objects.create(name='Parent', slug='ph-p')
        cls.child = Organization.objects.create(
            name='Child', slug='ph-c', parent=cls.parent,
        )
        cls.unrelated = Organization.objects.create(name='Stranger', slug='ph-s')

    def test_for_organization_inherits_descendants_by_default(self):
        from assets.models import Asset
        a_parent = Asset.objects.create(
            organization=self.parent, name='Parent Asset', asset_type='other',
        )
        a_child = Asset.objects.create(
            organization=self.child, name='Child Asset', asset_type='other',
        )
        a_other = Asset.objects.create(
            organization=self.unrelated, name='Other Asset', asset_type='other',
        )
        names = set(
            Asset.objects.for_organization(self.parent)
                          .values_list('name', flat=True)
        )
        self.assertEqual(names, {'Parent Asset', 'Child Asset'})
        self.assertNotIn('Other Asset', names)

    def test_for_organization_with_strict_skips_descendants(self):
        from assets.models import Asset
        Asset.objects.create(
            organization=self.parent, name='Parent only', asset_type='other',
        )
        Asset.objects.create(
            organization=self.child, name='Child', asset_type='other',
        )
        names = set(
            Asset.objects.for_organization(self.parent, include_descendants=False)
                          .values_list('name', flat=True)
        )
        self.assertEqual(names, {'Parent only'})

    def test_child_query_does_not_see_parent_rows(self):
        from assets.models import Asset
        Asset.objects.create(
            organization=self.parent, name='Parent secret', asset_type='other',
        )
        names = set(
            Asset.objects.for_organization(self.child)
                          .values_list('name', flat=True)
        )
        self.assertNotIn('Parent secret', names)


class OrgFormParentValidationTests(TestCase):
    """Form prevents picking yourself or a descendant as parent."""

    @classmethod
    def setUpTestData(cls):
        cls.a = Organization.objects.create(name='A', slug='form-a')
        cls.b = Organization.objects.create(name='B', slug='form-b', parent=cls.a)
        cls.c = Organization.objects.create(name='C', slug='form-c', parent=cls.b)
        cls.unrelated = Organization.objects.create(name='U', slug='form-u')

    def test_form_excludes_self_and_descendants_from_parent_choices(self):
        from accounts.forms import OrganizationForm
        form = OrganizationForm(instance=self.a)
        choices = list(form.fields['parent'].queryset.values_list('pk', flat=True))
        self.assertNotIn(self.a.pk, choices)
        self.assertNotIn(self.b.pk, choices)
        self.assertNotIn(self.c.pk, choices)
        self.assertIn(self.unrelated.pk, choices)
