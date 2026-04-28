"""
BaseDistributorProvider — interface every distributor connector
implements. Methods cover the catalog/pricing/stock/order/webhook
lifecycle, NOT the company/contact/ticket lifecycle that
PSAConnection providers handle.

Subclasses implement: test_connection(), list_products(), get_pricing(),
check_stock(), place_order(), handle_webhook().

All HTTP goes through the same retry/session helpers as the PSA
BaseProvider; we share `_validate_base_url` to defend against SSRF.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from ..base import BaseProvider, ProviderError


logger = logging.getLogger('integrations.distributors')


class BaseDistributorProvider(BaseProvider):
    """
    Minimum viable interface. Subclasses MUST override every method that
    isn't decorated with `# default ok`. SSRF + retry + session sharing
    inherited from BaseProvider.
    """

    provider_name = 'Base Distributor Provider'
    supports_companies = False
    supports_contacts = False
    supports_tickets = False
    supports_webhooks = True  # most distributors emit ASN / order webhooks

    # Subclasses set this; used to populate base_url when the connection
    # row leaves it blank. Lets admins create connections without needing
    # to know the production URL up front.
    DEFAULT_BASE_URL = ''

    def __init__(self, connection):
        # If the operator left base_url blank, fall back to the provider's
        # production endpoint BEFORE BaseProvider's SSRF validator runs.
        if not connection.base_url and self.DEFAULT_BASE_URL:
            connection.base_url = self.DEFAULT_BASE_URL
        super().__init__(connection)

    # ---- abstract methods --------------------------------------------------

    def test_connection(self) -> bool:
        raise NotImplementedError

    def list_products(self, *, search: Optional[str] = None,
                      page_size: int = 50, **kwargs) -> List[Dict[str, Any]]:
        """Return product catalog rows. Each row should contain at least
        sku, name, manufacturer, manufacturer_part_number."""
        raise NotImplementedError

    def get_pricing(self, sku: str, *, qty: int = 1,
                    customer_id: Optional[str] = None) -> Dict[str, Any]:
        """Return {sku, currency, unit_price, list_price, qty, available_qty,
        valid_until} or {'error': '...'} on failure."""
        raise NotImplementedError

    def check_stock(self, sku: str, *,
                    location: Optional[str] = None) -> Dict[str, Any]:
        """Return {sku, total_qty, locations: [{name, qty}]} or {'error': ...}."""
        raise NotImplementedError

    def place_order(self, *, items: List[Dict[str, Any]],
                    ship_to: Dict[str, Any],
                    purchase_order: Optional[str] = None,
                    customer_id: Optional[str] = None) -> Dict[str, Any]:
        """Return {order_id, status, total, items: [...]} or {'error': ...}."""
        raise NotImplementedError

    def handle_webhook(self, *, headers: Dict[str, str], raw_body: bytes) -> Dict[str, Any]:
        """
        Parse + validate an inbound webhook from the distributor.
        Subclasses do signature verification here using
        `self.connection.get_webhook_secret()`. Return a dict to log
        in DistributorWebhookEvent.process_error / processed.
        """
        raise NotImplementedError

    # ---- shared helpers ----------------------------------------------------

    def _normalize_money(self, value, currency='USD'):
        try:
            return {'amount': float(value), 'currency': currency}
        except (TypeError, ValueError):
            return {'amount': 0.0, 'currency': currency}
