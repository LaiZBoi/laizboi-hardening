"""Distributor provider registry.

Maps DistributorConnection.provider_type → provider class. Mirrors the
PSA `PROVIDER_REGISTRY` pattern but for distributor catalogs/pricing.
"""
from .ingram_xvantage import IngramXvantageProvider


PROVIDER_REGISTRY = {
    'ingram_xvantage': IngramXvantageProvider,
}


def get_distributor_provider(connection):
    cls = PROVIDER_REGISTRY.get(connection.provider_type)
    if cls is None:
        return None
    return cls(connection)
