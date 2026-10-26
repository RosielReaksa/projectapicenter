"""Gateway key management (control plane)."""

from __future__ import annotations

import json
from datetime import datetime

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import client_ip, current_user
from app.core import audit, limits
from app.core.auth import generate_gateway_key, gateway_key_prefix, hash_gateway_key
from app.db import get_session
from app.errors import error_response
from app.models import GatewayKey, Partner, User
from app.schemas import KeyCreateRequest, KeyUpdateRequest
from app.utils import iso, utcnow

router = APIRouter(prefix="/admin/keys", tags=["admin-keys"])


def _parse_expiry(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _serialize(key: GatewayKey, usage: limits.Usage | None = None) -> dict:
    return {
        "id": key.id,
        "name": key.name,
        "partner_id": key.partner_id,
        "partner_name": key.partner.name if key.partner else None,
        "partner_slug": key.partner.slug if key.partner else None,
        "prefix": key.key_prefix,
        "daily_limit": key.daily_limit,
        "monthly_cap_usd": key.monthly_cap_usd,
        "rpm_limit": key.rpm_limit,
        "tpm_limit": key.tpm_limit,
        "concurrency_limit": key.concurrency_limit,
        "allowed_models": json.loads(key.allowed_models) if key.allowed_models else None,
        "allowed_ips": json.loads(key.allowed_ips) if key.allowed_ips else None,
        "allow_free_tier": key.allow_free_tier,
        "expires_at": iso(key.expires_at),
        "is_active": key.is_active,
        "last_used_at": iso(key.last_used_at),
        "created_at": iso(key.created_at),
        "requests_today": usage.requests_today if usage else None,
        "cost_this_month": round(usage.cost_this_month, 6) if usage else None,
    }


def _visible_partner_ids(user: User) -> list[int] | None:
    """None means 'all partners' (owner)."""
    if user.is_owner:
        return None
    return [user.partner_id] if user.partner_id else []


@router.get("")
async def list_keys(
    user: User = Depends(current_user), session: AsyncSession = Depends(get_session)
):
    query = (
        select(GatewayKey)
        .options(selectinload(GatewayKey.partner))
        .order_by(GatewayKey.created_at.desc())
    )
    visible = _visible_partner_ids(user)
    if visible is not None:
        query = query.where(GatewayKey.partner_id.in_(visible or [-1]))

    keys = (await session.execute(query)).scalars().all()
    out = []
    for key in keys:
        usage = await limits.key_usage(session, key.id)
        out.append(_serialize(key, usage))
    return {"keys": out}


@router.post("", status_code=201)
async def create_key(
    payload: KeyCreateRequest,
    request: Request,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    partner_id = payload.partner_id or user.partner_id
    if not partner_id:
        return error_response(400, "invalid_request", "partner_id is required.", "")

    if not user.is_owner and partner_id != user.partner_id:
        return error_response(403, "forbidden", "You can only create keys for your own partner.", "")

    partner = await session.get(Partner, partner_id)
    if not partner or not partner.is_active:
        return error_response(400, "invalid_request", "Unknown or inactive partner.", "")

    raw_key = generate_gateway_key()
    key = GatewayKey(
        partner_id=partner_id,
        name=payload.name.strip(),
        key_hash=hash_gateway_key(raw_key),
        key_prefix=gateway_key_prefix(raw_key),
        daily_limit=payload.daily_limit,
        monthly_cap_usd=payload.monthly_cap_usd,
        rpm_limit=payload.rpm_limit,
        tpm_limit=payload.tpm_limit,
        concurrency_limit=payload.concurrency_limit,
        allowed_models=json.dumps(payload.allowed_models) if payload.allowed_models else None,
        allowed_ips=json.dumps(payload.allowed_ips) if payload.allowed_ips else None,
        allow_free_tier=payload.allow_free_tier,
        expires_at=_parse_expiry(payload.expires_at),
        created_at=utcnow(),
    )
    session.add(key)
    await session.commit()
    await session.refresh(key)
    key.partner = partner

    await audit.record(
        session,
        "key.create",
        user_id=user.id,
        target=f"{partner.slug}/{key.name} ({key.key_prefix})",
        ip=client_ip(request),
    )

    body = _serialize(key)
    body["key"] = raw_key  # shown exactly once
    return body


@router.patch("/{key_id}")
async def update_key(
    key_id: int,
    payload: KeyUpdateRequest,
    request: Request,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    key = await session.get(GatewayKey, key_id)
    if not key:
        return error_response(404, "not_found", "Key not found.", "")
    if not user.is_owner and key.partner_id != user.partner_id:
        return error_response(403, "forbidden", "Not your key.", "")

    data = payload.model_dump(exclude_unset=True)
    if data.get("name"):
        key.name = data["name"].strip()
    for field in (
        "daily_limit",
        "monthly_cap_usd",
        "rpm_limit",
        "tpm_limit",
        "concurrency_limit",
        "allow_free_tier",
        "is_active",
    ):
        if field in data:
            setattr(key, field, data[field])
    if "allowed_models" in data:
        key.allowed_models = json.dumps(data["allowed_models"]) if data["allowed_models"] else None
    if "allowed_ips" in data:
        key.allowed_ips = json.dumps(data["allowed_ips"]) if data["allowed_ips"] else None
    if "expires_at" in data:
        key.expires_at = _parse_expiry(data["expires_at"])

    await session.commit()
    await audit.record(
        session, "key.update", user_id=user.id, target=str(key.id), ip=client_ip(request)
    )
    return _serialize(key)


@router.post("/{key_id}/disable")
async def disable_key(
    key_id: int,
    request: Request,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    return await _set_active(key_id, False, request, user, session)


@router.post("/{key_id}/enable")
async def enable_key(
    key_id: int,
    request: Request,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    return await _set_active(key_id, True, request, user, session)


async def _set_active(
    key_id: int, active: bool, request: Request, user: User, session: AsyncSession
):
    key = await session.get(GatewayKey, key_id)
    if not key:
        return error_response(404, "not_found", "Key not found.", "")
    if not user.is_owner and key.partner_id != user.partner_id:
        return error_response(403, "forbidden", "Not your key.", "")

    key.is_active = active
    await session.commit()
    await audit.record(
        session,
        "key.enable" if active else "key.disable",
        user_id=user.id,
        target=str(key_id),
        ip=client_ip(request),
    )
    return {"id": key.id, "is_active": active}


@router.delete("/{key_id}", status_code=204)
async def delete_key(
    key_id: int,
    request: Request,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    key = await session.get(GatewayKey, key_id)
    if not key:
        return error_response(404, "not_found", "Key not found.", "")
    if not user.is_owner and key.partner_id != user.partner_id:
        return error_response(403, "forbidden", "Not your key.", "")

    await session.delete(key)
    await session.commit()
    await audit.record(
        session, "key.delete", user_id=user.id, target=str(key_id), ip=client_ip(request)
    )
    return None
