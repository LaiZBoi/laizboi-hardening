"""
GoCardless adapter — Phase 15 v8 (v3.17.296) stub.

Direct Debit / ACH via the GoCardless Pro API. Live charge() lands
when an MSP completes the OAuth flow.

API reference: https://developer.gocardless.com/api-reference/
"""
from __future__ import annotations

from typing import Any, Dict

from .base import BasePaymentProvider, PaymentProviderError


class GoCardlessProvider(BasePaymentProvider):
    provider_type = 'gocardless'
    provider_name = 'GoCardless'
    DEFAULT_BASE_URL = 'https://api.gocardless.com'

    def charge(self, payment_intent: Dict[str, Any]) -> Dict[str, Any]:
        """Stub. Live implementation will POST to /payments with:
          amount   = int(amount * 100)
          currency = uppercase ISO code (e.g. GBP, USD)
          links.mandate = stored customer mandate id
        Returns the Payment id on success.
        """
        creds = self.credentials
        if not creds.get('access_token'):
            return {'success': False, 'error': 'GoCardless OAuth not completed'}
        return {
            'success': False,
            'charge_id': None,
            'error': 'GoCardless live charge not yet implemented in this build',
        }
