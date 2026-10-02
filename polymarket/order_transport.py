"""Authenticated, secret-ephemeral transport for bounded Polymarket orders."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Awaitable, Callable, Mapping

from .account_connection import normalize_evm_address
from .order_lifecycle import OrderLifecycle, OrderLifecycleError

POST_ORDER_PATH = "/order"
CANCEL_ORDER_PATH = "/order"
GET_ORDER_PREFIX = "/data/order/"
GET_TRADES_PATH = "/data/trades"
GET_OPEN_ORDERS_PATH = "/data/orders"
GET_BALANCE_ALLOWANCE_PATH = "/balance-allowance"
_ZERO_ADDRESS = "0x" + "0" * 40


class OrderTransportError(RuntimeError):
    """Raised when an authenticated request or provider binding fails closed."""


@dataclass(frozen=True, repr=False)
class ClobCredentials:
    """Ephemeral L2 credentials; callers must source these from a server secret store."""

    key: str
    secret: str
    passphrase: str

    def __post_init__(self) -> None:
        for field in ("key", "secret", "passphrase"):
            value = getattr(self, field)
            if not isinstance(value, str) or not value or len(value) > 512:
                raise OrderTransportError("CLOB credentials are invalid")
        try:
            decoded = base64.urlsafe_b64decode(self.secret + "=" * (-len(self.secret) % 4))
        except (ValueError, TypeError) as exc:
            raise OrderTransportError("CLOB credential secret is invalid") from exc
        if len(decoded) < 16:
            raise OrderTransportError("CLOB credential secret is invalid")


Request = Callable[..., Awaitable[Mapping[str, Any]]]
CredentialProvider = Callable[[], Awaitable[ClobCredentials]]


def _canonical_body(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=True)


def _hmac_signature(secret: str, timestamp: int, method: str, path: str, body: str = "") -> str:
    try:
        key = base64.urlsafe_b64decode(secret + "=" * (-len(secret) % 4))
    except (ValueError, TypeError) as exc:
        raise OrderTransportError("CLOB credential secret is invalid") from exc
    message = f"{timestamp}{method}{path}{body}".encode("utf-8")
    return base64.urlsafe_b64encode(
        hmac.new(key, message, hashlib.sha256).digest()
    ).decode("ascii")


def _amount(value: Any, field: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise OrderTransportError(f"{field} is invalid") from exc
    if not parsed.is_finite() or parsed < 0:
        raise OrderTransportError(f"{field} is invalid")
    return parsed


def validate_signed_order(lifecycle: OrderLifecycle, order: Mapping[str, Any]) -> dict[str, Any]:
    """Verify the signed order fields remain inside the exact approved binding."""
    expected = {
        "salt", "maker", "signer", "taker", "tokenId",
        "makerAmount", "takerAmount", "expiration", "nonce",
        "feeRateBps", "side", "signatureType", "signature",
    }
    if not isinstance(order, Mapping) or set(order) != expected:
        raise OrderTransportError("signed order shape is invalid")
    binding = lifecycle.binding
    if normalize_evm_address(order.get("maker"), "maker") != binding.maker_address:
        raise OrderTransportError("signed order maker does not match approval")
    if normalize_evm_address(order.get("signer"), "signer") != binding.session_signer_address:
        raise OrderTransportError("signed order signer does not match approval")
    if normalize_evm_address(order.get("taker"), "taker") != _ZERO_ADDRESS:
        raise OrderTransportError("only public orders are supported")
    if str(order.get("tokenId")) != binding.token_id or str(order.get("side")).upper() != "BUY":
        raise OrderTransportError("signed order market binding does not match approval")
    maker_raw = _amount(order.get("makerAmount"), "makerAmount")
    taker_raw = _amount(order.get("takerAmount"), "takerAmount")
    if maker_raw != maker_raw.to_integral_value() or taker_raw != taker_raw.to_integral_value():
        raise OrderTransportError("signed order raw amounts must be integers")
    if maker_raw <= 0 or taker_raw <= 0:
        raise OrderTransportError("signed order amounts must be positive")
    size = taker_raw / Decimal(1_000_000)
    price = maker_raw / taker_raw
    if size > binding.maximum_size or price > binding.maximum_price:
        raise OrderTransportError("signed order exceeds approved price or size")
    try:
        expiration = int(str(order.get("expiration")))
        int(str(order.get("salt")))
        int(str(order.get("nonce")))
        fee = int(str(order.get("feeRateBps")))
        signature_type = int(order.get("signatureType"))
    except (TypeError, ValueError) as exc:
        raise OrderTransportError("signed order numeric fields are invalid") from exc
    if expiration < 0 or expiration > int(binding.expires_at.timestamp()):
        raise OrderTransportError("signed order expiration exceeds approval")
    if not 0 <= fee <= 10_000 or signature_type not in {0, 1, 2}:
        raise OrderTransportError("signed order policy fields are invalid")
    signature = order.get("signature")
    if not isinstance(signature, str) or re.fullmatch(r"0x[0-9a-fA-F]{130}", signature) is None:
        raise OrderTransportError("signed order signature is invalid")
    return dict(order)


class AuthenticatedOrderTransport:
    """Execute exact CLOB requests with ephemeral credentials and verified results."""

    def __init__(self, *, credential_provider: CredentialProvider, request: Request):
        self._credential_provider = credential_provider
        self._request = request

    async def _call(
        self, method: str, path: str, *, timestamp: int, body: Mapping[str, Any] | None = None,
        params: Mapping[str, Any] | None = None, session_signer_address: str,
        credentials: ClobCredentials | None = None,
    ) -> Mapping[str, Any]:
        credentials = credentials or await self._credential_provider()
        if not isinstance(credentials, ClobCredentials):
            raise OrderTransportError("credential provider returned an invalid object")
        canonical = _canonical_body(body) if body is not None else ""
        headers = {
            "POLY_ADDRESS": normalize_evm_address(session_signer_address, "session signer"),
            "POLY_SIGNATURE": _hmac_signature(
                credentials.secret, timestamp, method, path, canonical
            ),
            "POLY_TIMESTAMP": str(timestamp),
            "POLY_API_KEY": credentials.key,
            "POLY_PASSPHRASE": credentials.passphrase,
        }
        result = await self._request(
            method=method, path=path, headers=headers, body=body, params=params
        )
        if not isinstance(result, Mapping):
            raise OrderTransportError("CLOB returned an invalid response")
        return result

    async def balance_allowance(
        self, *, timestamp: int, session_signer_address: str,
        signature_type: int = 2,
    ) -> Mapping[str, Any]:
        if signature_type not in {0, 1, 2}:
            raise OrderTransportError("signature type is invalid")
        return await self._call(
            "GET", GET_BALANCE_ALLOWANCE_PATH, timestamp=timestamp,
            params={"asset_type": "COLLATERAL", "signature_type": signature_type},
            session_signer_address=session_signer_address,
        )

    async def list_open_orders(
        self, *, timestamp: int, session_signer_address: str,
        next_cursor: str | None = None,
    ) -> Mapping[str, Any]:
        params: dict[str, Any] = {}
        if next_cursor is not None:
            if not isinstance(next_cursor, str) or not next_cursor or len(next_cursor) > 512:
                raise OrderTransportError("open-orders cursor is invalid")
            params["next_cursor"] = next_cursor
        return await self._call(
            "GET", GET_OPEN_ORDERS_PATH, timestamp=timestamp, params=params,
            session_signer_address=session_signer_address,
        )

    async def submit(
        self, lifecycle: OrderLifecycle, signed_order: Mapping[str, Any], *,
        now: datetime, timestamp: int,
    ) -> OrderLifecycle:
        submitting = lifecycle.begin_submission(now)
        order = validate_signed_order(submitting, signed_order)
        credentials = await self._credential_provider()
        payload = {
            "order": order, "owner": credentials.key, "orderType": "GTC",
            "deferExec": False, "postOnly": False,
        }
        try:
            response = await self._call(
                "POST", POST_ORDER_PATH, timestamp=timestamp, body=payload,
                session_signer_address=submitting.binding.session_signer_address,
                credentials=credentials,
            )
            return submitting.record_submission(now, response)
        except (OrderLifecycleError, OrderTransportError):
            raise
        except Exception:
            return submitting.submission_unknown(
                now, "authenticated transport outcome unknown"
            )

    async def cancel(
        self, lifecycle: OrderLifecycle, *, now: datetime, timestamp: int,
    ) -> OrderLifecycle:
        pending = lifecycle.request_cancel(now)
        try:
            response = await self._call(
                "DELETE", CANCEL_ORDER_PATH, timestamp=timestamp,
                body={"orderID": pending.order_id},
                session_signer_address=pending.binding.session_signer_address,
            )
            return pending.record_cancel(now, response)
        except (OrderLifecycleError, OrderTransportError):
            raise
        except Exception:
            return pending

    async def reconcile(
        self, lifecycle: OrderLifecycle, *, now: datetime, timestamp: int,
    ) -> OrderLifecycle:
        if not lifecycle.order_id:
            raise OrderTransportError("order ID is required for reconciliation")
        path = GET_ORDER_PREFIX + lifecycle.order_id
        order = dict(await self._call(
            "GET", path, timestamp=timestamp,
            session_signer_address=lifecycle.binding.session_signer_address,
        ))
        trades_result = await self._call(
            "GET", GET_TRADES_PATH, timestamp=timestamp,
            params={"market": lifecycle.binding.condition_id,
                    "asset_id": lifecycle.binding.token_id, "next_cursor": "MA=="},
            session_signer_address=lifecycle.binding.session_signer_address,
        )
        trades = trades_result.get("data", [])
        if not isinstance(trades, list):
            raise OrderTransportError("authenticated trades response is invalid")
        trade_ids: list[str] = []
        transaction_hashes: list[str] = []
        expected = set(order.get("associate_trades", []))
        for trade in trades:
            if not isinstance(trade, Mapping) or trade.get("id") not in expected:
                continue
            if (str(trade.get("market")) != lifecycle.binding.condition_id
                    or str(trade.get("asset_id")) != lifecycle.binding.token_id
                    or normalize_evm_address(trade.get("maker_address"), "trade maker")
                    != lifecycle.binding.maker_address):
                raise OrderTransportError("authenticated trade identity drifted")
            trade_ids.append(str(trade["id"]))
            tx_hash = trade.get("transaction_hash")
            if tx_hash:
                if not isinstance(tx_hash, str) or re.fullmatch(r"0x[0-9a-fA-F]{64}", tx_hash) is None:
                    raise OrderTransportError("authenticated trade hash is invalid")
                transaction_hashes.append(tx_hash.lower())
        if set(trade_ids) != expected:
            raise OrderTransportError("authenticated trade evidence is incomplete")
        order["associate_trades"] = trade_ids
        order["transaction_hashes"] = transaction_hashes
        return lifecycle.reconcile(
            now, order, session_signer_address=lifecycle.binding.session_signer_address
        )
