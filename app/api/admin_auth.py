"""Admin authentication: password + TOTP, sessions, 2FA enrolment."""

from __future__ import annotations

import base64
import io
import secrets

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthFailure, client_ip, current_user
from app.config import settings
from app.core import audit
from app.core.auth import (
    SESSION_COOKIE,
    clear_login_failures,
    decrypt_totp,
    encrypt_totp,
    hash_password,
    issue_session,
    login_locked,
    new_totp_secret,
    record_login_failure,
    totp_uri,
    verify_password,
    verify_totp,
)
from app.db import get_session
from app.models import User
from app.schemas import LoginRequest, PasswordChangeRequest, TotpVerifyRequest
from app.utils import iso

router = APIRouter(prefix="/admin/auth", tags=["admin-auth"])

CSRF_COOKIE = "gw_csrf"
MAX_AGE = settings.session_absolute_hours * 3600


def _cookie(response: Response, name: str, value: str, *, http_only: bool) -> None:
    response.set_cookie(
        name,
        value,
        httponly=http_only,
        secure=settings.cookie_secure,
        samesite="strict",
        max_age=MAX_AGE,
        path="/",
    )


@router.post("/login")
async def login(
    payload: LoginRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    ip = client_ip(request)
    identifier = f"{payload.email.lower().strip()}|{ip}"

    locked = login_locked(identifier)
    if locked:
        return JSONResponse(
            status_code=429,
            content={
                "error": {
                    "code": "login_locked",
                    "message": f"Too many failed attempts. Try again in {max(1, locked // 60)} minute(s).",
                    "request_id": getattr(request.state, "request_id", ""),
                }
            },
            headers={"Retry-After": str(locked)},
        )

    user = (
        await session.execute(
            select(User).where(User.email == payload.email.lower().strip())
        )
    ).scalar_one_or_none()

    if not user or not user.is_active or not verify_password(user.password_hash, payload.password):
        record_login_failure(identifier)
        raise AuthFailure(401, "invalid_credentials", "Email or password is incorrect.")

    if user.totp_enabled:
        if not payload.totp_code:
            raise AuthFailure(
                401, "totp_required", "Enter the 6-digit code from your authenticator app."
            )
        if not verify_totp(decrypt_totp(user.totp_secret), payload.totp_code):
            record_login_failure(identifier)
            raise AuthFailure(401, "invalid_totp", "That code is not valid.")

    clear_login_failures(identifier)
    await audit.record(session, "auth.login", user_id=user.id, target=user.email, ip=ip)

    response = JSONResponse(
        {
            "user": {
                "id": user.id,
                "email": user.email,
                "role": user.role,
                "totp_enabled": user.totp_enabled,
            },
            "expires_in_hours": settings.session_hours,
        }
    )
    _cookie(response, SESSION_COOKIE, issue_session(user.id), http_only=True)
    # Readable by the SPA so it can echo it back in X-CSRF-Token.
    _cookie(response, CSRF_COOKIE, secrets.token_urlsafe(24), http_only=False)
    return response


@router.post("/logout")
async def logout():
    response = Response(status_code=204)
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")
    return response


@router.get("/me")
async def me(user: User = Depends(current_user)):
    return {
        "id": user.id,
        "email": user.email,
        "role": user.role,
        "totp_enabled": user.totp_enabled,
        "created_at": iso(user.created_at),
        "environment": settings.environment,
    }


# ------------------------------------------------------------------- 2FA
@router.post("/totp/setup")
async def totp_setup(
    user: User = Depends(current_user), session: AsyncSession = Depends(get_session)
):
    """Generate a new secret and QR code. 2FA only turns on after /totp/enable."""
    if user.totp_enabled:
        raise AuthFailure(400, "already_enabled", "Two-factor is already enabled.")

    secret = new_totp_secret()
    user.totp_secret = encrypt_totp(secret)
    await session.commit()

    uri = totp_uri(secret, user.email)
    qr_png_base64 = ""
    try:
        import qrcode

        image = qrcode.make(uri)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        qr_png_base64 = base64.b64encode(buffer.getvalue()).decode()
    except Exception:  # pragma: no cover - QR is a convenience, the URI is authoritative
        qr_png_base64 = ""

    return {"secret": secret, "otpauth_uri": uri, "qr_png_base64": qr_png_base64}


@router.post("/totp/enable")
async def totp_enable(
    payload: TotpVerifyRequest,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    if not user.totp_secret:
        raise AuthFailure(400, "no_setup", "Start the setup step first.")
    if not verify_totp(decrypt_totp(user.totp_secret), payload.code):
        raise AuthFailure(400, "invalid_totp", "That code is not valid. Check the clock on your phone.")

    user.totp_enabled = True
    await session.commit()
    await audit.record(session, "auth.totp_enabled", user_id=user.id, target=user.email)
    return {"totp_enabled": True}


@router.post("/totp/disable")
async def totp_disable(
    payload: TotpVerifyRequest,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    if not user.totp_enabled:
        return {"totp_enabled": False}
    if not verify_totp(decrypt_totp(user.totp_secret), payload.code):
        raise AuthFailure(400, "invalid_totp", "That code is not valid.")

    user.totp_enabled = False
    user.totp_secret = None
    await session.commit()
    await audit.record(session, "auth.totp_disabled", user_id=user.id, target=user.email)
    return {"totp_enabled": False}


@router.post("/password")
async def change_password(
    payload: PasswordChangeRequest,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    if not verify_password(user.password_hash, payload.current_password):
        raise AuthFailure(400, "invalid_password", "Current password is incorrect.")
    if len(payload.new_password) < 10:
        raise AuthFailure(400, "weak_password", "Use at least 10 characters.")

    user.password_hash = hash_password(payload.new_password)
    await session.commit()
    await audit.record(session, "auth.password_changed", user_id=user.id, target=user.email)
    return {"ok": True}
