"""Authenticated, secret-ephemeral transport for bounded Polymarket orders."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Awaitable, Callable, Mapping

from .account_connection import normalize_evm_address
from .order_lifecycle import OrderLifecycle, OrderLifecycleError, OrderState
from .order_signing import (
    ZERO_BYTES32, OrderSigningError, UnsignedDepositWalletOrder,
    verify_deposit_wallet_order_signature,
)

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


def validate_signed_order(lifecycle: OrderLifecycle, order: Mapping[str, Any]) -> dict[str, Any]:
    """Verify one current Deposit Wallet order against the exact approval binding."""
    expected = {
        "builder", "expiration", "maker", "makerAmount", "metadata", "salt",
        "side", "signature", "signatureType", "signer", "takerAmount",
        "timestamp", "tokenId",
    }
    if not isinstance(order, Mapping) or set(order) != expected:
        raise OrderTransportError("signed order shape is invalid")
    binding = lifecycle.binding
    maker = normalize_evm_address(order.get("maker"), "maker")
    signer = normalize_evm_address(order.get("signer"), "signer")
    if maker != binding.maker_address or signer != binding.maker_address:
        raise OrderTransportError("Deposit Wallet order identity does not match approval")
    if str(order.get("tokenId")) != binding.token_id:
        raise OrderTransportError("signed order token does not match approval")
    if str(order.get("side")).upper() != binding.side:
        raise OrderTransportError("signed order side does not match approval")
    if order.get("builder") != ZERO_BYTES32 or order.get("metadata") != ZERO_BYTES32:
        raise OrderTransportError("unreviewed builder or metadata is blocked")
    try:
        unsigned = UnsignedDepositWalletOrder(
            exchange_address=binding.exchange_address,
            maker=maker,
            token_id=binding.token_id,
            maker_amount=int(str(order.get("makerAmount"))),
            taker_amount=int(str(order.get("takerAmount"))),
            salt=int(str(order.get("salt"))),
            timestamp=int(str(order.get("timestamp"))),
            side=binding.side,
            expiration=int(str(order.get("expiration"))),
            signature_type=int(order.get("signatureType")),
            metadata=str(order.get("metadata")),
            builder=str(order.get("builder")),
            protocol_version=binding.protocol_version,
        )
    except (TypeError, ValueError, OrderSigningError) as exc:
        raise OrderTransportError("signed order fields are invalid") from exc
    created_ms = int(binding.created_at.timestamp() * 1000)
    expires_ms = int(binding.expires_at.timestamp() * 1000)
    if not created_ms <= unsigned.timestamp < expires_ms:
        raise OrderTransportError("signed order timestamp is outside approval")
    size = Decimal(unsigned.taker_amount) / Decimal(1_000_000)
    price = Decimal(unsigned.maker_amount) / Decimal(unsigned.taker_amount)
    if binding.order_type in {"FAK", "FOK"}:
        price_exceeded = price >= binding.maximum_price + Decimal("0.0001")
    else:
        price_exceeded = price > binding.maximum_price
    if size > binding.maximum_size or price_exceeded:
        raise OrderTransportError("signed order exceeds approved price or size")
    try:
        verify_deposit_wallet_order_signature(
            unsigned, str(order.get("signature")),
            expected_session_address=binding.session_signer_address,
        )
    except (OrderSigningError, ValueError) as exc:
        raise OrderTransportError("signed order signature is invalid") from exc
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
        signature_type: int = 3,
    ) -> Mapping[str, Any]:
        if signature_type not in {0, 1, 2, 3}:
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
        return await self.submit_prepared(
            lifecycle.begin_submission(now), signed_order,
            now=now, timestamp=timestamp,
        )

    async def submit_prepared(
        self, submitting: OrderLifecycle, signed_order: Mapping[str, Any], *,
        now: datetime, timestamp: int,
    ) -> OrderLifecycle:
        if submitting.state is not OrderState.SUBMITTING:
            raise OrderTransportError("order is not in persisted submitting state")
        order = validate_signed_order(submitting, signed_order)
        credentials = await self._credential_provider()
        payload = {
            "order": order, "owner": credentials.key,
            "orderType": submitting.binding.order_type, "deferExec": False,
        }
        try:
            response = await self._call(
                "POST", POST_ORDER_PATH, timestamp=timestamp, body=payload,
                session_signer_address=submitting.binding.session_signer_address,
                credentials=credentials,
            )
            return submitting.record_submission(now, response)
        except Exception:
            return submitting.submission_unknown(
                now, "authenticated transport outcome unknown"
            )

    async def cancel(
        self, lifecycle: OrderLifecycle, *, now: datetime, timestamp: int,
    ) -> OrderLifecycle:
        return await self.cancel_prepared(
            lifecycle.request_cancel(now), now=now, timestamp=timestamp
        )

    async def cancel_prepared(
        self, pending: OrderLifecycle, *, now: datetime, timestamp: int,
    ) -> OrderLifecycle:
        if pending.state is not OrderState.CANCEL_PENDING or not pending.order_id:
            raise OrderTransportError("order is not in persisted cancel-pending state")
        try:
            response = await self._call(
                "DELETE", CANCEL_ORDER_PATH, timestamp=timestamp,
                body={"orderID": pending.order_id},
                session_signer_address=pending.binding.session_signer_address,
            )
            return pending.record_cancel(now, response)
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
