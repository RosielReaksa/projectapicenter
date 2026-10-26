"""Admin provider management: keys, connection tests, models."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import client_ip, owner_user
from app.core import audit, health
from app.core.crypto import decrypt, encrypt
from app.db import get_session
from app.errors import error_response
from app.models import Model, Provider, User
from app.providers import openai_compat
from app.providers.registry import defaults_for
from app.schemas import (
    ModelUpdateRequest,
    ModelUpsertRequest,
    ProviderCreateRequest,
    ProviderUpdateRequest,
)
from app.utils import iso, mask_key, utcnow

log = logging.getLogger("gateway.admin.providers")
router = APIRouter(prefix="/admin/providers", tags=["admin-providers"])


def _serialize(provider: Provider) -> dict:
    key_hint = None
    if provider.api_key_enc:
        try:
            key_hint = mask_key(decrypt(provider.api_key_enc))
        except ValueError:
            key_hint = "unreadable"

    return {
        "id": provider.id,
        "name": provider.name,
        "label": provider.label or provider.name,
        "base_url": provider.base_url,
        "has_key": provider.has_key,
        "key_hint": key_hint,
        "is_free_tier": provider.is_free_tier,
        "may_train_on_prompts": provider.may_train_on_prompts,
        "monthly_cap_usd": provider.monthly_cap_usd,
        "rpm_limit": provider.rpm_limit,
        "priority": provider.priority,
        "is_active": provider.is_active,
        "health_status": provider.health_status,
        "last_error": provider.last_error,
        "last_checked_at": iso(provider.last_checked_at),
        "last_latency_ms": provider.last_latency_ms,
        "model_count": len(provider.models),
        "enabled_model_count": sum(1 for m in provider.models if m.is_enabled),
        "created_at": iso(provider.created_at),
    }


def _serialize_model(model: Model) -> dict:
    return {
        "id": model.id,
        "provider_id": model.provider_id,
        "provider": model.provider.name if model.provider else None,
        "provider_label": (model.provider.label or model.provider.name) if model.provider else None,
        "model_name": model.model_name,
        "name": model.public_name,
        "alias": model.alias,
        "context_length": model.context_length,
        "input_price_per_m": model.input_price_per_m,
        "output_price_per_m": model.output_price_per_m,
        "supports_vision": model.supports_vision,
        "supports_tools": model.supports_tools,
        "is_enabled": model.is_enabled,
        "is_free": model.is_free,
        "description": model.description,
        "prices_checked_at": iso(model.prices_checked_at),
    }


async def _load(session: AsyncSession, provider_id: int) -> Provider | None:
    return (
        await session.execute(
            select(Provider).options(selectinload(Provider.models)).where(Provider.id == provider_id)
        )
    ).scalar_one_or_none()


@router.get("")
async def list_providers(
    _: User = Depends(owner_user), session: AsyncSession = Depends(get_session)
):
    providers = (
        await session.execute(
            select(Provider).options(selectinload(Provider.models)).order_by(Provider.priority, Provider.name)
        )
    ).scalars().all()
    breakers = await health.snapshot([p.id for p in providers])

    out = []
    for provider in providers:
        body = _serialize(provider)
        breaker = breakers.get(provider.id, {})
        if breaker.get("open"):
            body["health_status"] = "cooling_down"
        body["breaker"] = breaker
        out.append(body)
    return {"providers": out}


@router.post("", status_code=201)
async def create_provider(
    payload: ProviderCreateRequest,
    request: Request,
    user: User = Depends(owner_user),
    session: AsyncSession = Depends(get_session),
):
    name = payload.name.strip().lower()
    existing = (
        await session.execute(select(Provider).where(Provider.name == name))
    ).scalar_one_or_none()
    if existing:
        return error_response(
            409, "conflict", f"Provider '{name}' already exists. Update it instead.", ""
        )

    seed = defaults_for(name)
    base_url = payload.base_url or (seed.base_url if seed else "")
    if not base_url:
        return error_response(400, "invalid_request", "base_url is required for a custom provider.", "")

    provider = Provider(
        name=name,
        label=payload.label or (seed.label if seed else name.title()),
        base_url=base_url,
        api_key_enc=encrypt(payload.api_key) if payload.api_key else None,
        is_free_tier=(
            payload.is_free_tier if payload.is_free_tier is not None else (seed.free_tier if seed else False)
        ),
        may_train_on_prompts=payload.may_train_on_prompts,
        monthly_cap_usd=payload.monthly_cap_usd,
        rpm_limit=payload.rpm_limit,
        priority=payload.priority if payload.priority is not None else (seed.priority if seed else 100),
        is_active=payload.is_active if payload.is_active is not None else True,
        created_at=utcnow(),
    )
    session.add(provider)
    await session.flush()

    if payload.seed_models and seed:
        for model in seed.models:
            session.add(
                Model(
                    provider_id=provider.id,
                    model_name=model.name,
                    alias=model.alias,
                    context_length=model.context_length,
                    input_price_per_m=model.input_price_per_m,
                    output_price_per_m=model.output_price_per_m,
                    supports_vision=model.supports_vision,
                    supports_tools=model.supports_tools,
                    prices_checked_at=utcnow(),
                )
            )

    await session.commit()
    await audit.record(
        session, "provider.create", user_id=user.id, target=name, ip=client_ip(request)
    )
    return _serialize(await _load(session, provider.id))


@router.patch("/{provider_id}")
async def update_provider(
    provider_id: int,
    payload: ProviderUpdateRequest,
    request: Request,
    user: User = Depends(owner_user),
    session: AsyncSession = Depends(get_session),
):
    provider = await _load(session, provider_id)
    if not provider:
        return error_response(404, "not_found", "Provider not found.", "")

    data = payload.model_dump(exclude_unset=True)
    if data.get("api_key"):
        provider.api_key_enc = encrypt(data["api_key"].strip())
        await health.reset(provider.id)
        provider.health_status = "unknown"
        provider.last_error = None

    for field in (
        "label",
        "base_url",
        "is_free_tier",
        "may_train_on_prompts",
        "monthly_cap_usd",
        "rpm_limit",
        "priority",
        "is_active",
    ):
        if field in data and data[field] is not None:
            setattr(provider, field, data[field])

    await session.commit()
    await audit.record(
        session, "provider.update", user_id=user.id, target=provider.name, ip=client_ip(request)
    )
    return _serialize(await _load(session, provider_id))


@router.delete("/{provider_id}", status_code=204)
async def delete_provider(
    provider_id: int,
    request: Request,
    user: User = Depends(owner_user),
    session: AsyncSession = Depends(get_session),
):
    provider = await session.get(Provider, provider_id)
    if not provider:
        return error_response(404, "not_found", "Provider not found.", "")

    name = provider.name
    await session.delete(provider)
    await session.commit()
    await health.reset(provider_id)
    await audit.record(session, "provider.delete", user_id=user.id, target=name, ip=client_ip(request))
    return None


@router.post("/{provider_id}/test")
async def test_provider(
    provider_id: int,
    request: Request,
    user: User = Depends(owner_user),
    session: AsyncSession = Depends(get_session),
):
    provider = await _load(session, provider_id)
    if not provider:
        return error_response(404, "not_found", "Provider not found.", "")
    if not provider.api_key_enc:
        return error_response(400, "invalid_request", "Add an API key first.", "")

    model_name = next((m.model_name for m in provider.models if m.is_enabled), None)
    if not model_name:
        return error_response(400, "invalid_request", "No enabled model to test with.", "")

    try:
        api_key = decrypt(provider.api_key_enc)
    except ValueError as exc:
        return error_response(500, "internal_error", str(exc), "")

    result = await openai_compat.test_connection(
        request.app.state.http, provider.base_url, api_key, model_name
    )

    provider.last_checked_at = utcnow()
    provider.last_latency_ms = result["latency_ms"]
    if result["ok"]:
        provider.health_status = "healthy"
        provider.last_error = None
        await health.record_success(provider.id)
    else:
        provider.health_status = "down"
        provider.last_error = f"{result.get('code')}: {result.get('error')}"[:500]
        await health.record_failure(provider.id)
    await session.commit()

    await audit.record(
        session, "provider.test", user_id=user.id, target=provider.name, ip=client_ip(request)
    )
    return {**result, "provider": provider.name}


@router.post("/{provider_id}/refresh-models")
async def refresh_models(
    provider_id: int,
    request: Request,
    user: User = Depends(owner_user),
    session: AsyncSession = Depends(get_session),
):
    """Pull the provider's model list and add anything new (prices default to 0)."""
    provider = await _load(session, provider_id)
    if not provider:
        return error_response(404, "not_found", "Provider not found.", "")
    if not provider.api_key_enc:
        return error_response(400, "invalid_request", "Add an API key first.", "")

    try:
        api_key = decrypt(provider.api_key_enc)
    except ValueError as exc:
        return error_response(500, "internal_error", str(exc), "")

    listed = await openai_compat.list_models(request.app.state.http, provider.base_url, api_key)
    known = {m.model_name for m in provider.models}
    added = 0
    for item in listed:
        name = item.get("id")
        if not name or name in known:
            continue
        session.add(Model(provider_id=provider.id, model_name=name))
        added += 1

    await session.commit()
    await audit.record(
        session,
        "provider.refresh_models",
        user_id=user.id,
        target=provider.name,
        ip=client_ip(request),
    )
    return {"found": len(listed), "added": added, "provider": provider.name}


# ------------------------------------------------------------------ models
models_router = APIRouter(prefix="/admin/models", tags=["admin-models"])


@models_router.get("")
async def list_models(
    _: User = Depends(owner_user), session: AsyncSession = Depends(get_session)
):
    models = (
        await session.execute(
            select(Model).options(selectinload(Model.provider)).order_by(Model.provider_id, Model.model_name)
        )
    ).scalars().all()
    return {"models": [_serialize_model(m) for m in models]}


@models_router.get("/{model_id}")
async def get_model(
    model_id: int, _: User = Depends(owner_user), session: AsyncSession = Depends(get_session)
):
    model = (
        await session.execute(
            select(Model).options(selectinload(Model.provider)).where(Model.id == model_id)
        )
    ).scalar_one_or_none()
    if not model:
        return error_response(404, "not_found", "Model not found.", "")
    return _serialize_model(model)


@models_router.post("", status_code=201)
async def upsert_model(
    payload: ModelUpsertRequest,
    request: Request,
    user: User = Depends(owner_user),
    session: AsyncSession = Depends(get_session),
):
    provider = await session.get(Provider, payload.provider_id)
    if not provider:
        return error_response(400, "invalid_request", "Unknown provider.", "")

    existing = (
        await session.execute(
            select(Model).where(
                Model.provider_id == payload.provider_id,
                Model.model_name == payload.model_name,
            )
        )
    ).scalar_one_or_none()

    if existing:
        for field, value in payload.model_dump(exclude={"provider_id", "model_name"}).items():
            setattr(existing, field, value)
        existing.prices_checked_at = utcnow()
        model = existing
    else:
        model = Model(**payload.model_dump(), prices_checked_at=utcnow())
        session.add(model)

    await session.commit()
    await session.refresh(model)
    model.provider = provider
    await audit.record(
        session,
        "model.upsert",
        user_id=user.id,
        target=f"{provider.name}/{model.model_name}",
        ip=client_ip(request),
    )
    return _serialize_model(model)


@models_router.patch("/{model_id}")
async def update_model(
    model_id: int,
    payload: ModelUpdateRequest,
    request: Request,
    user: User = Depends(owner_user),
    session: AsyncSession = Depends(get_session),
):
    model = await session.get(Model, model_id)
    if not model:
        return error_response(404, "not_found", "Model not found.", "")

    data = payload.model_dump(exclude_unset=True)
    price_changed = "input_price_per_m" in data or "output_price_per_m" in data
    for field, value in data.items():
        if value is not None:
            setattr(model, field, value)
    if price_changed:
        model.prices_checked_at = utcnow()

    await session.commit()
    model = (
        await session.execute(
            select(Model).options(selectinload(Model.provider)).where(Model.id == model_id)
        )
    ).scalar_one()
    await audit.record(
        session, "model.update", user_id=user.id, target=str(model_id), ip=client_ip(request)
    )
    return _serialize_model(model)


@models_router.delete("/{model_id}", status_code=204)
async def delete_model(
    model_id: int,
    user: User = Depends(owner_user),
    session: AsyncSession = Depends(get_session),
):
    model = await session.get(Model, model_id)
    if not model:
        return error_response(404, "not_found", "Model not found.", "")
    await session.delete(model)
    await session.commit()
    return None
