"""Shared FastAPI dependencies: gateway-key auth and admin sessions."""

from __future__ import annotations

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.auth import (
    SESSION_COOKIE,
    AuthError,
    KeyContext,
    authenticate_gateway_key,
    read_session,
)
from app.db import get_session
from app.models import User


class AuthFailure(Exception):
    """Raised by dependencies; converted to the standard error shape."""

    def __init__(self, status: int, code: str, message: str):
        self.status = status
        self.code = code
        self.message = message


def client_ip(request: Request) -> str:
    """Real client address.

    X-Forwarded-For is trusted only when TRUST_PROXY_HEADERS is on, otherwise
    any caller could spoof an allow-listed IP.
    """
    if settings.trust_proxy_headers:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def bearer_token(request: Request) -> str:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return ""


async def require_gateway_key(
    request: Request, session: AsyncSession = Depends(get_session)
) -> KeyContext:
    try:
        return await authenticate_gateway_key(session, bearer_token(request), client_ip(request))
    except AuthError as exc:
        raise AuthFailure(exc.status, exc.code, exc.message) from exc


async def current_user(
    request: Request, session: AsyncSession = Depends(get_session)
) -> User:
    """Signed-in console user. Enforces the console IP allow list."""
    allowed = settings.allowed_ips
    if allowed and client_ip(request) not in allowed:
        raise AuthFailure(403, "ip_not_allowed", "This IP address is not allowed to use the console.")

    payload = read_session(request.cookies.get(SESSION_COOKIE, ""))
    if not payload:
        raise AuthFailure(401, "not_authenticated", "Sign in to continue.")

    user = await session.get(User, int(payload.get("uid") or 0))
    if not user or not user.is_active:
        raise AuthFailure(401, "not_authenticated", "Session is no longer valid.")

    request.state.user = user
    return user


async def owner_user(user: User = Depends(current_user)) -> User:
    if not user.is_owner:
        raise AuthFailure(403, "forbidden", "Only the owner can do that.")
    return user
