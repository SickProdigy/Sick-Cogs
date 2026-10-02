"""Restart-safe lifecycle models for narrowly scoped Polymarket orders."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Mapping

from .account_connection import normalize_evm_address


class OrderLifecycleError(ValueError):
    """Raised when an order lifecycle transition or provider result is invalid."""


class OrderState(str, Enum):
    APPROVED = "approved"
    SUBMITTING = "submitting"
    UNKNOWN = "unknown"
    LIVE = "live"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCEL_PENDING = "cancel_pending"
    CANCELED = "canceled"
    EXPIRED = "expired"
    REJECTED = "rejected"


def _utc_timestamp(value: datetime, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise OrderLifecycleError(f"{field} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _decimal(value: Any, field: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise OrderLifecycleError(f"{field} must be a decimal") from exc
    if not parsed.is_finite():
        raise OrderLifecycleError(f"{field} must be finite")
    return parsed


def _short_text(value: Any, field: str, *, limit: int = 500) -> str:
    if not isinstance(value, str) or not value.strip():
        raise OrderLifecycleError(f"{field} must be a non-empty string")
    cleaned = value.strip()
    if len(cleaned) > limit:
        raise OrderLifecycleError(f"{field} is too long")
    return cleaned


def _identifier(value: Any, field: str) -> str:
    cleaned = _short_text(value, field, limit=256)
    if any(character.isspace() for character in cleaned):
        raise OrderLifecycleError(f"{field} cannot contain whitespace")
    return cleaned


def _hex_fingerprint(value: str) -> str:
    cleaned = _short_text(value, "approval_fingerprint", limit=64).lower()
    if len(cleaned) != 64 or any(character not in "0123456789abcdef" for character in cleaned):
        raise OrderLifecycleError("approval_fingerprint must be 64 lowercase hex characters")
    return cleaned


def _string_tuple(value: Any, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise OrderLifecycleError(f"{field} must be a list")
    return tuple(_identifier(item, field) for item in value)


@dataclass(frozen=True)
class OrderBinding:
    """Immutable user approval and session-signer scope for one market buy."""

    discord_user_id: int
    approval_fingerprint: str
    condition_id: str
    token_id: str
    maker_address: str
    session_signer_address: str
    side: str
    maximum_price: Decimal
    maximum_size: Decimal
    created_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.discord_user_id, int) or self.discord_user_id <= 0:
            raise OrderLifecycleError("discord_user_id must be a positive integer")
        object.__setattr__(self, "approval_fingerprint", _hex_fingerprint(self.approval_fingerprint))
        object.__setattr__(self, "condition_id", _identifier(self.condition_id, "condition_id"))
        token_id = _identifier(self.token_id, "token_id")
        if not token_id.isdigit():
            raise OrderLifecycleError("token_id must contain only digits")
        object.__setattr__(self, "token_id", token_id)
        object.__setattr__(self, "maker_address", normalize_evm_address(self.maker_address, "maker_address"))
        object.__setattr__(
            self,
            "session_signer_address",
            normalize_evm_address(self.session_signer_address, "session_signer_address"),
        )
        side = _short_text(self.side, "side", limit=8).upper()
        if side != "BUY":
            raise OrderLifecycleError("only BUY order bindings are supported")
        object.__setattr__(self, "side", side)
        price = _decimal(self.maximum_price, "maximum_price")
        size = _decimal(self.maximum_size, "maximum_size")
        if price <= 0 or price > 1:
            raise OrderLifecycleError("maximum_price must be greater than zero and at most one")
        if size <= 0:
            raise OrderLifecycleError("maximum_size must be greater than zero")
        object.__setattr__(self, "maximum_price", price)
        object.__setattr__(self, "maximum_size", size)
        created_at = _utc_timestamp(self.created_at, "created_at")
        expires_at = _utc_timestamp(self.expires_at, "expires_at")
        if expires_at <= created_at:
            raise OrderLifecycleError("expires_at must be after created_at")
        object.__setattr__(self, "created_at", created_at)
        object.__setattr__(self, "expires_at", expires_at)

    @property
    def idempotency_key(self) -> str:
        payload = {
            "approval_fingerprint": self.approval_fingerprint,
            "condition_id": self.condition_id,
            "created_at": self.created_at.isoformat(),
            "discord_user_id": self.discord_user_id,
            "expires_at": self.expires_at.isoformat(),
            "maker_address": self.maker_address,
            "maximum_price": str(self.maximum_price),
            "maximum_size": str(self.maximum_size),
            "session_signer_address": self.session_signer_address,
            "side": self.side,
            "token_id": self.token_id,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def to_record(self) -> dict[str, Any]:
        return {
            "discord_user_id": self.discord_user_id,
            "approval_fingerprint": self.approval_fingerprint,
            "condition_id": self.condition_id,
            "token_id": self.token_id,
            "maker_address": self.maker_address,
            "session_signer_address": self.session_signer_address,
            "side": self.side,
            "maximum_price": str(self.maximum_price),
            "maximum_size": str(self.maximum_size),
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
        }

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> "OrderBinding":
        try:
            return cls(
                discord_user_id=record["discord_user_id"],
                approval_fingerprint=record["approval_fingerprint"],
                condition_id=record["condition_id"],
                token_id=record["token_id"],
                maker_address=record["maker_address"],
                session_signer_address=record["session_signer_address"],
                side=record["side"],
                maximum_price=record["maximum_price"],
                maximum_size=record["maximum_size"],
                created_at=datetime.fromisoformat(record["created_at"]),
                expires_at=datetime.fromisoformat(record["expires_at"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise OrderLifecycleError("invalid order binding record") from exc


@dataclass(frozen=True)
class OrderLifecycle:
    """Persistable state machine that forbids blind retries after ambiguity."""

    binding: OrderBinding
    state: OrderState
    revision: int
    updated_at: datetime
    order_id: str | None = None
    matched_size: Decimal = Decimal("0")
    trade_ids: tuple[str, ...] = ()
    transaction_hashes: tuple[str, ...] = ()
    last_error: str | None = None
    reconciled_at: datetime | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.binding, OrderBinding):
            raise OrderLifecycleError("binding must be an OrderBinding")
        try:
            state = self.state if isinstance(self.state, OrderState) else OrderState(self.state)
        except ValueError as exc:
            raise OrderLifecycleError("invalid order state") from exc
        object.__setattr__(self, "state", state)
        if not isinstance(self.revision, int) or self.revision < 0:
            raise OrderLifecycleError("revision must be a non-negative integer")
        updated_at = _utc_timestamp(self.updated_at, "updated_at")
        if updated_at < self.binding.created_at:
            raise OrderLifecycleError("updated_at cannot precede the approval")
        object.__setattr__(self, "updated_at", updated_at)
        if self.order_id is not None:
            object.__setattr__(self, "order_id", _identifier(self.order_id, "order_id"))
        requires_order_id = {
            OrderState.LIVE,
            OrderState.PARTIALLY_FILLED,
            OrderState.FILLED,
            OrderState.CANCEL_PENDING,
            OrderState.CANCELED,
            OrderState.EXPIRED,
        }
        if state in requires_order_id and not self.order_id:
            raise OrderLifecycleError(f"{state.value} state requires an order_id")
        matched_size = _decimal(self.matched_size, "matched_size")
        if matched_size < 0 or matched_size > self.binding.maximum_size:
            raise OrderLifecycleError("matched_size is outside the approved bound")
        object.__setattr__(self, "matched_size", matched_size)
        object.__setattr__(self, "trade_ids", _string_tuple(self.trade_ids, "trade_ids"))
        object.__setattr__(
            self,
            "transaction_hashes",
            _string_tuple(self.transaction_hashes, "transaction_hashes"),
        )
        if self.last_error is not None:
            object.__setattr__(self, "last_error", _short_text(self.last_error, "last_error"))
        if self.reconciled_at is not None:
            reconciled_at = _utc_timestamp(self.reconciled_at, "reconciled_at")
            if reconciled_at < self.binding.created_at or reconciled_at > updated_at:
                raise OrderLifecycleError("reconciled_at is outside the lifecycle timeline")
            object.__setattr__(self, "reconciled_at", reconciled_at)

    @classmethod
    def approved(cls, binding: OrderBinding) -> "OrderLifecycle":
        return cls(
            binding=binding,
            state=OrderState.APPROVED,
            revision=0,
            updated_at=binding.created_at,
        )

    def _at(self, now: datetime) -> datetime:
        timestamp = _utc_timestamp(now, "now")
        if timestamp < self.updated_at:
            raise OrderLifecycleError("lifecycle timestamps must be monotonic")
        return timestamp

    def _transition(self, now: datetime, **changes: Any) -> "OrderLifecycle":
        return replace(
            self,
            revision=self.revision + 1,
            updated_at=self._at(now),
            **changes,
        )

    def begin_submission(self, now: datetime) -> "OrderLifecycle":
        if self.state is not OrderState.APPROVED:
            raise OrderLifecycleError("only an approved order can begin submission")
        if self._at(now) >= self.binding.expires_at:
            raise OrderLifecycleError("the approval has expired")
        return self._transition(now, state=OrderState.SUBMITTING, last_error=None)

    def submission_unknown(self, now: datetime, reason: str) -> "OrderLifecycle":
        if self.state is not OrderState.SUBMITTING:
            raise OrderLifecycleError("only a submitting order can become unknown")
        return self._transition(
            now,
            state=OrderState.UNKNOWN,
            last_error=_short_text(reason, "reason"),
        )

    def record_submission(self, now: datetime, response: Mapping[str, Any]) -> "OrderLifecycle":
        if self.state is not OrderState.SUBMITTING:
            raise OrderLifecycleError("submission result is not expected in the current state")
        if not isinstance(response, Mapping):
            raise OrderLifecycleError("submission response must be an object")
        accepted = response.get("success")
        if accepted is False:
            reason = response.get("error") or response.get("message") or "provider rejected order"
            return self._transition(
                now,
                state=OrderState.REJECTED,
                last_error=_short_text(reason, "submission error"),
            )
        if accepted is not True:
            raise OrderLifecycleError("submission response must include an explicit success result")
        order_id = _identifier(response.get("orderID") or response.get("order_id"), "order_id")
        status = _short_text(response.get("status"), "status", limit=32).lower()
        if status == "live":
            state = OrderState.LIVE
        elif status in {"matched", "delayed"}:
            state = OrderState.UNKNOWN
        else:
            raise OrderLifecycleError("unrecognized successful submission status")
        return self._transition(
            now,
            state=state,
            order_id=order_id,
            last_error=None if state is OrderState.LIVE else "authenticated reconciliation required",
        )

    def request_cancel(self, now: datetime) -> "OrderLifecycle":
        if self.state not in {OrderState.LIVE, OrderState.PARTIALLY_FILLED}:
            raise OrderLifecycleError("only a live order can request cancellation")
        return self._transition(now, state=OrderState.CANCEL_PENDING, last_error=None)

    def record_cancel(self, now: datetime, response: Mapping[str, Any]) -> "OrderLifecycle":
        if self.state is not OrderState.CANCEL_PENDING or not self.order_id:
            raise OrderLifecycleError("cancel result is not expected in the current state")
        if not isinstance(response, Mapping):
            raise OrderLifecycleError("cancel response must be an object")
        canceled = _string_tuple(response.get("canceled", ()), "canceled")
        not_canceled = response.get("not_canceled", {})
        if not isinstance(not_canceled, Mapping):
            raise OrderLifecycleError("not_canceled must be an object")
        if self.order_id in canceled:
            return self._transition(now, state=OrderState.CANCELED, last_error=None)
        if self.order_id in not_canceled:
            return self._transition(
                now,
                state=OrderState.UNKNOWN,
                last_error=_short_text(not_canceled[self.order_id], "cancel error"),
            )
        raise OrderLifecycleError("cancel response does not reference the expected order")

    def reconcile(
        self,
        now: datetime,
        payload: Mapping[str, Any],
        *,
        session_signer_address: str,
    ) -> "OrderLifecycle":
        if not self.order_id:
            raise OrderLifecycleError("an order_id is required for authenticated reconciliation")
        if not isinstance(payload, Mapping):
            raise OrderLifecycleError("order payload must be an object")
        if normalize_evm_address(session_signer_address, "session_signer_address") != self.binding.session_signer_address:
            raise OrderLifecycleError("session signer does not match the approved order")
        if _identifier(payload.get("id"), "id") != self.order_id:
            raise OrderLifecycleError("provider order id does not match")
        if _identifier(payload.get("market"), "market") != self.binding.condition_id:
            raise OrderLifecycleError("provider market does not match")
        if _identifier(payload.get("asset_id"), "asset_id") != self.binding.token_id:
            raise OrderLifecycleError("provider token does not match")
        maker = normalize_evm_address(payload.get("maker_address"), "maker_address")
        if maker != self.binding.maker_address:
            raise OrderLifecycleError("provider maker does not match")
        side = _short_text(payload.get("side"), "side", limit=8).upper()
        if side != self.binding.side:
            raise OrderLifecycleError("provider side does not match")
        price = _decimal(payload.get("price"), "price")
        original_size = _decimal(payload.get("original_size"), "original_size")
        matched_size = _decimal(payload.get("size_matched"), "size_matched")
        if price <= 0 or price > self.binding.maximum_price:
            raise OrderLifecycleError("provider price exceeds the approved bound")
        if original_size <= 0 or original_size > self.binding.maximum_size:
            raise OrderLifecycleError("provider size exceeds the approved bound")
        if matched_size < 0 or matched_size > original_size:
            raise OrderLifecycleError("provider matched size is invalid")
        if matched_size < self.matched_size:
            raise OrderLifecycleError("provider matched size cannot decrease")
        status = _short_text(payload.get("status"), "status", limit=32).upper()
        if status == "LIVE":
            if matched_size == original_size:
                state = OrderState.FILLED
            elif matched_size > 0:
                state = OrderState.PARTIALLY_FILLED
            else:
                state = OrderState.LIVE
        elif status in {"MATCHED", "FILLED"}:
            if matched_size != original_size:
                raise OrderLifecycleError("filled order must match its original size")
            state = OrderState.FILLED
        elif status in {"CANCELED", "CANCELLED"}:
            state = OrderState.CANCELED
        elif status == "EXPIRED":
            state = OrderState.EXPIRED
        else:
            raise OrderLifecycleError("unrecognized provider order status")
        trade_ids = _string_tuple(payload.get("associate_trades", ()), "associate_trades")
        transaction_hashes = _string_tuple(
            payload.get("transaction_hashes", ()), "transaction_hashes"
        )
        timestamp = self._at(now)
        return self._transition(
            timestamp, state=state, matched_size=matched_size, trade_ids=trade_ids,
            transaction_hashes=transaction_hashes, last_error=None, reconciled_at=timestamp,
        )

    def to_record(self) -> dict[str, Any]:
        return {
            "binding": self.binding.to_record(), "state": self.state.value,
            "revision": self.revision, "updated_at": self.updated_at.isoformat(),
            "order_id": self.order_id, "matched_size": str(self.matched_size),
            "trade_ids": list(self.trade_ids),
            "transaction_hashes": list(self.transaction_hashes),
            "last_error": self.last_error,
            "reconciled_at": self.reconciled_at.isoformat() if self.reconciled_at else None,
        }

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> "OrderLifecycle":
        try:
            reconciled_at = record.get("reconciled_at")
            return cls(
                binding=OrderBinding.from_record(record["binding"]),
                state=OrderState(record["state"]), revision=record["revision"],
                updated_at=datetime.fromisoformat(record["updated_at"]),
                order_id=record.get("order_id"),
                matched_size=record.get("matched_size", "0"),
                trade_ids=tuple(record.get("trade_ids", ())),
                transaction_hashes=tuple(record.get("transaction_hashes", ())),
                last_error=record.get("last_error"),
                reconciled_at=datetime.fromisoformat(reconciled_at) if reconciled_at else None,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise OrderLifecycleError("invalid order lifecycle record") from exc
