"""Immutable current market metadata, quote, and bounded trade approval models."""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_DOWN, ROUND_HALF_EVEN
import hashlib
from typing import Any, Mapping


class OrderIntentError(ValueError):
    """Raised when public quote or approval data fails closed."""


_ROUNDING_BY_TICK = {
    Decimal("0.1"): (3, 1, 2),
    Decimal("0.01"): (4, 2, 2),
    Decimal("0.005"): (5, 3, 2),
    Decimal("0.0025"): (6, 4, 2),
    Decimal("0.001"): (5, 3, 2),
    Decimal("0.0001"): (6, 4, 2),
}
_ALLOWED_TICKS = frozenset(_ROUNDING_BY_TICK)
_MINIMUM_TICK = min(_ALLOWED_TICKS)


def _decimal(value: Any, field: str, *, positive: bool = True) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise OrderIntentError(f"{field} must be decimal.") from exc
    if not result.is_finite() or (result <= 0 if positive else result < 0):
        raise OrderIntentError(f"{field} is outside its allowed range.")
    return result


def _decimal_places(value: Decimal) -> int:
    exponent = value.normalize().as_tuple().exponent
    return 0 if isinstance(exponent, str) else max(0, -exponent)


def _round(value: Decimal, decimals: int, rounding: str) -> Decimal:
    if _decimal_places(value) <= decimals:
        return value
    return value.quantize(Decimal(10) ** -decimals, rounding=rounding)


def _atomic(value: Decimal) -> int:
    return int((value * Decimal(10**6)).quantize(Decimal(1), rounding=ROUND_HALF_EVEN))


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
        if not bids and not asks:
            raise OrderIntentError("Order book has no liquidity.")
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

    def to_record(self) -> dict[str, Any]:
        return {
            "token_id": self.token_id,
            "bids": [[format(price, "f"), format(size, "f")] for price, size in self.bids],
            "asks": [[format(price, "f"), format(size, "f")] for price, size in self.asks],
            "minimum_order_size": format(self.minimum_order_size, "f"),
            "tick_size": format(self.tick_size, "f"),
            "negative_risk": self.negative_risk,
            "book_hash": self.book_hash,
            "captured_at": self.captured_at,
        }

    @classmethod
    def from_record(cls, record: Any) -> "OrderBookSnapshot":
        expected = {
            "token_id", "bids", "asks", "minimum_order_size", "tick_size",
            "negative_risk", "book_hash", "captured_at",
        }
        if not isinstance(record, Mapping) or set(record) != expected:
            raise OrderIntentError("Stored order book has an invalid shape.")
        def stored_levels(name: str) -> list[dict[str, str]]:
            raw = record[name]
            if not isinstance(raw, list):
                raise OrderIntentError("Stored order book levels are invalid.")
            return [
                {"price": row[0], "size": row[1]}
                for row in raw
                if isinstance(row, list) and len(row) == 2
            ]
        bids, asks = stored_levels("bids"), stored_levels("asks")
        if len(bids) != len(record["bids"]) or len(asks) != len(record["asks"]):
            raise OrderIntentError("Stored order book levels are invalid.")
        return cls.from_payload({
            "asset_id": record["token_id"], "bids": bids, "asks": asks,
            "min_order_size": record["minimum_order_size"],
            "tick_size": record["tick_size"],
            "neg_risk": record["negative_risk"], "hash": record["book_hash"],
        }, captured_at=int(record["captured_at"]))

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

    def to_record(self) -> dict[str, Any]:
        return {
            "token_ids": list(self.token_ids),
            "tick_size": format(self.tick_size, "f"),
            "negative_risk": self.negative_risk,
            "fee_rate": format(self.fee_rate, "f"),
            "fee_exponent": format(self.fee_exponent, "f"),
        }

    @classmethod
    def from_record(
        cls, record: Any, *, expected_token_id: str,
    ) -> "MarketOrderMetadata":
        expected = {
            "token_ids", "tick_size", "negative_risk", "fee_rate",
            "fee_exponent",
        }
        if not isinstance(record, Mapping) or set(record) != expected:
            raise OrderIntentError("Stored market metadata has an invalid shape.")
        tokens = record["token_ids"]
        if not isinstance(tokens, list):
            raise OrderIntentError("Stored market token list is invalid.")
        return cls.from_payload({
            "mts": record["tick_size"], "nr": record["negative_risk"],
            "t": [{"t": token} for token in tokens],
            "fd": {"r": record["fee_rate"], "e": record["fee_exponent"]},
        }, expected_token_id=expected_token_id)

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
        if not self.quote.asks:
            raise OrderIntentError("Order book has no sell liquidity.")
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
    def order_amounts(self) -> tuple[int, int]:
        amount_decimals, _price_decimals, size_decimals = _ROUNDING_BY_TICK[
            self.quote.tick_size
        ]
        maker = _round(self.maximum_notional, size_decimals, ROUND_DOWN)
        shares = maker / self.max_price
        if _decimal_places(shares) > amount_decimals:
            shares = _round(shares, amount_decimals + 4, ROUND_CEILING)
            if _decimal_places(shares) > amount_decimals:
                shares = _round(shares, amount_decimals, ROUND_DOWN)
        maker_amount, taker_amount = _atomic(maker), _atomic(shares)
        if maker_amount <= 0 or taker_amount <= 0:
            raise OrderIntentError("Protected buy rounds to zero.")
        encoded_price = Decimal(maker_amount) / Decimal(taker_amount)
        if (
            shares < self.quote.minimum_order_size
            or encoded_price < self.max_price
            or encoded_price >= self.max_price + _MINIMUM_TICK
        ):
            raise OrderIntentError(
                "Protected buy rounding cannot preserve the approved bounds."
            )
        return maker_amount, taker_amount

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

    def to_record(self) -> dict[str, Any]:
        return {
            "requester_id": self.requester_id,
            "market_id": self.market_id,
            "condition_id": self.condition_id,
            "outcome": self.outcome,
            "quote": self.quote.to_record(),
            "metadata": self.metadata.to_record(),
            "max_price": format(self.max_price, "f"),
            "max_spend_pusd": format(self.max_spend_pusd, "f"),
            "created_at": self.created_at,
            "expires_at": self.expires_at,
        }

    @classmethod
    def from_record(cls, record: Any) -> "MarketBuyApproval":
        expected = {
            "requester_id", "market_id", "condition_id", "outcome", "quote",
            "metadata", "max_price", "max_spend_pusd", "created_at",
            "expires_at",
        }
        if not isinstance(record, Mapping) or set(record) != expected:
            raise OrderIntentError("Stored market approval has an invalid shape.")
        try:
            quote = OrderBookSnapshot.from_record(record["quote"])
            metadata = MarketOrderMetadata.from_record(
                record["metadata"], expected_token_id=quote.token_id
            )
            approval = cls.create(
                requester_id=int(record["requester_id"]),
                market_id=str(record["market_id"]),
                condition_id=str(record["condition_id"]),
                outcome=str(record["outcome"]), quote=quote, metadata=metadata,
                max_price=str(record["max_price"]),
                max_spend_pusd=str(record["max_spend_pusd"]),
                expires_at=int(record["expires_at"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise OrderIntentError("Stored market approval is invalid.") from exc
        if approval.created_at != int(record["created_at"]):
            raise OrderIntentError("Stored market approval timestamp changed.")
        return approval

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


@dataclass(frozen=True, slots=True)
class MarketSellApproval:
    """Immutable position-backed FAK sell with a minimum execution price."""

    requester_id: int
    market_id: str
    condition_id: str
    outcome: str
    quote: OrderBookSnapshot
    metadata: MarketOrderMetadata
    shares: Decimal
    available_shares: Decimal
    min_price: Decimal
    created_at: int
    expires_at: int

    def __post_init__(self) -> None:
        if (
            self.requester_id <= 0 or not self.market_id
            or not self.condition_id or not self.outcome
        ):
            raise OrderIntentError("Sell approval identity is incomplete.")
        self.metadata.require_matches(self.quote)
        if not self.quote.bids:
            raise OrderIntentError("Order book has no buy liquidity.")
        if (
            self.min_price <= 0 or self.min_price >= 1
            or self.min_price % self.quote.tick_size
        ):
            raise OrderIntentError(
                "Minimum price does not conform to the live tick size."
            )
        if self.quote.best_bid is None or self.quote.best_bid < self.min_price:
            raise OrderIntentError("Minimum price exceeds the current best bid.")
        if (
            self.shares <= 0 or self.available_shares <= 0
            or self.shares > self.available_shares
            or _decimal_places(self.shares) > 6
            or _decimal_places(self.available_shares) > 6
        ):
            raise OrderIntentError(
                "Sell shares exceed the exact available position balance."
            )
        if self.shares < self.quote.minimum_order_size:
            raise OrderIntentError("Sell shares cannot satisfy the market minimum.")
        if self.created_at != self.quote.captured_at or self.expires_at <= self.created_at:
            raise OrderIntentError("Sell approval timestamps are not bound to the quote.")

    @classmethod
    def create(
        cls, *, requester_id: int, market_id: str, condition_id: str,
        outcome: str, quote: OrderBookSnapshot, metadata: MarketOrderMetadata,
        shares: str, available_shares: str, min_price: str, expires_at: int,
    ) -> "MarketSellApproval":
        return cls(
            requester_id, market_id, condition_id, outcome, quote, metadata,
            _decimal(shares, "shares"),
            _decimal(available_shares, "available_shares"),
            _decimal(min_price, "min_price"),
            quote.captured_at, expires_at,
        )

    @property
    def effective_fee_rate(self) -> Decimal:
        if self.metadata.fee_rate == 0:
            return Decimal(0)
        return self.metadata.fee_rate * (
            (self.min_price * (Decimal(1) - self.min_price))
            ** self.metadata.fee_exponent
        )

    @property
    def minimum_gross_pusd(self) -> Decimal:
        return (self.shares * self.min_price).quantize(
            Decimal("0.000001"), rounding=ROUND_CEILING
        )

    @property
    def maximum_fee_pusd(self) -> Decimal:
        return (self.shares * self.effective_fee_rate).quantize(
            Decimal("0.000001"), rounding=ROUND_CEILING
        )

    @property
    def minimum_net_pusd(self) -> Decimal:
        return max(
            Decimal(0), self.minimum_gross_pusd - self.maximum_fee_pusd
        )

    @property
    def order_amounts(self) -> tuple[int, int]:
        maker_amount = _atomic(self.shares)
        taker_amount = int(
            (self.shares * self.min_price * Decimal(10**6)).to_integral_value(
                rounding=ROUND_CEILING
            )
        )
        if maker_amount <= 0 or taker_amount <= 0:
            raise OrderIntentError("Protected sell rounds to zero.")
        encoded_price = Decimal(taker_amount) / Decimal(maker_amount)
        if encoded_price < self.min_price:
            raise OrderIntentError(
                "Protected sell rounding cannot preserve the approved bounds."
            )
        return maker_amount, taker_amount

    @property
    def fingerprint(self) -> str:
        fields = (
            "SELL", str(self.requester_id), self.market_id, self.condition_id,
            self.outcome, self.quote.token_id, self.quote.book_hash,
            str(self.quote.negative_risk),
            format(self.quote.minimum_order_size, "f"),
            format(self.quote.tick_size, "f"), format(self.quote.best_bid, "f"),
            format(self.metadata.fee_rate, "f"),
            format(self.metadata.fee_exponent, "f"),
            format(self.shares, "f"), format(self.available_shares, "f"),
            format(self.min_price, "f"), str(self.created_at),
            str(self.expires_at),
        )
        return hashlib.sha256("|".join(fields).encode()).hexdigest()

    def to_record(self) -> dict[str, Any]:
        return {
            "requester_id": self.requester_id,
            "market_id": self.market_id,
            "condition_id": self.condition_id,
            "outcome": self.outcome,
            "quote": self.quote.to_record(),
            "metadata": self.metadata.to_record(),
            "shares": format(self.shares, "f"),
            "available_shares": format(self.available_shares, "f"),
            "min_price": format(self.min_price, "f"),
            "created_at": self.created_at,
            "expires_at": self.expires_at,
        }

    @classmethod
    def from_record(cls, record: Any) -> "MarketSellApproval":
        expected = {
            "requester_id", "market_id", "condition_id", "outcome", "quote",
            "metadata", "shares", "available_shares", "min_price",
            "created_at", "expires_at",
        }
        if not isinstance(record, Mapping) or set(record) != expected:
            raise OrderIntentError("Stored sell approval has an invalid shape.")
        try:
            quote = OrderBookSnapshot.from_record(record["quote"])
            metadata = MarketOrderMetadata.from_record(
                record["metadata"], expected_token_id=quote.token_id
            )
            approval = cls.create(
                requester_id=int(record["requester_id"]),
                market_id=str(record["market_id"]),
                condition_id=str(record["condition_id"]),
                outcome=str(record["outcome"]), quote=quote, metadata=metadata,
                shares=str(record["shares"]),
                available_shares=str(record["available_shares"]),
                min_price=str(record["min_price"]),
                expires_at=int(record["expires_at"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise OrderIntentError("Stored sell approval is invalid.") from exc
        if approval.created_at != int(record["created_at"]):
            raise OrderIntentError("Stored sell approval timestamp changed.")
        return approval

    def require_fresh(
        self, fresh: OrderBookSnapshot, metadata: MarketOrderMetadata, *,
        available_shares: Decimal, now: int,
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
            raise OrderIntentError(
                "Market constraints changed; reapproval is required."
            )
        if fresh.best_bid is None or fresh.best_bid < self.min_price:
            raise OrderIntentError("Price fell below the approved minimum.")
        if available_shares != self.available_shares or self.shares > available_shares:
            raise OrderIntentError(
                "Position balance changed; reapproval is required."
            )
