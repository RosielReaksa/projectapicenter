"""Shared FastAPI dependencies: gateway-key auth and admin sessions."""

from __future__ import annotations

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core import config_cache, metrics, overload
from app.core.auth import (
    KEY_PREFIX,
    SESSION_COOKIE,
    AuthError,
    KeyContext,
    check_key_policy,
    hash_gateway_key,
    read_session,
)
from app.db import SessionLocal, get_session
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


async def require_gateway_key(request: Request) -> KeyContext:
    """Authenticate the bearer key for the data plane.

    No request-scoped session is injected: auth reads from the hot-path config
    cache and, on a miss, opens one short session and closes it before the
    provider call can start (LOAD_FIX requirement 1/2).
    """
    # Maintenance is checked first, before the key lookup, so a blocked caller
    # gets a clear 503 even with an invalid key (MAINTENANCE_SPEC §4.4).
    await enforce_maintenance(request)

    raw_key = bearer_token(request)
    try:
        context = await authenticate_cached(raw_key, client_ip(request))
    except AuthError as exc:
        raise AuthFailure(exc.status, exc.code, exc.message) from exc

    request.state.key_context = context
    return context


async def authenticate_cached(raw_key: str, ip: str) -> KeyContext:
    """Key auth against the config cache, with one short DB read on a miss."""
    if not raw_key or not raw_key.startswith(KEY_PREFIX):
        raise AuthError(401, "invalid_api_key", "Missing or malformed API key.")

    key_hash = hash_gateway_key(raw_key)
    snapshot = config_cache.cached_key(key_hash)
    if snapshot is None:
        async with SessionLocal() as session:
            snapshot = await config_cache.load_key(session, key_hash)
        if snapshot is None:
            raise AuthError(401, "invalid_api_key", "API key not recognised.")

    # One shared policy function, applied to the detached snapshot, so cached
    # auth cannot drift from the database-backed path.
    check_key_policy(snapshot, snapshot.partner, ip)
    return KeyContext(key=snapshot, partner=snapshot.partner, raw_key=raw_key)


async def enforce_maintenance(request: Request) -> None:
    """Raise the structured 503 when full maintenance is on.

    Requests from an allow-listed IP still pass, for owner testing.
    """
    from app.core import maintenance

    try:
        snapshot = await maintenance.get_state()
    except Exception:
        # State unreadable: behave per MAINT_FAIL_MODE (§11).
        if settings.maint_fail_mode == "closed":
            raise AuthFailure(
                503,
                "state_unavailable",
                "Maintenance state is temporarily unavailable. Please retry shortly.",
            )
        return

    state = snapshot.global_state
    if not state.active:
        return

    if client_ip(request) in state.allow_ips:
        return

    raise MaintenanceActive(state)


class MaintenanceActive(Exception):
    """Carries the structured maintenance response (handled in errors.py)."""

    def __init__(self, state):
        self.state = state
        self.message = state.message or "The service is temporarily paused for maintenance."
        self.expected_end = state.expected_end
        self.retry_after = state.retry_after(settings.maintenance_retry_after)


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
