"""Playground, members, settings, audit and danger-zone endpoints."""

from __future__ import annotations

import json
from datetime import timedelta

from fastapi import APIRouter, Depends, Request
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import client_ip, current_user, owner_user
from app.config import settings
from app.core import audit, cache as response_cache, config_cache, health, settings_store
from app.core.auth import generate_gateway_key, gateway_key_prefix, hash_password, hash_gateway_key
from app.core.config_cache import KeySnapshot
from app.core.execute import ChatError, execute_chat
from app.db import get_session
from app.errors import error_response
from app.models import GatewayKey, Model, Partner, Preset, Provider, RequestLog, User
from app.schemas import MemberCreateRequest, MemberUpdateRequest, PlaygroundRequest, SettingsUpdateRequest
from app.utils import iso, utcnow

router = APIRouter(prefix="/admin", tags=["admin"])


async def _playground_key(session: AsyncSession, partner: Partner) -> KeySnapshot:
    """The key the playground spends against, as a detached snapshot.

    Reuses the partner's first active key, or creates an internal one so that
    limits, logging and cost all behave exactly as they do for /v1 traffic.
    """
    key = (
        await session.execute(
            select(GatewayKey)
            .where(GatewayKey.partner_id == partner.id, GatewayKey.is_active.is_(True))
            .order_by(GatewayKey.created_at)
        )
    ).scalars().first()

    if key is None:
        raw = generate_gateway_key()
        key = GatewayKey(
            partner_id=partner.id,
            name="Playground (auto)",
            key_hash=hash_gateway_key(raw),
            key_prefix=gateway_key_prefix(raw),
            is_active=True,
            created_at=utcnow(),
        )
        session.add(key)
        await session.commit()
        await session.refresh(key)

    return config_cache.key_snapshot(key, partner)


# --------------------------------------------------------------- playground
@router.post("/playground/run")
async def playground_run(
    payload: PlaygroundRequest,
    request: Request,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    """Same pipeline as /v1, using the signed-in user's partner instead of a key."""
    partner = None
    if user.partner_id:
        partner = await session.get(Partner, user.partner_id)
    if partner is None:
        partner = (
            await session.execute(
                select(Partner).where(Partner.is_active.is_(True)).order_by(Partner.id)
            )
        ).scalars().first()
    if partner is None:
        return error_response(
            400, "invalid_request", "Create a partner first, then add a provider key.", ""
        )

    # The playground is a control-plane call, so its own request session is
    # fine here; it is released before execute_chat opens its short sessions.
    key_snapshot = await _playground_key(session, partner)

    messages = list(payload.messages)
    if payload.system:
        messages = [{"role": "system", "content": payload.system}] + messages

    body: dict = {"model": payload.model, "messages": messages}
    if payload.temperature is not None:
        body["temperature"] = payload.temperature
    if payload.max_tokens is not None:
        body["max_tokens"] = payload.max_tokens
    if payload.top_p is not None:
        body["top_p"] = payload.top_p
    if payload.stop:
        body["stop"] = payload.stop
    if payload.preset:
        body["preset"] = payload.preset

    try:
        outcome = await execute_chat(
            key=key_snapshot,
            partner=partner,
            body=body,
            client=request.app.state.http,
            source="playground",
        )
    except ChatError as exc:
        return error_response(
            exc.status, exc.code, exc.message, exc.request_id, attempts=exc.attempts or None
        )

    content = ""
    choices = outcome.body.get("choices") or []
    if choices:
        content = (choices[0].get("message") or {}).get("content") or ""

    return {
        "content": content,
        "raw": outcome.body,
        "gateway": {
            "provider": outcome.provider,
            "model": outcome.model,
            "cached": outcome.cached,
            "cost_usd": outcome.cost_usd,
            "price_usd": outcome.price_usd,
            "latency_ms": outcome.latency_ms,
            "prompt_tokens": outcome.prompt_tokens,
            "completion_tokens": outcome.completion_tokens,
            "request_id": outcome.request_id,
            "estimated_usage": outcome.estimated_usage,
            "fallbacks_tried": [a.as_dict() for a in outcome.attempts],
        },
    }


# ----------------------------------------------------------------- members
@router.get("/members")
async def list_members(
    _: User = Depends(owner_user), session: AsyncSession = Depends(get_session)
):
    users = (await session.execute(select(User).order_by(User.created_at))).scalars().all()
    out = []
    for member in users:
        out.append(
            {
                "id": member.id,
                "email": member.email,
                "role": member.role,
                "partner_id": member.partner_id,
                "is_active": member.is_active,
                "totp_enabled": member.totp_enabled,
                "created_at": iso(member.created_at),
            }
        )
    return {"members": out}


@router.post("/members", status_code=201)
async def create_member(
    payload: MemberCreateRequest,
    request: Request,
    user: User = Depends(owner_user),
    session: AsyncSession = Depends(get_session),
):
    email = payload.email.lower().strip()
    existing = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if existing:
        return error_response(409, "conflict", "That email already has an account.", "")

    if payload.partner_id:
        partner = await session.get(Partner, payload.partner_id)
        if not partner:
            return error_response(400, "invalid_request", "Unknown partner.", "")

    member = User(
        email=email,
        role=payload.role,
        password_hash=hash_password(payload.password),
        partner_id=payload.partner_id,
        created_at=utcnow(),
    )
    session.add(member)
    await session.commit()
    await session.refresh(member)
    await audit.record(session, "member.create", user_id=user.id, target=email, ip=client_ip(request))
    return {
        "id": member.id,
        "email": member.email,
        "role": member.role,
        "partner_id": member.partner_id,
        "is_active": True,
    }


@router.patch("/members/{member_id}")
async def update_member(
    member_id: int,
    payload: MemberUpdateRequest,
    request: Request,
    user: User = Depends(owner_user),
    session: AsyncSession = Depends(get_session),
):
    member = await session.get(User, member_id)
    if not member:
        return error_response(404, "not_found", "Member not found.", "")
    if member.id == user.id and payload.is_active is False:
        return error_response(400, "invalid_request", "You cannot deactivate your own account.", "")

    if payload.is_active is not None:
        member.is_active = payload.is_active
        if not payload.is_active and member.partner_id:
            # Deactivating a partner user does not touch partner keys; that is
            # an explicit partner-level action.
            pass
    if payload.password:
        member.password_hash = hash_password(payload.password)
    if payload.role:
        member.role = payload.role
    if payload.partner_id is not None:
        member.partner_id = payload.partner_id

    await session.commit()
    await audit.record(
        session, "member.update", user_id=user.id, target=member.email, ip=client_ip(request)
    )
    return {
        "id": member.id,
        "email": member.email,
        "role": member.role,
        "partner_id": member.partner_id,
        "is_active": member.is_active,
    }


@router.delete("/members/{member_id}", status_code=204)
async def delete_member(
    member_id: int,
    request: Request,
    user: User = Depends(owner_user),
    session: AsyncSession = Depends(get_session),
):
    if member_id == user.id:
        return error_response(400, "invalid_request", "You cannot delete your own account.", "")

    member = await session.get(User, member_id)
    if not member:
        return error_response(404, "not_found", "Member not found.", "")

    email = member.email
    await session.delete(member)
    await session.commit()
    await audit.record(session, "member.delete", user_id=user.id, target=email, ip=client_ip(request))
    return None


# ---------------------------------------------------------------- settings
@router.get("/settings")
async def get_settings(
    _: User = Depends(owner_user), session: AsyncSession = Depends(get_session)
):
    values = await settings_store.load_all(session)
    return {
        **values,
        "environment": settings.environment,
        "cookie_secure": settings.cookie_secure,
        "database": "sqlite" if settings.is_sqlite else "postgresql",
        "shared_state": "redis" if settings.redis_url else "memory",
        "cache": await response_cache.stats(session),
        "idempotency_ttl_seconds": settings.idempotency_ttl_seconds,
        "concurrency_slot_ttl": settings.concurrency_slot_ttl,
    }


@router.put("/settings")
async def update_settings(
    payload: SettingsUpdateRequest,
    request: Request,
    user: User = Depends(owner_user),
    session: AsyncSession = Depends(get_session),
):
    values = {k: v for k, v in payload.model_dump(exclude_unset=True).items() if v is not None}
    if not values:
        return error_response(400, "invalid_request", "Nothing to update.", "")

    await settings_store.set_many(session, values)
    await audit.record(
        session, "settings.update", user_id=user.id, target=",".join(values), ip=client_ip(request)
    )
    return {"ok": True, **values}


@router.get("/audit")
async def list_audit(
    _: User = Depends(owner_user),
    session: AsyncSession = Depends(get_session),
    limit: int = 200,
):
    from app.models import AuditLog

    rows = (
        await session.execute(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit))
    ).scalars().all()
    return {
        "entries": [
            {
                "id": r.id,
                "action": r.action,
                "target": r.target,
                "ip": r.ip,
                "user_id": r.user_id,
                "time": iso(r.created_at),
            }
            for r in rows
        ]
    }


# ------------------------------------------------------------- danger zone
@router.post("/danger/clear-cache")
async def clear_cache(user: User = Depends(owner_user), session: AsyncSession = Depends(get_session)):
    await response_cache.clear(session)
    await audit.record(session, "danger.clear_cache", user_id=user.id)
    return {"ok": True}


@router.post("/danger/reset-health")
async def reset_health(user: User = Depends(owner_user), session: AsyncSession = Depends(get_session)):
    await health.reset()
    await audit.record(session, "danger.reset_health", user_id=user.id)
    return {"ok": True}


@router.post("/danger/purge-logs")
async def purge_logs(
    user: User = Depends(owner_user), session: AsyncSession = Depends(get_session)
):
    days = int(settings_store.get("retention_days", 30) or 30)
    cutoff = utcnow() - timedelta(days=days)
    result = await session.execute(delete(RequestLog).where(RequestLog.created_at < cutoff))
    await session.commit()
    await audit.record(session, "danger.purge_logs", user_id=user.id, target=f">{days}d")
    return {"deleted": result.rowcount or 0, "older_than_days": days}


@router.get("/export")
async def export_data(
    _: User = Depends(owner_user), session: AsyncSession = Depends(get_session)
):
    """Configuration export. Provider keys are never included."""
    providers = (await session.execute(select(Provider))).scalars().all()
    models = (await session.execute(select(Model))).scalars().all()
    partners = (await session.execute(select(Partner))).scalars().all()
    keys = (await session.execute(select(GatewayKey))).scalars().all()
    presets = (await session.execute(select(Preset))).scalars().all()
    members = (await session.execute(select(User))).scalars().all()

    return {
        "exported_at": iso(utcnow()),
        "members": [
            {"email": m.email, "role": m.role, "partner_id": m.partner_id, "is_active": m.is_active}
            for m in members
        ],
        "partners": [
            {
                "name": p.name,
                "slug": p.slug,
                "concurrency_limit": p.concurrency_limit,
                "rpm_limit": p.rpm_limit,
                "tpm_limit": p.tpm_limit,
                "monthly_cap_usd": p.monthly_cap_usd,
                "markup_multiplier": p.markup_multiplier,
                "allow_free_tier": p.allow_free_tier,
                "is_active": p.is_active,
            }
            for p in partners
        ],
        "providers": [
            {
                "name": p.name,
                "label": p.label,
                "base_url": p.base_url,
                "is_free_tier": p.is_free_tier,
                "monthly_cap_usd": p.monthly_cap_usd,
                "priority": p.priority,
                "is_active": p.is_active,
                "has_key": p.has_key,
            }
            for p in providers
        ],
        "models": [
            {
                "provider_id": m.provider_id,
                "model_name": m.model_name,
                "alias": m.alias,
                "context_length": m.context_length,
                "input_price_per_m": m.input_price_per_m,
                "output_price_per_m": m.output_price_per_m,
                "is_enabled": m.is_enabled,
            }
            for m in models
        ],
        "keys": [
            {
                "name": k.name,
                "prefix": k.key_prefix,
                "partner_id": k.partner_id,
                "is_active": k.is_active,
                "created_at": iso(k.created_at),
            }
            for k in keys
        ],
        "presets": [{"name": p.name, "params": json.loads(p.params_json or "{}")} for p in presets],
    }
