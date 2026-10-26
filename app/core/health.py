"""Per-provider circuit breaker, shared through the store (brief §5.5).

States:
  closed     - healthy, traffic flows
  open       - tripped, provider skipped until the cool-down elapses
  half_open  - cool-down elapsed, one probe request is allowed through

Opens after 3 failures within 60 seconds. Cool-down starts at 30 seconds and
doubles up to 10 minutes. A provider's ``Retry-After`` overrides the cool-down.
"""

from __future__ import annotations

import logging
import time

from app.core.store import get_store

log = logging.getLogger("gateway.health")

FAILURE_THRESHOLD = 3
FAILURE_WINDOW = 60.0
BASE_COOLDOWN = 30.0
MAX_COOLDOWN = 600.0
STATE_TTL = 900  # keep breaker state around well past the max cool-down


def _fresh(now: float) -> dict:
    return {"failures": [], "opened_at": None, "cooldown": BASE_COOLDOWN, "probing": False}


async def is_available(provider_id: int) -> bool:
    """True when a request may be sent to this provider."""
    state = await get_store().breaker_get(provider_id)
    if not state or state.get("opened_at") is None:
        return True

    elapsed = time.time() - state["opened_at"]
    if elapsed < state["cooldown"]:
        return False

    # Cool-down finished: allow a single probe through (half-open).
    if not state.get("probing"):
        state["probing"] = True
        await get_store().breaker_set(provider_id, state, STATE_TTL)
    return True


async def record_success(provider_id: int) -> None:
    await get_store().breaker_clear(provider_id)


async def record_failure(provider_id: int, retry_after: float | None = None) -> None:
    store = get_store()
    state = await store.breaker_get(provider_id) or _fresh(time.time())
    now = time.time()

    failures = [t for t in state.get("failures", []) if now - t <= FAILURE_WINDOW]
    failures.append(now)
    state["failures"] = failures
    state["probing"] = False

    tripped = len(failures) >= FAILURE_THRESHOLD or retry_after is not None
    if tripped:
        previous = state.get("cooldown") or BASE_COOLDOWN
        # A provider-supplied Retry-After is authoritative; otherwise back off.
        cooldown = (
            min(max(float(retry_after), 1.0), MAX_COOLDOWN)
            if retry_after is not None
            else min(previous * 2 if state.get("opened_at") else BASE_COOLDOWN, MAX_COOLDOWN)
        )
        state["opened_at"] = now
        state["cooldown"] = cooldown
        log.warning(
            "Circuit opened for provider %s (cooldown %.0fs)", provider_id, cooldown,
            extra={"provider": provider_id},
        )

    await store.breaker_set(provider_id, state, STATE_TTL)


async def snapshot(provider_ids: list[int]) -> dict[int, dict]:
    """Breaker view for the dashboard."""
    store = get_store()
    now = time.time()
    out: dict[int, dict] = {}
    for provider_id in provider_ids:
        state = await store.breaker_get(provider_id)
        if not state:
            out[provider_id] = {"open": False, "failures": 0, "cooldown": 0, "probing": False}
            continue
        opened = state.get("opened_at")
        out[provider_id] = {
            "open": bool(opened and now - opened < state.get("cooldown", 0)),
            "failures": len(state.get("failures", [])),
            "cooldown": state.get("cooldown", 0),
            "probing": state.get("probing", False),
            "retry_in": max(0, int(state.get("cooldown", 0) - (now - opened))) if opened else 0,
        }
    return out


async def reset(provider_id: int | None = None) -> None:
    store = get_store()
    if provider_id is None:
        # Only reachable from the dashboard's "reset health" action.
        from sqlalchemy import select

        from app.db import SessionLocal
        from app.models import Provider

        async with SessionLocal() as session:
            ids = [p.id for p in (await session.execute(select(Provider))).scalars().all()]
        for pid in ids:
            await store.breaker_clear(pid)
    else:
        await store.breaker_clear(provider_id)
