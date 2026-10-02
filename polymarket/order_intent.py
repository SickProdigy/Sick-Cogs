"""Immutable public quote and bounded approval models; no signing or submission."""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_DOWN
import hashlib


class OrderIntentError(ValueError):
    """Raised when public quote or approval data fails closed."""


def _decimal(value, field: str, *, positive: bool = True) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise OrderIntentError(f"{field} must be decimal.") from exc
    if not result.is_finite() or (result <= 0 if positive else result < 0):
        raise OrderIntentError(f"{field} is outside its allowed range.")
    return result


def _levels(value, field: str) -> tuple[tuple[Decimal, Decimal], ...]:
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
        bids, asks = _levels(payload.get("bids"), "bids"), _levels(payload.get("asks"), "asks")
        if not asks:
            raise OrderIntentError("Order book has no sell liquidity.")
        minimum = _decimal(payload.get("min_order_size"), "min_order_size")
        tick = _decimal(payload.get("tick_size"), "tick_size")
        if tick >= 1 or (Decimal("1") % tick):
            raise OrderIntentError("tick_size must divide one.")
        if not isinstance(payload.get("neg_risk"), bool):
            raise OrderIntentError("neg_risk must be explicit.")
        book_hash = str(payload.get("hash") or "").strip()
        if not book_hash or len(book_hash) > 256:
            raise OrderIntentError("Order book hash is invalid.")
        return cls(token_id, bids, asks, minimum, tick, payload["neg_risk"], book_hash, captured_at)

    @property
    def best_ask(self) -> Decimal:
        return min(price for price, _size in self.asks)

    @property
    def best_bid(self) -> Decimal | None:
        return max((price for price, _size in self.bids), default=None)


@dataclass(frozen=True, slots=True)
class MarketBuyApproval:
    requester_id: int
    market_id: str
    condition_id: str
    outcome: str
    quote: OrderBookSnapshot
    max_price: Decimal
    max_spend_pusd: Decimal
    maximum_base_fee_bps: int
    created_at: int
    expires_at: int

    def __post_init__(self):
        if self.requester_id <= 0 or not self.market_id or not self.condition_id or not self.outcome:
            raise OrderIntentError("Approval identity is incomplete.")
        if self.max_price <= 0 or self.max_price > 1 or self.max_price % self.quote.tick_size:
            raise OrderIntentError("Maximum price does not conform to the live tick size.")
        if self.max_price < self.quote.best_ask:
            raise OrderIntentError("Maximum price is below the current best ask.")
        if self.max_spend_pusd <= 0 or self.max_spend_pusd.as_tuple().exponent < -6:
            raise OrderIntentError("Maximum spend must be positive pUSD with at most six decimals.")
        if not 0 <= self.maximum_base_fee_bps <= 10_000:
            raise OrderIntentError("Maximum fee is invalid.")
        if self.created_at != self.quote.captured_at or self.expires_at <= self.created_at:
            raise OrderIntentError("Approval timestamps are not bound to the quote.")
        if self.maximum_notional < self.quote.minimum_order_size * self.max_price:
            raise OrderIntentError("Maximum spend cannot satisfy the market minimum.")

    @classmethod
    def create(cls, *, requester_id: int, market_id: str, condition_id: str,
               outcome: str, quote: OrderBookSnapshot, max_price: str,
               max_spend_pusd: str, maximum_base_fee_bps: int, expires_at: int):
        return cls(
            requester_id, market_id, condition_id, outcome, quote,
            _decimal(max_price, "max_price"), _decimal(max_spend_pusd, "max_spend_pusd"),
            int(maximum_base_fee_bps), quote.captured_at, expires_at,
        )

    @property
    def maximum_notional(self) -> Decimal:
        rate = Decimal(self.maximum_base_fee_bps) / Decimal(10_000)
        multiplier = Decimal(1) + rate * (Decimal(1) - self.quote.best_ask)
        return (self.max_spend_pusd / multiplier).quantize(Decimal("0.000001"), rounding=ROUND_DOWN)

    @property
    def maximum_fee_pusd(self) -> Decimal:
        return self.max_spend_pusd - self.maximum_notional

    @property
    def fingerprint(self) -> str:
        fields = (
            str(self.requester_id), self.market_id, self.condition_id, self.outcome,
            self.quote.token_id, self.quote.book_hash, str(self.quote.negative_risk),
            format(self.quote.minimum_order_size, "f"), format(self.quote.tick_size, "f"),
            format(self.quote.best_ask, "f"), format(self.max_price, "f"),
            format(self.max_spend_pusd, "f"), str(self.maximum_base_fee_bps),
            str(self.created_at), str(self.expires_at),
        )
        return hashlib.sha256("|".join(fields).encode()).hexdigest()

    def require_fresh(self, fresh: OrderBookSnapshot, *, base_fee_bps: int, now: int) -> None:
        if now < self.created_at or now >= self.expires_at:
            raise OrderIntentError("Approval is expired or not current.")
        if fresh.captured_at < self.created_at or fresh.captured_at > now:
            raise OrderIntentError("Final quote timestamp is invalid.")
        if (
            fresh.token_id != self.quote.token_id
            or fresh.tick_size != self.quote.tick_size
            or fresh.minimum_order_size != self.quote.minimum_order_size
            or fresh.negative_risk != self.quote.negative_risk
        ):
            raise OrderIntentError("Market constraints changed; reapproval is required.")
        if fresh.best_ask > self.max_price or base_fee_bps > self.maximum_base_fee_bps:
            raise OrderIntentError("Price or fee exceeded the approved maximum.")
