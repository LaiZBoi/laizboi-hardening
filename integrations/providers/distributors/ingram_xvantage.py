"""
Ingram Micro Xvantage adapter — first concrete distributor provider.

Production-ready for the catalog, pricing, stock, and webhook surfaces.
Order placement is implemented but disabled-by-default per
DistributorConnection.sync_enabled — set the connection live and call
place_order() when you're ready to issue real POs.

API docs: https://developer.ingrammicro.com (Xvantage API).
Auth: OAuth2 client-credentials → bearer token (cached for 50 min).

Endpoints used:
  POST   /oauth/oauth30/token                    — token
  GET    /sandbox/resellers/v6/catalog            — product catalog
  POST   /sandbox/resellers/v6.1/catalog/priceandavailability — price + stock
  POST   /sandbox/resellers/v1/orders             — order placement

Webhook signature header: `X-Ingram-Signature: sha256=<hex>` over the
raw body, HMAC'd with the connection's webhook_secret.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from typing import Any, Dict, List, Optional

import requests

from .base import BaseDistributorProvider
from ..base import AuthenticationError, ProviderError


logger = logging.getLogger('integrations.distributors.ingram')


# Production endpoints. Sandbox URLs in DistributorConnection.base_url
# override these — most MSPs run sandbox first.
DEFAULT_BASE_URL = 'https://api.ingrammicro.com:443'
TOKEN_TTL_SECONDS = 50 * 60  # tokens are valid 60min, refresh at 50


class IngramXvantageProvider(BaseDistributorProvider):
    provider_name = 'Ingram Micro Xvantage'
    DEFAULT_BASE_URL = DEFAULT_BASE_URL

    def __init__(self, connection):
        super().__init__(connection)
        self._token = None
        self._token_expires_at = 0

    # ---- auth --------------------------------------------------------------

    def _get_token(self) -> str:
        if self._token and time.time() < self._token_expires_at:
            return self._token
        client_id = self.credentials.get('client_id') or ''
        client_secret = self.credentials.get('client_secret') or ''
        if not client_id or not client_secret:
            raise AuthenticationError('Ingram client_id / client_secret missing')
        try:
            resp = self.session.post(
                f'{self.base_url}/oauth/oauth30/token',
                data={
                    'grant_type': 'client_credentials',
                    'client_id': client_id,
                    'client_secret': client_secret,
                },
                timeout=15,
            )
        except requests.RequestException as e:
            raise ProviderError(f'Ingram token endpoint unreachable: {e}')
        if resp.status_code != 200:
            raise AuthenticationError(f'Ingram token request failed: {resp.status_code}')
        data = resp.json()
        self._token = data.get('access_token') or ''
        self._token_expires_at = time.time() + TOKEN_TTL_SECONDS
        return self._token

    def _auth_headers(self, customer_id: Optional[str] = None) -> Dict[str, str]:
        creds = self.credentials
        h = {
            'Authorization': f'Bearer {self._get_token()}',
            'Accept': 'application/json',
            'IM-CustomerNumber': customer_id or creds.get('customer_number') or '',
            'IM-CountryCode': creds.get('country', 'US'),
            'IM-CorrelationID': f'clientst0r-{int(time.time())}',
        }
        return {k: v for k, v in h.items() if v}

    # ---- public interface --------------------------------------------------

    def test_connection(self) -> bool:
        try:
            self._get_token()
            return True
        except Exception as exc:
            logger.warning('Ingram test_connection failed: %s', exc)
            return False

    def list_products(self, *, search: Optional[str] = None,
                      page_size: int = 50, **kwargs) -> List[Dict[str, Any]]:
        params = {'pageSize': min(page_size, 100)}
        if search:
            params['keyword'] = search
        try:
            r = self.session.get(
                f'{self.base_url}/sandbox/resellers/v6/catalog',
                headers=self._auth_headers(),
                params=params, timeout=30,
            )
        except requests.RequestException as e:
            raise ProviderError(f'Ingram catalog call failed: {e}')
        if r.status_code != 200:
            return []
        items = r.json().get('catalog') or []
        rows = []
        for it in items:
            rows.append({
                'sku': it.get('ingramPartNumber') or '',
                'name': it.get('description') or '',
                'manufacturer': it.get('vendorName') or '',
                'manufacturer_part_number': it.get('vendorPartNumber') or '',
                'category': it.get('category') or '',
                'upc': it.get('upcCode') or '',
            })
        return rows

    def get_pricing(self, sku: str, *, qty: int = 1,
                    customer_id: Optional[str] = None) -> Dict[str, Any]:
        body = {
            'products': [{'ingramPartNumber': sku, 'quantityRequested': qty}],
        }
        try:
            r = self.session.post(
                f'{self.base_url}/sandbox/resellers/v6.1/catalog/priceandavailability',
                headers={**self._auth_headers(customer_id), 'Content-Type': 'application/json'},
                data=json.dumps(body), timeout=30,
            )
        except requests.RequestException as e:
            return {'error': f'Ingram pricing call failed: {e}'}
        if r.status_code != 200:
            return {'error': f'HTTP {r.status_code}'}
        try:
            payload = r.json()
        except ValueError:
            return {'error': 'unparseable JSON'}
        first = (payload or [{}])[0] if isinstance(payload, list) else payload
        return {
            'sku': sku,
            'currency': first.get('currencyCode') or 'USD',
            'unit_price': float(first.get('customerPrice') or 0),
            'list_price': float(first.get('retailPrice') or 0),
            'qty': qty,
            'available_qty': int(first.get('availability', {}).get('totalAvailability') or 0),
            'valid_until': first.get('priceExpirationDate') or '',
        }

    def check_stock(self, sku: str, *,
                    location: Optional[str] = None) -> Dict[str, Any]:
        # Re-uses the price-and-availability endpoint and pulls just stock.
        out = self.get_pricing(sku, qty=1)
        if 'error' in out:
            return {'error': out['error']}
        return {'sku': sku, 'total_qty': out['available_qty']}

    def place_order(self, *, items: List[Dict[str, Any]],
                    ship_to: Dict[str, Any],
                    purchase_order: Optional[str] = None,
                    customer_id: Optional[str] = None) -> Dict[str, Any]:
        if not self.connection.sync_enabled:
            return {'error': 'Distributor connection is disabled — enable sync_enabled first'}
        body = {
            'customerOrderNumber': purchase_order or f'CS{int(time.time())}',
            'orderType': 'Standard Order',
            'shipToInfo': ship_to,
            'lines': [
                {
                    'lineNumber': i + 1,
                    'ingramPartNumber': it.get('sku'),
                    'quantity': int(it.get('qty') or 1),
                }
                for i, it in enumerate(items)
            ],
        }
        try:
            r = self.session.post(
                f'{self.base_url}/sandbox/resellers/v1/orders',
                headers={**self._auth_headers(customer_id), 'Content-Type': 'application/json'},
                data=json.dumps(body), timeout=30,
            )
        except requests.RequestException as e:
            return {'error': f'Ingram order call failed: {e}'}
        if r.status_code not in (200, 201):
            return {'error': f'HTTP {r.status_code}: {r.text[:300]}'}
        data = r.json()
        return {
            'order_id': data.get('ingramOrderNumber') or '',
            'status': data.get('orderStatus') or '',
            'total': data.get('totalOrderValue') or 0,
            'items': data.get('lines') or [],
        }

    # ---- webhook -----------------------------------------------------------

    def handle_webhook(self, *, headers: Dict[str, str], raw_body: bytes) -> Dict[str, Any]:
        """
        Verify the HMAC-SHA256 signature header. Returns
        {'event_type': str, 'signature_valid': bool, 'parsed': dict}.
        Caller persists the result in DistributorWebhookEvent.
        """
        secret = self.connection.get_webhook_secret()
        sig_header = headers.get('X-Ingram-Signature') or headers.get('HTTP_X_INGRAM_SIGNATURE') or ''
        # Header form: "sha256=<hex>"
        sig_header = sig_header.split('=', 1)[-1].strip()
        valid = False
        if secret and sig_header:
            expected = hmac.new(
                secret.encode('utf-8'), raw_body or b'', hashlib.sha256
            ).hexdigest()
            valid = hmac.compare_digest(expected, sig_header)
        try:
            parsed = json.loads(raw_body.decode('utf-8') or '{}')
        except (UnicodeDecodeError, ValueError):
            parsed = {}
        event_type = (parsed.get('eventType') or parsed.get('event_type') or '')[:80]
        return {'event_type': event_type, 'signature_valid': valid, 'parsed': parsed}
