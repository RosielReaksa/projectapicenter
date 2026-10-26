"""Admin review of public access requests (HOMEPAGE_SPEC §4.4).

Approving does not create anything by itself: it marks the request approved and
hands the owner a prefill for the normal partner form, so limits and the first
key are always chosen deliberately.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import client_ip, owner_user
from app.core import audit
from app.db import get_session
from app.errors import error_response
from app.models import AccessRequest, User
from app.schemas import AccessRequestReject
from app.utils import iso, utcnow

router = APIRouter(prefix="/admin/access-requests", tags=["admin-access"])


def _serialize(row: AccessRequest) -> dict:
    return {
        "id": row.id,
        "name": row.name,
        "email": row.email,
        "company": row.company,
        "use_case": row.use_case,
        "expected_usage": row.expected_usage,
        "status": row.status,
        "ip": row.ip,
        "notes": row.notes,
        "created_at": iso(row.created_at),
        "reviewed_at": iso(row.reviewed_at),
        "reviewed_by": row.reviewed_by,
    }


@router.get("")
async def list_access_requests(
    _: User = Depends(owner_user),
    session: AsyncSession = Depends(get_session),
    status: str | None = None,
    limit: int = 100,
):
    query = select(AccessRequest).order_by(AccessRequest.created_at.desc()).limit(limit)
    if status in ("pending", "approved", "rejected"):
        query = query.where(AccessRequest.status == status)
    rows = (await session.execute(query)).scalars().all()

    pending = (
        await session.execute(
            select(func.count())
            .select_from(AccessRequest)
            .where(AccessRequest.status == "pending")
        )
    ).scalar() or 0

    return {"requests": [_serialize(r) for r in rows], "pending_count": int(pending)}


@router.post("/{request_id}/approve")
async def approve_access_request(
    request_id: int,
    request: Request,
    user: User = Depends(owner_user),
    session: AsyncSession = Depends(get_session),
):
    row = await session.get(AccessRequest, request_id)
    if not row:
        return error_response(404, "not_found", "Access request not found.", "")

    row.status = "approved"
    row.reviewed_at = utcnow()
    row.reviewed_by = user.id
    await session.commit()
    await audit.record(
        session,
        "access_request.approve",
        user_id=user.id,
        target=row.email,
        ip=client_ip(request),
    )

    # Hand the owner a prefill for the existing partner form.
    return {
        "request": _serialize(row),
        "prefill": {
            "name": row.company,
            "notes": f"Access request from {row.name} <{row.email}>\n{row.use_case}",
            "login_email": row.email,
        },
    }


@router.post("/{request_id}/reject")
async def reject_access_request(
    request_id: int,
    payload: AccessRequestReject,
    request: Request,
    user: User = Depends(owner_user),
    session: AsyncSession = Depends(get_session),
):
    row = await session.get(AccessRequest, request_id)
    if not row:
        return error_response(404, "not_found", "Access request not found.", "")

    row.status = "rejected"
    row.reviewed_at = utcnow()
    row.reviewed_by = user.id
    if payload.notes:
        row.notes = payload.notes.strip()[:1000]
    await session.commit()
    await audit.record(
        session,
        "access_request.reject",
        user_id=user.id,
        target=row.email,
        ip=client_ip(request),
    )
    return _serialize(row)
