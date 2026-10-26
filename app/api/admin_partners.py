"""Partner management (brief §4.4).

A partner is a company or website: it owns gateway keys, has its own limits,
its own usage view, and optionally a login of its own.
"""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import client_ip, current_user, owner_user
from app.core import audit, limits
from app.core.auth import hash_password
from app.db import get_session
from app.errors import error_response
from app.models import GatewayKey, Partner, User
from app.schemas import PartnerCreateRequest, PartnerUpdateRequest
from app.utils import iso, utcnow

router = APIRouter(prefix="/admin/partners", tags=["admin-partners"])


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:60] or "partner"


def serialize(partner: Partner, key_count: int | None = None) -> dict:
    return {
        "id": partner.id,
        "name": partner.name,
        "slug": partner.slug,
        "concurrency_limit": partner.concurrency_limit,
        "rpm_limit": partner.rpm_limit,
        "tpm_limit": partner.tpm_limit,
        "daily_request_limit": partner.daily_request_limit,
        "monthly_cap_usd": partner.monthly_cap_usd,
        "over_capacity": partner.over_capacity,
        "queue_timeout_ms": partner.queue_timeout_ms,
        "markup_multiplier": partner.markup_multiplier,
        "allow_free_tier": partner.allow_free_tier,
        "is_active": partner.is_active,
        "notes": partner.notes,
        "key_count": key_count,
        "created_at": iso(partner.created_at),
    }


@router.get("")
async def list_partners(
    _: User = Depends(current_user), session: AsyncSession = Depends(get_session)
):
    partners = (
        await session.execute(select(Partner).order_by(Partner.created_at))
    ).scalars().all()

    counts = dict(
        (
            await session.execute(
                select(GatewayKey.partner_id, func.count(GatewayKey.id)).group_by(
                    GatewayKey.partner_id
                )
            )
        ).all()
    )
    return {"partners": [serialize(p, int(counts.get(p.id, 0))) for p in partners]}


@router.post("", status_code=201)
async def create_partner(
    payload: PartnerCreateRequest,
    request: Request,
    user: User = Depends(owner_user),
    session: AsyncSession = Depends(get_session),
):
    slug = payload.slug or slugify(payload.name)
    existing = (
        await session.execute(select(Partner).where(Partner.slug == slug))
    ).scalar_one_or_none()
    if existing:
        return error_response(409, "conflict", f"A partner with slug '{slug}' already exists.", "")

    partner = Partner(
        name=payload.name.strip(),
        slug=slug,
        concurrency_limit=payload.concurrency_limit,
        rpm_limit=payload.rpm_limit,
        tpm_limit=payload.tpm_limit,
        daily_request_limit=payload.daily_request_limit,
        monthly_cap_usd=payload.monthly_cap_usd,
        over_capacity=payload.over_capacity,
        queue_timeout_ms=payload.queue_timeout_ms,
        markup_multiplier=payload.markup_multiplier,
        allow_free_tier=payload.allow_free_tier,
        notes=payload.notes,
        created_at=utcnow(),
    )
    session.add(partner)
    await session.flush()

    # Optionally give the partner a console login of its own.
    if payload.login_email:
        if not payload.login_password:
            return error_response(
                400, "invalid_request", "login_password is required with login_email.", ""
            )
        clash = (
            await session.execute(select(User).where(User.email == payload.login_email.lower()))
        ).scalar_one_or_none()
        if clash:
            return error_response(409, "conflict", "That email already has an account.", "")
        session.add(
            User(
                email=payload.login_email.lower().strip(),
                role="partner",
                password_hash=hash_password(payload.login_password),
                partner_id=partner.id,
                created_at=utcnow(),
            )
        )

    await session.commit()
    await session.refresh(partner)
    await audit.record(
        session, "partner.create", user_id=user.id, target=partner.slug, ip=client_ip(request)
    )
    return serialize(partner, 0)


@router.patch("/{partner_id}")
async def update_partner(
    partner_id: int,
    payload: PartnerUpdateRequest,
    request: Request,
    user: User = Depends(owner_user),
    session: AsyncSession = Depends(get_session),
):
    partner = await session.get(Partner, partner_id)
    if not partner:
        return error_response(404, "not_found", "Partner not found.", "")

    data = payload.model_dump(exclude_unset=True)
    for field, value in data.items():
        if value is not None:
            setattr(partner, field, value)

    # Deactivating a partner disables every one of its keys.
    if data.get("is_active") is False:
        keys = (
            await session.execute(select(GatewayKey).where(GatewayKey.partner_id == partner.id))
        ).scalars().all()
        for key in keys:
            key.is_active = False

    await session.commit()
    await audit.record(
        session, "partner.update", user_id=user.id, target=partner.slug, ip=client_ip(request)
    )
    return serialize(partner)


@router.delete("/{partner_id}", status_code=204)
async def delete_partner(
    partner_id: int,
    request: Request,
    user: User = Depends(owner_user),
    session: AsyncSession = Depends(get_session),
):
    partner = await session.get(Partner, partner_id)
    if not partner:
        return error_response(404, "not_found", "Partner not found.", "")

    slug = partner.slug
    await session.delete(partner)
    await session.commit()
    await audit.record(session, "partner.delete", user_id=user.id, target=slug, ip=client_ip(request))
    return None


@router.get("/{partner_id}/usage")
async def partner_usage_detail(
    partner_id: int,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    if not user.is_owner and user.partner_id != partner_id:
        return error_response(403, "forbidden", "Not your partner.", "")

    partner = await session.get(Partner, partner_id)
    if not partner:
        return error_response(404, "not_found", "Partner not found.", "")

    usage = await limits.partner_usage(session, partner_id)
    return {
        "partner": serialize(partner),
        "usage": {
            "requests_today": usage.requests_today,
            "tokens_this_month": usage.tokens_this_month,
            "cost_this_month": round(usage.cost_this_month, 6),
            "price_this_month": round(usage.price_this_month, 6),
            "margin_usd": round(usage.price_this_month - usage.cost_this_month, 6),
            "daily_request_limit": partner.daily_request_limit,
            "monthly_cap_usd": partner.monthly_cap_usd,
        },
    }
