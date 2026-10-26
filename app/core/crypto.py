"""Symmetric encryption for provider keys and TOTP secrets."""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings

_fernet = Fernet(settings.master_key.encode())


def encrypt(plaintext: str) -> str:
    return _fernet.encrypt(plaintext.encode()).decode()


def decrypt(token: str) -> str:
    try:
        return _fernet.decrypt(token.encode()).decode()
    except InvalidToken as exc:  # pragma: no cover - misconfigured master key
        raise ValueError(
            "Cannot decrypt stored secret: MASTER_KEY does not match the one used "
            "to encrypt it. Restore data/master.key or re-enter provider keys."
        ) from exc


def fingerprint(secret: str) -> str:
    """Short stable hint shown in the UI so a key can be recognised, never the key."""
    import hashlib

    return hashlib.sha256(secret.encode()).hexdigest()[:8]
