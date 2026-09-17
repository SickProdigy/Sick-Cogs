"""Typed public market data for a future, still-disabled wallet handoff."""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import hashlib
import json
from typing import Any

POLYMARKET_CHAIN_ID = 137
POLYMARKET_COLLATERAL_SYMBOL = "pUSD"


class MarketSnapshotError(ValueError):
    """Raised when public market data cannot safely form a future handoff snapshot."""


def _decimal(value: Any, field: str) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise MarketSnapshotError(f"{field} must be a decimal value.") from exc
    if not parsed.is_finite() or parsed < 0:
        raise MarketSnapshotError(f"{field} must be a finite non-negative value.")
    return parsed


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
    chain_id: int
    collateral_symbol: str
    slug: str
    question: str
    outcomes: tuple[str, ...]
    outcome_token_ids: tuple[str, ...]
    outcome_prices: tuple[str, ...]
    end_date: str | None
    fee_rate: Decimal | None
    tick_size: Decimal | None
    minimum_order_size: Decimal | None
    quote_timestamp: int

    @classmethod
    def from_market(cls, market: dict[str, Any], *, quote_timestamp: int) -> "MarketSnapshot":
        if not isinstance(market, dict):
            raise MarketSnapshotError("Market data must be an object.")
        if not isinstance(quote_timestamp, int) or quote_timestamp <= 0:
            raise MarketSnapshotError("A positive immutable quote timestamp is required.")
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
            market_id=fields["id"], condition_id=fields["conditionId"],
            chain_id=POLYMARKET_CHAIN_ID, collateral_symbol=POLYMARKET_COLLATERAL_SYMBOL,
            slug=fields["slug"],
            question=fields["question"], outcomes=outcomes, outcome_token_ids=token_ids,
            outcome_prices=prices, end_date=str(market.get("endDateIso") or market.get("endDate") or "") or None,
            fee_rate=_decimal(market.get("fee"), "fee"),
            tick_size=_decimal(market.get("orderPriceMinTickSize"), "orderPriceMinTickSize"),
            minimum_order_size=_decimal(market.get("orderMinSize"), "orderMinSize"),
            quote_timestamp=quote_timestamp,
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
        if self.snapshot.chain_id != POLYMARKET_CHAIN_ID or self.snapshot.collateral_symbol != POLYMARKET_COLLATERAL_SYMBOL:
            raise MarketSnapshotError("Intent snapshot is not the reviewed Polygon pUSD target.")

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
            self.snapshot.condition_id, str(self.snapshot.chain_id), self.snapshot.collateral_symbol,
            self.snapshot.outcome_token_ids[self.outcome_index], str(self.snapshot.quote_timestamp),
            format(self.max_pusd, "f"), str(self.created_at), str(self.expires_at)))
        return hashlib.sha256(material.encode()).hexdigest()
