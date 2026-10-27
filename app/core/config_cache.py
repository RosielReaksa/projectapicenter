"""Hot-path configuration cache.

The data plane must not read the database to find out who the caller is or
which models exist. Everything it needs is small and changes rarely, so it is
cached in-process for a short TTL and invalidated when an admin writes.

Two caches:

* **key cache**   gateway key hash -> key + partner snapshot (auth)
* **catalog**     providers + models + routing rules (routing)

Invalidation is a version counter bumped by admin writes. In a multi-instance
deployment the bump only reaches the instance that served the write, so the
TTL (``config_cache_ttl``, default 10s) is the real bound on staleness. That is
the same trade-off the maintenance state makes, and it is documented.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.models import GatewayKey, Model, Partner, Provider, RoutingRule
from app.utils import parse_str_list

# ----------------------------------------------------------------- snapshots


@dataclass(slots=True, frozen=True)
class PartnerSnapshot:
    """Plain, detached copy of the partner fields the data plane reads."""

    id: int
    name: str
    slug: str
    is_active: bool
    suspended: bool
    suspended_reason_public: str | None
    concurrency_limit: int | None
    rpm_limit: int | None
    tpm_limit: int | None
    daily_request_limit: int | None
    monthly_cap_usd: float | None
    over_capacity: str
    queue_timeout_ms: int
    markup_multiplier: float
    allow_free_tier: bool


@dataclass(slots=True, frozen=True)
class KeySnapshot:
    """Plain, detached copy of the gateway-key fields the data plane reads."""

    id: int
    name: str
    key_prefix: str
    is_active: bool
    expires_at: Any
    last_used_at: Any
    daily_limit: int | None
    monthly_cap_usd: float | None
    rpm_limit: int | None
    tpm_limit: int | None
    concurrency_limit: int | None
    allowed_models: str | None
    allowed_ips: str | None
    allow_free_tier: bool | None
    partner: PartnerSnapshot


@dataclass(slots=True, frozen=True)
class ProviderSnapshot:
    id: int
    name: str
    label: str
    base_url: str
    api_key_enc: str | None
    is_free_tier: bool
    monthly_cap_usd: float | None
    priority: int
    is_active: bool
    paused: bool


@dataclass(slots=True, frozen=True)
class ModelSnapshot:
    id: int
    provider_id: int
    model_name: str
    alias: str | None
    context_length: int | None
    input_price_per_m: float
    output_price_per_m: float
    supports_vision: bool
    supports_tools: bool
    is_enabled: bool
    provider: ProviderSnapshot

    @property
    def public_name(self) -> str:
        return self.alias or self.model_name

    @property
    def is_free(self) -> bool:
        return self.input_price_per_m == 0 and self.output_price_per_m == 0


@dataclass(slots=True, frozen=True)
class RuleSnapshot:
    id: int
    position: int
    condition_json: str
    action_json: str


@dataclass(slots=True)
class Catalog:
    models: list[ModelSnapshot] = field(default_factory=list)
    rules: list[RuleSnapshot] = field(default_factory=list)
    fetched_at: float = 0.0


# ------------------------------------------------------------------- caches
_key_cache: dict[str, tuple[KeySnapshot, float]] = {}
_catalog: Catalog | None = None
_version = 0


def ttl() -> float:
    """Catalog TTL: providers, models and routing rules."""
    return float(settings.config_cache_ttl)


def key_ttl() -> float:
    """Key TTL: shorter than the catalog so a revocation lands quickly.

    A cached key is only stale for this long after the owner disables it, and
    admin writes invalidate in-process immediately.
    """
    return float(settings.key_cache_ttl)


def bump() -> None:
    """Invalidate every cache. Called by admin writes."""
    global _version, _catalog
    _version += 1
    _catalog = None
    _key_cache.clear()


def invalidate_key(key_hash: str) -> None:
    _key_cache.pop(key_hash, None)
    bump()


def stats() -> dict[str, int]:
    now = time.monotonic()
    live = sum(1 for _, at in _key_cache.values() if now - at < key_ttl())
    return {"cached_keys": live, "catalog_loaded": int(_catalog is not None)}


# ------------------------------------------------------------- key lookups
def _partner_snapshot(partner: Partner) -> PartnerSnapshot:
    return PartnerSnapshot(
        id=partner.id,
        name=partner.name,
        slug=partner.slug,
        is_active=partner.is_active,
        suspended=partner.suspended,
        suspended_reason_public=partner.suspended_reason_public,
        concurrency_limit=partner.concurrency_limit,
        rpm_limit=partner.rpm_limit,
        tpm_limit=partner.tpm_limit,
        daily_request_limit=partner.daily_request_limit,
        monthly_cap_usd=partner.monthly_cap_usd,
        over_capacity=partner.over_capacity,
        queue_timeout_ms=partner.queue_timeout_ms,
        markup_multiplier=partner.markup_multiplier,
        allow_free_tier=partner.allow_free_tier,
    )


def partner_snapshot(partner: Partner) -> PartnerSnapshot:
    """Detached partner fields, for callers that have no key of their own."""
    return _partner_snapshot(partner)


def key_snapshot(key: GatewayKey, partner: Partner) -> KeySnapshot:
    """Detached copy of a key and its partner, safe to use without a session."""
    return KeySnapshot(
        id=key.id,
        name=key.name,
        key_prefix=key.key_prefix,
        is_active=key.is_active,
        expires_at=key.expires_at,
        last_used_at=key.last_used_at,
        daily_limit=key.daily_limit,
        monthly_cap_usd=key.monthly_cap_usd,
        rpm_limit=key.rpm_limit,
        tpm_limit=key.tpm_limit,
        concurrency_limit=key.concurrency_limit,
        allowed_models=key.allowed_models,
        allowed_ips=key.allowed_ips,
        allow_free_tier=key.allow_free_tier,
        partner=_partner_snapshot(partner),
    )


def cached_key(key_hash: str) -> KeySnapshot | None:
    entry = _key_cache.get(key_hash)
    if not entry:
        return None
    snapshot, at = entry
    if time.monotonic() - at >= key_ttl():
        _key_cache.pop(key_hash, None)
        return None
    return snapshot


def allowed_list(raw: str | None) -> list[str]:
    """Parse a JSON string list column (allowed_models / allowed_ips)."""
    return parse_str_list(raw)


async def load_key(session: AsyncSession, key_hash: str) -> KeySnapshot | None:
    """One short query, then the result is cached for the TTL.

    The version is captured before the query. If an admin write bumps it while
    the query is in flight, the value just read may already be stale (it was
    read before the write committed), so it is returned to this caller but NOT
    stored. Without that check, a bump landing between the read and the store
    would repopulate the cache with a key the owner had just revoked, for a
    full TTL.
    """
    version_before = _version
    row = (
        await session.execute(
            select(GatewayKey)
            .options(selectinload(GatewayKey.partner))
            .where(GatewayKey.key_hash == key_hash)
        )
    ).scalar_one_or_none()
    if not row or not row.partner:
        return None
    snapshot = key_snapshot(row, row.partner)
    if _version == version_before:
        _key_cache[key_hash] = (snapshot, time.monotonic())
    return snapshot


# ---------------------------------------------------------------- catalog
async def load_catalog(session: AsyncSession) -> Catalog:
    models = (
        await session.execute(
            select(Model)
            .join(Provider, Model.provider_id == Provider.id)
            .options(selectinload(Model.provider))
            .where(Model.is_enabled.is_(True), Provider.is_active.is_(True))
        )
    ).scalars().all()

    rules = (
        await session.execute(
            select(RoutingRule)
            .where(RoutingRule.is_active.is_(True))
            .order_by(RoutingRule.position, RoutingRule.id)
        )
    ).scalars().all()

    snapshots: list[ModelSnapshot] = []
    for model in models:
        provider = model.provider
        if provider.paused:  # manual maintenance pause (§4.1)
            continue
        if not provider.api_key_enc:  # a provider without a key serves nothing
            continue
        snapshots.append(
            ModelSnapshot(
                id=model.id,
                provider_id=provider.id,
                model_name=model.model_name,
                alias=model.alias,
                context_length=model.context_length,
                input_price_per_m=model.input_price_per_m,
                output_price_per_m=model.output_price_per_m,
                supports_vision=model.supports_vision,
                supports_tools=model.supports_tools,
                is_enabled=model.is_enabled,
                provider=ProviderSnapshot(
                    id=provider.id,
                    name=provider.name,
                    label=provider.label or provider.name,
                    base_url=provider.base_url,
                    api_key_enc=provider.api_key_enc,
                    is_free_tier=provider.is_free_tier,
                    monthly_cap_usd=provider.monthly_cap_usd,
                    priority=provider.priority,
                    is_active=provider.is_active,
                    paused=provider.paused,
                ),
            )
        )

    return Catalog(
        models=snapshots,
        rules=[
            RuleSnapshot(
                id=r.id,
                position=r.position,
                condition_json=r.condition_json,
                action_json=r.action_json,
            )
            for r in rules
        ],
        fetched_at=time.monotonic(),
    )


def cached_catalog() -> Catalog | None:
    if _catalog is None:
        return None
    if time.monotonic() - _catalog.fetched_at >= ttl():
        return None
    return _catalog


async def get_catalog(session: AsyncSession) -> Catalog:
    """Return the cached catalog, loading it once when cold or expired.

    Same version guard as load_key: a load that raced an admin write is
    returned but not stored, so a just-paused provider is not resurrected in
    the cache for a full TTL.
    """
    global _catalog
    hit = cached_catalog()
    if hit is not None:
        return hit
    version_before = _version
    catalog = await load_catalog(session)
    if _version == version_before:
        _catalog = catalog
    return catalog
