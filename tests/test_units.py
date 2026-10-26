"""Unit tests for pure logic: cost/price, cache, rules, breaker, store, limits."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.cache import cache_key, is_cacheable  # noqa: E402
from app.core.cost import compute_cost, compute_price  # noqa: E402
from app.core.router import (  # noqa: E402
    RequestFeatures,
    _apply_action,
    _order_key,
    _rule_matches,
    extract_features,
)
from app.core.limits import _tightest, _tightest_float  # noqa: E402
from app.core.store import MemoryStore  # noqa: E402
from app.models import Model, Partner, Provider  # noqa: E402
from app.utils import estimate_tokens, mask_key  # noqa: E402


def _model(input_price=1.0, output_price=2.0) -> Model:
    return Model(
        id=1,
        provider_id=1,
        model_name="m",
        input_price_per_m=input_price,
        output_price_per_m=output_price,
    )


# ------------------------------------------------------------ cost & price
def test_cost_one_million_each():
    assert compute_cost(_model(), 1_000_000, 1_000_000) == pytest.approx(3.0)


def test_cost_small_request():
    assert compute_cost(_model(), 1000, 500) == pytest.approx(0.002)


def test_cost_free_model():
    assert compute_cost(_model(0, 0), 5000, 5000) == 0.0


def test_cost_without_model():
    assert compute_cost(None, 100, 100) == 0.0


def test_price_applies_markup():
    partner = Partner(id=1, name="p", slug="p", markup_multiplier=1.5)
    assert compute_price(0.10, partner) == pytest.approx(0.15)


def test_price_without_markup():
    partner = Partner(id=1, name="p", slug="p", markup_multiplier=1.0)
    assert compute_price(0.10, partner) == pytest.approx(0.10)


def test_price_explicit_markup_wins():
    partner = Partner(id=1, name="p", slug="p", markup_multiplier=1.5)
    assert compute_price(0.10, partner, markup=2.0) == pytest.approx(0.20)


# ------------------------------------------------------------------ cache
def test_cache_key_is_deterministic():
    a = cache_key(partner_id=1, model="m", messages=[{"role": "user", "content": "hi"}], params={})
    b = cache_key(partner_id=1, model="m", messages=[{"role": "user", "content": "hi"}], params={})
    assert a == b


def test_cache_key_differs_per_partner():
    """Brief §4.7: cache entries must never be shared across partners."""
    a = cache_key(partner_id=1, model="m", messages=[{"role": "user", "content": "hi"}], params={})
    b = cache_key(partner_id=2, model="m", messages=[{"role": "user", "content": "hi"}], params={})
    assert a != b


def test_cacheable_only_at_temperature_zero():
    assert is_cacheable({"messages": [], "temperature": 0}, "m") is True
    assert is_cacheable({"messages": [], "temperature": 0.7}, "m") is False
    assert is_cacheable({"messages": []}, "m") is False


def test_cache_not_used_for_auto_route():
    assert is_cacheable({"messages": [], "temperature": 0}, "auto") is False


def test_cache_not_used_with_tools_or_images():
    assert is_cacheable({"messages": [], "temperature": 0, "tools": [{}]}, "m") is False
    body = {
        "messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {}}]}],
        "temperature": 0,
    }
    assert is_cacheable(body, "m") is False


def test_cache_disabled_when_ttl_zero():
    assert is_cacheable({"messages": [], "temperature": 0}, "m", ttl_seconds=0) is False


# --------------------------------------------------------------- features
def test_extract_features_image_and_text():
    body = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "hello"},
                    {"type": "image_url", "image_url": {}},
                ],
            }
        ]
    }
    features = extract_features(body)
    assert features.has_image is True
    assert features.approx_chars == 5


def test_extract_features_tools():
    assert extract_features({"messages": [], "tools": [{}]}).needs_tools is True


# ------------------------------------------------------------------- rules
def test_rule_matches_image():
    assert _rule_matches({"has_image": True}, RequestFeatures(has_image=True)) is True
    assert _rule_matches({"has_image": True}, RequestFeatures(has_image=False)) is False


def test_rule_matches_preset():
    assert _rule_matches({"preset": "chat"}, RequestFeatures(preset="chat")) is True


def test_rule_unknown_key_never_matches():
    """A typo must not silently match every request."""
    assert _rule_matches({"has_imgae": True}, RequestFeatures(has_image=True)) is False


def test_rule_empty_condition_never_matches():
    assert _rule_matches({}, RequestFeatures()) is False


def test_rule_multiple_conditions_must_all_match():
    features = RequestFeatures(has_image=True, approx_chars=10)
    assert _rule_matches({"has_image": True, "max_chars": 50}, features) is True
    assert _rule_matches({"has_image": True, "max_chars": 5}, features) is False


def _candidate(model_id: int, provider: Provider) -> object:
    model = Model(id=model_id, provider_id=provider.id, model_name=f"m{model_id}")
    return type("C", (), {"model": model, "provider": provider, "is_free": False})()


def test_action_use_models_puts_chosen_first():
    provider = Provider(id=1, name="p", base_url="x", priority=1)
    first, second = _candidate(1, provider), _candidate(2, provider)
    result = _apply_action([first, second], {"use_models": [2]})
    assert result[0] is second


def test_action_excludes_provider():
    p1 = Provider(id=1, name="one", base_url="x", priority=1)
    p2 = Provider(id=2, name="two", base_url="y", priority=2)
    result = _apply_action([_candidate(1, p1), _candidate(2, p2)], {"exclude_providers": ["one"]})
    assert len(result) == 1
    assert result[0].provider.name == "two"


def test_free_tier_providers_rank_last():
    """Brief §4.6: paid routes are primary, free tiers are fallback only."""
    paid = Provider(id=1, name="paid", base_url="x", priority=99, is_free_tier=False)
    free = Provider(id=2, name="free", base_url="y", priority=1, is_free_tier=True)
    cheap = _candidate(1, paid)
    free_candidate = _candidate(2, free)
    ordered = sorted([free_candidate, cheap], key=_order_key)
    assert ordered[0] is cheap


# ------------------------------------------------------------------ store
@pytest.mark.asyncio
async def test_window_counter_blocks_after_limit():
    store = MemoryStore()
    for _ in range(3):
        await store.incr_window("k", 60)
    assert await store.incr_window("k", 60) == 4


@pytest.mark.asyncio
async def test_concurrency_slots_are_capped_and_released():
    store = MemoryStore()
    assert await store.acquire_slot("p", 2, 60) is True
    assert await store.acquire_slot("p", 2, 60) is True
    assert await store.acquire_slot("p", 2, 60) is False
    await store.release_slot("p")
    assert await store.acquire_slot("p", 2, 60) is True


@pytest.mark.asyncio
async def test_idempotency_lock_is_exclusive():
    store = MemoryStore()
    assert await store.idem_begin("abc", 60) is True
    assert await store.idem_begin("abc", 60) is False
    await store.idem_store("abc", {"ok": True}, 60)
    assert await store.idem_get("abc") == {"ok": True}


# ----------------------------------------------------------------- limits
def test_tightest_picks_smallest_limit():
    assert _tightest(100, 10, None) == 10
    assert _tightest(None, None) is None


def test_tightest_float():
    assert _tightest_float(5.0, 2.5, None) == 2.5
    assert _tightest_float(None, None) is None


# ------------------------------------------------------------------ utils
def test_mask_key_shows_last_four():
    assert mask_key("sk-gw-abcdef1234") == "************1234"


def test_estimate_tokens():
    assert estimate_tokens("abcdefgh") >= 1
    assert estimate_tokens("") == 0
