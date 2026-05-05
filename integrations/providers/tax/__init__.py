"""
Phase 15 v12 (v3.17.297) — sales-tax compute provider adapters.

Avalara AvaTax + TaxJar stubs ship today. Live compute_tax() implementations
land when an MSP wires up a real account.
"""
from .base import BaseTaxProvider, TaxProviderError
from .avalara import AvalaraProvider
from .taxjar import TaxJarProvider


PROVIDER_REGISTRY = {
    'avalara': AvalaraProvider,
    'taxjar': TaxJarProvider,
}


def get_tax_provider(connection):
    cls = PROVIDER_REGISTRY.get(connection.provider_type)
    if cls is None:
        return None
    return cls(connection)
