"""Routing configuration, rules, presets and UI lookups."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import client_ip, current_user, owner_user
from app.core import audit, cache as response_cache, settings_store
from app.core.config_cache import key_snapshot, partner_snapshot
from app.core.router import NoCandidate, extract_features, pick_candidates
from app.db import get_session
from app.errors import error_response
from app.models import GatewayKey, Model, Partner, Preset, Provider, RoutingRule, User
from app.schemas import PresetUpsertRequest, RuleUpsertRequest
from app.utils import iso, utcnow

router = APIRouter(prefix="/admin/routing", tags=["admin-routing"])


def _serialize_rule(rule: RoutingRule) -> dict:
    return {
        "id": rule.id,
        "position": rule.position,
        "condition": json.loads(rule.condition_json or "{}"),
        "action": json.loads(rule.action_json or "{}"),
        "is_active": rule.is_active,
    }


@router.get("")
async def get_routing(
    _: User = Depends(owner_user), session: AsyncSession = Depends(get_session)
):
    values = await settings_store.load_all(session)
    rules = (
        await session.execute(select(RoutingRule).order_by(RoutingRule.position, RoutingRule.id))
    ).scalars().all()

    models = (
        await session.execute(
            select(Model)
            .join(Provider, Model.provider_id == Provider.id)
            .options(selectinload(Model.provider))
            .order_by(Provider.priority, Model.model_name)
        )
    ).scalars().all()

    return {
        "auto_route": values.get("auto_route") or [],
        "rules": [_serialize_rule(r) for r in rules],
        "catalog": [
            {
                "id": m.id,
                "name": m.public_name,
                "provider": m.provider.name,
                "provider_label": m.provider.label or m.provider.name,
                "is_free": m.is_free,
                "free_tier_provider": m.provider.is_free_tier,
                "is_enabled": m.is_enabled,
                "input_price_per_m": m.input_price_per_m,
                "output_price_per_m": m.output_price_per_m,
                "supports_vision": m.supports_vision,
                "supports_tools": m.supports_tools,
            }
            for m in models
        ],
        "cache_enabled": bool(values.get("cache_enabled")),
        "cache_ttl_seconds": int(values.get("cache_ttl_seconds") or 3600),
        "max_attempts": int(values.get("max_attempts") or 3),
    }


@router.put("")
async def update_routing(
    payload: dict,
    request: Request,
    user: User = Depends(owner_user),
    session: AsyncSession = Depends(get_session),
):
    allowed = {"auto_route", "cache_enabled", "cache_ttl_seconds", "max_attempts"}
    values = {k: v for k, v in payload.items() if k in allowed}
    if "auto_route" in values and not isinstance(values["auto_route"], list):
        return error_response(400, "invalid_request", "auto_route must be a list of model ids.", "")

    await settings_store.set_many(session, values)
    if values.get("cache_enabled") is False:
        await response_cache.clear(session)

    await audit.record(
        session, "routing.update", user_id=user.id, target=",".join(values), ip=client_ip(request)
    )
    return {"ok": True, **values}


@router.post("/rules", status_code=201)
async def create_rule(
    payload: RuleUpsertRequest,
    request: Request,
    user: User = Depends(owner_user),
    session: AsyncSession = Depends(get_session),
):
    rule = RoutingRule(
        position=payload.position,
        condition_json=json.dumps(payload.condition),
        action_json=json.dumps(payload.action),
        is_active=payload.is_active,
    )
    session.add(rule)
    await session.commit()
    await session.refresh(rule)
    await audit.record(
        session, "routing.rule_create", user_id=user.id, target=str(rule.id), ip=client_ip(request)
    )
    return _serialize_rule(rule)


@router.patch("/rules/{rule_id}")
async def update_rule(
    rule_id: int,
    payload: RuleUpsertRequest,
    user: User = Depends(owner_user),
    session: AsyncSession = Depends(get_session),
):
    rule = await session.get(RoutingRule, rule_id)
    if not rule:
        return error_response(404, "not_found", "Rule not found.", "")

    rule.position = payload.position
    rule.condition_json = json.dumps(payload.condition)
    rule.action_json = json.dumps(payload.action)
    rule.is_active = payload.is_active
    await session.commit()
    return _serialize_rule(rule)


@router.delete("/rules/{rule_id}", status_code=204)
async def delete_rule(
    rule_id: int,
    user: User = Depends(owner_user),
    session: AsyncSession = Depends(get_session),
):
    rule = await session.get(RoutingRule, rule_id)
    if not rule:
        return error_response(404, "not_found", "Rule not found.", "")
    await session.delete(rule)
    await session.commit()
    return None


@router.post("/preview")
async def preview_route(
    payload: dict,
    user: User = Depends(owner_user),
    session: AsyncSession = Depends(get_session),
):
    """Show which candidates a sample request would try, without calling a provider."""
    requested_model = str(payload.get("model") or "auto")
    messages = payload.get("messages") or [{"role": "user", "content": "hello"}]
    features = extract_features(
        {"messages": messages, "tools": payload.get("tools")}, payload.get("preset")
    )

    partner = (
        await session.execute(
            select(Partner).where(Partner.is_active.is_(True)).order_by(Partner.id)
        )
    ).scalars().first()
    key = (
        await session.execute(
            select(GatewayKey)
            .where(GatewayKey.partner_id == partner.id, GatewayKey.is_active.is_(True))
        )
    ).scalars().first() if partner else None

    # pick_candidates works from detached snapshots and opens its own short
    # session for spend aggregates (LOAD_FIX: no shared request session).
    key_snap = key_snapshot(key, partner) if key is not None else None
    partner_snap = key_snap.partner if key_snap is not None else (
        partner_snapshot(partner) if partner is not None else None
    )

    try:
        candidates = await pick_candidates(
            requested_model, key_snap, features, partner=partner_snap, allow_free_tier=True
        )
    except NoCandidate as exc:
        return {"candidates": [], "error": exc.message}

    return {
        "candidates": [
            {
                "model_id": c.model.id,
                "model": c.model.public_name,
                "provider": c.provider.name,
                "provider_label": c.provider.label or c.provider.name,
                "is_free": c.is_free,
                "free_tier_provider": c.provider.is_free_tier,
                "input_price_per_m": c.model.input_price_per_m,
            }
            for c in candidates
        ],
        "features": {
            "has_image": features.has_image,
            "needs_tools": features.needs_tools,
            "approx_chars": features.approx_chars,
        },
    }


# ------------------------------------------------------------------ presets
presets_router = APIRouter(prefix="/admin/presets", tags=["admin-presets"])


def _serialize_preset(preset: Preset, model: Model | None = None) -> dict:
    return {
        "id": preset.id,
        "name": preset.name,
        "system_prompt": preset.system_prompt,
        "model_id": preset.model_id,
        "model": model.public_name if model else None,
        "params": json.loads(preset.params_json or "{}"),
        "created_at": iso(preset.created_at),
    }


@presets_router.get("")
async def list_presets(
    _: User = Depends(current_user), session: AsyncSession = Depends(get_session)
):
    rows = (
        await session.execute(
            select(Preset, Model).outerjoin(Model, Preset.model_id == Model.id).order_by(Preset.name)
        )
    ).all()
    return {"presets": [_serialize_preset(p, m) for p, m in rows]}


@presets_router.post("", status_code=201)
async def create_preset(
    payload: PresetUpsertRequest,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    existing = (
        await session.execute(select(Preset).where(Preset.name == payload.name))
    ).scalar_one_or_none()
    if existing:
        return error_response(409, "conflict", "A preset with that name already exists.", "")

    preset = Preset(
        name=payload.name,
        system_prompt=payload.system_prompt,
        model_id=payload.model_id,
        params_json=json.dumps(payload.params),
        created_at=utcnow(),
    )
    session.add(preset)
    await session.commit()
    await session.refresh(preset)
    model = await session.get(Model, preset.model_id) if preset.model_id else None
    return _serialize_preset(preset, model)


@presets_router.delete("/{preset_id}", status_code=204)
async def delete_preset(
    preset_id: int, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)
):
    preset = await session.get(Preset, preset_id)
    if not preset:
        return error_response(404, "not_found", "Preset not found.", "")
    await session.delete(preset)
    await session.commit()
    return None


# ------------------------------------------------------------- UI lookups
lookup_router = APIRouter(prefix="/admin/lookup", tags=["admin-lookup"])


@lookup_router.get("")
async def lookup(
    user: User = Depends(current_user), session: AsyncSession = Depends(get_session)
):
    """Dropdown data. Partners get only their own keys and no provider detail."""
    owner = user.is_owner

    providers = []
    models = []
    if owner:
        providers = (
            await session.execute(select(Provider).order_by(Provider.priority))
        ).scalars().all()
        models = (
            await session.execute(
                select(Model)
                .join(Provider, Model.provider_id == Provider.id)
                .options(selectinload(Model.provider))
                .order_by(Provider.priority, Model.model_name)
            )
        ).scalars().all()

    key_query = select(GatewayKey).order_by(GatewayKey.name)
    if not owner:
        key_query = key_query.where(GatewayKey.partner_id == (user.partner_id or -1))
    keys = (await session.execute(key_query)).scalars().all()

    partners = []
    if owner:
        partners = (
            await session.execute(select(Partner).order_by(Partner.name))
        ).scalars().all()
    elif user.partner_id:
        me = await session.get(Partner, user.partner_id)
        partners = [me] if me else []

    return {
        "providers": [
            {"id": p.id, "name": p.name, "label": p.label or p.name, "has_key": p.has_key}
            for p in providers
        ],
        "models": [
            {
                "id": m.id,
                "name": m.public_name,
                "provider": m.provider.name,
                "is_free": m.is_free,
                "is_enabled": m.is_enabled,
            }
            for m in models
        ],
        "keys": [{"id": k.id, "name": k.name, "prefix": k.key_prefix} for k in keys],
        "partners": [
            {"id": p.id, "name": p.name, "slug": p.slug, "is_active": p.is_active} for p in partners
        ],
    }
