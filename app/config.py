"""Application settings, read from environment / .env."""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- identity -------------------------------------------------------
    app_name: str = "Private LLM Gateway"
    environment: str = "production"

    # --- public site (HOMEPAGE_SPEC §8: configurable text) --------------
    public_base_url: str = ""            # e.g. https://gw.example.com; empty = request origin
    support_email: str = "support@example.com"
    product_tagline: str = "One API for fast, reliable AI models."
    # Show model prices on the public pages. Off by default (§11).
    public_pricing: bool = False
    # Optional status page link; hidden until monitoring exists (§11).
    status_page_url: str = ""
    # Set true once the owner has replaced the placeholder legal text.
    legal_reviewed: bool = False

    # --- storage --------------------------------------------------------
    # PostgreSQL from day one (AGENT_BUILD_BRIEF §4.1).
    # SQLite is supported for unit tests only.
    database_url: str = "postgresql+asyncpg://gateway:gateway@localhost:5432/gateway"
    db_pool_size: int = 10
    db_max_overflow: int = 20

    # Shared state: rate counters, concurrency, circuit breakers, idempotency.
    # Empty = in-memory (single instance / tests only).
    redis_url: str = ""
    redis_prefix: str = "gw"

    # --- secrets --------------------------------------------------------
    master_key: str = ""                 # Fernet key for provider keys at rest
    session_secret: str = ""             # signs admin session cookies

    # --- security -------------------------------------------------------
    admin_allowed_ips: str = ""
    trust_proxy_headers: bool = False    # only behind a proxy you control
    cookie_secure: bool = True
    session_hours: int = 8
    session_absolute_hours: int = 24 * 7
    cors_origins: str = ""               # /v1 has no browser CORS by default

    # --- limits ---------------------------------------------------------
    global_monthly_cap_usd: float = 50.0
    max_request_bytes: int = 1_000_000
    max_tokens_ceiling: int = 32_000
    max_attempts: int = 3
    request_timeout_seconds: float = 60.0
    provider_timeout_seconds: float = 45.0

    # Concurrency safety net: an acquired slot auto-expires after this long,
    # so a crashed instance can never permanently hold capacity.
    concurrency_slot_ttl: int = 120

    # --- behaviour ------------------------------------------------------
    log_prompts: str = "metadata"        # off | metadata | full
    log_format: str = "json"             # json | text
    retention_days: int = 30
    idempotency_ttl_seconds: int = 86_400
    # Shared secret for /metrics. Empty = loopback only.
    metrics_token: str = ""

    # --- first run ------------------------------------------------------
    admin_email: str = "admin@localhost"
    admin_password: str = ""
    seed_demo_data: bool = True

    # --- derived --------------------------------------------------------
    @property
    def allowed_ips(self) -> list[str]:
        return [ip.strip() for ip in self.admin_allowed_ips.split(",") if ip.strip()]

    @property
    def allowed_origins(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


def _persisted_secret(filename: str, length: int = 48) -> str:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / filename
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    import secrets

    value = secrets.token_urlsafe(length)
    path.write_text(value, encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    log.warning("Generated %s - back this file up.", path)
    return value


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    if not settings.master_key:
        from cryptography.fernet import Fernet

        key_path = DATA_DIR / "master.key"
        if key_path.exists():
            settings.master_key = key_path.read_text(encoding="utf-8").strip()
        else:
            settings.master_key = Fernet.generate_key().decode()
            key_path.write_text(settings.master_key, encoding="utf-8")
            log.warning(
                "Generated a new MASTER_KEY at %s. Provider keys are encrypted with it - "
                "back it up, never commit it.",
                key_path,
            )

    if not settings.session_secret:
        settings.session_secret = _persisted_secret("session.secret")

    return settings


settings = get_settings()
