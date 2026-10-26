"""Runtime settings: DB overrides on top of environment defaults."""

from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings as env
from app.models import Setting

_DEFAULTS: dict[str, object] = {
    "global_monthly_cap_usd": env.global_monthly_cap_usd,
    "log_prompts": env.log_prompts,
    "retention_days": env.retention_days,
    "max_tokens_ceiling": env.max_tokens_ceiling,
    "max_request_bytes": env.max_request_bytes,
    "admin_allowed_ips": env.admin_allowed_ips,
    "request_timeout_seconds": env.request_timeout_seconds,
    "max_attempts": env.max_attempts,
    "auto_route": [],            # ordered model ids for model="auto"; empty = cheapest first
    "cache_enabled": True,
    "cache_ttl_seconds": 3600,
}

_cache: dict[str, object] = {}


async def load_all(session: AsyncSession) -> dict[str, object]:
    result = await session.execute(select(Setting))
    values = dict(_DEFAULTS)
    for row in result.scalars().all():
        if row.key not in _DEFAULTS:
            values[row.key] = row.value
            continue
        try:
            values[row.key] = json.loads(row.value)
        except json.JSONDecodeError:
            values[row.key] = row.value
    _cache.clear()
    _cache.update(values)
    return values


def get(key: str, default=None):
    if key in _cache:
        return _cache[key]
    return _DEFAULTS.get(key, default)


async def set_many(session: AsyncSession, values: dict[str, object]) -> None:
    for key, value in values.items():
        row = await session.get(Setting, key)
        encoded = json.dumps(value)
        if row:
            row.value = encoded
        else:
            session.add(Setting(key=key, value=encoded))
        _cache[key] = value
    await session.commit()


def all_values() -> dict[str, object]:
    return dict(_cache) if _cache else dict(_DEFAULTS)
