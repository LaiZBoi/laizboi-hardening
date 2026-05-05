"""TaxJar adapter — Phase 15 v12 (v3.17.297) stub.

Live compute_tax() will POST to /v2/taxes with from/to addresses +
line items + amount, then read amount_to_collect from the response.

API reference: https://developers.taxjar.com/api/reference/
"""
from __future__ import annotations

from typing import Any, Dict
from decimal import Decimal

from .base import BaseTaxProvider, TaxProviderError


class TaxJarProvider(BaseTaxProvider):
    provider_type = 'taxjar'
    provider_name = 'TaxJar'
    DEFAULT_BASE_URL = 'https://api.taxjar.com'

    def compute_tax(self, invoice) -> Dict[str, Any]:
        creds = self.credentials
        if not creds.get('api_key'):
            return {'success': False, 'tax_amount': Decimal('0'),
                    'breakdown': [],
                    'error': 'TaxJar API key not configured'}
        return {
            'success': False,
            'tax_amount': Decimal('0'),
            'breakdown': [],
            'error': 'TaxJar live compute_tax not yet implemented in this build',
        }
