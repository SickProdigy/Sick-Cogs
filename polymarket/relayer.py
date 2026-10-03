"""Exact Polymarket Builder/Relayer transport for Deposit Wallet operations."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass, field
from typing import Awaitable, Callable

import aiohttp

from .account_connection import AccountConnectionError
from .deposit_wallet import DepositWalletCreationPlan
from .production_manifest import POLYMARKET_PRODUCTION_MANIFEST
from .settlement import SettlementPlan
from .bridge import BridgeWithdrawalPlan


SUBMIT_PATH = "/submit"
TRANSACTION_PATH = "/transaction"
Transport = Callable[..., Awaitable[object]]


@dataclass(frozen=True, slots=True)
class BuilderCredentials:
    api_key: str = field(repr=False)
    secret: str = field(repr=False)
    passphrase: str = field(repr=False)

    def __post_init__(self) -> None:
        if not all(isinstance(value, str) and value.strip() for value in (
            self.api_key, self.secret, self.passphrase,
        )):
            raise AccountConnectionError("Polymarket Builder credentials are incomplete.")
        try:
            decoded = base64.b64decode(
                self.secret + "=" * (-len(self.secret) % 4),
                altchars=b"-_", validate=True,
            )
        except (ValueError, base64.binascii.Error) as exc:
            raise AccountConnectionError("Polymarket Builder secret is invalid.") from exc
        if not decoded:
            raise AccountConnectionError("Polymarket Builder secret is invalid.")

    def headers(self, method: str, path: str, body: str, timestamp: int) -> dict[str, str]:
        secret = base64.b64decode(
            self.secret + "=" * (-len(self.secret) % 4), altchars=b"-_"
        )
        message = f"{timestamp}{method}{path}{body}".encode("utf-8")
        signature = base64.urlsafe_b64encode(
            hmac.new(secret, message, hashlib.sha256).digest()
        ).decode("ascii")
        return {
            "POLY_BUILDER_API_KEY": self.api_key,
            "POLY_BUILDER_PASSPHRASE": self.passphrase,
            "POLY_BUILDER_SIGNATURE": signature,
            "POLY_BUILDER_TIMESTAMP": str(timestamp),
        }


class DepositWalletRelayerClient:
    """Submit and read only reviewed Deposit Wallet operations."""

    def __init__(self, transport: Transport | None = None):
        self._transport = transport or self._http_transport

    @staticmethod
    async def _http_transport(method: str, url: str, *, headers=None, body=None, params=None):
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.request(
                method, url, headers=headers, data=body, params=params
            ) as response:
                payload = await response.json(content_type=None)
                if response.status < 200 or response.status >= 300:
                    raise AccountConnectionError(
                        f"Polymarket Relayer returned HTTP {response.status}."
                    )
                return payload

    async def submit_creation(
        self, plan: DepositWalletCreationPlan, credentials: BuilderCredentials,
        *, timestamp: int,
    ) -> dict:
        body = json.dumps(plan.relayer_request(), separators=(",", ":"))
        headers = credentials.headers("POST", SUBMIT_PATH, body, timestamp)
        headers["Content-Type"] = "application/json"
        payload = await self._transport(
            "POST", POLYMARKET_PRODUCTION_MANIFEST.relayer_api + SUBMIT_PATH,
            headers=headers, body=body, params=None,
        )
        if not isinstance(payload, dict) or set(payload) - {
            "transactionID", "transactionHash", "state", "hash"
        }:
            raise AccountConnectionError("Polymarket Relayer submission is invalid.")
        return {
            "transaction_id": payload.get("transactionID"),
            "transaction_hash": payload.get("transactionHash") or None,
        }

    async def submit_settlement(
        self, plan: SettlementPlan, signature: str,
        credentials: BuilderCredentials, *, timestamp: int,
    ) -> dict:
        body = json.dumps(
            plan.relayer_request(signature), separators=(",", ":"),
            ensure_ascii=True,
        )
        headers = credentials.headers("POST", SUBMIT_PATH, body, timestamp)
        headers.update({
            "Content-Type": "application/json",
            "Idempotency-Key": plan.idempotency_key,
        })
        payload = await self._transport(
            "POST", POLYMARKET_PRODUCTION_MANIFEST.relayer_api + SUBMIT_PATH,
            headers=headers, body=body, params=None,
        )
        allowed = {"transactionID", "transactionHash", "state", "hash"}
        if not isinstance(payload, dict) or set(payload) - allowed:
            raise AccountConnectionError("Polymarket settlement submission is invalid.")
        transaction_id = payload.get("transactionID")
        if not isinstance(transaction_id, str) or not transaction_id or len(transaction_id) > 128:
            raise AccountConnectionError("Settlement transaction identity is invalid.")
        transaction_hash = payload.get("transactionHash") or None
        if transaction_hash is not None and (
            not isinstance(transaction_hash, str)
            or len(transaction_hash) != 66 or not transaction_hash.startswith("0x")
        ):
            raise AccountConnectionError("Settlement transaction hash is invalid.")
        return {
            "transaction_id": transaction_id,
            "transaction_hash": transaction_hash.lower() if transaction_hash else None,
        }

    async def get_settlement(
        self, plan: SettlementPlan, transaction_id: str,
    ) -> dict:
        if not isinstance(transaction_id, str) or not transaction_id or len(transaction_id) > 128:
            raise AccountConnectionError("Settlement transaction identity is invalid.")
        payload = await self._transport(
            "GET", POLYMARKET_PRODUCTION_MANIFEST.relayer_api + TRANSACTION_PATH,
            headers={"Accept": "application/json"}, body=None,
            params={"id": transaction_id},
        )
        if not isinstance(payload, list) or len(payload) != 1 or not isinstance(payload[0], dict):
            raise AccountConnectionError("Settlement transaction is unavailable.")
        item = payload[0]
        required = {"transactionID", "state", "from", "to", "proxyAddress", "type"}
        if not required.issubset(item):
            raise AccountConnectionError("Settlement transaction is incomplete.")
        return {
            "transaction_id": item["transactionID"],
            "transaction_hash": item.get("transactionHash") or None,
            "state": item["state"],
            "from": str(item["from"]).lower(),
            "to": str(item["to"]).lower(),
            "proxy_address": str(item["proxyAddress"]).lower(),
            "type": item["type"],
            "error_msg": item.get("errorMsg"),
        }


    async def submit_withdrawal(
        self, plan: BridgeWithdrawalPlan, signature: str,
        credentials: BuilderCredentials, *, timestamp: int,
    ) -> dict:
        body = json.dumps(
            plan.relayer_request(signature), separators=(",", ":"), ensure_ascii=True,
        )
        headers = credentials.headers("POST", SUBMIT_PATH, body, timestamp)
        headers.update({"Content-Type": "application/json", "Idempotency-Key": plan.idempotency_key})
        payload = await self._transport(
            "POST", POLYMARKET_PRODUCTION_MANIFEST.relayer_api + SUBMIT_PATH,
            headers=headers, body=body, params=None,
        )
        allowed = {"transactionID", "transactionHash", "state", "hash"}
        if not isinstance(payload, dict) or set(payload) - allowed:
            raise AccountConnectionError("Polymarket withdrawal submission is invalid.")
        transaction_id = payload.get("transactionID")
        if not isinstance(transaction_id, str) or not transaction_id or len(transaction_id) > 128:
            raise AccountConnectionError("Withdrawal transaction identity is invalid.")
        transaction_hash = payload.get("transactionHash") or None
        if transaction_hash is not None and (
            not isinstance(transaction_hash, str) or re.fullmatch(
                r"0x[0-9a-fA-F]{64}", transaction_hash
            ) is None
        ):
            raise AccountConnectionError("Withdrawal transaction hash is invalid.")
        return {"transaction_id": transaction_id,
                "transaction_hash": transaction_hash.lower() if transaction_hash else None}

    async def get_withdrawal(
        self, plan: BridgeWithdrawalPlan, transaction_id: str,
    ) -> dict:
        if not isinstance(transaction_id, str) or not transaction_id or len(transaction_id) > 128:
            raise AccountConnectionError("Withdrawal transaction identity is invalid.")
        payload = await self._transport(
            "GET", POLYMARKET_PRODUCTION_MANIFEST.relayer_api + TRANSACTION_PATH,
            headers={"Accept": "application/json"}, body=None,
            params={"id": transaction_id},
        )
        if not isinstance(payload, list) or len(payload) != 1 or not isinstance(payload[0], dict):
            raise AccountConnectionError("Withdrawal transaction is unavailable.")
        item = payload[0]
        required = {"transactionID", "state", "from", "to", "proxyAddress", "type"}
        if not required.issubset(item):
            raise AccountConnectionError("Withdrawal transaction is incomplete.")
        return {
            "transaction_id": item["transactionID"],
            "transaction_hash": item.get("transactionHash") or None,
            "state": item["state"], "from": str(item["from"]).lower(),
            "to": str(item["to"]).lower(),
            "proxy_address": str(item["proxyAddress"]).lower(),
            "type": item["type"], "error_msg": item.get("errorMsg"),
        }

    async def get_creation(self, plan: DepositWalletCreationPlan) -> dict:
        if not plan.relayer_transaction_id:
            raise AccountConnectionError("Relayer transaction identity is missing.")
        payload = await self._transport(
            "GET", POLYMARKET_PRODUCTION_MANIFEST.relayer_api + TRANSACTION_PATH,
            headers={"Accept": "application/json"}, body=None,
            params={"id": plan.relayer_transaction_id},
        )
        if not isinstance(payload, list) or len(payload) != 1 or not isinstance(payload[0], dict):
            raise AccountConnectionError("Polymarket Relayer transaction is unavailable.")
        item = payload[0]
        required = {"transactionID", "state", "from", "to", "proxyAddress", "type"}
        if not required.issubset(item):
            raise AccountConnectionError("Polymarket Relayer transaction is incomplete.")
        return {
            "transaction_id": item["transactionID"],
            "transaction_hash": item.get("transactionHash") or None,
            "state": item["state"],
            "from": str(item["from"]).lower(),
            "to": str(item["to"]).lower(),
            "proxy_address": str(item["proxyAddress"]).lower(),
            "type": item["type"],
            "error_msg": item.get("errorMsg"),
        }
