"""Avalara AvaTax adapter — Phase 15 v12 (v3.17.297) stub.

Live compute_tax() will POST to /api/v2/transactions/create with
companyCode + addresses + line items, then read totalTax from the
response.

API reference: https://developer.avalara.com/api-reference/avatax/rest/v2/
"""
from __future__ import annotations

from typing import Any, Dict
from decimal import Decimal

from .base import BaseTaxProvider, TaxProviderError


class AvalaraProvider(BaseTaxProvider):
    provider_type = 'avalara'
    provider_name = 'Avalara AvaTax'
    DEFAULT_BASE_URL = 'https://rest.avatax.com'

    def compute_tax(self, invoice) -> Dict[str, Any]:
        creds = self.credentials
        if not (creds.get('account_id') and creds.get('license_key')):
            return {'success': False, 'tax_amount': Decimal('0'),
                    'breakdown': [],
                    'error': 'Avalara credentials not configured'}
        return {
            'success': False,
            'tax_amount': Decimal('0'),
            'breakdown': [],
            'error': 'Avalara live compute_tax not yet implemented in this build',
        }
