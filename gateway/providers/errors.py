"""Normalized provider error taxonomy.

Every adapter MUST catch its own SDK/HTTP exceptions and re-raise one of
these. No raw provider exception is allowed to leak past gateway/providers/
-- that's what lets the failover/breaker layer treat all providers alike.
"""

from __future__ import annotations


class ProviderError(Exception):
    """Base class for all normalized provider failures."""

    retryable: bool = False

    def __init__(self, provider: str, message: str):
        self.provider = provider
        self.message = message
        super().__init__(f"[{provider}] {message}")


class ProviderTimeoutError(ProviderError):
    retryable = True


class ProviderRateLimitError(ProviderError):
    retryable = True


class ProviderUnavailableError(ProviderError):
    """5xx / connection refused / blackholed -- circuit-breaker-worthy."""

    retryable = True


class ProviderAuthError(ProviderError):
    """Bad/missing API key. Not retryable -- retrying won't fix a 401."""

    retryable = False


class ProviderInvalidRequestError(ProviderError):
    """4xx caused by the request itself (bad schema, disallowed model, ...)."""

    retryable = False
