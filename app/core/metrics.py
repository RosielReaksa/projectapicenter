"""Prometheus metrics (brief §5.5). Served on an internal-only path."""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram, generate_latest

REQUEST_COUNT = Counter(
    "gateway_requests_total",
    "Requests handled by the data plane.",
    ["partner", "status"],
)
REQUEST_LATENCY = Histogram(
    "gateway_request_latency_ms",
    "End-to-end latency of gateway requests, in milliseconds.",
    buckets=(25, 50, 100, 250, 500, 1000, 2500, 5000, 15000, 30000, 60000),
)
TOKENS = Counter(
    "gateway_tokens_total",
    "Tokens processed, by provider and direction.",
    ["provider", "direction"],
)
PROVIDER_FAILURES = Counter(
    "gateway_provider_failures_total",
    "Provider call failures (429, 5xx, timeout, empty).",
    ["provider"],
)
CACHE_HITS = Counter("gateway_cache_hits_total", "Responses served from cache.")
IN_FLIGHT = Gauge("gateway_in_flight", "Requests currently in flight.")
BREAKER_OPEN = Gauge("gateway_breaker_open", "1 when a provider's circuit is open.", ["provider"])


def record_request(partner: str, status: str, latency_ms: int) -> None:
    REQUEST_COUNT.labels(partner=partner or "unknown", status=status).inc()
    if latency_ms:
        REQUEST_LATENCY.observe(latency_ms)


def record_provider_failure(provider: str) -> None:
    PROVIDER_FAILURES.labels(provider=provider).inc()


def record_tokens(provider: str, prompt_tokens: int, completion_tokens: int) -> None:
    TOKENS.labels(provider=provider, direction="input").inc(max(0, prompt_tokens))
    TOKENS.labels(provider=provider, direction="output").inc(max(0, completion_tokens))


def record_cache_hit() -> None:
    CACHE_HITS.inc()


def set_breaker(provider: str, is_open: bool) -> None:
    BREAKER_OPEN.labels(provider=provider).set(1 if is_open else 0)


def render() -> bytes:
    return generate_latest()
