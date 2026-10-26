"""Token cost and partner price (brief §5.3)."""

from __future__ import annotations

from app.models import Model, Partner


def compute_cost(model: Model | None, prompt_tokens: int, completion_tokens: int) -> float:
    """What we pay the provider."""
    if model is None:
        return 0.0
    return round(
        (prompt_tokens / 1_000_000) * (model.input_price_per_m or 0.0)
        + (completion_tokens / 1_000_000) * (model.output_price_per_m or 0.0),
        8,
    )


def compute_price(cost_usd: float, partner: Partner | None, markup: float | None = None) -> float:
    """What the partner is charged: cost times the partner's markup multiplier."""
    multiplier = markup if markup is not None else (partner.markup_multiplier if partner else 1.0)
    return round(cost_usd * (multiplier or 1.0), 8)


def is_free(model: Model | None) -> bool:
    return model is None or (
        (model.input_price_per_m or 0) == 0 and (model.output_price_per_m or 0) == 0
    )
