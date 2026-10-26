"""Append-only audit trail."""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog
from app.utils import utcnow

log = logging.getLogger("gateway.audit")


async def record(
    session: AsyncSession,
    action: str,
    *,
    user_id: int | None = None,
    target: str | None = None,
    ip: str | None = None,
) -> None:
    """Never let an audit failure break the action that was being audited."""
    try:
        session.add(
            AuditLog(user_id=user_id, action=action, target=target, ip=ip, created_at=utcnow())
        )
        await session.commit()
    except Exception:  # pragma: no cover
        log.exception("Audit write failed for %s", action)
        await session.rollback()
