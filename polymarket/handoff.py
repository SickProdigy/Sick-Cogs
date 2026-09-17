"""Typed public market data for a future, still-disabled wallet handoff."""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import hashlib
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


@dataclass(frozen=True, slots=True)
class FutureHandoffIntent:
    """A bounded proposal for later approval; it cannot produce or submit an order."""

    requester_id: int
    snapshot: MarketSnapshot
    outcome_index: int
    max_pusd: Decimal
    created_at: int
    expires_at: int

    def __post_init__(self):
        if self.requester_id <= 0:
            raise MarketSnapshotError("A positive immutable requester ID is required.")
        if not 0 <= self.outcome_index < len(self.snapshot.outcomes):
            raise MarketSnapshotError("Selected outcome is outside this market snapshot.")
        if self.max_pusd <= 0 or self.max_pusd.as_tuple().exponent < -6:
            raise MarketSnapshotError("Maximum pUSD must be positive with at most 6 decimal places.")
        if self.expires_at <= self.created_at:
            raise MarketSnapshotError("Intent expiry must be after creation.")

    @classmethod
    def create(cls, *, requester_id: int, snapshot: MarketSnapshot, outcome_index: int,
               max_pusd: str, created_at: int, expires_at: int) -> "FutureHandoffIntent":
        try:
            amount = Decimal(max_pusd)
        except (InvalidOperation, ValueError) as exc:
            raise MarketSnapshotError("Maximum pUSD must be a decimal amount.") from exc
        return cls(requester_id, snapshot, outcome_index, amount, created_at, expires_at)

    @property
    def selected_outcome(self) -> str:
        return self.snapshot.outcomes[self.outcome_index]

    @property
    def fingerprint(self) -> str:
        material = "|".join((str(self.requester_id), self.snapshot.market_id,
            self.snapshot.condition_id, self.snapshot.outcome_token_ids[self.outcome_index],
            format(self.max_pusd, "f"), str(self.created_at), str(self.expires_at)))
        return hashlib.sha256(material.encode()).hexdigest()
