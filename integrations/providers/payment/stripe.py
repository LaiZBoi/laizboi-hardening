"""
Stripe adapter — Phase 15 v8 (v3.17.296) stub.

Live charge() implementation lands when an MSP connects a Stripe
account via OAuth Connect. Today we expose `test_connection` (checks
that an API key is configured) and a `charge()` shim that documents
the expected request shape.

API reference: https://stripe.com/docs/api/charges/create
"""
from __future__ import annotations

from typing import Any, Dict

from .base import BasePaymentProvider, PaymentProviderError


class StripeProvider(BasePaymentProvider):
    provider_type = 'stripe'
    provider_name = 'Stripe'
    DEFAULT_BASE_URL = 'https://api.stripe.com'

    def charge(self, payment_intent: Dict[str, Any]) -> Dict[str, Any]:
        """Stub. Live implementation will POST to /v1/charges with:
          amount      = int(amount * 100)  (Stripe wants cents)
          currency    = lowercase ISO code
          customer    = stored Stripe customer id
          description = invoice / charge description
        Returns the Charge object id on success.
        """
        creds = self.credentials
        if not creds.get('api_key'):
            return {'success': False, 'error': 'Stripe API key not configured'}
        # Real implementation would import stripe SDK and POST here.
        # The stub returns a predictable error so callers can branch
        # on it during development.
        return {
            'success': False,
            'charge_id': None,
            'error': 'Stripe live charge not yet implemented in this build',
        }
