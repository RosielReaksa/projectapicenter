"""Gateway-key auth, admin sessions, password hashing, 2FA."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.models import GatewayKey, Partner
from app.utils import utcnow

KEY_PREFIX = "sk-gw-"
_ph = PasswordHasher()
_serializer = URLSafeTimedSerializer(settings.session_secret, salt="admin-session")

SESSION_COOKIE = "gw_session"


# ---------------------------------------------------------------- passwords
def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _ph.verify(password_hash, password)
    except (VerifyMismatchError, ValueError):
        return False


# ------------------------------------------------------------ gateway keys
def generate_gateway_key() -> str:
    """32 random bytes, url-safe. Shown once, stored only as SHA-256."""
    return KEY_PREFIX + secrets.token_urlsafe(32)


def hash_gateway_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def gateway_key_prefix(raw: str) -> str:
    return raw[:12]


def _allowed_list(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        value = json.loads(raw)
        return [str(v) for v in value] if isinstance(value, list) else []
    except json.JSONDecodeError:
        return []


@dataclass(slots=True)
class KeyContext:
    key: GatewayKey
    partner: Partner


class AuthError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


async def authenticate_gateway_key(
    session: AsyncSession, raw_key: str, client_ip: str | None
) -> KeyContext:
    if not raw_key or not raw_key.startswith(KEY_PREFIX):
        raise AuthError(401, "invalid_api_key", "Missing or malformed API key.")

    row = await session.execute(
        select(GatewayKey)
        .options(selectinload(GatewayKey.partner))
        .where(GatewayKey.key_hash == hash_gateway_key(raw_key))
    )
    key = row.scalar_one_or_none()
    if not key:
        raise AuthError(401, "invalid_api_key", "API key not recognised.")

    if not key.is_active:
        raise AuthError(403, "key_disabled", "This API key has been disabled.")
    if key.expires_at and key.expires_at < utcnow():
        raise AuthError(403, "key_expired", "This API key has expired.")

    partner = key.partner
    if partner is None or not partner.is_active:
        raise AuthError(403, "partner_disabled", "The partner owning this key is inactive.")

    allowed_ips = _allowed_list(key.allowed_ips)
    if allowed_ips and client_ip not in allowed_ips:
        raise AuthError(
            403, "ip_not_allowed", "Requests from this IP address are not allowed for this key."
        )

    return KeyContext(key=key, partner=partner)


# -------------------------------------------------------------- admin auth
def issue_session(user_id: int) -> str:
    return _serializer.dumps({"uid": user_id, "iat": int(time.time())})


def read_session(token: str) -> dict | None:
    if not token:
        return None
    try:
        payload = _serializer.loads(token, max_age=settings.session_hours * 3600)
    except (BadSignature, SignatureExpired):
        return None

    issued = int(payload.get("iat", 0))
    if time.time() - issued > settings.session_absolute_hours * 3600:
        return None
    return payload


# --------------------------------------------------------------------- 2FA
def new_totp_secret() -> str:
    import pyotp

    return pyotp.random_base32()


def encrypt_totp(secret: str) -> str:
    from app.core.crypto import encrypt

    return encrypt(secret)


def decrypt_totp(encrypted: str | None) -> str:
    from app.core.crypto import decrypt

    return decrypt(encrypted) if encrypted else ""


def totp_uri(secret: str, email: str) -> str:
    import pyotp

    return pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name=settings.app_name)


def verify_totp(secret: str, code: str) -> bool:
    import pyotp

    if not code or not secret:
        return False
    return pyotp.TOTP(secret).verify(code.strip().replace(" ", ""), valid_window=1)


# ------------------------------------------------------------- login limits
_login_attempts: dict[str, list[float]] = {}
LOGIN_WINDOW = 15 * 60
LOGIN_MAX_FAILURES = 5


def login_locked(identifier: str) -> int:
    stamps = [t for t in _login_attempts.get(identifier, []) if t > time.time() - LOGIN_WINDOW]
    _login_attempts[identifier] = stamps
    if len(stamps) >= LOGIN_MAX_FAILURES:
        return int(LOGIN_WINDOW - (time.time() - stamps[0])) + 1
    return 0


def record_login_failure(identifier: str) -> None:
    _login_attempts.setdefault(identifier, []).append(time.time())


def clear_login_failures(identifier: str) -> None:
    _login_attempts.pop(identifier, None)


def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())
