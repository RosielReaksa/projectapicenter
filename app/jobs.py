"""Background jobs: cache cleanup, retention, provider health probes."""

from __future__ import annotations

import logging
from datetime import timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import delete, select
from sqlalchemy.orm import selectinload

from app.core import cache as response_cache
from app.core import settings_store
from app.core.crypto import decrypt
from app.core.health import record_failure, record_success
from app.db import SessionLocal
from app.models import DailySummary, MaintenanceHistory, MaintenanceWindow, Provider, RequestLog
from app.providers import openai_compat
from app.utils import utcnow

log = logging.getLogger("gateway.jobs")

scheduler: AsyncIOScheduler | None = None


async def cleanup_cache() -> None:
    async with SessionLocal() as session:
        values = await settings_store.load_all(session)
        ttl = int(values.get("cache_ttl_seconds", 3600) or 3600)
        removed = await response_cache.purge_expired(session, ttl)
        if removed:
            log.info("Cache cleanup removed %d expired entries.", removed)


async def aggregate_and_prune() -> None:
    """Summarise rows past retention into daily_summary, then delete them."""
    async with SessionLocal() as session:
        values = await settings_store.load_all(session)
        retention = int(values.get("retention_days", 30) or 30)
        cutoff = utcnow() - timedelta(days=retention)

        rows = (
            await session.execute(
                select(
                    RequestLog.created_at,
                    RequestLog.partner_id,
                    RequestLog.provider_id,
                    RequestLog.model_id,
                    RequestLog.status_code,
                    RequestLog.prompt_tokens,
                    RequestLog.completion_tokens,
                    RequestLog.cost_usd,
                    RequestLog.price_usd,
                ).where(RequestLog.created_at < cutoff)
            )
        ).all()
        if not rows:
            return

        buckets: dict[tuple, dict] = {}
        for created, partner_id, provider_id, model_id, status, pt, ct, cost, price in rows:
            key = (created.date().isoformat(), partner_id, provider_id, model_id)
            bucket = buckets.setdefault(
                key,
                {"requests": 0, "errors": 0, "prompt": 0, "completion": 0, "cost": 0.0, "price": 0.0},
            )
            bucket["requests"] += 1
            bucket["errors"] += 0 if status == 200 else 1
            bucket["prompt"] += pt or 0
            bucket["completion"] += ct or 0
            bucket["cost"] += cost or 0.0
            bucket["price"] += price or 0.0

        for (day, partner_id, provider_id, model_id), bucket in buckets.items():
            existing = (
                await session.execute(
                    select(DailySummary).where(
                        DailySummary.day == day,
                        DailySummary.partner_id == partner_id,
                        DailySummary.provider_id == provider_id,
                        DailySummary.model_id == model_id,
                    )
                )
            ).scalar_one_or_none()
            if existing:
                existing.requests += bucket["requests"]
                existing.errors += bucket["errors"]
                existing.prompt_tokens += bucket["prompt"]
                existing.completion_tokens += bucket["completion"]
                existing.cost_usd += bucket["cost"]
                existing.price_usd += bucket["price"]
            else:
                session.add(
                    DailySummary(
                        day=day,
                        partner_id=partner_id,
                        provider_id=provider_id,
                        model_id=model_id,
                        requests=bucket["requests"],
                        errors=bucket["errors"],
                        prompt_tokens=bucket["prompt"],
                        completion_tokens=bucket["completion"],
                        cost_usd=bucket["cost"],
                        price_usd=bucket["price"],
                    )
                )

        await session.execute(delete(RequestLog).where(RequestLog.created_at < cutoff))
        await session.commit()
        log.info("Retention: summarised and pruned %d request rows.", len(rows))


async def probe_providers() -> None:
    """Light liveness check so a recovered provider closes its circuit breaker."""
    from app.main import app_http

    if app_http is None:
        return

    async with SessionLocal() as session:
        # selectinload: without it, reading provider.models below happens outside
        # a greenlet and raises MissingGreenlet, which killed this job silently.
        providers = (
            await session.execute(
                select(Provider)
                .options(selectinload(Provider.models))
                .where(Provider.is_active.is_(True))
            )
        ).scalars().all()

        for provider in providers:
            if not provider.api_key_enc:
                continue
            try:
                api_key = decrypt(provider.api_key_enc)
            except ValueError:
                continue
            model_name = next((m.model_name for m in provider.models if m.is_enabled), None)
            if not model_name:
                continue

            result = await openai_compat.test_connection(
                app_http, provider.base_url, api_key, model_name, timeout=20.0
            )
            provider.last_checked_at = utcnow()
            provider.last_latency_ms = result["latency_ms"]
            if result["ok"]:
                provider.health_status = "healthy"
                provider.last_error = None
                await record_success(provider.id)
            else:
                provider.health_status = "down"
                provider.last_error = f"{result.get('code')}: {result.get('error')}"[:500]
                await record_failure(provider.id)
        await session.commit()


def start() -> None:
    global scheduler
    if scheduler is not None:
        return
    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(cleanup_cache, "interval", minutes=30, id="cache_cleanup")
    scheduler.add_job(aggregate_and_prune, "cron", hour=3, minute=30, id="retention")
    scheduler.add_job(probe_providers, "interval", minutes=5, id="health_probe")
    scheduler.add_job(
        run_maintenance_windows, "interval", seconds=30, id="maintenance_windows"
    )
    scheduler.add_job(
        reload_maintenance_state, "interval", seconds=30, id="maintenance_reload"
    )
    scheduler.start()
    log.info("Background jobs scheduled.")


def stop() -> None:
    global scheduler
    if scheduler is not None:
        scheduler.shutdown(wait=False)
        scheduler = None


# ------------------------------------------------- scheduled maintenance (§6)
async def run_maintenance_windows() -> None:
    """Start and end scheduled windows. Only one instance runs this at a time."""
    from app.core import maintenance
    from app.core.store import get_store

    store = get_store()
    # Redis lock so a cluster runs the scheduler exactly once (§6, §11).
    acquired = await store.idem_begin("maintenance-scheduler", 55)
    if not acquired:
        return

    async with SessionLocal() as session:
        now = utcnow()
        windows = (
            await session.execute(
                select(MaintenanceWindow).where(
                    MaintenanceWindow.status.in_(["scheduled", "active"])
                )
            )
        ).scalars().all()

        for window in windows:
            if window.status == "scheduled" and window.auto_enable and window.starts_at <= now < window.ends_at:
                await maintenance.start_global(
                    session,
                    message=window.banner_text,
                    expected_end=window.ends_at,
                    allow_ips=[],
                    started_by=None,
                )
                window.status = "active"
                session.add(
                    MaintenanceHistory(
                        created_at=now,
                        user_id=None,
                        source="scheduler",
                        level="global",
                        target=str(window.id),
                        action="on",
                        reason=window.banner_text,
                    )
                )
                await session.commit()
                log.info("Scheduled maintenance started (window %s)", window.id)

            elif window.status == "active" and now >= window.ends_at:
                current = await maintenance.get_state(force=True)
                # Do not auto-disable if the owner manually extended it (§6).
                expected = current.global_state.expected_end
                if expected and expected > window.ends_at:
                    continue
                await maintenance.stop_global(session)
                window.status = "completed"
                session.add(
                    MaintenanceHistory(
                        created_at=now,
                        user_id=None,
                        source="scheduler",
                        level="global",
                        target=str(window.id),
                        action="off",
                        reason="scheduled end",
                    )
                )
                await session.commit()
                log.info("Scheduled maintenance ended (window %s)", window.id)

            elif window.status == "scheduled" and now >= window.ends_at:
                window.status = "completed"
                await session.commit()


async def reload_maintenance_state() -> None:
    """Safety net: re-read durable state every 30s (§7, §11)."""
    from app.core import maintenance

    await maintenance.restore_redis_on_startup()
