"""Candidate selection: rules, health, caps and ordering (backend-design §5)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core import health, limits, settings_store
from app.models import GatewayKey, Model, Partner, Provider, RoutingRule


class NoCandidate(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


@dataclass(slots=True)
class Candidate:
    model: Model
    provider: Provider
    reason: str = ""

    @property
    def label(self) -> str:
        return f"{self.provider.name}/{self.model.public_name}"

    @property
    def is_free(self) -> bool:
        return self.model.input_price_per_m == 0 and self.model.output_price_per_m == 0


@dataclass(slots=True)
class RequestFeatures:
    has_image: bool = False
    needs_tools: bool = False
    approx_chars: int = 0
    preset: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def extract_features(body: dict[str, Any], preset: str | None = None) -> RequestFeatures:
    has_image = False
    chars = 0
    for message in body.get("messages") or []:
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if isinstance(content, str):
            chars += len(content)
            continue
        if isinstance(content, list):
            for part in content:
                if not isinstance(part, dict):
                    continue
                if part.get("type") in ("image_url", "input_image", "image"):
                    has_image = True
                elif part.get("type") == "text":
                    chars += len(str(part.get("text", "")))
    return RequestFeatures(
        has_image=has_image,
        needs_tools=bool(body.get("tools")),
        approx_chars=chars,
        preset=preset,
    )


# ------------------------------------------------------------------- rules
_CONDITION_KEYS = {"has_image", "needs_tools", "max_chars", "min_chars", "preset"}


def _rule_matches(condition: dict[str, Any], features: RequestFeatures) -> bool:
    if not condition:
        return False
    for key, expected in condition.items():
        if key not in _CONDITION_KEYS:
            # A typo must never widen routing.
            return False
        if key == "has_image" and bool(expected) != features.has_image:
            return False
        if key == "needs_tools" and bool(expected) != features.needs_tools:
            return False
        if key == "max_chars" and features.approx_chars > int(expected):
            return False
        if key == "min_chars" and features.approx_chars < int(expected):
            return False
        if key == "preset" and str(expected) != (features.preset or ""):
            return False
    return True


def _apply_action(candidates: list[Candidate], action: dict[str, Any]) -> list[Candidate]:
    if not action:
        return candidates

    if isinstance(action.get("use_models"), list):
        wanted = [int(m) for m in action["use_models"]]
        chosen = [c for c in candidates if c.model.id in wanted]
        chosen.sort(key=lambda c: wanted.index(c.model.id))
        if chosen:
            return chosen + [c for c in candidates if c not in chosen]

    if action.get("prefer_provider"):
        name = str(action["prefer_provider"])
        first = [c for c in candidates if c.provider.name == name]
        if first:
            return first + [c for c in candidates if c.provider.name != name]

    if isinstance(action.get("exclude_providers"), list):
        blocked = {str(p) for p in action["exclude_providers"]}
        return [c for c in candidates if c.provider.name not in blocked]

    return candidates


# -------------------------------------------------------------- selection
def _allowed_names(key: GatewayKey) -> set[str]:
    if not key.allowed_models:
        return set()
    try:
        values = json.loads(key.allowed_models)
        return {str(v) for v in values} if isinstance(values, list) else set()
    except json.JSONDecodeError:
        return set()


def _order_key(candidate: Candidate) -> tuple:
    """Cheapest first, but free-tier providers always rank last (brief §4.6)."""
    return (
        1 if candidate.provider.is_free_tier else 0,
        candidate.provider.priority,
        candidate.model.input_price_per_m,
        candidate.model.output_price_per_m,
    )


def _free_tier_allowed(partner: Partner | None, key: GatewayKey | None) -> bool:
    """Free tiers are opt-in: the key may override the partner, else default off."""
    if key is not None and key.allow_free_tier is not None:
        return key.allow_free_tier
    if partner is not None:
        return partner.allow_free_tier
    return False


async def _catalog(session: AsyncSession) -> list[Candidate]:
    rows = (
        await session.execute(
            select(Model)
            .join(Provider, Model.provider_id == Provider.id)
            .options(selectinload(Model.provider))
            .where(Model.is_enabled.is_(True), Provider.is_active.is_(True))
        )
    ).scalars().all()
    # A provider without a key cannot serve anything.
    return [Candidate(model=m, provider=m.provider) for m in rows if m.provider.api_key_enc]


async def pick_candidates(
    session: AsyncSession,
    requested_model: str,
    key: GatewayKey | None,
    features: RequestFeatures,
    *,
    partner: Partner | None = None,
    allow_free_tier: bool | None = None,
) -> list[Candidate]:
    catalog = await _catalog(session)
    if not catalog:
        raise NoCandidate(
            "No providers are configured yet. Add a provider key in the dashboard first."
        )

    # ---- key model allow list -----------------------------------------
    allowed = _allowed_names(key) if key is not None else set()
    if allowed:
        everything = catalog
        catalog = [
            c for c in catalog
            if c.model.public_name in allowed or c.model.model_name in allowed
        ]
        if not catalog:
            raise NoCandidate("This key is not allowed to use any configured model.")
        requested = (requested_model or "auto").strip()
        if requested not in ("auto", "") and not any(
            c.model.public_name == requested or c.model.model_name == requested for c in catalog
        ) and any(
            c.model.public_name == requested or c.model.model_name == requested
            for c in everything
        ):
            raise NoCandidate(f"This key is not allowed to use the model '{requested}'.")

    # ---- free-tier policy ----------------------------------------------
    free_ok = (
        allow_free_tier
        if allow_free_tier is not None
        else _free_tier_allowed(partner, key)
    )
    if not free_ok:
        catalog = [c for c in catalog if not c.provider.is_free_tier]
        if not catalog:
            raise NoCandidate(
                "Only free-tier providers are configured, and free tiers are disabled for "
                "this partner. Enable them on the partner or add a paid provider."
            )

    # ---- requested model ------------------------------------------------
    requested = (requested_model or "auto").strip()
    if requested and requested != "auto":
        exact = [
            c for c in catalog
            if c.model.public_name == requested or c.model.model_name == requested
        ]
        if not exact:
            raise NoCandidate(
                f"Unknown model '{requested}'. Call GET /v1/models for the available list."
            )
        exact.sort(key=lambda c: c.provider.priority)
        rest = sorted([c for c in catalog if c not in exact], key=_order_key)
        candidates = exact + rest
    else:
        candidates = sorted(catalog, key=_order_key)
        preferred_ids = [int(m) for m in (settings_store.get("auto_route") or [])]
        if preferred_ids:
            preferred = [c for c in candidates if c.model.id in preferred_ids]
            preferred.sort(key=lambda c: preferred_ids.index(c.model.id))
            candidates = preferred + [c for c in candidates if c not in preferred]

    # ---- rules ----------------------------------------------------------
    rules = (
        await session.execute(
            select(RoutingRule)
            .where(RoutingRule.is_active.is_(True))
            .order_by(RoutingRule.position, RoutingRule.id)
        )
    ).scalars().all()
    for rule in rules:
        try:
            condition = json.loads(rule.condition_json or "{}")
            action = json.loads(rule.action_json or "{}")
        except json.JSONDecodeError:
            continue
        if _rule_matches(condition, features):
            candidates = _apply_action(candidates, action)

    # ---- capability filters ---------------------------------------------
    if features.has_image:
        candidates = [c for c in candidates if c.model.supports_vision]
        if not candidates:
            raise NoCandidate("No available model accepts images.")

    if features.needs_tools:
        candidates = [c for c in candidates if c.model.supports_tools]
        if not candidates:
            raise NoCandidate("No available model supports tool calling.")

    # ---- health ---------------------------------------------------------
    healthy: list[Candidate] = []
    for candidate in candidates:
        if await health.is_available(candidate.provider.id):
            healthy.append(candidate)
    if not healthy:
        raise NoCandidate("All providers are cooling down after failures. Try again shortly.")
    candidates = healthy

    # ---- global spend kill switch ---------------------------------------
    cap = float(settings_store.get("global_monthly_cap_usd", 0) or 0)
    if await limits.paid_routes_blocked(session, cap):
        candidates = [c for c in candidates if c.is_free]
        if not candidates:
            raise NoCandidate(
                "The global monthly spend cap is reached, so only free models may run, "
                "and none are available."
            )

    # ---- provider monthly caps -------------------------------------------
    spend: dict[int, float] = {}
    filtered: list[Candidate] = []
    for candidate in candidates:
        provider_cap = candidate.provider.monthly_cap_usd
        if provider_cap is not None:
            pid = candidate.provider.id
            if pid not in spend:
                spend[pid] = await limits.provider_spend(session, pid)
            if spend[pid] >= provider_cap:
                continue
        filtered.append(candidate)
    candidates = filtered

    if not candidates:
        raise NoCandidate("No provider is currently able to serve this request.")

    max_attempts = int(settings_store.get("max_attempts", 3) or 3)
    return candidates[:max_attempts]
