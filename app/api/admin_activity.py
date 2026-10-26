"""Admin activity log: filters, detail drawer, CSV export."""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthFailure, current_user
from app.db import get_session
from app.models import GatewayKey, Model, Partner, Provider, RequestLog, User
from app.utils import iso

router = APIRouter(prefix="/admin/activity", tags=["admin-activity"])


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _serialize(row: RequestLog, key, partner, provider, model, owner: bool = True) -> dict:
    body = {
        "id": row.id,
        "request_id": row.request_id,
        "time": iso(row.created_at),
        "key_id": row.gateway_key_id,
        "key_name": key.name if key else None,
        "key_prefix": key.key_prefix if key else None,
        "partner_id": row.partner_id,
        "partner": partner.name if partner else None,
        "partner_slug": partner.slug if partner else None,
        "end_user": row.end_user,
        "requested_model": row.requested_model,
        "provider": provider.name if provider else None,
        "model": model.public_name if model else None,
        "status_code": row.status_code,
        "latency_ms": row.latency_ms,
        "prompt_tokens": row.prompt_tokens,
        "completion_tokens": row.completion_tokens,
        "total_tokens": row.prompt_tokens + row.completion_tokens,
        "cost_usd": row.cost_usd if owner else row.price_usd,
        "price_usd": row.price_usd if owner else None,
        "cached": row.cached,
        "error": row.error,
        "fallbacks_tried": json.loads(row.fallbacks_tried) if row.fallbacks_tried else [],
        "source": row.source,
        "has_payload": bool(row.payload_json),
    }
    return body


def _base_query(user: User):
    query = (
        select(RequestLog, GatewayKey, Partner, Provider, Model)
        .outerjoin(GatewayKey, RequestLog.gateway_key_id == GatewayKey.id)
        .outerjoin(Partner, RequestLog.partner_id == Partner.id)
        .outerjoin(Provider, RequestLog.provider_id == Provider.id)
        .outerjoin(Model, RequestLog.model_id == Model.id)
    )
    if not user.is_owner:
        query = query.where(RequestLog.partner_id == (user.partner_id or -1))
    return query


def _apply(
    query, *, start, end, key_id, provider, status, cached, model, partner_id, end_user, search
):
    if start:
        query = query.where(RequestLog.created_at >= start)
    if end:
        query = query.where(RequestLog.created_at <= end)
    if key_id:
        query = query.where(RequestLog.gateway_key_id == key_id)
    if partner_id:
        query = query.where(RequestLog.partner_id == partner_id)
    if provider:
        query = query.where(Provider.name == provider)
    if model:
        query = query.where(RequestLog.requested_model == model)
    if end_user:
        query = query.where(RequestLog.end_user == end_user)
    if status == "success":
        query = query.where(RequestLog.status_code == 200)
    elif status == "error":
        query = query.where(RequestLog.status_code != 200)
    if cached is not None:
        query = query.where(RequestLog.cached.is_(cached))
    if search:
        query = query.where(RequestLog.request_id.ilike(f"%{search}%"))
    return query


@router.get("")
async def list_activity(
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
    start: str | None = None,
    end: str | None = None,
    key_id: int | None = None,
    partner_id: int | None = None,
    provider: str | None = None,
    status: str | None = None,
    cached: bool | None = None,
    model: str | None = None,
    end_user: str | None = None,
    search: str | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    query = _apply(
        _base_query(user),
        start=_parse_date(start),
        end=_parse_date(end),
        key_id=key_id,
        partner_id=partner_id,
        provider=provider,
        status=status,
        cached=cached,
        model=model,
        end_user=end_user,
        search=search,
    )
    total = (
        await session.execute(select(func.count()).select_from(query.subquery()))
    ).scalar() or 0
    rows = await session.execute(
        query.order_by(RequestLog.created_at.desc(), RequestLog.id.desc()).limit(limit).offset(offset)
    )
    return {
        "requests": [_serialize(*row, owner=user.is_owner) for row in rows.all()],
        "total": int(total),
        "limit": limit,
        "offset": offset,
    }


@router.get("/export/csv")
async def export_csv(
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
    start: str | None = None,
    end: str | None = None,
    key_id: int | None = None,
    partner_id: int | None = None,
    provider: str | None = None,
    status: str | None = None,
    cached: bool | None = None,
):
    query = _apply(
        _base_query(user),
        start=_parse_date(start),
        end=_parse_date(end),
        key_id=key_id,
        partner_id=partner_id,
        provider=provider,
        status=status,
        cached=cached,
        model=None,
        end_user=None,
        search=None,
    )
    rows = await session.execute(query.order_by(RequestLog.created_at.desc()).limit(50_000))

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "time", "request_id", "partner", "key", "end_user", "requested_model",
            "provider", "model", "status", "latency_ms", "prompt_tokens",
            "completion_tokens", "cost_usd", "price_usd", "cached", "error",
        ]
    )
    for log, key, partner, prov, mod in rows.all():
        writer.writerow(
            [
                iso(log.created_at),
                log.request_id,
                partner.slug if partner else "",
                key.key_prefix if key else "",
                log.end_user or "",
                log.requested_model or "",
                prov.name if prov else "",
                mod.public_name if mod else "",
                log.status_code,
                log.latency_ms,
                log.prompt_tokens,
                log.completion_tokens,
                f"{log.cost_usd:.8f}",
                f"{log.price_usd:.8f}",
                int(log.cached),
                (log.error or "")[:200],
            ]
        )

    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="activity.csv"'},
    )


@router.get("/{log_id}")
async def get_activity(
    log_id: int,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    row = await session.get(RequestLog, log_id)
    if not row:
        raise AuthFailure(404, "not_found", "Request not found.")
    if not user.is_owner and row.partner_id != user.partner_id:
        raise AuthFailure(403, "forbidden", "That request belongs to another partner.")

    key = await session.get(GatewayKey, row.gateway_key_id) if row.gateway_key_id else None
    partner = await session.get(Partner, row.partner_id) if row.partner_id else None
    provider = await session.get(Provider, row.provider_id) if row.provider_id else None
    model = await session.get(Model, row.model_id) if row.model_id else None

    body = _serialize(row, key, partner, provider, model, owner=user.is_owner)
    body["payload"] = json.loads(row.payload_json) if row.payload_json else None
    return body
