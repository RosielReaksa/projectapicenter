"""Pydantic request/response shapes."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field


# ------------------------------------------------------------------ /v1
class ChatRequest(BaseModel):
    """OpenAI-compatible chat body. Unknown fields are passed through to the provider."""

    model_config = ConfigDict(extra="allow")

    model: str = "auto"
    messages: list[dict[str, Any]] = Field(min_length=1)
    temperature: float | None = None
    max_tokens: int | None = None
    top_p: float | None = None
    stop: str | list[str] | None = None
    stream: bool = False
    preset: str | None = None


class GatewayMeta(BaseModel):
    provider: str
    model: str
    cached: bool = False
    cost: float = 0.0
    fallbacks_tried: list[dict[str, Any]] = []
    latency_ms: int = 0


# ----------------------------------------------------------------- admin
class LoginRequest(BaseModel):
    email: str
    password: str
    totp_code: str | None = None


class TotpVerifyRequest(BaseModel):
    code: str


class PasswordChangeRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=10)


class PartnerCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    slug: str | None = None
    concurrency_limit: int | None = Field(default=None, ge=1)
    rpm_limit: int | None = Field(default=None, ge=1)
    tpm_limit: int | None = Field(default=None, ge=1)
    daily_request_limit: int | None = Field(default=None, ge=0)
    monthly_cap_usd: float | None = Field(default=None, ge=0)
    over_capacity: Literal["reject", "queue"] = "reject"
    queue_timeout_ms: int = Field(default=2000, ge=0, le=30_000)
    markup_multiplier: float = Field(default=1.0, ge=0)
    allow_free_tier: bool = False
    notes: str | None = None
    login_email: EmailStr | None = None
    login_password: str | None = Field(default=None, min_length=10)


class PartnerUpdateRequest(BaseModel):
    name: str | None = None
    concurrency_limit: int | None = Field(default=None, ge=1)
    rpm_limit: int | None = Field(default=None, ge=1)
    tpm_limit: int | None = Field(default=None, ge=1)
    daily_request_limit: int | None = Field(default=None, ge=0)
    monthly_cap_usd: float | None = Field(default=None, ge=0)
    over_capacity: Literal["reject", "queue"] | None = None
    queue_timeout_ms: int | None = Field(default=None, ge=0, le=30_000)
    markup_multiplier: float | None = Field(default=None, ge=0)
    allow_free_tier: bool | None = None
    is_active: bool | None = None
    notes: str | None = None


class KeyCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    partner_id: int | None = None
    daily_limit: int | None = Field(default=None, ge=0)
    monthly_cap_usd: float | None = Field(default=None, ge=0)
    rpm_limit: int | None = Field(default=None, ge=1)
    tpm_limit: int | None = Field(default=None, ge=1)
    concurrency_limit: int | None = Field(default=None, ge=1)
    allowed_models: list[str] | None = None
    allowed_ips: list[str] | None = None
    allow_free_tier: bool | None = None
    expires_at: str | None = None


class KeyUpdateRequest(BaseModel):
    name: str | None = None
    daily_limit: int | None = Field(default=None, ge=0)
    monthly_cap_usd: float | None = Field(default=None, ge=0)
    rpm_limit: int | None = Field(default=None, ge=1)
    tpm_limit: int | None = Field(default=None, ge=1)
    concurrency_limit: int | None = Field(default=None, ge=1)
    allowed_models: list[str] | None = None
    allowed_ips: list[str] | None = None
    allow_free_tier: bool | None = None
    expires_at: str | None = None
    is_active: bool | None = None


class ProviderCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    label: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    is_free_tier: bool | None = None
    may_train_on_prompts: bool = False
    monthly_cap_usd: float | None = Field(default=None, ge=0)
    rpm_limit: int | None = Field(default=None, ge=0)
    priority: int | None = None
    is_active: bool | None = None
    seed_models: bool = True


class ProviderUpdateRequest(BaseModel):
    label: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    is_free_tier: bool | None = None
    may_train_on_prompts: bool | None = None
    monthly_cap_usd: float | None = Field(default=None, ge=0)
    rpm_limit: int | None = Field(default=None, ge=0)
    priority: int | None = None
    is_active: bool | None = None


class ModelUpsertRequest(BaseModel):
    provider_id: int
    model_name: str
    alias: str | None = None
    context_length: int | None = None
    input_price_per_m: float = Field(default=0.0, ge=0)
    output_price_per_m: float = Field(default=0.0, ge=0)
    supports_vision: bool = False
    supports_tools: bool = False
    is_enabled: bool = True
    description: str | None = None


class ModelUpdateRequest(BaseModel):
    alias: str | None = None
    context_length: int | None = None
    input_price_per_m: float | None = Field(default=None, ge=0)
    output_price_per_m: float | None = Field(default=None, ge=0)
    supports_vision: bool | None = None
    supports_tools: bool | None = None
    is_enabled: bool | None = None
    description: str | None = None


class RuleUpsertRequest(BaseModel):
    position: int = 0
    condition: dict[str, Any] = {}
    action: dict[str, Any] = {}
    is_active: bool = True


class PresetUpsertRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    system_prompt: str | None = None
    model_id: int | None = None
    params: dict[str, Any] = {}


class PlaygroundRequest(BaseModel):
    model: str = "auto"
    messages: list[dict[str, Any]] = Field(min_length=1)
    system: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    top_p: float | None = None
    stop: str | list[str] | None = None
    preset: str | None = None


class MemberCreateRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10)
    role: Literal["owner", "member", "partner"] = "partner"
    partner_id: int | None = None


class MemberUpdateRequest(BaseModel):
    is_active: bool | None = None
    password: str | None = Field(default=None, min_length=10)
    role: Literal["owner", "member", "partner"] | None = None
    partner_id: int | None = None


class SettingsUpdateRequest(BaseModel):
    global_monthly_cap_usd: float | None = Field(default=None, ge=0)
    log_prompts: Literal["off", "metadata", "full"] | None = None
    retention_days: int | None = Field(default=None, ge=1, le=3650)
    max_tokens_ceiling: int | None = Field(default=None, ge=1)
    max_request_bytes: int | None = Field(default=None, ge=1024)
    admin_allowed_ips: str | None = None
    request_timeout_seconds: float | None = Field(default=None, ge=1, le=600)
    max_attempts: int | None = Field(default=None, ge=1, le=10)
    cache_enabled: bool | None = None
    cache_ttl_seconds: int | None = Field(default=None, ge=0, le=86400 * 7)
    public_pricing: bool | None = None


# --------------------------------------------------------------- public site
class AccessRequestCreate(BaseModel):
    """Public form body (HOMEPAGE_SPEC §4.1). Lengths are enforced server-side."""

    name: str = Field(min_length=1, max_length=100)
    email: EmailStr
    company: str = Field(min_length=1, max_length=200)
    use_case: str = Field(min_length=1, max_length=1000)
    expected_usage: Literal[
        "Under 1k requests/day",
        "1k to 10k",
        "10k to 100k",
        "Over 100k",
        "Not sure",
    ]
    accepted_terms: bool
    # Honeypot: real users never see or fill this.
    website_url: str | None = Field(default=None, max_length=200)


class AccessRequestReject(BaseModel):
    notes: str | None = Field(default=None, max_length=1000)


# ------------------------------------------------------------- maintenance
class MaintenanceStartRequest(BaseModel):
    message: str = Field(default="", max_length=300)
    expected_end: str | None = None
    allow_ips: list[str] = []
    allow_current_ip: bool = True
    reason: str = Field(min_length=1, max_length=500)


class MaintenanceStopRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


class DegradedRequest(BaseModel):
    active: bool
    paid_only: bool = False
    free_only: bool = False
    reduce_concurrency_pct: int = Field(default=0, ge=0, le=100)
    max_tokens: int | None = Field(default=None, ge=1)
    reject_low_priority: bool = False
    message: str = Field(default="", max_length=300)
    reason: str | None = Field(default=None, max_length=500)


class ProviderPauseRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


class PartnerSuspendRequest(BaseModel):
    reason_internal: str = Field(min_length=1, max_length=1000)
    reason_public: str | None = Field(default=None, max_length=300)


class WindowCreateRequest(BaseModel):
    starts_at: str
    ends_at: str
    banner_text: str = Field(min_length=1, max_length=300)
    auto_enable: bool = False


class WindowUpdateRequest(BaseModel):
    ends_at: str | None = None
    banner_text: str | None = Field(default=None, max_length=300)
    auto_enable: bool | None = None
