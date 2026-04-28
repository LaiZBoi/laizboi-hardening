"""Distributor provider registry.

Maps DistributorConnection.provider_type → provider class. Mirrors the
PSA `PROVIDER_REGISTRY` pattern but for distributor catalogs/pricing.
"""
from .ingram_xvantage import IngramXvantageProvider
from .pax8 import Pax8Provider
from .synnex import TDSynnexProvider


PROVIDER_REGISTRY = {
    'ingram_xvantage': IngramXvantageProvider,
    'pax8': Pax8Provider,
    'synnex': TDSynnexProvider,
}


def get_distributor_provider(connection):
    cls = PROVIDER_REGISTRY.get(connection.provider_type)
    if cls is None:
        return None
    return cls(connection)
