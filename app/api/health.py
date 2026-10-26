"""Health and metrics endpoints (brief §5.5)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import PlainTextResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import __version__
from app.api.deps import AuthFailure
from app.config import settings
from app.core import health, metrics
from app.core.store import get_store
from app.db import get_session, ping_db
from app.models import Provider
from app.utils import iso, utcnow

router = APIRouter(tags=["gateway"])


@router.get("/health")
async def health_check(session: AsyncSession = Depends(get_session)):
    """Public liveness + provider status. Contains no secrets."""
    db_ok = await ping_db()
    store_ok = await get_store().healthy()

    providers = (
        await session.execute(select(Provider).where(Provider.is_active.is_(True)))
    ).scalars().all()
    breakers = await health.snapshot([p.id for p in providers])

    items = []
    for provider in providers:
        breaker = breakers.get(provider.id, {})
        status = provider.health_status
        if breaker.get("open"):
            status = "cooling_down"
        items.append(
            {
                "name": provider.name,
                "status": status,
                "configured": provider.has_key,
                "free_tier": provider.is_free_tier,
                "last_latency_ms": provider.last_latency_ms,
                "last_error": provider.last_error,
                "last_checked_at": iso(provider.last_checked_at),
                "retry_in_seconds": breaker.get("retry_in", 0),
            }
        )

    ready = db_ok and store_ok
    usable = [i for i in items if i["configured"] and i["status"] not in ("cooling_down", "misconfigured")]
    return {
        "status": "ok" if ready and usable else ("degraded" if ready else "down"),
        "version": __version__,
        "time": iso(utcnow()),
        "checks": {"database": db_ok, "shared_state": store_ok},
        "shared_state": "redis" if settings.redis_url else "memory",
        "providers": items,
        "usable_providers": len(usable),
    }


@router.get("/readyz", include_in_schema=False)
async def readyz(session: AsyncSession = Depends(get_session)):
    """Kubernetes/load-balancer readiness: is this instance able to serve?"""
    if not await ping_db():
        return Response(status_code=503, content="database unavailable")
    return {"status": "ready"}


@router.get("/metrics", include_in_schema=False)
async def prometheus_metrics(
    request: Request,
    x_metrics_token: str | None = Header(default=None),
):
    """Internal only. Guarded by a shared token, never exposed publicly."""
    token = settings.metrics_token
    if token:
        if not x_metrics_token or not secrets_compare(x_metrics_token, token):
            raise AuthFailure(403, "forbidden", "Metrics require a valid token.")
    else:
        # No token configured: allow only from loopback, so a default deploy is safe.
        client = request.client.host if request.client else ""
        if client not in ("127.0.0.1", "::1", "localhost"):
            raise AuthFailure(403, "forbidden", "Metrics are restricted to local access.")

    return PlainTextResponse(metrics.render(), media_type="text/plain; version=0.0.4")


def secrets_compare(a: str, b: str) -> bool:
    import hmac

    return hmac.compare_digest(a.encode(), b.encode())
