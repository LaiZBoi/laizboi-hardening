"""Accounting provider registry — maps AccountingConnection.provider_type → class."""
from .quickbooks_online import QuickBooksOnlineProvider
from .xero import XeroProvider


PROVIDER_REGISTRY = {
    'quickbooks_online': QuickBooksOnlineProvider,
    'xero': XeroProvider,
}


def get_accounting_provider(connection):
    cls = PROVIDER_REGISTRY.get(connection.provider_type)
    if cls is None:
        return None
    return cls(connection)
