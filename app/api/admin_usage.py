"""Dashboard overview and usage/cost analytics, with partner scoping."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_user
from app.config import settings
from app.core import cache as response_cache
from app.core import health, limits, settings_store
from app.db import get_session
from app.models import GatewayKey, Model, Partner, Provider, RequestLog, User
from app.utils import day_start, days_ago, iso, month_start, utcnow

router = APIRouter(prefix="/admin", tags=["admin-usage"])


def _scope(query, user: User):
    """Non-owner users only ever see their own partner's traffic."""
    if user.is_owner:
        return query
    return query.where(RequestLog.partner_id == (user.partner_id or -1))


@router.get("/overview")
async def overview(
    user: User = Depends(current_user), session: AsyncSession = Depends(get_session)
):
    today = day_start()
    month = month_start()

    requests_today, tokens_in, tokens_out, cost_today, price_today, errors_today = (
        await session.execute(
            _scope(
                select(
                    func.count(RequestLog.id),
                    func.coalesce(func.sum(RequestLog.prompt_tokens), 0),
                    func.coalesce(func.sum(RequestLog.completion_tokens), 0),
                    func.coalesce(func.sum(RequestLog.cost_usd), 0.0),
                    func.coalesce(func.sum(RequestLog.price_usd), 0.0),
                    func.coalesce(func.sum(case((RequestLog.status_code != 200, 1), else_=0)), 0),
                ),
                user,
            ).where(RequestLog.created_at >= today)
        )
    ).one()

    requests_month, cost_month, price_month = (
        await session.execute(
            _scope(
                select(
                    func.count(RequestLog.id),
                    func.coalesce(func.sum(RequestLog.cost_usd), 0.0),
                    func.coalesce(func.sum(RequestLog.price_usd), 0.0),
                ),
                user,
            ).where(RequestLog.created_at >= month)
        )
    ).one()

    series = []
    for offset in range(6, -1, -1):
        start = days_ago(offset)
        end = start.replace(hour=23, minute=59, second=59)
        count, cost = (
            await session.execute(
                _scope(
                    select(
                        func.count(RequestLog.id),
                        func.coalesce(func.sum(RequestLog.cost_usd), 0.0),
                    ),
                    user,
                ).where(RequestLog.created_at >= start, RequestLog.created_at <= end)
            )
        ).one()
        series.append(
            {"day": start.date().isoformat(), "requests": int(count), "cost": round(float(cost), 6)}
        )

    providers = (
        await session.execute(select(Provider).order_by(Provider.priority))
    ).scalars().all()
    breakers = await health.snapshot([p.id for p in providers])
    provider_health = []
    for provider in providers:
        if not provider.is_active:
            continue
        breaker = breakers.get(provider.id, {})
        provider_health.append(
            {
                "id": provider.id,
                "name": provider.name,
                "label": provider.label or provider.name,
                "status": "cooling_down" if breaker.get("open") else provider.health_status,
                "configured": provider.has_key,
                "free_tier": provider.is_free_tier,
                "last_error": provider.last_error,
                "last_latency_ms": provider.last_latency_ms,
                "last_checked_at": iso(provider.last_checked_at),
                "retry_in_seconds": breaker.get("retry_in", 0),
            }
        )

    recent_rows = (
        await session.execute(
            _scope(
                select(RequestLog, GatewayKey, Partner, Provider)
                .outerjoin(GatewayKey, RequestLog.gateway_key_id == GatewayKey.id)
                .outerjoin(Partner, RequestLog.partner_id == Partner.id)
                .outerjoin(Provider, RequestLog.provider_id == Provider.id),
                user,
            )
            .order_by(RequestLog.created_at.desc(), RequestLog.id.desc())
            .limit(10)
        )
    ).all()
    recent = [
        {
            "id": log.id,
            "time": iso(log.created_at),
            "partner": partner.name if partner else None,
            "key_name": key.name if key else None,
            "requested_model": log.requested_model,
            "provider": prov.name if prov else None,
            "status_code": log.status_code,
            "latency_ms": log.latency_ms,
            "cost_usd": log.cost_usd,
            "cached": log.cached,
        }
        for log, key, partner, prov in recent_rows
    ]

    cap = float(settings_store.get("global_monthly_cap_usd", settings.global_monthly_cap_usd) or 0)
    spent = await limits.global_spend(session)
    owner = user.is_owner

    # A partner is measured against their own cap and their own spend, never
    # the global kill switch and never our margin.
    partner = await session.get(Partner, user.partner_id) if user.partner_id else None
    partner_use = (
        await limits.partner_usage(session, user.partner_id) if user.partner_id else None
    )
    if not owner:
        cap = float(partner.monthly_cap_usd or 0) if partner else 0.0
        spent = partner_use.cost_this_month if partner_use else 0.0

    alerts: list[dict] = []
    if owner:
        if cap and spent >= cap * 0.8:
            pct = int(spent / cap * 100)
            alerts.append(
                {
                    "level": "danger" if pct >= 100 else "warning",
                    "message": (
                        f"Global monthly spend is at {pct}% of the ${cap:.2f} cap."
                        + (" Paid routes are now blocked." if pct >= 100 else "")
                    ),
                }
            )
        misconfigured = [p["name"] for p in provider_health if p["status"] == "misconfigured"]
        if misconfigured:
            alerts.append(
                {
                    "level": "danger",
                    "message": f"Provider rejected its key: {', '.join(misconfigured)}.",
                }
            )
        if not any(p["configured"] for p in provider_health):
            alerts.append(
                {
                    "level": "warning",
                    "message": "No provider has an API key yet. Add one to start serving requests.",
                }
            )
    elif cap and spent >= cap * 0.8:
        pct = int(spent / cap * 100)
        alerts.append(
            {
                "level": "danger" if pct >= 100 else "warning",
                "message": f"Your monthly spend is at {pct}% of the ${cap:.2f} cap.",
            }
        )

    return {
        "role": "owner" if owner else "partner",
        "today": {
            "requests": int(requests_today),
            "tokens_in": int(tokens_in),
            "tokens_out": int(tokens_out),
            "tokens": int(tokens_in) + int(tokens_out),
            "cost_usd": round(float(price_today if not owner else cost_today), 6),
            "price_usd": round(float(price_today), 6) if owner else None,
            "errors": int(errors_today),
            "error_rate": round(float(errors_today) / requests_today * 100, 2) if requests_today else 0.0,
        },
        "month": {
            "requests": int(requests_month),
            "cost_usd": round(float(cost_month if owner else price_month), 6),
            "price_usd": round(float(price_month), 6) if owner else None,
            "margin_usd": round(float(price_month) - float(cost_month), 6) if owner else None,
            "cap_usd": cap,
            "spend_pct": round(spent / cap * 100, 1) if cap else 0.0,
        },
        "series": series,
        "providers": provider_health,
        "recent": recent,
        "alerts": alerts,
        "cache": await response_cache.stats(session),
        "generated_at": iso(utcnow()),
    }


@router.get("/usage")
async def usage(
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
    days: int = Query(default=30, ge=1, le=365),
):
    start = days_ago(days - 1)
    owner = user.is_owner

    daily = []
    for offset in range(days - 1, -1, -1):
        day = days_ago(offset)
        end = day.replace(hour=23, minute=59, second=59)
        count, cost, price, tokens, cached = (
            await session.execute(
                _scope(
                    select(
                        func.count(RequestLog.id),
                        func.coalesce(func.sum(RequestLog.cost_usd), 0.0),
                        func.coalesce(func.sum(RequestLog.price_usd), 0.0),
                        func.coalesce(
                            func.sum(RequestLog.prompt_tokens + RequestLog.completion_tokens), 0
                        ),
                        func.coalesce(
                            func.sum(case((RequestLog.cached.is_(True), 1), else_=0)), 0
                        ),
                    ),
                    user,
                ).where(RequestLog.created_at >= day, RequestLog.created_at <= end)
            )
        ).one()
        daily.append(
            {
                "day": day.date().isoformat(),
                "requests": int(count),
                "cost": round(float(cost if owner else price), 6),
                "price": round(float(price), 6) if owner else None,
                "tokens": int(tokens),
                "cached": int(cached),
            }
        )

    by_model = (
        await session.execute(
            _scope(
                select(
                    Model.alias,
                    Model.model_name,
                    Provider.name,
                    func.count(RequestLog.id),
                    func.coalesce(
                        func.sum(RequestLog.prompt_tokens + RequestLog.completion_tokens), 0
                    ),
                    func.coalesce(func.sum(RequestLog.cost_usd), 0.0),
                    func.coalesce(func.sum(RequestLog.price_usd), 0.0),
                )
                .join(Model, RequestLog.model_id == Model.id)
                .join(Provider, RequestLog.provider_id == Provider.id),
                user,
            )
            .where(RequestLog.created_at >= start)
            .group_by(Model.alias, Model.model_name, Provider.name)
            .order_by(func.count(RequestLog.id).desc())
            .limit(25)
        )
    ).all()

    by_provider = (
        await session.execute(
            _scope(
                select(
                    Provider.name,
                    func.count(RequestLog.id),
                    func.coalesce(func.sum(RequestLog.cost_usd), 0.0),
                    func.coalesce(func.avg(RequestLog.latency_ms), 0),
                ).join(Provider, RequestLog.provider_id == Provider.id),
                user,
            )
            .where(RequestLog.created_at >= start)
            .group_by(Provider.name)
            .order_by(func.count(RequestLog.id).desc())
        )
    ).all()

    by_key = (
        await session.execute(
            _scope(
                select(
                    GatewayKey.name,
                    GatewayKey.key_prefix,
                    func.count(RequestLog.id),
                    func.coalesce(func.sum(RequestLog.cost_usd), 0.0),
                    func.coalesce(func.sum(RequestLog.price_usd), 0.0),
                ).join(GatewayKey, RequestLog.gateway_key_id == GatewayKey.id),
                user,
            )
            .where(RequestLog.created_at >= start)
            .group_by(GatewayKey.name, GatewayKey.key_prefix)
            .order_by(func.count(RequestLog.id).desc())
        )
    ).all()

    by_partner = (
        await session.execute(
            _scope(
                select(
                    Partner.name,
                    Partner.slug,
                    func.count(RequestLog.id),
                    func.coalesce(func.sum(RequestLog.cost_usd), 0.0),
                    func.coalesce(func.sum(RequestLog.price_usd), 0.0),
                ).join(Partner, RequestLog.partner_id == Partner.id),
                user,
            )
            .where(RequestLog.created_at >= start)
            .group_by(Partner.name, Partner.slug)
            .order_by(func.count(RequestLog.id).desc())
        )
    ).all()

    total_requests = sum(d["requests"] for d in daily)
    cached_total = sum(d["cached"] for d in daily)
    cap = float(settings_store.get("global_monthly_cap_usd", settings.global_monthly_cap_usd) or 0)
    spent = await limits.global_spend(session)

    # Partners are measured against their own cap; the global kill switch is
    # the owner's business.
    if not owner and user.partner_id:
        partner = await session.get(Partner, user.partner_id)
        partner_use = await limits.partner_usage(session, user.partner_id)
        cap = float(partner.monthly_cap_usd or 0) if partner else 0.0
        spent = partner_use.cost_this_month

    return {
        "days": days,
        "role": "owner" if owner else "partner",
        "daily": daily,
        "by_model": [
            {
                "model": alias or model_name,
                "provider": provider,
                "requests": int(count),
                "tokens": int(tokens),
                "cost": round(float(cost if owner else price), 6),
                "price": round(float(price), 6) if owner else None,
            }
            for alias, model_name, provider, count, tokens, cost, price in by_model
        ],
        "by_provider": [
            {
                "provider": provider,
                "requests": int(count),
                "cost": round(float(cost if owner else 0.0), 6) if owner else None,
                "avg_latency_ms": int(latency or 0),
            }
            for provider, count, cost, latency in by_provider
        ],
        "by_key": [
            {
                "key": name,
                "prefix": prefix,
                "requests": int(count),
                "cost": round(float(cost if owner else price), 6),
                "price": round(float(price), 6) if owner else None,
            }
            for name, prefix, count, cost, price in by_key
        ],
        "by_partner": [
            {
                "partner": name,
                "slug": slug,
                "requests": int(count),
                "cost": round(float(cost if owner else price), 6),
                "price": round(float(price), 6) if owner else None,
                "margin": round(float(price) - float(cost), 6) if owner else None,
            }
            for name, slug, count, cost, price in by_partner
        ],
        "budget": {
            "month_to_date_usd": round(spent, 6),
            "cap_usd": cap,
            "pct": round(spent / cap * 100, 1) if cap else 0.0,
        },
        "cache": {
            "requests": total_requests,
            "hits": cached_total,
            "hit_rate": round(cached_total / total_requests * 100, 1) if total_requests else 0.0,
        },
    }
