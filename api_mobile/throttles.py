"""
Mobile API throttles.

`MobileLoginRateThrottle` is the per-IP rate limit for `/api/mobile/v1/auth/login/`.
Reuses the existing `login` scope rate (10/hour) configured in
`config/settings.py::REST_FRAMEWORK['DEFAULT_THROTTLE_RATES']`.
"""
from rest_framework.throttling import AnonRateThrottle


class MobileLoginRateThrottle(AnonRateThrottle):
    """Per-IP throttle for mobile login attempts. 10/hour."""
    scope = 'login'
