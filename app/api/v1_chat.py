"""Data plane: /v1/chat/completions, /v1/models, /v1/usage (brief §5.1)."""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import require_gateway_key
from app.config import settings
from app.core import limits
from app.core.auth import KeyContext
from app.core.execute import ChatError, execute_chat
from app.core.store import get_store
from app.db import get_session
from app.errors import error_response
from app.models import Model, Provider
from app.schemas import ChatRequest
from app.utils import iso

router = APIRouter(prefix="/v1", tags=["gateway"])
log = logging.getLogger("gateway.api")


def _idempotency_key(request: Request, context: KeyContext) -> str | None:
    raw = request.headers.get("idempotency-key")
    if not raw:
        return None
    # Scoped to the key so two partners can never collide on the same value.
    return f"{context.key.id}:{raw[:128]}"


@router.post("/chat/completions")
async def chat_completions(
    payload: ChatRequest,
    request: Request,
    background: BackgroundTasks,
    context: KeyContext = Depends(require_gateway_key),
    session: AsyncSession = Depends(get_session),
):
    body = payload.model_dump(exclude_none=True)
    # Forward any other OpenAI-compatible fields untouched.
    body.update(payload.model_extra or {})

    if body.get("stream"):
        return error_response(
            400,
            "streaming_not_supported",
            "Streaming is not enabled on this gateway yet. Omit 'stream' (or send false).",
            getattr(request.state, "request_id", ""),
        )

    end_user = request.headers.get("x-end-user")
    if end_user:
        end_user = end_user.strip()[:255]

    # ---- idempotency (brief §4.9) ------------------------------------
    store = get_store()
    idem = _idempotency_key(request, context)
    if idem:
        replay = await store.idem_get(idem)
        if replay:
            return JSONResponse(
                content=replay["body"],
                status_code=replay["status"],
                headers={
                    "X-Request-Id": replay["request_id"],
                    "Idempotent-Replayed": "true",
                    "X-Gateway-Cached": "false",
                },
            )
        if not await store.idem_begin(idem, settings.idempotency_ttl_seconds):
            # Another instance is working on this exact request right now.
            return error_response(
                409,
                "idempotency_in_progress",
                "A request with this Idempotency-Key is already in flight. Retry shortly.",
                getattr(request.state, "request_id", ""),
                {"Retry-After": "1"},
            )

    try:
        result = await execute_chat(
            session,
            key=context.key,
            partner=context.partner,
            body=body,
            client=request.app.state.http,
            source="api",
            end_user=end_user,
            background=background,
        )
    except ChatError as exc:
        if idem:
            await store.idem_store(
                idem,
                {
                    "body": {"error": {"code": exc.code, "message": exc.message,
                                        "request_id": exc.request_id}},
                    "status": exc.status,
                    "request_id": exc.request_id,
                },
                settings.idempotency_ttl_seconds,
            )
        raise

    if idem:
        await store.idem_store(
            idem,
            {"body": result.body, "status": 200, "request_id": result.request_id},
            settings.idempotency_ttl_seconds,
        )

    return JSONResponse(
        content=result.body,
        headers={
            "X-Request-Id": result.request_id,
            "X-Gateway-Cached": "true" if result.cached else "false",
            "X-Gateway-Provider": result.provider,
        },
    )


@router.get("/models")
async def list_models(
    context: KeyContext = Depends(require_gateway_key),
    session: AsyncSession = Depends(get_session),
):
    rows = (
        await session.execute(
            select(Model)
            .join(Provider, Model.provider_id == Provider.id)
            .options(selectinload(Model.provider))
            .where(Model.is_enabled.is_(True), Provider.is_active.is_(True))
            .order_by(Provider.priority, Model.model_name)
        )
    ).scalars().all()

    allowed: set[str] = set()
    if context.key.allowed_models:
        try:
            allowed = {str(v) for v in json.loads(context.key.allowed_models)}
        except json.JSONDecodeError:
            allowed = set()

    free_ok = (
        context.key.allow_free_tier
        if context.key.allow_free_tier is not None
        else context.partner.allow_free_tier
    )

    data = []
    for model in rows:
        if not free_ok and model.provider.is_free_tier:
            continue
        if allowed and model.public_name not in allowed and model.model_name not in allowed:
            continue
        data.append(
            {
                "id": model.public_name,
                "object": "model",
                "owned_by": model.provider.name,
                "provider": model.provider.name,
                "context_length": model.context_length,
                "pricing": {
                    "input_per_million": model.input_price_per_m,
                    "output_per_million": model.output_price_per_m,
                },
                "supports_vision": model.supports_vision,
                "supports_tools": model.supports_tools,
                "free_tier": model.provider.is_free_tier,
            }
        )

    data.append(
        {
            "id": "auto",
            "object": "model",
            "owned_by": "gateway",
            "provider": "router",
            "description": "Automatic routing: cheapest healthy provider first, with fallback.",
        }
    )
    return {"object": "list", "data": data}


@router.get("/usage")
async def key_usage(
    context: KeyContext = Depends(require_gateway_key),
    session: AsyncSession = Depends(get_session),
):
    usage = await limits.key_usage(session, context.key.id)
    partner_usage = await limits.partner_usage(session, context.partner.id)
    return {
        "key": {
            "id": context.key.id,
            "name": context.key.name,
            "prefix": context.key.key_prefix,
        },
        "partner": {
            "id": context.partner.id,
            "name": context.partner.name,
            "slug": context.partner.slug,
        },
        "requests_today": usage.requests_today,
        "daily_limit": context.key.daily_limit,
        "cost_this_month": round(usage.cost_this_month, 6),
        "price_this_month": round(usage.price_this_month, 6),
        "monthly_cap_usd": context.key.monthly_cap_usd,
        "partner_usage": {
            "id": context.partner.id,
            "name": context.partner.name,
            "slug": context.partner.slug,
            "requests_today": partner_usage.requests_today,
            "cost_this_month": round(partner_usage.cost_this_month, 6),
            "monthly_cap_usd": context.partner.monthly_cap_usd,
            "concurrency_limit": context.partner.concurrency_limit,
            "rpm_limit": context.partner.rpm_limit,
            "tpm_limit": context.partner.tpm_limit,
        },
        "expires_at": iso(context.key.expires_at),
        "last_used_at": iso(context.key.last_used_at),
    }
