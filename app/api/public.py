"""Public endpoints: no auth, safe for anonymous visitors (HOMEPAGE_SPEC §5, §4).

Everything here is deliberately narrow. Nothing returns a provider name,
internal id, key, cost or price unless the owner has turned public pricing on.
"""

from __future__ import annotations

import logging
import time
from datetime import timedelta

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import client_ip
from app.config import settings
from app.core import settings_store
from app.core.store import get_store
from app.db import get_session
from app.errors import error_response
from app.models import AccessRequest, Model, Provider
from app.schemas import AccessRequestCreate
from app.utils import utcnow

log = logging.getLogger("gateway.public")

router = APIRouter(prefix="/public", tags=["public"])

# Public model list cache: the page must not hammer the database (§5).
_MODEL_CACHE: dict[str, object] = {"at": 0.0, "payload": None}
_MODEL_TTL = 60.0

# Abuse protection (§4.3).
ACCESS_RATE_LIMIT = 5
ACCESS_RATE_WINDOW = 3600
ACCESS_DEDUPE_WINDOW = 24 * 3600


def public_pricing_enabled() -> bool:
    value = settings_store.get("public_pricing", settings.public_pricing)
    return bool(value)


@router.get("/config")
async def public_site_config(request: Request):
    """Everything the public pages need to render, including the base URL."""
    base = settings.public_base_url.rstrip("/") or str(request.base_url).rstrip("/")
    return {
        "app_name": settings.app_name,
        "tagline": settings.product_tagline,
        "base_url": base,
        "api_base_url": f"{base}/v1",
        "support_email": settings.support_email,
        "status_page_url": settings.status_page_url or None,
        "public_pricing": public_pricing_enabled(),
        "legal_reviewed": settings.legal_reviewed,
        "environment": settings.environment,
    }


@router.get("/models")
async def public_models(session: AsyncSession = Depends(get_session)):
    """Enabled models only, with public-safe fields (no provider names or ids)."""
    now = time.monotonic()
    if _MODEL_CACHE["payload"] is not None and now - float(_MODEL_CACHE["at"]) < _MODEL_TTL:
        return _MODEL_CACHE["payload"]

    rows = (
        await session.execute(
            select(Model)
            .join(Provider, Model.provider_id == Provider.id)
            .where(Model.is_enabled.is_(True), Provider.is_active.is_(True))
            .order_by(Provider.priority, Model.model_name)
        )
    ).scalars().all()

    show_prices = public_pricing_enabled()
    data = []
    for model in rows:
        entry = {
            "id": model.public_name,
            "name": model.public_name,
            "context_length": model.context_length,
            "supports_vision": model.supports_vision,
            "supports_tools": model.supports_tools,
            "free": model.is_free,
        }
        if show_prices:
            entry["input_price_per_m"] = model.input_price_per_m
            entry["output_price_per_m"] = model.output_price_per_m
        data.append(entry)

    payload = {
        "models": data,
        "public_pricing": show_prices,
        "count": len(data),
        "generated_at": utcnow().replace(microsecond=0).isoformat() + "Z",
    }
    _MODEL_CACHE.update({"at": now, "payload": payload})
    return payload


@router.post("/access-requests", status_code=201)
async def create_access_request(
    payload: AccessRequestCreate,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    ip = client_ip(request)

    # 1. Honeypot: a real visitor never fills the hidden field. Pretend success
    #    so a bot learns nothing, but store nothing.
    if (payload.website_url or "").strip():
        log.info("Access request discarded: honeypot filled (ip=%s)", ip)
        return {"ok": True, "message": "Thanks. We'll review your request and reply by email."}

    if not payload.accepted_terms:
        return error_response(
            400,
            "terms_required",
            "Please accept the Terms of Service and Privacy Policy.",
            "",
        )

    # 2. Per-IP rate limit, shared across instances via the store.
    store = get_store()
    hits = await store.incr_window(f"access-request:{ip}", ACCESS_RATE_WINDOW)
    if hits > ACCESS_RATE_LIMIT:
        return error_response(
            429,
            "rate_limit_exceeded",
            "Too many requests from this address. Please try again later.",
            "",
            {"Retry-After": str(ACCESS_RATE_WINDOW)},
        )

    email = str(payload.email).lower().strip()
    name = payload.name.strip()
    company = payload.company.strip()
    use_case = payload.use_case.strip()

    # 3. Deduplicate: the same email within 24h updates one pending record.
    cutoff = utcnow() - timedelta(seconds=ACCESS_DEDUPE_WINDOW)
    existing = (
        await session.execute(
            select(AccessRequest)
            .where(
                AccessRequest.email == email,
                AccessRequest.status == "pending",
                AccessRequest.created_at >= cutoff,
            )
            .order_by(AccessRequest.created_at.desc())
        )
    ).scalars().first()

    if existing:
        existing.name = name
        existing.company = company
        existing.use_case = use_case
        existing.expected_usage = payload.expected_usage
        existing.ip = ip
    else:
        session.add(
            AccessRequest(
                name=name,
                email=email,
                company=company,
                use_case=use_case,
                expected_usage=payload.expected_usage,
                status="pending",
                ip=ip,
                created_at=utcnow(),
            )
        )
    await session.commit()

    # The same message regardless of whether the email was already known, so the
    # form never reveals who has an account (§4.2).
    return {"ok": True, "message": "Thanks. We'll review your request and reply by email."}


@router.get("/status")
async def public_status(session: AsyncSession = Depends(get_session)):
    """Public maintenance state. No internal detail, no provider names (§8)."""
    from app.core import maintenance

    status = await maintenance.public_status()
    status["upcoming_window"] = await maintenance.upcoming_window(session)
    return status
