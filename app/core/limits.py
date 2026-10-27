"""Rate, concurrency and spend limits (brief §5.1, §5.2, §5.3).

Enforcement order, as specified:
  1. partner rate limit (RPM)
  2. key rate limit (RPM)
  3. concurrency slot (partner, then key)
  4. daily request limit (partner, then key)
  5. spend caps (key, partner, global)
  6. tokens-per-minute (partner, then key) - checked against a rolling window

The most restrictive value wins when a partner and a key both set a limit.
"""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.store import get_store
from app.models import GatewayKey, Partner, RequestLog
from app.utils import day_start, month_start

log = logging.getLogger("gateway.limits")


class LimitExceeded(Exception):
    def __init__(self, code: str, message: str, retry_after: int | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retry_after = retry_after


@dataclass(slots=True)
class Usage:
    requests_today: int
    tokens_this_month: int
    cost_this_month: float
    price_this_month: float


def _tightest(*values: int | None) -> int | None:
    """The smallest non-None limit; None means unlimited. 0 is a real limit."""
    present = [v for v in values if v is not None]
    return min(present) if present else None


async def key_usage(session: AsyncSession, key_id: int) -> Usage:
    today = await session.execute(
        select(func.count())
        .select_from(RequestLog)
        .where(RequestLog.gateway_key_id == key_id, RequestLog.created_at >= day_start())
    )
    totals = await session.execute(
        select(
            func.coalesce(func.sum(RequestLog.prompt_tokens + RequestLog.completion_tokens), 0),
            func.coalesce(func.sum(RequestLog.cost_usd), 0.0),
            func.coalesce(func.sum(RequestLog.price_usd), 0.0),
        ).where(RequestLog.gateway_key_id == key_id, RequestLog.created_at >= month_start())
    )
    tokens, cost, price = totals.one()
    return Usage(
        requests_today=int(today.scalar() or 0),
        tokens_this_month=int(tokens or 0),
        cost_this_month=float(cost or 0.0),
        price_this_month=float(price or 0.0),
    )


async def partner_usage(session: AsyncSession, partner_id: int) -> Usage:
    today = await session.execute(
        select(func.count())
        .select_from(RequestLog)
        .where(RequestLog.partner_id == partner_id, RequestLog.created_at >= day_start())
    )
    totals = await session.execute(
        select(
            func.coalesce(func.sum(RequestLog.prompt_tokens + RequestLog.completion_tokens), 0),
            func.coalesce(func.sum(RequestLog.cost_usd), 0.0),
            func.coalesce(func.sum(RequestLog.price_usd), 0.0),
        ).where(RequestLog.partner_id == partner_id, RequestLog.created_at >= month_start())
    )
    tokens, cost, price = totals.one()
    return Usage(
        requests_today=int(today.scalar() or 0),
        tokens_this_month=int(tokens or 0),
        cost_this_month=float(cost or 0.0),
        price_this_month=float(price or 0.0),
    )


async def provider_spend(session: AsyncSession, provider_id: int) -> float:
    result = await session.execute(
        select(func.coalesce(func.sum(RequestLog.cost_usd), 0.0)).where(
            RequestLog.provider_id == provider_id,
            RequestLog.created_at >= month_start(),
        )
    )
    return float(result.scalar() or 0.0)


# ------------------------------------------------------- cached spend checks
# Spend caps are decided with aggregates over the request log. Running one per
# request is exactly the hot-path database work LOAD_FIX asks to remove, and at
# high concurrency a slow aggregate is what keeps a connection checked out. The
# results are therefore cached for a couple of seconds.
#
# The trade-off: a partner can exceed a cap by up to `spend_cache_ttl` seconds of
# traffic. That is acceptable for a monthly budget and is bounded by the global
# in-flight cap; it is not acceptable for a hard per-request limit, which is
# what the Redis/DB counters are still used for.
_spend_cache: dict[tuple[str, int], tuple[float, float]] = {}
# Single-flight locks, keyed by loop as well as subject: an asyncio.Lock binds
# to the loop that first contends for it, and this process can run several.
_spend_locks: dict[tuple[int, str, int], asyncio.Lock] = {}
# A stale value is only served this far past its TTL, and only when a refresh
# fails. Kept close to the TTL so a spend cap cannot be silently bypassed for
# long; a failed refresh is logged either way.
_SPEND_STALE_HARD_LIMIT = 15.0


def _spend_ttl() -> float:
    return float(settings.spend_cache_ttl)


def _cached(kind: str, obj_id: int) -> float | None:
    entry = _spend_cache.get((kind, obj_id))
    if not entry:
        return None
    value, at = entry
    if time.monotonic() - at >= _spend_ttl():
        return None
    return value


def _stale_ok(kind: str, obj_id: int) -> float | None:
    entry = _spend_cache.get((kind, obj_id))
    if not entry:
        return None
    value, at = entry
    if time.monotonic() - at < _SPEND_STALE_HARD_LIMIT:
        return value
    return None


def _remember(kind: str, obj_id: int, value: float) -> float:
    _spend_cache[(kind, obj_id)] = (value, time.monotonic())
    return value


def invalidate_spend() -> None:
    """Throw the spend cache away (admin writes, tests)."""
    _spend_cache.clear()


async def _resolve(kind: str, obj_id: int, fetch) -> float:
    """Single-flight cache read: one aggregate at a time per subject.

    Without this, every request in a concurrent burst misses on the same
    expired entry and runs the same table scan at once - a cache stampede that
    holds the whole pool. With it, the first caller computes and the rest share
    the result.

    ``fetch`` opens its own short session. That matters: callers must not hold
    a connection while they wait on this lock, or a burst would park a hundred
    checked-out connections behind one aggregate and exhaust the pool - the
    exact failure this fix exists to prevent.
    """
    hit = _cached(kind, obj_id)
    if hit is not None:
        return hit

    lock_key = (id(asyncio.get_running_loop()), kind, obj_id)
    lock = _spend_locks.get(lock_key)
    if lock is None:
        lock = _spend_locks[lock_key] = asyncio.Lock()

    async with lock:
        # Another caller may have refreshed it while we waited.
        hit = _cached(kind, obj_id)
        if hit is not None:
            return hit
        try:
            return _remember(kind, obj_id, await fetch())
        except Exception:
            stale = _stale_ok(kind, obj_id)
            if stale is not None:
                log.warning(
                    "Spend refresh failed; serving the last known %s spend", kind,
                    exc_info=True,
                )
                return stale
            raise


async def cached_key_cost(key_id: int) -> float:
    async def fetch() -> float:
        async with _own_session() as session:
            return (await key_usage(session, key_id)).cost_this_month

    return await _resolve("key", key_id, fetch)


async def cached_partner_cost(partner_id: int) -> float:
    async def fetch() -> float:
        async with _own_session() as session:
            return (await partner_usage(session, partner_id)).cost_this_month

    return await _resolve("partner", partner_id, fetch)


async def cached_provider_spend(provider_id: int) -> float:
    async def fetch() -> float:
        async with _own_session() as session:
            return await provider_spend(session, provider_id)

    return await _resolve("provider", provider_id, fetch)


async def cached_global_spend() -> float:
    async def fetch() -> float:
        async with _own_session() as session:
            return await global_spend(session)

    return await _resolve("global", 0, fetch)


def _own_session():
    from app.db import SessionLocal

    return SessionLocal()


async def global_spend(session: AsyncSession) -> float:
    result = await session.execute(
        select(func.coalesce(func.sum(RequestLog.cost_usd), 0.0)).where(
            RequestLog.created_at >= month_start()
        )
    )
    return float(result.scalar() or 0.0)


async def paid_routes_blocked(session: AsyncSession, cap: float | None = None) -> bool:
    """Global kill switch: past the cap, only free models may run."""
    limit = settings.global_monthly_cap_usd if cap is None else cap
    if not limit or limit <= 0:
        return False
    return await global_spend(session) >= limit


# --------------------------------------------------------------- checking
# Daily limits are counted in the shared store rather than by COUNT(*) on the
# request log. The key includes the UTC date so the window aligns with the
# calendar day instead of drifting from first use, and so it resets at midnight
# like `Retry-After` promises. Two consequences of the store being the counter:
#   * without Redis the counter is per instance and is lost on restart, so the
#     enforced daily total can exceed the configured limit;
#   * a restart forgives the day's count.
# Both are covered in LOAD_FIX_REPORT §6, which lists what degrades without
# Redis.
def _day_key(kind: str, obj_id: int) -> str:
    return f"daily:{kind}:{obj_id}:{day_start().date().isoformat()}"


async def check_rate_and_daily(
    session: AsyncSession | None, partner, key
) -> None:
    """Rate, daily and spend checks.

    ``partner`` and ``key`` are snapshots from app.core.config_cache, not ORM
    rows, so this never needs a live session for the rate limits. The daily and
    spend checks fall back to the database only when a limit is actually
    configured; both are cached counters in Redis when Redis is present.
    """
    store = get_store()

    partner_rpm = partner.rpm_limit
    if partner_rpm:
        count = await store.incr_window(f"rpm:partner:{partner.id}", 60)
        if count > partner_rpm:
            raise LimitExceeded(
                "rate_limit_exceeded",
                f"Partner rate limit of {partner_rpm} requests per minute reached.",
                retry_after=60,
            )

    key_rpm = key.rpm_limit
    if key_rpm:
        count = await store.incr_window(f"rpm:key:{key.id}", 60)
        if count > key_rpm:
            raise LimitExceeded(
                "rate_limit_exceeded",
                f"Key rate limit of {key_rpm} requests per minute reached.",
                retry_after=60,
            )

    # Each limit is checked against the traffic it actually governs: a key's
    # daily limit must not be consumed by a sibling key's requests.
    if partner.daily_request_limit is not None:
        used = await store.incr_window(_day_key("partner", partner.id), 86400)
        if used > partner.daily_request_limit:
            raise LimitExceeded(
                "daily_limit_reached",
                f"Partner daily limit of {partner.daily_request_limit} requests reached.",
                retry_after=_seconds_until_tomorrow(),
            )
    if key.daily_limit is not None:
        used = await store.incr_window(_day_key("key", key.id), 86400)
        if used > key.daily_limit:
            raise LimitExceeded(
                "daily_limit_reached",
                f"Daily limit of {key.daily_limit} requests reached for this key.",
                retry_after=_seconds_until_tomorrow(),
            )

    # A cap of 0 means "no spend allowed at all", so test for None explicitly.
    # Spend needs an aggregate over the request log. The cached helpers open
    # their own short session rather than borrowing the caller's, so a caller
    # waiting on the single-flight lock is not also holding a connection.
    cap = _tightest_float(key.monthly_cap_usd, partner.monthly_cap_usd)
    if cap is not None and session is not None:
        if key.monthly_cap_usd is not None:
            key_cost = await cached_key_cost(key.id)
            if key_cost >= key.monthly_cap_usd:
                raise LimitExceeded(
                    "spend_cap_reached",
                    f"Key spend cap of ${key.monthly_cap_usd:.2f} reached this month.",
                )
        if partner.monthly_cap_usd is not None:
            partner_cost = await cached_partner_cost(partner.id)
            if partner_cost >= partner.monthly_cap_usd:
                raise LimitExceeded(
                    "spend_cap_reached",
                    f"Partner spend cap of ${partner.monthly_cap_usd:.2f} reached this month.",
                )


async def check_tpm(partner: Partner, key: GatewayKey, estimated_tokens: int) -> None:
    """Tokens per minute, measured on the token count reported for past requests.

    A request is admitted if the window still has room; its own usage is
    charged to the window after the call completes.
    """
    store = get_store()
    limit = _tightest(partner.tpm_limit, key.tpm_limit)
    if not limit:
        return

    scope = f"partner:{partner.id}" if partner.tpm_limit else f"key:{key.id}"
    count = await store.incr_window(f"tpm:{scope}", 60, amount=max(1, estimated_tokens))
    if count > limit:
        raise LimitExceeded(
            "token_rate_limit_exceeded",
            f"Token rate limit of {limit:,} tokens per minute reached.",
            retry_after=60,
        )


def _seconds_until_tomorrow() -> int:
    from datetime import timedelta

    from app.utils import utcnow

    tomorrow = day_start() + timedelta(days=1)
    return max(1, int((tomorrow - utcnow()).total_seconds()))


def _tightest_float(*values: float | None) -> float | None:
    present = [v for v in values if v is not None]
    return min(present) if present else None


# ------------------------------------------------------------ concurrency
@asynccontextmanager
async def concurrency_slot(partner: Partner, key: GatewayKey, reduce_pct: int = 0):
    """Reserve an in-flight slot for the partner and the key.

    Slots always carry a TTL, so a crashed instance cannot leak capacity.
    The slot is released in a finally block, on errors and disconnects alike.

    reduce_pct is the degraded-mode reduction (MAINTENANCE_SPEC §4.2): the
    effective cap is scaled down, never below 1.
    """
    store = get_store()
    ttl = settings.concurrency_slot_ttl
    acquired: list[str] = []

    partner_limit = _tightest(partner.concurrency_limit, key.concurrency_limit)
    if partner_limit and reduce_pct:
        partner_limit = max(1, int(partner_limit * (100 - reduce_pct) / 100))
    if partner_limit:
        scope = f"partner:{partner.id}"
        ok = await store.acquire_slot(scope, partner_limit, ttl)
        if not ok:
            if partner.over_capacity == "queue":
                deadline = asyncio.get_event_loop().time() + partner.queue_timeout_ms / 1000
                while not ok and asyncio.get_event_loop().time() < deadline:
                    await asyncio.sleep(0.05)
                    ok = await store.acquire_slot(scope, partner_limit, ttl)
            if not ok:
                raise LimitExceeded(
                    "concurrency_limit_exceeded",
                    f"Partner concurrency limit of {partner_limit} in-flight requests reached.",
                    retry_after=1,
                )
        acquired.append(scope)

    if key.concurrency_limit:
        scope = f"key:{key.id}"
        ok = await store.acquire_slot(scope, key.concurrency_limit, ttl)
        if not ok:
            for held in acquired:
                await store.release_slot(held)
            raise LimitExceeded(
                "concurrency_limit_exceeded",
                f"Key concurrency limit of {key.concurrency_limit} in-flight requests reached.",
                retry_after=1,
            )
        acquired.append(scope)

    try:
        yield
    finally:
        for scope in reversed(acquired):
            await store.release_slot(scope)


def clamp_max_tokens(requested: int | None) -> int | None:
    if requested is None:
        return None
    return max(1, min(int(requested), settings.max_tokens_ceiling))


def usage_snapshot(key: GatewayKey, partner: Partner, usage: Usage) -> dict:
    return {
        "key_id": key.id,
        "partner_id": partner.id,
        "requests_today": usage.requests_today,
        "daily_limit": _tightest(partner.daily_request_limit, key.daily_limit),
        "cost_this_month": round(usage.cost_this_month, 6),
        "monthly_cap_usd": _tightest_float(key.monthly_cap_usd, partner.monthly_cap_usd),
    }
