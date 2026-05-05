"""
Phase 15 v8 (v3.17.296) — Payment processor adapters.

Stripe + GoCardless stubs ship today. Live OAuth flows + actual
`charge()` calls land when an MSP connects a real account.
"""
from .base import BasePaymentProvider, PaymentProviderError
from .stripe import StripeProvider
from .gocardless import GoCardlessProvider


PROVIDER_REGISTRY = {
    'stripe': StripeProvider,
    'gocardless': GoCardlessProvider,
}


def get_payment_provider(connection):
    """Resolve a connection to its provider adapter instance."""
    cls = PROVIDER_REGISTRY.get(connection.provider_type)
    if cls is None:
        return None
    return cls(connection)
