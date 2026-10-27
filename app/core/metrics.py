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

# ---- pool and overload (LOAD_FIX §6) -------------------------------------
DB_CONNECTIONS_OUT = Gauge(
    "gateway_db_connections_checked_out",
    "Database connections currently checked out of the pool.",
)
DB_POOL_WAIT = Histogram(
    "gateway_db_pool_wait_seconds",
    "Time spent waiting for a database connection from the pool.",
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 3, 10),
)
REQUESTS_SHED = Counter(
    "gateway_requests_shed_total",
    "Requests rejected immediately by the per-instance in-flight cap.",
)


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


def record_shed() -> None:
    REQUESTS_SHED.inc()


def observe_pool_wait(seconds: float) -> None:
    DB_POOL_WAIT.observe(max(0.0, seconds))


def set_in_flight(count: int) -> None:
    IN_FLIGHT.set(count)


def set_db_connections_checked_out(count: int) -> None:
    DB_CONNECTIONS_OUT.set(count)


def set_breaker(provider: str, is_open: bool) -> None:
    BREAKER_OPEN.labels(provider=provider).set(1 if is_open else 0)


def render() -> bytes:
    refresh_pool_gauges()
    return generate_latest()


def refresh_pool_gauges() -> None:
    """Pull current pool numbers into the gauges before a scrape."""
    try:
        from app.db import pool_stats

        set_db_connections_checked_out(int(pool_stats().get("checkedout", 0)))
    except Exception:  # pragma: no cover - metrics must never break a scrape
        return
