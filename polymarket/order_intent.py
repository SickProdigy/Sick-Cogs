"""Immutable current market metadata, quote, and bounded buy approval models."""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_DOWN
import hashlib
from typing import Any, Mapping


class OrderIntentError(ValueError):
    """Raised when public quote or approval data fails closed."""


_ALLOWED_TICKS = {
    Decimal("0.1"), Decimal("0.01"), Decimal("0.005"),
    Decimal("0.0025"), Decimal("0.001"), Decimal("0.0001"),
}


def _decimal(value: Any, field: str, *, positive: bool = True) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise OrderIntentError(f"{field} must be decimal.") from exc
    if not result.is_finite() or (result <= 0 if positive else result < 0):
        raise OrderIntentError(f"{field} is outside its allowed range.")
    return result


def _levels(value: Any, field: str) -> tuple[tuple[Decimal, Decimal], ...]:
    if not isinstance(value, list):
        raise OrderIntentError(f"{field} must be a list.")
    levels = []
    for level in value:
        if not isinstance(level, dict):
            raise OrderIntentError(f"{field} contains an invalid level.")
        price = _decimal(level.get("price"), f"{field}.price")
        size = _decimal(level.get("size"), f"{field}.size")
        if price > 1:
            raise OrderIntentError(f"{field}.price exceeds one.")
        levels.append((price, size))
    return tuple(levels)


@dataclass(frozen=True, slots=True)
class OrderBookSnapshot:
    token_id: str
    bids: tuple[tuple[Decimal, Decimal], ...]
    asks: tuple[tuple[Decimal, Decimal], ...]
    minimum_order_size: Decimal
    tick_size: Decimal
    negative_risk: bool
    book_hash: str
    captured_at: int

    @classmethod
    def from_payload(cls, payload: dict, *, captured_at: int) -> "OrderBookSnapshot":
        if not isinstance(payload, dict) or captured_at <= 0:
            raise OrderIntentError("A timestamped order book object is required.")
        token_id = str(payload.get("asset_id") or "").strip()
        if not token_id.isdigit():
            raise OrderIntentError("Order book token ID is invalid.")
        bids = _levels(payload.get("bids"), "bids")
        asks = _levels(payload.get("asks"), "asks")
        if not asks:
            raise OrderIntentError("Order book has no sell liquidity.")
        minimum = _decimal(payload.get("min_order_size"), "min_order_size")
        tick = _decimal(payload.get("tick_size"), "tick_size")
        if tick not in _ALLOWED_TICKS:
            raise OrderIntentError("tick_size is not officially supported.")
        if not isinstance(payload.get("neg_risk"), bool):
            raise OrderIntentError("neg_risk must be explicit.")
        book_hash = str(payload.get("hash") or "").strip()
        if not book_hash or len(book_hash) > 256:
            raise OrderIntentError("Order book hash is invalid.")
        return cls(
            token_id, bids, asks, minimum, tick, payload["neg_risk"],
            book_hash, captured_at,
        )

    @property
    def best_ask(self) -> Decimal:
        return min(price for price, _size in self.asks)

    @property
    def best_bid(self) -> Decimal | None:
        return max((price for price, _size in self.bids), default=None)


@dataclass(frozen=True, slots=True)
class MarketOrderMetadata:
    token_ids: tuple[str, ...]
    tick_size: Decimal
    negative_risk: bool
    fee_rate: Decimal
    fee_exponent: Decimal

    @classmethod
    def from_payload(
        cls, payload: Any, *, expected_token_id: str,
    ) -> "MarketOrderMetadata":
        if not isinstance(payload, Mapping):
            raise OrderIntentError("CLOB market metadata must be an object.")
        tick = _decimal(payload.get("mts"), "market tick size")
        if tick not in _ALLOWED_TICKS:
            raise OrderIntentError("Market tick size is not officially supported.")
        if not isinstance(payload.get("nr", False), bool):
            raise OrderIntentError("Market negative-risk flag is invalid.")
        raw_tokens = payload.get("t")
        if not isinstance(raw_tokens, list) or not raw_tokens:
            raise OrderIntentError("Market token list is invalid.")
        tokens = []
        for row in raw_tokens:
            if not isinstance(row, Mapping):
                raise OrderIntentError("Market token row is invalid.")
            token = str(row.get("t") or "")
            if not token.isdigit() or token in tokens:
                raise OrderIntentError("Market token identity is invalid.")
            tokens.append(token)
        if expected_token_id not in tokens:
            raise OrderIntentError("Quoted token is not in current market metadata.")
        raw_fee = payload.get("fd")
        if raw_fee is None:
            rate, exponent = Decimal(0), Decimal(0)
        elif isinstance(raw_fee, Mapping):
            rate = _decimal(raw_fee.get("r"), "platform fee rate", positive=False)
            exponent = _decimal(raw_fee.get("e"), "platform fee exponent", positive=False)
        else:
            raise OrderIntentError("Platform fee metadata is invalid.")
        if rate > 1 or exponent > 10 or exponent != exponent.to_integral_value():
            raise OrderIntentError("Platform fee metadata exceeds reviewed bounds.")
        return cls(tuple(tokens), tick, payload.get("nr", False), rate, exponent)

    def require_matches(self, quote: OrderBookSnapshot) -> None:
        if (
            quote.token_id not in self.token_ids
            or quote.tick_size != self.tick_size
            or quote.negative_risk != self.negative_risk
        ):
            raise OrderIntentError("Order book and market metadata disagree.")


@dataclass(frozen=True, slots=True)
class MarketBuyApproval:
    requester_id: int
    market_id: str
    condition_id: str
    outcome: str
    quote: OrderBookSnapshot
    metadata: MarketOrderMetadata
    max_price: Decimal
    max_spend_pusd: Decimal
    created_at: int
    expires_at: int

    def __post_init__(self):
        if self.requester_id <= 0 or not self.market_id or not self.condition_id or not self.outcome:
            raise OrderIntentError("Approval identity is incomplete.")
        self.metadata.require_matches(self.quote)
        if self.max_price <= 0 or self.max_price >= 1 or self.max_price % self.quote.tick_size:
            raise OrderIntentError("Maximum price does not conform to the live tick size.")
        if self.max_price < self.quote.best_ask:
            raise OrderIntentError("Maximum price is below the current best ask.")
        if self.max_spend_pusd <= 0 or self.max_spend_pusd.as_tuple().exponent < -6:
            raise OrderIntentError("Maximum spend must be positive pUSD with at most six decimals.")
        if self.created_at != self.quote.captured_at or self.expires_at <= self.created_at:
            raise OrderIntentError("Approval timestamps are not bound to the quote.")
        if self.maximum_notional < self.quote.minimum_order_size * self.max_price:
            raise OrderIntentError("Maximum spend cannot satisfy the market minimum.")

    @classmethod
    def create(
        cls, *, requester_id: int, market_id: str, condition_id: str,
        outcome: str, quote: OrderBookSnapshot, metadata: MarketOrderMetadata,
        max_price: str, max_spend_pusd: str, expires_at: int,
    ) -> "MarketBuyApproval":
        return cls(
            requester_id, market_id, condition_id, outcome, quote, metadata,
            _decimal(max_price, "max_price"),
            _decimal(max_spend_pusd, "max_spend_pusd"),
            quote.captured_at, expires_at,
        )

    @property
    def effective_fee_rate(self) -> Decimal:
        if self.metadata.fee_rate == 0:
            return Decimal(0)
        return self.metadata.fee_rate * (
            (self.max_price * (Decimal(1) - self.max_price))
            ** self.metadata.fee_exponent
        )

    @property
    def maximum_notional(self) -> Decimal:
        multiplier = Decimal(1) + self.effective_fee_rate / self.max_price
        return (self.max_spend_pusd / multiplier).quantize(
            Decimal("0.000001"), rounding=ROUND_DOWN
        )

    @property
    def maximum_fee_pusd(self) -> Decimal:
        return self.max_spend_pusd - self.maximum_notional

    @property
    def fingerprint(self) -> str:
        fields = (
            str(self.requester_id), self.market_id, self.condition_id, self.outcome,
            self.quote.token_id, self.quote.book_hash, str(self.quote.negative_risk),
            format(self.quote.minimum_order_size, "f"),
            format(self.quote.tick_size, "f"), format(self.quote.best_ask, "f"),
            format(self.metadata.fee_rate, "f"),
            format(self.metadata.fee_exponent, "f"),
            format(self.max_price, "f"), format(self.max_spend_pusd, "f"),
            str(self.created_at), str(self.expires_at),
        )
        return hashlib.sha256("|".join(fields).encode()).hexdigest()

    def require_fresh(
        self, fresh: OrderBookSnapshot, metadata: MarketOrderMetadata, *, now: int,
    ) -> None:
        if now < self.created_at or now >= self.expires_at:
            raise OrderIntentError("Approval is expired or not current.")
        if fresh.captured_at < self.created_at or fresh.captured_at > now:
            raise OrderIntentError("Final quote timestamp is invalid.")
        metadata.require_matches(fresh)
        if (
            fresh.token_id != self.quote.token_id
            or metadata != self.metadata
            or fresh.minimum_order_size != self.quote.minimum_order_size
        ):
            raise OrderIntentError("Market constraints changed; reapproval is required.")
        if fresh.best_ask > self.max_price:
            raise OrderIntentError("Price exceeded the approved maximum.")
