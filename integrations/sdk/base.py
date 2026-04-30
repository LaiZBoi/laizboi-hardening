"""
Integration SDK -- provider plugin interface.

Phase 7 exposes a stable API for adding new integration providers
(PSA, RMM, network controllers, distributors, accounting, security
vendors) without touching the core integrations app. Each provider
implements `IntegrationProvider`; the registry resolves them by slug.
"""
from abc import ABC, abstractmethod


class IntegrationProvider(ABC):
    """Abstract base class for all integration providers."""

    #: Unique provider slug (e.g. 'connectwise', 'ingram', 'crowdstrike-falcon').
    slug = None

    #: Human-readable label.
    label = None

    #: Provider category -- one of: 'psa', 'rmm', 'distributor',
    #: 'accounting', 'network', 'security_edr', 'security_av',
    #: 'security_firewall', 'storage', 'backup', 'other'
    category = None

    #: Optional icon name (Font Awesome).
    icon = 'fa-plug'

    @abstractmethod
    def test_connection(self, connection) -> dict:
        """Return {'ok': bool, 'message': str}."""

    @abstractmethod
    def sync(self, connection) -> dict:
        """Return {'ok': bool, 'records_imported': int, 'errors': []}."""

    def webhook_handler(self, connection, request):
        """Optional inbound webhook handler. Override if the provider
        supports push events (recommended for security alerts)."""
        from .exceptions import NotSupported
        raise NotSupported('webhook_handler not implemented for ' + str(self.slug))
