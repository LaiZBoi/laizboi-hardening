"""BaseTaxProvider — Phase 15 v12 (v3.17.297) interface for sales-tax
compute services."""
from __future__ import annotations

import logging
from typing import Any, Dict


logger = logging.getLogger('integrations.tax')


class TaxProviderError(Exception):
    pass


class BaseTaxProvider:
    """Subclasses MUST set:
      provider_type
      provider_name
      DEFAULT_BASE_URL

    And SHOULD implement:
      compute_tax(invoice) -> Dict
        Returns {success: bool, tax_amount: Decimal, breakdown: list,
                 error: str|None}
    """
    provider_type = 'base'
    provider_name = 'Base Tax Provider'
    DEFAULT_BASE_URL = ''

    def __init__(self, connection):
        self.connection = connection
        if not connection.base_url and self.DEFAULT_BASE_URL:
            connection.base_url = self.DEFAULT_BASE_URL

    @property
    def credentials(self) -> Dict[str, Any]:
        return self.connection.get_credentials()

    def test_connection(self) -> bool:
        """Stub: True when an API key is configured."""
        creds = self.credentials
        return bool(creds.get('api_key') or creds.get('account_id'))

    def compute_tax(self, invoice) -> Dict[str, Any]:
        """Compute sales tax for an Invoice. Stub raises
        NotImplementedError so callers know the wire-up is missing."""
        raise NotImplementedError(
            f'{self.provider_name} compute_tax() not yet wired — '
            f'connect a real account to enable.')
