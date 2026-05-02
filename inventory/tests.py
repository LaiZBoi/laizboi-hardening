"""
Baseline test coverage for the inventory/ app.

InventoryItem + transactions for spare parts, consumables, hardware
stock. Bug here = wrong stock counts (which become wrong reorder
triggers, wrong on-hand reports, wrong audit). Plus the QR-code
auto-generation: every item gets a unique `INV-XXXXXXXXXXXX` code
on first save, used for printable QR sheets.

Coverage areas:
  * `InventoryItem.save()` auto-generates QR code on first save.
  * `is_low_stock` boundary conditions (at, below, above min).
  * `total_value` math (quantity × unit_cost) + None handling.
  * `InventoryTransaction` `__str__` includes item + signed quantity.
  * `OrganizationManager.for_organization()` filtering.
"""
from __future__ import annotations

from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase

from core.models import Organization
from inventory.models import (
    InventoryCategory,
    InventoryItem,
    InventoryLocation,
    InventoryTransaction,
)


class InventoryItemSaveTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organization.objects.create(name='InvCo', slug='inv-co')

    def test_qr_code_auto_generated_on_first_save(self):
        item = InventoryItem.objects.create(
            organization=self.org, name='M2 SSD', item_type='spare_part',
            quantity=10,
        )
        # Format: "INV-" + 12 hex chars uppercased.
        self.assertTrue(item.qr_code.startswith('INV-'))
        self.assertEqual(len(item.qr_code), 4 + 12)

    def test_qr_code_unique_across_items(self):
        a = InventoryItem.objects.create(
            organization=self.org, name='A', quantity=1,
        )
        b = InventoryItem.objects.create(
            organization=self.org, name='B', quantity=1,
        )
        self.assertNotEqual(a.qr_code, b.qr_code)

    def test_explicit_qr_code_preserved_on_save(self):
        item = InventoryItem.objects.create(
            organization=self.org, name='Custom-QR', quantity=1,
            qr_code='INV-CUSTOM-1234',
        )
        self.assertEqual(item.qr_code, 'INV-CUSTOM-1234')

    def test_str_returns_name(self):
        item = InventoryItem.objects.create(
            organization=self.org, name='HDMI cable', quantity=20,
        )
        self.assertEqual(str(item), 'HDMI cable')


class InventoryItemStockLogicTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organization.objects.create(name='StockCo', slug='stock-co')

    def test_is_low_stock_true_when_at_minimum(self):
        item = InventoryItem.objects.create(
            organization=self.org, name='At-min', quantity=5, min_quantity=5,
        )
        # Boundary case: AT minimum is "low" (the comparison is `<=`).
        self.assertTrue(item.is_low_stock)

    def test_is_low_stock_true_when_below_minimum(self):
        item = InventoryItem.objects.create(
            organization=self.org, name='Below-min', quantity=2, min_quantity=5,
        )
        self.assertTrue(item.is_low_stock)

    def test_is_low_stock_false_when_above_minimum(self):
        item = InventoryItem.objects.create(
            organization=self.org, name='Above-min', quantity=10, min_quantity=5,
        )
        self.assertFalse(item.is_low_stock)

    def test_total_value_computed_when_unit_cost_set(self):
        item = InventoryItem.objects.create(
            organization=self.org, name='With-cost',
            quantity=12, unit_cost=Decimal('5.50'),
        )
        self.assertEqual(item.total_value, Decimal('66.00'))

    def test_total_value_none_when_unit_cost_null(self):
        item = InventoryItem.objects.create(
            organization=self.org, name='No-cost', quantity=12,
        )
        self.assertIsNone(item.total_value)


class InventoryItemFilteringTests(TestCase):
    """OrganizationManager — tenant filter for inventory listings."""

    @classmethod
    def setUpTestData(cls):
        cls.org_a = Organization.objects.create(name='InvA', slug='inv-a')
        cls.org_b = Organization.objects.create(name='InvB', slug='inv-b')

    def test_for_organization_filters_to_one_tenant(self):
        InventoryItem.objects.create(organization=self.org_a, name='A1', quantity=1)
        InventoryItem.objects.create(organization=self.org_a, name='A2', quantity=1)
        InventoryItem.objects.create(organization=self.org_b, name='B1', quantity=1)
        for_a = list(InventoryItem.objects.for_organization(self.org_a))
        self.assertEqual(len(for_a), 2)
        self.assertEqual({i.name for i in for_a}, {'A1', 'A2'})


class InventoryTransactionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organization.objects.create(name='TxCo', slug='tx-co')
        cls.user = User.objects.create_user('tx-user', email='t@x.com', password='pw')
        cls.item = InventoryItem.objects.create(
            organization=cls.org, name='Probe', quantity=10,
        )

    def test_str_includes_item_type_and_signed_quantity_change(self):
        tx_in = InventoryTransaction.objects.create(
            item=self.item, transaction_type='stock_in',
            quantity_change=5, quantity_after=15,
            performed_by=self.user,
        )
        s = str(tx_in)
        self.assertIn('Probe', s)
        self.assertIn('stock_in', s)
        self.assertIn('+5', s)

    def test_negative_quantity_change_renders_with_minus(self):
        tx_out = InventoryTransaction.objects.create(
            item=self.item, transaction_type='stock_out',
            quantity_change=-3, quantity_after=7,
            performed_by=self.user,
        )
        # `:+d` format spec emits an explicit sign — `-3`.
        self.assertIn('-3', str(tx_out))


class InventoryCategoryAndLocationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organization.objects.create(name='CatLocCo', slug='cat-loc-co')

    def test_category_str_returns_name(self):
        c = InventoryCategory.objects.create(
            organization=self.org, name='Network gear',
        )
        self.assertEqual(str(c), 'Network gear')

    def test_location_str_returns_name(self):
        l = InventoryLocation.objects.create(
            organization=self.org, name='Shelf A',
        )
        self.assertEqual(str(l), 'Shelf A')

    def test_category_default_color_set(self):
        c = InventoryCategory.objects.create(organization=self.org, name='X')
        self.assertEqual(c.color, '#6c757d')
