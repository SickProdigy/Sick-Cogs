"""Authenticated transport for the exact Polymarket session-key lifecycle."""

from __future__ import annotations

import json
from typing import Awaitable, Callable

import aiohttp

from .account_connection import AccountConnectionError, normalize_evm_address
from .order_transport import ClobCredentials, OrderTransportError, _hmac_signature
from .production_manifest import POLYMARKET_PRODUCTION_MANIFEST
from .relayer import BuilderCredentials
from .session_authorization import (
    SESSION_LIST_PATH, WALLET_PARAMS_PATH, SessionKeyOwnerApproval,
    sign_session_clob_auth,
)


Transport = Callable[..., Awaitable[object]]
TRANSACTION_PREFIX = "/v1/account/transactions/"
CREATE_CREDENTIALS_PATH = "/auth/api-key"
DERIVE_CREDENTIALS_PATH = "/auth/derive-api-key"


class SessionKeyTransport:
    """Use only reviewed Relayer and CLOB endpoints with ephemeral credentials."""

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
                        f"Polymarket session endpoint returned HTTP {response.status}."
                    )
                return payload

    async def get_wallet_nonce(self, owner_address: str) -> int:
        owner = normalize_evm_address(owner_address, "Deposit Wallet owner")
        payload = await self._transport(
            "GET",
            POLYMARKET_PRODUCTION_MANIFEST.relayer_api + WALLET_PARAMS_PATH,
            headers={"Accept": "application/json"}, body=None,
            params={"address": owner, "type": "WALLET"},
        )
        if not isinstance(payload, dict) or set(payload) != {"address", "nonce"}:
            raise AccountConnectionError("Session-key nonce response is invalid.")
        try:
            nonce = int(payload["nonce"])
        except (TypeError, ValueError) as exc:
            raise AccountConnectionError("Session-key nonce is invalid.") from exc
        if nonce < 0 or not isinstance(payload["address"], str):
            raise AccountConnectionError("Session-key nonce response is invalid.")
        return nonce

    async def submit(
        self, approval: SessionKeyOwnerApproval, signature: str,
        credentials: BuilderCredentials, *, timestamp: int,
    ) -> dict:
        request = approval.request_body(signature)
        body = json.dumps(request, separators=(",", ":"), ensure_ascii=True)
        headers = credentials.headers("POST", approval.endpoint, body, timestamp)
        headers.update({
            "Content-Type": "application/json",
            "Idempotency-Key": approval.idempotency_key,
        })
        payload = await self._transport(
            "POST",
            POLYMARKET_PRODUCTION_MANIFEST.relayer_api + approval.endpoint,
            headers=headers, body=body, params=None,
        )
        if not isinstance(payload, dict):
            raise AccountConnectionError("Session-key submission response is invalid.")
        allowed = {"operationId", "status", "transactionHash", "transactionId", "fenced"}
        if set(payload) - allowed or not {
            "operationId", "status", "transactionId"
        }.issubset(payload):
            raise AccountConnectionError("Session-key submission response is invalid.")
        if approval.action == "revoke" and payload.get("fenced") is not True:
            raise AccountConnectionError("Session-key revocation was not fenced.")
        for field in ("operationId", "status", "transactionId"):
            if not isinstance(payload[field], str) or not payload[field]:
                raise AccountConnectionError("Session-key submission identity is invalid.")
        transaction_hash = payload.get("transactionHash") or None
        if transaction_hash is not None and (
            not isinstance(transaction_hash, str)
            or len(transaction_hash) != 66
            or not transaction_hash.startswith("0x")
        ):
            raise AccountConnectionError("Session-key transaction hash is invalid.")
        return {
            "operation_id": payload["operationId"],
            "status": payload["status"],
            "transaction_id": payload["transactionId"],
            "transaction_hash": transaction_hash.lower() if transaction_hash else None,
            "fenced": payload.get("fenced"),
        }

    async def transaction(self, transaction_id: str) -> dict:
        if not isinstance(transaction_id, str) or not transaction_id or len(transaction_id) > 128:
            raise AccountConnectionError("Session-key transaction identity is invalid.")
        payload = await self._transport(
            "GET",
            POLYMARKET_PRODUCTION_MANIFEST.relayer_api
            + TRANSACTION_PREFIX + transaction_id,
            headers={"Accept": "application/json"}, body=None, params=None,
        )
        required = {"transaction_id", "transaction_hash", "state", "error_msg"}
        if not isinstance(payload, dict) or set(payload) != required:
            raise AccountConnectionError("Session-key transaction response is invalid.")
        if payload["transaction_id"] != transaction_id:
            raise AccountConnectionError("Session-key transaction identity changed.")
        if payload["state"] not in {
            "STATE_NEW", "STATE_EXECUTED", "STATE_MINED",
            "STATE_CONFIRMED", "STATE_FAILED", "STATE_INVALID",
        }:
            raise AccountConnectionError("Session-key transaction state is invalid.")
        return dict(payload)

    async def clob_credentials(
        self, *, address: str, signature: str, timestamp: int,
        nonce: int = 0, derive: bool = False,
    ) -> ClobCredentials:
        """Create or derive credentials from one independently verified ClobAuth."""

        owner = normalize_evm_address(address, "CLOB credential owner")
        if (
            not isinstance(signature, str)
            or len(signature) != 132 or not signature.startswith("0x")
            or any(character not in "0123456789abcdefABCDEF" for character in signature[2:])
            or timestamp <= 0 or not 0 <= nonce < 2**256
        ):
            raise AccountConnectionError("CLOB credential proof is invalid.")
        path = DERIVE_CREDENTIALS_PATH if derive else CREATE_CREDENTIALS_PATH
        method = "GET" if derive else "POST"
        payload = await self._transport(
            method, POLYMARKET_PRODUCTION_MANIFEST.clob_api + path,
            headers={
                "POLY_ADDRESS": owner, "POLY_SIGNATURE": signature.lower(),
                "POLY_TIMESTAMP": str(timestamp), "POLY_NONCE": str(nonce),
                "Accept": "application/json",
            },
            body=None, params=None,
        )
        if not isinstance(payload, dict) or set(payload) != {
            "apiKey", "secret", "passphrase"
        }:
            raise AccountConnectionError("Session CLOB credentials response is invalid.")
        try:
            return ClobCredentials(
                payload["apiKey"], payload["secret"], payload["passphrase"]
            )
        except OrderTransportError as exc:
            raise AccountConnectionError(
                "Session CLOB credentials response is invalid."
            ) from exc

    async def session_credentials(
        self, private_key: bytes, *, timestamp: int, nonce: int = 0,
        derive: bool = False,
    ) -> ClobCredentials:
        """Create or derive only the credentials owned by this session EOA."""

        signature, address = sign_session_clob_auth(
            private_key, timestamp=timestamp, nonce=nonce
        )
        return await self.clob_credentials(
            address=address, signature=signature, timestamp=timestamp,
            nonce=nonce, derive=derive,
        )

    async def create_or_derive_clob_credentials(
        self, *, address: str, signature: str, timestamp: int, nonce: int = 0,
    ) -> ClobCredentials:
        """Create credentials once, deriving the same key after retry or ambiguity."""

        try:
            return await self.clob_credentials(
                address=address, signature=signature, timestamp=timestamp,
                nonce=nonce, derive=False,
            )
        except (aiohttp.ClientError, AccountConnectionError, TimeoutError):
            return await self.clob_credentials(
                address=address, signature=signature, timestamp=timestamp,
                nonce=nonce, derive=True,
            )

    async def create_or_derive_session_credentials(
        self, private_key: bytes, *, timestamp: int, nonce: int = 0,
    ) -> ClobCredentials:
        signature, address = sign_session_clob_auth(
            private_key, timestamp=timestamp, nonce=nonce
        )
        return await self.create_or_derive_clob_credentials(
            address=address, signature=signature, timestamp=timestamp, nonce=nonce
        )

    async def active_session_keys(
        self, *, owner_address: str, wallet_address: str,
        credentials: ClobCredentials, timestamp: int,
    ) -> tuple[dict, ...]:
        owner = normalize_evm_address(owner_address, "Deposit Wallet owner")
        wallet = normalize_evm_address(wallet_address, "Deposit Wallet")
        headers = {
            "POLY_ADDRESS": owner,
            "POLY_SIGNATURE": _hmac_signature(
                credentials.secret, timestamp, "GET", SESSION_LIST_PATH
            ),
            "POLY_TIMESTAMP": str(timestamp),
            "POLY_API_KEY": credentials.key,
            "POLY_PASSPHRASE": credentials.passphrase,
            "Accept": "application/json",
        }
        payload = await self._transport(
            "GET", POLYMARKET_PRODUCTION_MANIFEST.clob_api + SESSION_LIST_PATH,
            headers=headers, body=None, params=None,
        )
        if not isinstance(payload, dict) or set(payload) != {"wallet", "signers"}:
            raise AccountConnectionError("Session-key registry response is invalid.")
        if normalize_evm_address(payload["wallet"], "registry wallet") != wallet:
            raise AccountConnectionError("Session-key registry wallet changed.")
        if not isinstance(payload["signers"], list):
            raise AccountConnectionError("Session-key registry signers are invalid.")
        signers = []
        for item in payload["signers"]:
            if not isinstance(item, dict) or set(item) != {
                "address", "scopes", "valid_until"
            }:
                raise AccountConnectionError("Session-key registry signer is invalid.")
            address = normalize_evm_address(item["address"], "session signer")
            if item["scopes"] != ["CLOB"]:
                raise AccountConnectionError("Session-key registry scope changed.")
            try:
                valid_until = int(item["valid_until"])
            except (TypeError, ValueError) as exc:
                raise AccountConnectionError(
                    "Session-key registry expiration is invalid."
                ) from exc
            signers.append({
                "address": address, "scopes": ("CLOB",),
                "valid_until": valid_until,
            })
        return tuple(signers)

    async def require_active(
        self, approval: SessionKeyOwnerApproval, *, credentials: ClobCredentials,
        timestamp: int,
    ) -> dict:
        signers = await self.active_session_keys(
            owner_address=approval.owner_address,
            wallet_address=approval.wallet_address,
            credentials=credentials, timestamp=timestamp,
        )
        matches = [
            signer for signer in signers
            if signer["address"] == approval.session_address
        ]
        if len(matches) != 1:
            raise AccountConnectionError("Expected session key is not active.")
        expected_expiry = approval.valid_until
        if approval.action != "authorize" or matches[0]["valid_until"] != expected_expiry:
            raise AccountConnectionError("Session-key registry expiration changed.")
        return matches[0]
