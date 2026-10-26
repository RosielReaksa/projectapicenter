"""Small shared helpers."""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone


def utcnow() -> datetime:
    """Naive UTC now.

    Everything is stored naive-UTC so SQLite and PostgreSQL behave the same.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


def iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.replace(microsecond=0).isoformat() + "Z"


def day_start(dt: datetime | None = None) -> datetime:
    dt = dt or utcnow()
    return dt.replace(hour=0, minute=0, second=0, microsecond=0)


def month_start(dt: datetime | None = None) -> datetime:
    dt = dt or utcnow()
    return dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def days_ago(n: int, dt: datetime | None = None) -> datetime:
    return day_start(dt) - timedelta(days=n)


def new_request_id() -> str:
    return "req_" + secrets.token_hex(12)


def mask_key(key: str, visible: int = 4) -> str:
    if not key:
        return ""
    if len(key) <= visible:
        return "*" * len(key)
    return "*" * max(0, len(key) - visible) + key[-visible:]


def estimate_tokens(text: str) -> int:
    """Rough token estimate when a provider does not report usage (~4 chars/token)."""
    if not text:
        return 0
    return max(1, len(text) // 4)
