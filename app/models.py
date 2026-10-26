"""SQLAlchemy tables.

Design notes:
* IDs are integers for compact logs, but partners carry a public slug.
* Money is stored twice: ``cost_usd`` (what we pay) and ``price_usd`` (what the
  partner is charged). Billing is out of scope for v1, the data is not.
* ``requests`` is the largest table: every filterable column is indexed.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.utils import utcnow


class User(Base):
    """A person who can sign in to the console (owner or partner admin)."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="owner")
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    totp_secret: Mapped[str | None] = mapped_column(String(512))
    totp_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    partner_id: Mapped[int | None] = mapped_column(ForeignKey("partners.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    partner: Mapped["Partner | None"] = relationship(back_populates="users")

    @property
    def is_owner(self) -> bool:
        return self.role == "owner"


class Partner(Base):
    """A company or website that owns gateway keys and has its own limits."""

    __tablename__ = "partners"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)

    # --- limits (brief §5.2) -------------------------------------------
    concurrency_limit: Mapped[int | None] = mapped_column(Integer)
    rpm_limit: Mapped[int | None] = mapped_column(Integer)
    tpm_limit: Mapped[int | None] = mapped_column(Integer)
    daily_request_limit: Mapped[int | None] = mapped_column(Integer)
    monthly_cap_usd: Mapped[float | None] = mapped_column(Float)
    # "reject" fails fast with 429; "queue" waits briefly (brief §5.2).
    over_capacity: Mapped[str] = mapped_column(String(16), default="reject", nullable=False)
    queue_timeout_ms: Mapped[int] = mapped_column(Integer, default=2000, nullable=False)

    # --- policy ---------------------------------------------------------
    markup_multiplier: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    allow_free_tier: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    keys: Mapped[list["GatewayKey"]] = relationship(back_populates="partner")
    users: Mapped[list[User]] = relationship(back_populates="partner")


class Provider(Base):
    __tablename__ = "providers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    label: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    base_url: Mapped[str] = mapped_column(String(512), nullable=False)
    api_key_enc: Mapped[str | None] = mapped_column(Text)

    # Free tiers are last-resort only and can be excluded per partner (brief §4.6).
    is_free_tier: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    may_train_on_prompts: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    monthly_cap_usd: Mapped[float | None] = mapped_column(Float)
    rpm_limit: Mapped[int | None] = mapped_column(Integer)
    priority: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    health_status: Mapped[str] = mapped_column(String(16), default="unknown", nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_latency_ms: Mapped[int | None] = mapped_column(Integer)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    models: Mapped[list["Model"]] = relationship(
        back_populates="provider", cascade="all, delete-orphan"
    )

    @property
    def has_key(self) -> bool:
        return bool(self.api_key_enc)


class Model(Base):
    __tablename__ = "models"
    __table_args__ = (UniqueConstraint("provider_id", "model_name", name="uq_model"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider_id: Mapped[int] = mapped_column(
        ForeignKey("providers.id", ondelete="CASCADE"), nullable=False
    )
    model_name: Mapped[str] = mapped_column(String(255), nullable=False)
    alias: Mapped[str | None] = mapped_column(String(255))
    context_length: Mapped[int | None] = mapped_column(Integer)
    input_price_per_m: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    output_price_per_m: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    supports_vision: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    supports_tools: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    # Surfaced in the Models page (spec §4.3 / §8).
    prices_checked_at: Mapped[datetime | None] = mapped_column(DateTime)

    provider: Mapped[Provider] = relationship(back_populates="models")

    @property
    def public_name(self) -> str:
        return self.alias or self.model_name

    @property
    def is_free(self) -> bool:
        return self.input_price_per_m == 0 and self.output_price_per_m == 0


class GatewayKey(Base):
    __tablename__ = "gateway_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    partner_id: Mapped[int] = mapped_column(ForeignKey("partners.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    key_prefix: Mapped[str] = mapped_column(String(32), nullable=False)

    # Per-key limits sit on top of per-partner limits.
    daily_limit: Mapped[int | None] = mapped_column(Integer)
    monthly_cap_usd: Mapped[float | None] = mapped_column(Float)
    rpm_limit: Mapped[int | None] = mapped_column(Integer)
    tpm_limit: Mapped[int | None] = mapped_column(Integer)
    concurrency_limit: Mapped[int | None] = mapped_column(Integer)

    allowed_models: Mapped[str | None] = mapped_column(Text)   # JSON list
    allowed_ips: Mapped[str | None] = mapped_column(Text)      # JSON list
    allow_free_tier: Mapped[bool | None] = mapped_column(Boolean)  # None = inherit partner
    expires_at: Mapped[datetime | None] = mapped_column(DateTime)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    partner: Mapped[Partner] = relationship(back_populates="keys")


class RoutingRule(Base):
    __tablename__ = "routing_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    condition_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    action_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Preset(Base):
    __tablename__ = "presets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    system_prompt: Mapped[str | None] = mapped_column(Text)
    model_id: Mapped[int | None] = mapped_column(ForeignKey("models.id", ondelete="SET NULL"))
    params_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class RequestLog(Base):
    __tablename__ = "requests"
    __table_args__ = (
        Index("idx_req_partner_time", "partner_id", "created_at"),
        Index("idx_req_key_time", "gateway_key_id", "created_at"),
        Index("idx_req_time", "created_at"),
        Index("idx_req_provider_time", "provider_id", "created_at"),
        Index("idx_req_end_user", "end_user", "created_at"),
        Index("idx_req_request_id", "request_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    request_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    gateway_key_id: Mapped[int | None] = mapped_column(
        ForeignKey("gateway_keys.id", ondelete="SET NULL")
    )
    partner_id: Mapped[int | None] = mapped_column(
        ForeignKey("partners.id", ondelete="SET NULL")
    )
    end_user: Mapped[str | None] = mapped_column(String(255))

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    requested_model: Mapped[str | None] = mapped_column(String(255))
    provider_id: Mapped[int | None] = mapped_column(ForeignKey("providers.id", ondelete="SET NULL"))
    model_id: Mapped[int | None] = mapped_column(ForeignKey("models.id", ondelete="SET NULL"))
    status_code: Mapped[int] = mapped_column(Integer, default=200, nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    cost_usd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)   # we pay
    price_usd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)  # partner pays

    cached: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    fallbacks_tried: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    payload_json: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(16), default="api", nullable=False)


class CacheEntry(Base):
    __tablename__ = "cache"

    hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    response_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class AuditLog(Base):
    __tablename__ = "audit_log"
    __table_args__ = (Index("idx_audit_time", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    target: Mapped[str | None] = mapped_column(String(255))
    ip: Mapped[str | None] = mapped_column(String(64))
    detail: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)


class AccessRequest(Base):
    """A public request for an invitation (HOMEPAGE_SPEC §4.4).

    Submitting one never creates a user, partner or key. The owner reviews it
    by hand and approval starts the normal partner-creation flow.
    """

    __tablename__ = "access_requests"
    __table_args__ = (Index("idx_access_status", "status", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    company: Mapped[str] = mapped_column(String(200), nullable=False)
    use_case: Mapped[str] = mapped_column(Text, nullable=False)
    expected_usage: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    ip: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime)
    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    notes: Mapped[str | None] = mapped_column(Text)


class DailySummary(Base):
    """Aggregated rows kept after raw request rows are pruned."""

    __tablename__ = "daily_summary"
    __table_args__ = (
        UniqueConstraint("day", "partner_id", "provider_id", "model_id", name="uq_summary"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    day: Mapped[str] = mapped_column(String(10), nullable=False)
    partner_id: Mapped[int | None] = mapped_column(Integer)
    provider_id: Mapped[int | None] = mapped_column(Integer)
    model_id: Mapped[int | None] = mapped_column(Integer)
    requests: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    errors: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    price_usd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
