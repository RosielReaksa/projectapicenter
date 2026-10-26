"""Common provider interface."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class ProviderError(Exception):
    """Base class. `retryable` decides whether the router moves to the next candidate."""

    retryable = True
    code = "provider_error"

    def __init__(self, message: str, status: int | None = None, retry_after: float | None = None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.retry_after = retry_after


class RateLimited(ProviderError):
    retryable = True
    code = "provider_rate_limited"


class ProviderUnavailable(ProviderError):
    retryable = True
    code = "provider_unavailable"


class ProviderTimeout(ProviderError):
    retryable = True
    code = "provider_timeout"


class BadRequest(ProviderError):
    """The upstream rejected the input itself - retrying elsewhere rarely helps."""

    retryable = False
    code = "provider_bad_request"


class ProviderAuthError(ProviderError):
    """The stored provider key is wrong or revoked. Owner must fix it."""

    retryable = True
    code = "provider_auth_error"


class EmptyResponse(ProviderError):
    retryable = True
    code = "provider_empty_response"


@dataclass(slots=True)
class ProviderResult:
    body: dict[str, Any]
    latency_ms: int
    prompt_tokens: int = 0
    completion_tokens: int = 0
    estimated_usage: bool = False


@dataclass(slots=True)
class ProviderCall:
    base_url: str
    api_key: str
    model_name: str
    payload: dict[str, Any]
    timeout: float = 45.0
    extra_headers: dict[str, str] = field(default_factory=dict)
