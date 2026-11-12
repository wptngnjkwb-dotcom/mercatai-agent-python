from __future__ import annotations


class MercataiError(Exception):
    """Base exception for all Mercatai SDK errors."""
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class AuthError(MercataiError):
    """Raised when authentication fails (401/403)."""


class NotFoundError(MercataiError):
    """Raised when a resource is not found (404)."""


class RateLimitError(MercataiError):
    """Raised when the rate limit is exceeded (429)."""


class ValidationError(MercataiError):
    """Raised when request validation fails (400)."""


class PaymentRequiredError(MercataiError):
    """Raised when payment is required (402) — e.g. Stripe onboarding not done."""


class RedirectError(MercataiError):
    """Raised when the server answers with a redirect (3xx).

    The SDK never follows a redirect: the standard HTTP clients drop the Authorization header when the host changes (the address without
    ``www`` redirects to the one with it), and an old-style 301 or 302 turns a POST into a GET. Set ``base_url`` to the address
    in ``location`` (without its query string) instead.
    """

    def __init__(self, message: str, status_code: int | None = None, location: str | None = None):
        super().__init__(message, status_code)
        self.location = location
