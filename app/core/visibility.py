"""What a non-owner partner is allowed to see.

The owner sees provider cost, the price charged to the partner, and the margin
between them. A partner sees only the amount they are charged: exposing our
cost would hand them our margin on every request.

Every partner-facing response goes through `money_view` so this rule lives in
one place instead of being re-implemented per endpoint.
"""

from __future__ import annotations

from typing import Any

from app.models import Partner, User


def is_owner(user: User | None) -> bool:
    return bool(user and user.role == "owner")


def money_view(user: User | None, cost: float, price: float) -> dict[str, Any]:
    """Shape a cost/price pair for the caller's role."""
    if is_owner(user):
        return {
            "cost_usd": round(float(cost), 6),
            "price_usd": round(float(price), 6),
            "margin_usd": round(float(price) - float(cost), 6),
        }
    # Partners are charged `price`; our cost and margin are internal.
    return {
        "cost_usd": round(float(price), 6),
        "price_usd": None,
        "margin_usd": None,
    }


def spend_cap(user: User | None, partner: Partner | None, global_cap: float) -> float:
    """The cap that applies to this caller's own spend."""
    if is_owner(user):
        return global_cap
    return float(partner.monthly_cap_usd or 0) if partner else 0.0


def hide_internal_metrics(user: User | None, payload: dict[str, Any]) -> dict[str, Any]:
    """Drop fields that only make sense for the owner."""
    if is_owner(user):
        return payload
    return {key: value for key, value in payload.items() if key != "margin_usd"}
