"""Exceptions raised by Integration SDK providers."""


class IntegrationError(Exception):
    pass


class NotSupported(IntegrationError):
    pass


class AuthFailed(IntegrationError):
    pass


class RateLimited(IntegrationError):
    pass
