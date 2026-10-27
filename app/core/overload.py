"""Per-instance in-flight gate (LOAD_FIX requirement 4).

Overload must never mean waiting for a database connection. When this instance
is already holding ``max_in_flight`` requests, new ones are shed immediately
with 429 + Retry-After.

The counter is a plain integer: every read-modify-write below happens with no
``await`` in between, so on a single-threaded event loop it is already atomic
and no lock is needed. A module-level ``asyncio.Lock`` would additionally bind
to whichever loop first contended for it, which breaks in any process that runs
more than one loop (the test suite, the CLI).
"""

from __future__ import annotations

import logging
import time

from app.config import settings
from app.core import metrics

log = logging.getLogger("gateway.overload")

_in_flight = 0
_shed = 0
_peak = 0
_last_log = 0.0
LOG_INTERVAL = 5.0

# How long the pool must stay completely full before requests are shed for it.
_pool_full_since = 0.0
POOL_SATURATED_GRACE = 0.5


class Overloaded(Exception):
    """Raised when this instance is at capacity."""

    def __init__(self, in_flight: int, cap: int, reason: str = "in_flight"):
        self.in_flight = in_flight
        self.cap = cap
        self.reason = reason
        self.retry_after = 1
        self.status = 429
        self.code = "gateway_overloaded"
        self.message = "The gateway is at capacity right now. Please retry shortly."


def snapshot() -> dict:
    return {
        "in_flight": _in_flight,
        "peak": _peak,
        "cap": settings.max_in_flight,
        "shed_total": _shed,
        "pool_saturated": _pool_saturated_now(),
    }


def _pool_saturated_now() -> bool:
    """Current pool state, without advancing the sustained-exhaustion timer."""
    stats = _pool()
    try:
        capacity = int(stats.get("size", 0)) + max(0, int(stats.get("overflow", 0)))
        return capacity > 0 and int(stats.get("checkedout", 0)) >= capacity
    except (TypeError, ValueError):  # pragma: no cover
        return False


async def acquire() -> None:
    """Take a slot, or raise Overloaded immediately.

    Two independent capacity signals can shed a request:

    * this instance holds ``max_in_flight`` requests already;
    * the database pool has no free connection.

    The second matters because a request that cannot get a connection will sit
    in the pool queue until the checkout timeout. Shedding it now, instead of
    letting it wait, is the whole point of this module (LOAD_FIX requirement 4).
    """
    global _in_flight, _shed, _peak

    cap = max(1, int(settings.max_in_flight))
    # No await between the check and the increment: atomic on the event loop.
    if _in_flight >= cap:
        _shed += 1
        current = _in_flight
        reason = "in_flight"
    elif _pool_saturated():
        _shed += 1
        current = _in_flight
        reason = "db_pool"
    else:
        _in_flight += 1
        _peak = max(_peak, _in_flight)
        metrics.set_in_flight(_in_flight)
        return

    metrics.record_shed()
    _log_shed(current, cap, reason)
    raise Overloaded(current, cap, reason)


def _pool_saturated_now() -> bool:
    """Current pool state, without advancing the sustained-exhaustion timer."""
    stats = _pool()
    try:
        capacity = int(stats.get("capacity") or 0)
        return capacity > 0 and int(stats.get("checkedout", 0)) >= capacity
    except (TypeError, ValueError):  # pragma: no cover
        return False


def _pool_saturated() -> bool:
    """True when the pool has been completely exhausted for a sustained moment.

    Checking "is every connection checked out" on its own is far too eager: a
    burst of short pre-flight queries legitimately fills the pool for about a
    millisecond, and shedding at that instant would reject perfectly healthy
    traffic well below the in-flight cap.

    The pool therefore has to stay exhausted for ``POOL_SATURATED_GRACE``
    before it counts as real starvation. That is long enough to ignore a burst
    of fast queries and short enough to beat the 3s checkout timeout by a wide
    margin.

    Capacity includes max_overflow: the pool is only exhausted once the base
    connections *and* every overflow slot are in use.
    """
    global _pool_full_since

    if not _pool_saturated_now():
        _pool_full_since = 0.0
        return False

    now = time.monotonic()
    if not _pool_full_since:
        _pool_full_since = now
        return False
    return now - _pool_full_since >= POOL_SATURATED_GRACE


def _log_shed(current: int, cap: int, reason: str) -> None:
    """One warning per interval, however many requests are shed.

    A shed storm would otherwise write thousands of near-identical lines and
    make the log useless precisely when it matters most.
    """
    global _last_log
    now = time.monotonic()
    if _last_log and now - _last_log < LOG_INTERVAL:
        return
    _last_log = now
    log.warning(
        "Shedding at capacity (%s): %d in flight of cap %d, %d shed so far (pool=%s)",
        reason,
        current,
        cap,
        _shed,
        _pool(),
    )


async def release() -> None:
    global _in_flight
    _in_flight = max(0, _in_flight - 1)
    metrics.set_in_flight(_in_flight)


def _pool() -> dict:
    try:
        from app.db import pool_stats

        return pool_stats()
    except Exception:  # pragma: no cover
        return {}


def reset() -> None:
    """Test helper: clear counters between cases."""
    global _in_flight, _shed, _peak, _last_log
    _in_flight = 0
    _shed = 0
    _peak = 0
    _last_log = 0.0
    metrics.set_in_flight(0)