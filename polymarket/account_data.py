"""Strict account-data models for Polymarket Data API and authenticated CLOB reads."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Sequence

from .account_connection import normalize_evm_address


class AccountDataError(RuntimeError):
    """Raised when account data is malformed or no longer matches its wallet."""


def _decimal(value: Any, field: str, *, negative: bool = False) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise AccountDataError(f"{field} is invalid") from exc
    if not parsed.is_finite() or (parsed < 0 and not negative):
        raise AccountDataError(f"{field} is invalid")
    return parsed


def _required_text(row: Mapping[str, Any], key: str, *, maximum: int = 512) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise AccountDataError(f"{key} is invalid")
    return value


@dataclass(frozen=True, slots=True)
class PortfolioValue:
    wallet: str
    value_pusd: Decimal

    @classmethod
    def from_response(cls, payload: Any, *, expected_wallet: str) -> "PortfolioValue":
        if not isinstance(payload, Mapping) or not isinstance(payload.get("data"), Mapping):
            raise AccountDataError("portfolio response did not match the official envelope")
        row = payload["data"]
        wallet = normalize_evm_address(row.get("proxy_wallet"), "portfolio wallet")
        expected = normalize_evm_address(expected_wallet, "expected account wallet")
        if wallet != expected:
            raise AccountDataError("portfolio response wallet does not match this account")
        return cls(wallet=wallet, value_pusd=_decimal(row.get("value"), "portfolio value"))


@dataclass(frozen=True, slots=True)
class PositionSummary:
    condition_id: str
    token_id: str
    title: str
    outcome: str
    size: Decimal
    average_price: Decimal
    current_price: Decimal
    current_value_pusd: Decimal
    total_pnl_pusd: Decimal
    redeemable: bool
    mergeable: bool

    @classmethod
    def from_response(cls, row: Any, *, expected_wallet: str) -> "PositionSummary":
        if not isinstance(row, Mapping):
            raise AccountDataError("position row is invalid")
        wallet = normalize_evm_address(row.get("proxy_wallet"), "position wallet")
        if wallet != normalize_evm_address(expected_wallet, "expected account wallet"):
            raise AccountDataError("position response wallet does not match this account")
        for key in ("redeemable", "mergeable"):
            if not isinstance(row.get(key), bool):
                raise AccountDataError(f"position {key} is invalid")
        title = row.get("title") or "Untitled market"
        outcome = row.get("outcome") or "Unknown outcome"
        if not isinstance(title, str) or not title or len(title) > 512:
            raise AccountDataError("position title is invalid")
        if not isinstance(outcome, str) or not outcome or len(outcome) > 128:
            raise AccountDataError("position outcome is invalid")
        return cls(
            condition_id=_required_text(row, "condition_id", maximum=128),
            token_id=_required_text(row, "token_id", maximum=128),
            title=title,
            outcome=outcome,
            size=_decimal(row.get("current_size"), "position size"),
            average_price=_decimal(row.get("avg_price"), "position average price"),
            current_price=_decimal(row.get("current_price"), "position current price"),
            current_value_pusd=_decimal(row.get("current_value"), "position value"),
            total_pnl_pusd=_decimal(row.get("total_pnl"), "position PnL", negative=True),
            redeemable=row["redeemable"],
            mergeable=row["mergeable"],
        )


def parse_positions_page(
    payload: Any, *, expected_wallet: str,
) -> tuple[tuple[PositionSummary, ...], str | None]:
    if not isinstance(payload, Mapping) or not isinstance(payload.get("data"), Sequence):
        raise AccountDataError("positions response did not match the official envelope")
    if isinstance(payload["data"], (str, bytes)):
        raise AccountDataError("positions response data is invalid")
    cursor = payload.get("next_cursor")
    if cursor is not None and (not isinstance(cursor, str) or len(cursor) > 512):
        raise AccountDataError("positions cursor is invalid")
    positions = tuple(
        PositionSummary.from_response(row, expected_wallet=expected_wallet)
        for row in payload["data"]
    )
    return positions, cursor


@dataclass(frozen=True, slots=True)
class CollateralBalance:
    atomic: int
    allowances: tuple[tuple[str, int], ...]

    @property
    def pusd(self) -> Decimal:
        return Decimal(self.atomic) / Decimal(1_000_000)

    @classmethod
    def from_response(cls, payload: Any) -> "CollateralBalance":
        if not isinstance(payload, Mapping) or set(payload) != {"balance", "allowances"}:
            raise AccountDataError("balance response did not match the official schema")
        raw_allowances = payload["allowances"]
        if not isinstance(raw_allowances, Mapping):
            raise AccountDataError("allowances is invalid")
        allowances = tuple(sorted(
            (
                normalize_evm_address(address, "allowance address"),
                _base_units(amount, "allowance"),
            )
            for address, amount in raw_allowances.items()
        ))
        return cls(_base_units(payload["balance"], "balance"), allowances)


def _base_units(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise AccountDataError(f"{field} is invalid")
    text = str(value)
    if not text.isdigit():
        raise AccountDataError(f"{field} is invalid")
    return int(text)


@dataclass(frozen=True, slots=True)
class OpenOrderSummary:
    order_id: str
    condition_id: str
    token_id: str
    maker_address: str
    side: str
    outcome: str
    price: Decimal
    original_size: Decimal
    matched_size: Decimal
    status: str

    @classmethod
    def from_response(cls, row: Any, *, expected_wallet: str) -> "OpenOrderSummary":
        if not isinstance(row, Mapping):
            raise AccountDataError("open-order row is invalid")
        maker = normalize_evm_address(row.get("maker_address"), "order maker")
        if maker != normalize_evm_address(expected_wallet, "expected account wallet"):
            raise AccountDataError("open-order maker does not match this account")
        side = _required_text(row, "side", maximum=8).upper()
        if side not in {"BUY", "SELL"}:
            raise AccountDataError("open-order side is invalid")
        return cls(
            order_id=_required_text(row, "id", maximum=256),
            condition_id=_required_text(row, "market", maximum=128),
            token_id=_required_text(row, "asset_id", maximum=128),
            maker_address=maker,
            side=side,
            outcome=_required_text(row, "outcome", maximum=128),
            price=_decimal(row.get("price"), "order price"),
            original_size=_decimal(row.get("original_size"), "order size"),
            matched_size=_decimal(row.get("size_matched"), "matched size"),
            status=_required_text(row, "status", maximum=64),
        )


def parse_open_orders_page(
    payload: Any, *, expected_wallet: str,
) -> tuple[tuple[OpenOrderSummary, ...], str | None]:
    if not isinstance(payload, Mapping) or not isinstance(payload.get("data"), list):
        raise AccountDataError("open-orders response did not match the official schema")
    cursor = payload.get("next_cursor")
    if cursor in {"", "LTE="}:
        cursor = None
    if cursor is not None and (not isinstance(cursor, str) or len(cursor) > 512):
        raise AccountDataError("open-orders cursor is invalid")
    orders = tuple(
        OpenOrderSummary.from_response(row, expected_wallet=expected_wallet)
        for row in payload["data"]
    )
    return orders, cursor
