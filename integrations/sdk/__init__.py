"""
Integration SDK package — provider plugin interface.

Public API:
    from integrations.sdk import IntegrationProvider, register, get, all_providers, by_category
    from integrations.sdk.exceptions import IntegrationError, NotSupported, AuthFailed, RateLimited
"""
from .base import IntegrationProvider
from .exceptions import (
    AuthFailed,
    IntegrationError,
    NotSupported,
    RateLimited,
)
from .registry import all_providers, by_category, get, register

__all__ = [
    'IntegrationProvider',
    'register',
    'get',
    'all_providers',
    'by_category',
    'IntegrationError',
    'NotSupported',
    'AuthFailed',
    'RateLimited',
]
