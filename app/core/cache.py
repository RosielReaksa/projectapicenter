"""Response cache, keyed per partner (brief §4.7)."""

from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CacheEntry
from app.utils import utcnow

DEFAULT_TTL_SECONDS = 3600


def cache_key(
    *,
    partner_id: int,
    model: str,
    messages: list[dict[str, Any]],
    params: dict[str, Any],
) -> str:
    """Partner id is part of the hash, so entries are never shared across partners."""
    material = json.dumps(
        {"p": partner_id, "m": model, "msg": messages, "params": params},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(material.encode()).hexdigest()


def is_cacheable(body: dict[str, Any], model: str, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> bool:
    if ttl_seconds <= 0:
        return False
    if model == "auto":
        return False
    if body.get("temperature") not in (0, 0.0):
        return False
    if body.get("tools") or body.get("tool_choice"):
        return False
    for message in body.get("messages", []):
        content = message.get("content")
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") in ("image_url", "input_image"):
                    return False
    return True


async def get(session: AsyncSession, key: str, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> dict | None:
    row = await session.get(CacheEntry, key)
    if not row:
        return None
    if row.created_at < utcnow() - timedelta(seconds=ttl_seconds):
        await session.delete(row)
        await session.commit()
        return None
    try:
        return json.loads(row.response_json)
    except json.JSONDecodeError:
        return None


async def put(session: AsyncSession, key: str, response: dict[str, Any]) -> None:
    session.add(
        CacheEntry(hash=key, response_json=json.dumps(response), created_at=utcnow())
    )
    await session.commit()


async def purge_expired(session: AsyncSession, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> int:
    cutoff = utcnow() - timedelta(seconds=ttl_seconds)
    result = await session.execute(delete(CacheEntry).where(CacheEntry.created_at < cutoff))
    await session.commit()
    return result.rowcount or 0


async def clear(session: AsyncSession) -> None:
    await session.execute(delete(CacheEntry))
    await session.commit()


async def stats(session: AsyncSession) -> dict[str, int]:
    total = (await session.execute(select(func.count()).select_from(CacheEntry))).scalar() or 0
    return {"entries": int(total)}
