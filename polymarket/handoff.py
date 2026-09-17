"""Typed public market data for a future, still-disabled wallet handoff."""

from dataclasses import dataclass
import json
from typing import Any


class MarketSnapshotError(ValueError):
    """Raised when public market data cannot safely form a future handoff snapshot."""


def _list(value: Any, field: str) -> tuple[str, ...]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise MarketSnapshotError(f"{field} is not a JSON list.") from exc
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise MarketSnapshotError(f"{field} must be a non-empty string list.")
    return tuple(value)


@dataclass(frozen=True, slots=True)
class MarketSnapshot:
    """Immutable public CLOB market facts; never a signed order or wallet instruction."""

    market_id: str
    condition_id: str
    slug: str
    question: str
    outcomes: tuple[str, ...]
    outcome_token_ids: tuple[str, ...]
    outcome_prices: tuple[str, ...]
    end_date: str | None
    fee_rate: float | None
    minimum_order_size: float | None

    @classmethod
    def from_market(cls, market: dict[str, Any]) -> "MarketSnapshot":
        if not isinstance(market, dict):
            raise MarketSnapshotError("Market data must be an object.")
        if not market.get("active") or market.get("closed"):
            raise MarketSnapshotError("Market is not active.")
        if not market.get("enableOrderBook") or not market.get("acceptingOrders"):
            raise MarketSnapshotError("Market is not accepting CLOB orders.")
        fields = {name: str(market.get(name) or "").strip() for name in ("id", "conditionId", "slug", "question")}
        if not all(fields.values()):
            raise MarketSnapshotError("Market is missing required identity fields.")
        outcomes = _list(market.get("outcomes"), "outcomes")
        token_ids = _list(market.get("clobTokenIds"), "clobTokenIds")
        prices = _list(market.get("outcomePrices"), "outcomePrices")
        if len(outcomes) != len(token_ids) or len(outcomes) != len(prices):
            raise MarketSnapshotError("Outcome, token, and price counts must match.")
        return cls(
            market_id=fields["id"], condition_id=fields["conditionId"], slug=fields["slug"],
            question=fields["question"], outcomes=outcomes, outcome_token_ids=token_ids,
            outcome_prices=prices, end_date=str(market.get("endDateIso") or market.get("endDate") or "") or None,
            fee_rate=float(market["fee"]) if market.get("fee") is not None else None,
            minimum_order_size=float(market["orderMinSize"]) if market.get("orderMinSize") is not None else None,
        )
