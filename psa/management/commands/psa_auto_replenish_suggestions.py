"""
Scan inventory across the project (`inventory.InventoryItem`,
`vehicles.VehicleInventoryItem`, `vehicles.ShopInventoryItem`) and find rows
where current_quantity <= min_quantity.

For each, build a suggestion dict and:
  1. If --create-prs: create a draft PurchaseRequisition grouping by
     preferred_vendor (one PR per vendor)
  2. Otherwise: just log them for the dashboard widget to surface

Idempotent: if there's already an open PR (status='draft' or 'submitted')
for the same SKU, skip — don't double-suggest.
"""
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import models


class Command(BaseCommand):
    help = 'Scan inventory for items below minimum stock; optionally create draft PRs.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--create-prs', action='store_true',
            help='Create draft PurchaseRequisition rows grouped by preferred_vendor.',
        )
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **opts):
        from psa.models import (
            PurchaseRequisition, PurchaseRequisitionLineItem,
        )

        below = []  # list of dicts: {item, qty_now, qty_min, qty_to_order, vendor}

        # ---- inventory app -------------------------------------------------
        try:
            from inventory.models import InventoryItem
            for it in InventoryItem.objects.filter(
                quantity__lte=models.F('min_quantity')
            ).exclude(min_quantity=0).select_related('preferred_vendor'):
                below.append(self._suggestion(it))
        except Exception as exc:  # noqa: BLE001
            self.stdout.write(f'inventory app: {exc}')

        # ---- vehicles app -------------------------------------------------
        try:
            from vehicles.models import VehicleInventoryItem
            for it in VehicleInventoryItem.objects.filter(
                quantity__lte=models.F('min_quantity')
            ).exclude(min_quantity=0).select_related('preferred_vendor'):
                below.append(self._suggestion(it))
        except Exception:  # noqa: BLE001
            pass
        try:
            from vehicles.models import ShopInventoryItem
            for it in ShopInventoryItem.objects.filter(
                quantity__lte=models.F('min_quantity')
            ).exclude(min_quantity=0).select_related('preferred_vendor'):
                below.append(self._suggestion(it))
        except Exception:  # noqa: BLE001
            pass

        self.stdout.write(f'Found {len(below)} items below minimum.')

        if opts['dry_run'] or not opts['create_prs']:
            for s in below:
                self.stdout.write(
                    f"  {s['name']}: qty={s['qty_now']} min={s['qty_min']} "
                    f"order={s['qty_to_order']} vendor={s['vendor_name'] or '(none)'}"
                )
            return

        # ---- group by vendor ----------------------------------------------
        by_vendor = {}
        for s in below:
            key = s['vendor_id'] or 0
            by_vendor.setdefault(key, []).append(s)

        from core.models import Organization
        msp = Organization.objects.filter(is_active=True).first()
        if not msp:
            self.stdout.write('No active org — skipping PR creation.')
            return

        # Skip items already on an open PR (by SKU)
        existing_skus = set()
        for pr in PurchaseRequisition.objects.filter(status__in=['draft', 'submitted']):
            for li in pr.line_items.all():
                if li.sku:
                    existing_skus.add(li.sku)

        created = 0
        for vendor_id, items in by_vendor.items():
            # Items with no SKU still get added (no dedupe key); items whose
            # SKU is already on an open PR are skipped to avoid duplication.
            new_items = [s for s in items if not s['sku'] or s['sku'] not in existing_skus]
            if not new_items:
                continue
            vendor_name = new_items[0]['vendor_name'] or 'Unassigned vendor'
            pr = PurchaseRequisition.objects.create(
                organization=msp,
                title=f'Auto-replenish from {vendor_name}',
                description='Auto-generated from stock-minimum scan. Review before submitting.',
                status='draft',
                notes='Created by psa_auto_replenish_suggestions',
            )
            for i, s in enumerate(new_items):
                PurchaseRequisitionLineItem.objects.create(
                    requisition=pr,
                    description=s['name'],
                    sku=s['sku'] or '',
                    quantity=s['qty_to_order'],
                    unit_price=s['unit_price'] or Decimal('0'),
                    sort_order=i,
                )
            pr.recompute_totals()
            pr.save(update_fields=['subtotal', 'tax_amount', 'total'])
            created += 1
            self.stdout.write(f'  Created PR {pr.pr_number} ({len(new_items)} items)')

        plural = 's' if created != 1 else ''
        self.stdout.write(self.style.SUCCESS(f'Created {created} draft PR{plural}.'))

    def _suggestion(self, item):
        qty_now = Decimal(str(item.quantity or 0))
        qty_min = Decimal(str(getattr(item, 'min_quantity', 0) or 0))
        rq = getattr(item, 'reorder_quantity', None) or 0
        rq = Decimal(str(rq))
        qty_to_order = rq if rq > 0 else max(qty_min * 2 - qty_now, Decimal('0'))
        vendor = getattr(item, 'preferred_vendor', None)
        return {
            'name': str(item),
            'sku': getattr(item, 'sku', '') or getattr(item, 'manufacturer_part_number', '') or '',
            'qty_now': qty_now,
            'qty_min': qty_min,
            'qty_to_order': qty_to_order,
            'unit_price': getattr(item, 'unit_cost', None) or getattr(item, 'unit_price', None),
            'vendor_id': vendor.pk if vendor else None,
            'vendor_name': vendor.name if vendor else '',
        }
