"""Maintenance state (MAINTENANCE_SPEC §3).

Four controls, from least to most disruptive:
  1. provider pause   - router skips one provider
  2. degraded mode    - gateway works with restrictions
  3. partner suspend  - one partner's keys get 403
  4. full maintenance - every /v1 call gets 503

Redis holds the live state that each request reads. PostgreSQL holds the
durable copy, and an instance reloads from it when Redis is empty or after a
restart. Writes go to both, then publish so every instance refreshes at once.

A short in-process cache (2s) bounds the read cost without letting instances
drift noticeably.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.store import get_store
from app.db import SessionLocal
from app.models import MaintenanceState, MaintenanceWindow, Partner, Provider
from app.utils import utcnow

log = logging.getLogger("gateway.maintenance")

CACHE_TTL = 2.0

# --------------------------------------------------------------------- state


@dataclass(slots=True)
class GlobalState:
    active: bool = False
    message: str = ""
    started_at: datetime | None = None
    expected_end: datetime | None = None
    allow_ips: list[str] = field(default_factory=list)
    started_by: int | None = None

    def retry_after(self, default: int) -> int:
        """Seconds a client should wait: until expected_end, else the default."""
        if self.expected_end:
            remaining = int((self.expected_end - utcnow()).total_seconds())
            return max(30, remaining)
        return default


@dataclass(slots=True)
class DegradedState:
    active: bool = False
    paid_only: bool = False
    free_only: bool = False
    reduce_concurrency_pct: int = 0
    max_tokens: int | None = None
    reject_low_priority: bool = False
    message: str = ""

    @property
    def restrictions(self) -> list[str]:
        out = []
        if self.paid_only:
            out.append("paid_only")
        if self.free_only:
            out.append("free_only")
        if self.reduce_concurrency_pct:
            out.append(f"concurrency -{self.reduce_concurrency_pct}%")
        if self.max_tokens:
            out.append(f"max_tokens {self.max_tokens}")
        if self.reject_low_priority:
            out.append("low priority rejected")
        return out


@dataclass(slots=True)
class MaintenanceSnapshot:
    global_state: GlobalState
    degraded: DegradedState
    paused_providers: set[int]
    suspended_partners: set[int]
    fetched_at: float
    stale: bool = False


_cache: MaintenanceSnapshot | None = None


# ------------------------------------------------------------------- helpers
def _dumps(value) -> str:
    return json.dumps(value, default=str)


def _parse_dt(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _loads(raw, fallback):
    if not raw:
        return fallback
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return fallback


def _global_from(data: dict) -> GlobalState:
    return GlobalState(
        active=bool(data.get("active")),
        message=data.get("message") or "",
        started_at=_parse_dt(data.get("started_at")),
        expected_end=_parse_dt(data.get("expected_end")),
        allow_ips=list(data.get("allow_ips") or []),
        started_by=data.get("started_by"),
    )


def _degraded_from(data: dict) -> DegradedState:
    return DegradedState(
        active=bool(data.get("active")),
        paid_only=bool(data.get("paid_only")),
        free_only=bool(data.get("free_only")),
        reduce_concurrency_pct=int(data.get("reduce_concurrency_pct") or 0),
        max_tokens=data.get("max_tokens"),
        reject_low_priority=bool(data.get("reject_low_priority")),
        message=data.get("message") or "",
    )


# ------------------------------------------------------------ load / refresh
async def load_from_db(session: AsyncSession) -> MaintenanceSnapshot:
    """Read the durable copy. Used at startup and as the Redis-down fallback."""
    row = await session.get(MaintenanceState, 1)
    global_state = GlobalState()
    degraded = DegradedState()
    if row:
        global_state = GlobalState(
            active=row.global_active,
            message=row.global_message or "",
            started_at=row.global_started_at,
            expected_end=row.global_expected_end,
            allow_ips=_loads(row.global_allow_ips, []),
            started_by=row.global_started_by,
        )
        degraded = _degraded_from(_loads(row.degraded_config, {}) | {"active": row.degraded_active})

    paused = {
        p.id
        for p in (
            await session.execute(select(Provider).where(Provider.paused.is_(True)))
        ).scalars()
    }
    suspended = {
        p.id
        for p in (
            await session.execute(select(Partner).where(Partner.suspended.is_(True)))
        ).scalars()
    }
    return MaintenanceSnapshot(
        global_state=global_state,
        degraded=degraded,
        paused_providers=paused,
        suspended_partners=suspended,
        fetched_at=time.monotonic(),
    )


async def push_to_redis(snapshot: MaintenanceSnapshot) -> None:
    """Mirror the durable state into Redis so every instance reads it."""
    store = get_store()
    if not hasattr(store, "_client"):  # MemoryStore: nothing to mirror
        return
    client = store._client
    prefix = settings.redis_prefix
    g = snapshot.global_state
    await client.set(
        f"{prefix}:maint:global",
        _dumps(
            {
                "active": g.active,
                "message": g.message,
                "started_at": g.started_at.isoformat() if g.started_at else None,
                "expected_end": g.expected_end.isoformat() if g.expected_end else None,
                "allow_ips": g.allow_ips,
                "started_by": g.started_by,
            }
        ),
    )
    d = snapshot.degraded
    await client.set(
        f"{prefix}:maint:degraded",
        _dumps(
            {
                "active": d.active,
                "paid_only": d.paid_only,
                "free_only": d.free_only,
                "reduce_concurrency_pct": d.reduce_concurrency_pct,
                "max_tokens": d.max_tokens,
                "reject_low_priority": d.reject_low_priority,
                "message": d.message,
            }
        ),
    )
    await client.publish(f"{prefix}:maint:changed", "1")


async def read_from_redis() -> MaintenanceSnapshot | None:
    store = get_store()
    if not hasattr(store, "_client"):
        return None
    try:
        client = store._client
        prefix = settings.redis_prefix
        raw_global = await client.get(f"{prefix}:maint:global")
        raw_degraded = await client.get(f"{prefix}:maint:degraded")
        if raw_global is None and raw_degraded is None:
            return None
        paused = await client.smembers(f"{prefix}:maint:paused_providers")
        suspended = await client.smembers(f"{prefix}:maint:suspended_partners")
        return MaintenanceSnapshot(
            global_state=_global_from(_loads(raw_global, {})),
            degraded=_degraded_from(_loads(raw_degraded, {})),
            paused_providers={int(x) for x in paused},
            suspended_partners={int(x) for x in suspended},
            fetched_at=time.monotonic(),
        )
    except Exception:
        log.warning("Could not read maintenance state from Redis", exc_info=True)
        return None


async def get_state(force: bool = False) -> MaintenanceSnapshot:
    """Current state, cached in-process for at most 2 seconds (§2.4)."""
    global _cache

    now = time.monotonic()
    if not force and _cache and now - _cache.fetched_at < CACHE_TTL:
        return _cache

    snapshot = await read_from_redis()
    if snapshot is None:
        # Redis empty or unreachable: fall back to PostgreSQL (§11).
        async with SessionLocal() as session:
            snapshot = await load_from_db(session)
        snapshot.stale = _cache is not None and now - _cache.fetched_at > 30

    _cache = snapshot
    return snapshot


def invalidate() -> None:
    global _cache
    _cache = None


# --------------------------------------------------------------- write paths
async def _persist_global(session: AsyncSession, state: GlobalState) -> None:
    row = await session.get(MaintenanceState, 1)
    if row is None:
        row = MaintenanceState(id=1)
        session.add(row)
    row.global_active = state.active
    row.global_message = state.message or None
    row.global_started_at = state.started_at
    row.global_expected_end = state.expected_end
    row.global_allow_ips = _dumps(state.allow_ips)
    row.global_started_by = state.started_by
    row.updated_at = utcnow()
    await session.commit()


async def _persist_degraded(session: AsyncSession, state: DegradedState) -> None:
    row = await session.get(MaintenanceState, 1)
    if row is None:
        row = MaintenanceState(id=1)
        session.add(row)
    row.degraded_active = state.active
    row.degraded_config = _dumps(
        {
            "paid_only": state.paid_only,
            "free_only": state.free_only,
            "reduce_concurrency_pct": state.reduce_concurrency_pct,
            "max_tokens": state.max_tokens,
            "reject_low_priority": state.reject_low_priority,
            "message": state.message,
        }
    )
    row.updated_at = utcnow()
    await session.commit()


async def start_global(
    session: AsyncSession,
    *,
    message: str,
    expected_end: datetime | None,
    allow_ips: list[str],
    started_by: int | None,
) -> GlobalState:
    state = GlobalState(
        active=True,
        message=message.strip()[:300],
        started_at=utcnow(),
        expected_end=expected_end,
        allow_ips=[ip.strip() for ip in allow_ips if ip.strip()],
        started_by=started_by,
    )
    await _persist_global(session, state)
    await _refresh(session)
    return state


async def stop_global(session: AsyncSession) -> GlobalState:
    state = GlobalState()
    await _persist_global(session, state)
    await _refresh(session)
    return state


async def set_degraded(session: AsyncSession, state: DegradedState) -> DegradedState:
    await _persist_degraded(session, state)
    await _refresh(session)
    return state


async def set_provider_paused(
    session: AsyncSession, provider: Provider, paused: bool, reason: str | None
) -> None:
    provider.paused = paused
    provider.paused_reason = (reason or None) if paused else None
    await session.commit()
    await _refresh(session)


async def set_partner_suspended(
    session: AsyncSession,
    partner: Partner,
    suspended: bool,
    reason_internal: str | None,
    reason_public: str | None,
) -> None:
    partner.suspended = suspended
    if suspended:
        partner.suspended_reason_internal = reason_internal
        partner.suspended_reason_public = reason_public
        partner.suspended_at = utcnow()
    else:
        partner.suspended_reason_internal = None
        partner.suspended_reason_public = None
        partner.suspended_at = None
    await session.commit()
    await _refresh(session)


async def _refresh(session: AsyncSession) -> None:
    """Rebuild the Redis mirror from PostgreSQL and publish the change."""
    invalidate()
    snapshot = await load_from_db(session)
    store = get_store()
    if hasattr(store, "_client"):
        try:
            client = store._client
            prefix = settings.redis_prefix
            await client.delete(f"{prefix}:maint:paused_providers")
            await client.delete(f"{prefix}:maint:suspended_partners")
            if snapshot.paused_providers:
                await client.sadd(
                    f"{prefix}:maint:paused_providers",
                    *[str(i) for i in snapshot.paused_providers],
                )
            if snapshot.suspended_partners:
                await client.sadd(
                    f"{prefix}:maint:suspended_partners",
                    *[str(i) for i in snapshot.suspended_partners],
                )
        except Exception:
            log.warning("Could not update paused/suspended sets in Redis", exc_info=True)
    await push_to_redis(snapshot)
    _cache = snapshot


async def restore_redis_on_startup() -> None:
    """Load durable state into Redis when an instance boots (§3.1)."""
    async with SessionLocal() as session:
        snapshot = await load_from_db(session)
        await push_to_redis(snapshot)
    invalidate()
    log.info(
        "Maintenance state loaded: global=%s degraded=%s paused=%d suspended=%d",
        snapshot.global_state.active,
        snapshot.degraded.active,
        len(snapshot.paused_providers),
        len(snapshot.suspended_partners),
    )


# ------------------------------------------------------------ public status
async def public_status() -> dict:
    """What /public/status returns: no internal detail, no provider names."""
    snapshot = await get_state()
    g = snapshot.global_state
    d = snapshot.degraded

    if g.active:
        state = "maintenance"
        message = g.message or "The service is temporarily unavailable."
    elif d.active or snapshot.paused_providers:
        state = "degraded"
        message = d.message or "Some services are running with restrictions."
    else:
        state = "normal"
        message = ""

    return {
        "state": state,
        "message": message,
        "expected_end": g.expected_end.isoformat() + "Z" if g.expected_end else None,
        "started_at": g.started_at.isoformat() + "Z" if g.started_at else None,
    }


async def upcoming_window(session: AsyncSession) -> dict | None:
    """The next scheduled window inside the banner lead time (§6)."""
    lead = timedelta(hours=settings.maintenance_banner_lead_hours)
    now = utcnow()
    row = (
        await session.execute(
            select(MaintenanceWindow)
            .where(
                MaintenanceWindow.status == "scheduled",
                MaintenanceWindow.starts_at <= now + lead,
                MaintenanceWindow.ends_at > now,
            )
            .order_by(MaintenanceWindow.starts_at)
        )
    ).scalars().first()
    if not row:
        return None
    return {
        "starts_at": row.starts_at.isoformat() + "Z",
        "ends_at": row.ends_at.isoformat() + "Z",
        "banner_text": row.banner_text,
        "auto_enable": row.auto_enable,
    }


# ------------------------------------------------------------------- drain
# In-flight counter used to show drain progress (§4.4). It is per instance and
# therefore approximate across a cluster, which is all the dashboard needs.
_in_flight = 0


def in_flight() -> int:
    return _in_flight


def track_start() -> None:
    global _in_flight
    _in_flight += 1


def track_end() -> None:
    global _in_flight
    _in_flight = max(0, _in_flight - 1)


def drain_status(snapshot: MaintenanceSnapshot) -> dict:
    """Progress of the drain after maintenance was switched on."""
    if not snapshot.global_state.active:
        return {"draining": False, "in_flight": _in_flight, "drained": False}

    started = snapshot.global_state.started_at
    elapsed = (utcnow() - started).total_seconds() if started else 0
    timeout = settings.maintenance_drain_timeout
    drained = _in_flight == 0 or elapsed >= timeout
    return {
        "draining": not drained,
        "in_flight": _in_flight,
        "drained": drained,
        "elapsed_seconds": int(elapsed),
        "timeout_seconds": timeout,
    }
