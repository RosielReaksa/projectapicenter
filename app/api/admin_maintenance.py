"""Maintenance controls (MAINTENANCE_SPEC §8).

All owner-only, and deliberately exempt from maintenance blocking so the owner
can always turn it off.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import client_ip, owner_user
from app.config import settings
from app.core import audit, maintenance
from app.db import get_session
from app.errors import error_response
from app.models import MaintenanceHistory, MaintenanceWindow, Partner, Provider, User
from app.schemas import (
    DegradedRequest,
    MaintenanceStartRequest,
    MaintenanceStopRequest,
    ProviderPauseRequest,
    PartnerSuspendRequest,
    WindowCreateRequest,
    WindowUpdateRequest,
)
from app.utils import iso, utcnow

router = APIRouter(prefix="/admin/maintenance", tags=["admin-maintenance"])


async def _record(
    session: AsyncSession,
    *,
    user: User | None,
    source: str,
    level: str,
    action: str,
    target: str | None = None,
    reason: str | None = None,
    ip: str | None = None,
) -> None:
    """Every change goes to maintenance_history and the main audit_log (§3.3)."""
    session.add(
        MaintenanceHistory(
            created_at=utcnow(),
            user_id=user.id if user else None,
            source=source,
            level=level,
            target=target,
            action=action,
            reason=reason,
        )
    )
    await session.commit()
    await audit.record(
        session,
        f"maintenance.{level}.{action}",
        user_id=user.id if user else None,
        target=target or reason,
        ip=ip,
    )


@router.get("")
async def get_maintenance(
    _: User = Depends(owner_user), session: AsyncSession = Depends(get_session)
):
    snapshot = await maintenance.get_state(force=True)

    providers = (await session.execute(select(Provider).order_by(Provider.priority))).scalars().all()
    partners = (await session.execute(select(Partner).order_by(Partner.name))).scalars().all()
    windows = (
        await session.execute(
            select(MaintenanceWindow)
            .where(MaintenanceWindow.status.in_(["scheduled", "active"]))
            .order_by(MaintenanceWindow.starts_at)
        )
    ).scalars().all()

    g = snapshot.global_state
    d = snapshot.degraded
    return {
        "global": {
            "active": g.active,
            "message": g.message,
            "started_at": iso(g.started_at),
            "expected_end": iso(g.expected_end),
            "allow_ips": g.allow_ips,
            "started_by": g.started_by,
        },
        "degraded": {
            "active": d.active,
            "paid_only": d.paid_only,
            "free_only": d.free_only,
            "reduce_concurrency_pct": d.reduce_concurrency_pct,
            "max_tokens": d.max_tokens,
            "reject_low_priority": d.reject_low_priority,
            "message": d.message,
            "restrictions": d.restrictions,
        },
        "providers": [
            {
                "id": p.id,
                "name": p.name,
                "label": p.label or p.name,
                "paused": p.paused,
                "paused_reason": p.paused_reason,
                "health_status": p.health_status,
                "has_key": p.has_key,
            }
            for p in providers
        ],
        "partners": [
            {
                "id": p.id,
                "name": p.name,
                "slug": p.slug,
                "suspended": p.suspended,
                "suspended_reason_public": p.suspended_reason_public,
                "suspended_at": iso(p.suspended_at),
                "is_active": p.is_active,
            }
            for p in partners
        ],
        "windows": [
            {
                "id": w.id,
                "starts_at": iso(w.starts_at),
                "ends_at": iso(w.ends_at),
                "banner_text": w.banner_text,
                "auto_enable": w.auto_enable,
                "status": w.status,
            }
            for w in windows
        ],
        "drain": maintenance.drain_status(snapshot),
        "stale": snapshot.stale,
        "defaults": {
            "retry_after": settings.maintenance_retry_after,
            "drain_timeout": settings.maintenance_drain_timeout,
            "fail_mode": settings.maint_fail_mode,
        },
    }


@router.post("/global/start")
async def start_maintenance(
    payload: MaintenanceStartRequest,
    request: Request,
    user: User = Depends(owner_user),
    session: AsyncSession = Depends(get_session),
):
    if not payload.reason.strip():
        return error_response(400, "reason_required", "A reason is required.", "")

    expected_end = _parse_dt(payload.expected_end)
    allow_ips = list(payload.allow_ips or [])
    ip = client_ip(request)
    if payload.allow_current_ip and ip and ip not in allow_ips:
        allow_ips.append(ip)

    state = await maintenance.start_global(
        session,
        message=payload.message or "The service is temporarily paused for maintenance.",
        expected_end=expected_end,
        allow_ips=allow_ips,
        started_by=user.id,
    )
    await _record(
        session,
        user=user,
        source="dashboard",
        level="global",
        action="on",
        reason=payload.reason,
        ip=ip,
    )
    return {
        "active": state.active,
        "message": state.message,
        "expected_end": iso(state.expected_end),
        "allow_ips": state.allow_ips,
        "retry_after": state.retry_after(settings.maintenance_retry_after),
    }


@router.post("/global/stop")
async def stop_maintenance(
    payload: MaintenanceStopRequest,
    request: Request,
    user: User = Depends(owner_user),
    session: AsyncSession = Depends(get_session),
):
    await maintenance.stop_global(session)
    await _record(
        session,
        user=user,
        source="dashboard",
        level="global",
        action="off",
        reason=payload.reason,
        ip=client_ip(request),
    )
    return {"active": False}


@router.post("/degraded")
async def set_degraded(
    payload: DegradedRequest,
    request: Request,
    user: User = Depends(owner_user),
    session: AsyncSession = Depends(get_session),
):
    state = maintenance.DegradedState(
        active=payload.active,
        paid_only=payload.paid_only,
        free_only=payload.free_only,
        reduce_concurrency_pct=payload.reduce_concurrency_pct,
        max_tokens=payload.max_tokens,
        reject_low_priority=payload.reject_low_priority,
        message=payload.message or "",
    )
    await maintenance.set_degraded(session, state)
    await _record(
        session,
        user=user,
        source="dashboard",
        level="degraded",
        action="on" if payload.active else "off",
        reason=payload.reason,
        ip=client_ip(request),
    )
    return {
        "active": state.active,
        "restrictions": state.restrictions,
    }


@router.get("/history")
async def history(
    _: User = Depends(owner_user),
    session: AsyncSession = Depends(get_session),
    limit: int = Query(default=100, ge=1, le=500),
):
    rows = (
        await session.execute(
            select(MaintenanceHistory)
            .order_by(MaintenanceHistory.created_at.desc())
            .limit(limit)
        )
    ).scalars().all()
    return {
        "entries": [
            {
                "id": r.id,
                "time": iso(r.created_at),
                "user_id": r.user_id,
                "source": r.source,
                "level": r.level,
                "target": r.target,
                "action": r.action,
                "reason": r.reason,
            }
            for r in rows
        ]
    }


# ------------------------------------------------------------------- windows
@router.get("/windows")
async def list_windows(
    _: User = Depends(owner_user), session: AsyncSession = Depends(get_session)
):
    rows = (
        await session.execute(select(MaintenanceWindow).order_by(MaintenanceWindow.starts_at))
    ).scalars().all()
    return {
        "windows": [
            {
                "id": w.id,
                "starts_at": iso(w.starts_at),
                "ends_at": iso(w.ends_at),
                "banner_text": w.banner_text,
                "auto_enable": w.auto_enable,
                "status": w.status,
            }
            for w in rows
        ]
    }


@router.post("/windows", status_code=201)
async def create_window(
    payload: WindowCreateRequest,
    request: Request,
    user: User = Depends(owner_user),
    session: AsyncSession = Depends(get_session),
):
    starts = _parse_dt(payload.starts_at)
    ends = _parse_dt(payload.ends_at)
    if not starts or not ends:
        return error_response(400, "invalid_request", "starts_at and ends_at are required.", "")
    if ends <= starts:
        return error_response(400, "invalid_request", "ends_at must be after starts_at.", "")

    window = MaintenanceWindow(
        starts_at=starts,
        ends_at=ends,
        banner_text=payload.banner_text.strip()[:300],
        auto_enable=payload.auto_enable,
        status="scheduled",
        created_by=user.id,
        created_at=utcnow(),
    )
    session.add(window)
    await session.commit()
    await session.refresh(window)
    await _record(
        session,
        user=user,
        source="dashboard",
        level="global",
        action="schedule",
        target=str(window.id),
        reason=window.banner_text,
        ip=client_ip(request),
    )
    return {"id": window.id, "status": window.status}


@router.patch("/windows/{window_id}")
async def update_window(
    window_id: int,
    payload: WindowUpdateRequest,
    user: User = Depends(owner_user),
    session: AsyncSession = Depends(get_session),
):
    window = await session.get(MaintenanceWindow, window_id)
    if not window:
        return error_response(404, "not_found", "Window not found.", "")
    if window.status != "scheduled":
        return error_response(
            400, "invalid_request", "Only a scheduled window can be edited.", ""
        )

    if payload.ends_at:
        ends = _parse_dt(payload.ends_at)
        if ends and ends > window.starts_at:
            window.ends_at = ends
    if payload.banner_text:
        window.banner_text = payload.banner_text.strip()[:300]
    if payload.auto_enable is not None:
        window.auto_enable = payload.auto_enable
    await session.commit()
    return {"id": window.id, "status": window.status}


@router.delete("/windows/{window_id}", status_code=204)
async def cancel_window(
    window_id: int,
    request: Request,
    user: User = Depends(owner_user),
    session: AsyncSession = Depends(get_session),
):
    window = await session.get(MaintenanceWindow, window_id)
    if not window:
        return error_response(404, "not_found", "Window not found.", "")
    window.status = "cancelled"
    await session.commit()
    await _record(
        session,
        user=user,
        source="dashboard",
        level="global",
        action="cancel",
        target=str(window_id),
        ip=client_ip(request),
    )
    return None


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


# --------------------------------------------------- provider / partner scope
providers_router = APIRouter(prefix="/admin/providers", tags=["admin-maintenance"])


@providers_router.post("/{provider_id}/pause")
async def pause_provider(
    provider_id: int,
    payload: ProviderPauseRequest,
    request: Request,
    user: User = Depends(owner_user),
    session: AsyncSession = Depends(get_session),
):
    provider = await session.get(Provider, provider_id)
    if not provider:
        return error_response(404, "not_found", "Provider not found.", "")
    await maintenance.set_provider_paused(session, provider, True, payload.reason)
    await _record(
        session,
        user=user,
        source="dashboard",
        level="provider",
        action="on",
        target=provider.name,
        reason=payload.reason,
        ip=client_ip(request),
    )
    return {"id": provider.id, "paused": True}


@providers_router.post("/{provider_id}/resume")
async def resume_provider(
    provider_id: int,
    request: Request,
    user: User = Depends(owner_user),
    session: AsyncSession = Depends(get_session),
):
    provider = await session.get(Provider, provider_id)
    if not provider:
        return error_response(404, "not_found", "Provider not found.", "")
    await maintenance.set_provider_paused(session, provider, False, None)
    await _record(
        session,
        user=user,
        source="dashboard",
        level="provider",
        action="off",
        target=provider.name,
        ip=client_ip(request),
    )
    return {"id": provider.id, "paused": False}


partners_router = APIRouter(prefix="/admin/partners", tags=["admin-maintenance"])


@partners_router.post("/{partner_id}/suspend")
async def suspend_partner(
    partner_id: int,
    payload: PartnerSuspendRequest,
    request: Request,
    user: User = Depends(owner_user),
    session: AsyncSession = Depends(get_session),
):
    partner = await session.get(Partner, partner_id)
    if not partner:
        return error_response(404, "not_found", "Partner not found.", "")
    if not payload.reason_internal.strip():
        return error_response(400, "reason_required", "An internal reason is required.", "")

    await maintenance.set_partner_suspended(
        session, partner, True, payload.reason_internal, payload.reason_public
    )
    await _record(
        session,
        user=user,
        source="dashboard",
        level="partner",
        action="on",
        target=partner.slug,
        reason=payload.reason_internal,
        ip=client_ip(request),
    )
    return {
        "id": partner.id,
        "suspended": True,
        "reason_public": partner.suspended_reason_public,
    }


@partners_router.post("/{partner_id}/resume")
async def resume_partner(
    partner_id: int,
    request: Request,
    user: User = Depends(owner_user),
    session: AsyncSession = Depends(get_session),
):
    partner = await session.get(Partner, partner_id)
    if not partner:
        return error_response(404, "not_found", "Partner not found.", "")
    await maintenance.set_partner_suspended(session, partner, False, None, None)
    await _record(
        session,
        user=user,
        source="dashboard",
        level="partner",
        action="off",
        target=partner.slug,
        ip=client_ip(request),
    )
    return {"id": partner.id, "suspended": False}
