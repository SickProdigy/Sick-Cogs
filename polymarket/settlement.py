"""Exact current-SDK settlement calls and immutable approval model."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, replace
from enum import Enum
from typing import Any, Mapping

from eth_hash.auto import keccak

from .account_connection import AccountConnectionError, normalize_evm_address
from .order_protocol import is_protocol_v3_position_id
from .production_manifest import POLYMARKET_PRODUCTION_MANIFEST

_UINT256_MAX = (1 << 256) - 1
_HEX32 = re.compile(r"^0x[0-9a-fA-F]{64}$")
_HEX31 = re.compile(r"^0x[0-9a-fA-F]{62}$")
_ID = re.compile(r"^[A-Za-z0-9_-]{32,128}$")
_BATCH_LIFETIME_SECONDS = 10 * 60
class SettlementState(str, Enum):
    APPROVED = "approved"
    SUBMITTING = "submitting"
    SUBMITTED = "submitted"
    CONFIRMED = "confirmed"
    FAILED = "failed"
    UNKNOWN = "unknown"


_BATCH_TYPES = {
    "Call": [
        {"name": "target", "type": "address"},
        {"name": "value", "type": "uint256"},
        {"name": "data", "type": "bytes"},
    ],
    "Batch": [
        {"name": "wallet", "type": "address"},
        {"name": "nonce", "type": "uint256"},
        {"name": "deadline", "type": "uint256"},
        {"name": "calls", "type": "Call[]"},
    ],
    "EIP712Domain": [
        {"name": "name", "type": "string"},
        {"name": "version", "type": "string"},
        {"name": "chainId", "type": "uint256"},
        {"name": "verifyingContract", "type": "address"},
    ],
}


def _uint(value: int, label: str) -> bytes:
    if not isinstance(value, int) or not 0 <= value <= _UINT256_MAX:
        raise AccountConnectionError(f"{label} is outside uint256.")
    return value.to_bytes(32, "big")


def _condition(value: str) -> bytes:
    if not isinstance(value, str) or _HEX32.fullmatch(value) is None:
        raise AccountConnectionError("Settlement condition ID is invalid.")
    return bytes.fromhex(value[2:])


def ctf_redeem_calldata(condition_id: str) -> str:
    """Match SDK redeemPositions(pUSD, zero parent, condition, [1, 2])."""
    selector = keccak(b"redeemPositions(address,bytes32,bytes32,uint256[])")[:4]
    collateral = bytes(12) + bytes.fromhex(
        POLYMARKET_PRODUCTION_MANIFEST.collateral_token[2:]
    )
    encoded = (
        selector + collateral + bytes(32) + _condition(condition_id)
        + _uint(128, "Settlement array offset")
        + _uint(2, "Settlement index count")
        + _uint(1, "Settlement YES index")
        + _uint(2, "Settlement NO index")
    )
    return "0x" + encoded.hex()


def decode_v3_position_id(position_id: str) -> tuple[str, int]:
    if not is_protocol_v3_position_id(position_id):
        raise AccountConnectionError("Settlement position is not protocol v3.")
    raw = int(position_id).to_bytes(32, "big")
    if raw[0] not in {1, 2, 3} or raw[-1] not in {0, 1}:
        raise AccountConnectionError("Protocol-v3 settlement position is invalid.")
    return "0x" + raw[:-1].hex(), raw[-1]


def router_redeem_calldata(position_id: str, amount_atomic: int) -> str:
    """Match SDK protocol-v2 Router redeem(bytes31,uint256,uint256)."""
    condition_id, outcome_index = decode_v3_position_id(position_id)
    condition_word = bytes.fromhex(condition_id[2:]) + bytes(1)
    selector = keccak(b"redeem(bytes31,uint256,uint256)")[:4]
    return "0x" + (
        selector + condition_word + _uint(outcome_index, "Settlement outcome")
        + _uint(amount_atomic, "Settlement amount")
    ).hex()


@dataclass(frozen=True, slots=True)
class SettlementCall:
    target: str
    data: str
    value: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "target", normalize_evm_address(self.target, "target"))
        if (
            not isinstance(self.data, str) or not self.data.startswith("0x")
            or len(self.data) < 10 or len(self.data) % 2
            or any(c not in "0123456789abcdefABCDEF" for c in self.data[2:])
            or self.value != 0
        ):
            raise AccountConnectionError("Settlement call is invalid.")
        object.__setattr__(self, "data", self.data.lower())

    def to_record(self) -> dict[str, Any]:
        return {"target": self.target, "value": str(self.value), "data": self.data}


@dataclass(frozen=True, slots=True)
class SettlementPlan:
    settlement_id: str
    discord_user_id: int
    profile_id: str
    owner_address: str
    wallet_address: str
    market_id: str
    condition_id: str
    token_id: str
    outcome: str
    amount_atomic: int
    protocol: str
    negative_risk: bool
    nonce: int
    created_at: int
    deadline: int
    idempotency_key: str
    calls: tuple[SettlementCall, ...]
    chain_id: int = 137

    def __post_init__(self) -> None:
        for field in ("owner_address", "wallet_address"):
            object.__setattr__(self, field, normalize_evm_address(getattr(self, field), field))
        condition_valid = (
            _HEX32.fullmatch(self.condition_id) is not None
            if self.protocol == "2"
            else _HEX31.fullmatch(self.condition_id) is not None
        )
        if (
            not _ID.fullmatch(self.settlement_id)
            or not _ID.fullmatch(self.idempotency_key)
            or self.discord_user_id <= 0 or not self.profile_id
            or not self.market_id or not self.token_id or not self.outcome
            or not 0 < self.amount_atomic <= _UINT256_MAX
            or self.protocol not in {"2", "3"} or not condition_valid
            or not isinstance(self.negative_risk, bool)
            or not 0 <= self.nonce <= _UINT256_MAX
            or self.created_at <= 0
            or self.deadline != self.created_at + _BATCH_LIFETIME_SECONDS
            or self.chain_id != 137 or self.owner_address == self.wallet_address
            or not 1 <= len(self.calls) <= 2
            or self.calls != self.expected_calls()
        ):
            raise AccountConnectionError("Settlement approval binding is invalid.")

    def expected_calls(self) -> tuple[SettlementCall, ...]:
        manifest = POLYMARKET_PRODUCTION_MANIFEST
        if self.protocol == "2":
            if is_protocol_v3_position_id(self.token_id):
                raise AccountConnectionError("Legacy settlement token routing changed.")
            target = (
                manifest.neg_risk_collateral_adapter
                if self.negative_risk else manifest.collateral_adapter
            )
            return (SettlementCall(target, ctf_redeem_calldata(self.condition_id)),)
        decoded_condition, _ = decode_v3_position_id(self.token_id)
        if decoded_condition != self.condition_id.lower():
            raise AccountConnectionError("Protocol-v3 condition identity changed.")
        return (SettlementCall(
            manifest.protocol_v2_router,
            router_redeem_calldata(self.token_id, self.amount_atomic),
        ),)

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(
            json.dumps(self.to_record(), sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    def typed_data(self) -> dict[str, Any]:
        return {
            "domain": {
                "name": "DepositWallet", "version": "1", "chainId": 137,
                "verifyingContract": self.wallet_address,
            },
            "types": {name: [dict(field) for field in fields] for name, fields in _BATCH_TYPES.items()},
            "primaryType": "Batch",
            "message": {
                "wallet": self.wallet_address, "nonce": str(self.nonce),
                "deadline": str(self.deadline),
                "calls": [call.to_record() for call in self.calls],
            },
        }

    def relayer_request(self, signature: str) -> dict[str, Any]:
        if not isinstance(signature, str) or re.fullmatch(r"0x[0-9a-fA-F]{130}", signature) is None:
            raise AccountConnectionError("Settlement owner signature is invalid.")
        return {
            "depositWalletParams": {
                "calls": [call.to_record() for call in self.calls],
                "deadline": str(self.deadline),
                "depositWallet": self.wallet_address,
            },
            "from": self.owner_address,
            "metadata": f"Redeem Polymarket position for market {self.market_id}",
            "nonce": str(self.nonce), "signature": signature.lower(),
            "to": POLYMARKET_PRODUCTION_MANIFEST.deposit_wallet_factory.lower(),
            "type": "WALLET",
        }

    def to_record(self) -> dict[str, Any]:
        result = asdict(self)
        result["calls"] = [call.to_record() for call in self.calls]
        return result

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> "SettlementPlan":
        try:
            values = dict(record)
            values["calls"] = tuple(
                SettlementCall(item["target"], item["data"], int(item["value"]))
                for item in values["calls"]
            )
            return cls(**values)
        except (KeyError, TypeError, ValueError) as exc:
            raise AccountConnectionError("Stored settlement is invalid.") from exc


@dataclass(frozen=True, slots=True)
class SettlementOperation:
    """Restart-safe public lifecycle; raw owner signatures never persist."""

    plan: SettlementPlan
    state: SettlementState = SettlementState.APPROVED
    owner_signature_digest: str | None = None
    transaction_id: str | None = None
    transaction_hash: str | None = None
    failure_digest: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.plan, SettlementPlan) or not isinstance(
            self.state, SettlementState
        ):
            raise AccountConnectionError("Settlement operation is invalid.")
        for value in (self.owner_signature_digest, self.failure_digest):
            if value is not None and re.fullmatch(r"[0-9a-f]{64}", value) is None:
                raise AccountConnectionError("Settlement operation digest is invalid.")
        if self.transaction_id is not None and (
            not isinstance(self.transaction_id, str)
            or not self.transaction_id or len(self.transaction_id) > 128
        ):
            raise AccountConnectionError("Settlement transaction ID is invalid.")
        if self.transaction_hash is not None and re.fullmatch(
            r"0x[0-9a-f]{64}", self.transaction_hash
        ) is None:
            raise AccountConnectionError("Settlement transaction hash is invalid.")
        if self.state is SettlementState.APPROVED and any((
            self.owner_signature_digest, self.transaction_id,
            self.transaction_hash, self.failure_digest,
        )):
            raise AccountConnectionError("Approved settlement contains results.")
        if self.state is SettlementState.SUBMITTING and not self.owner_signature_digest:
            raise AccountConnectionError("Submitting settlement lacks owner approval.")
        if self.state in {SettlementState.SUBMITTED, SettlementState.CONFIRMED} and (
            not self.owner_signature_digest or not self.transaction_id
        ):
            raise AccountConnectionError("Submitted settlement lacks identity.")
        if self.state is SettlementState.CONFIRMED and not self.transaction_hash:
            raise AccountConnectionError("Confirmed settlement lacks a transaction hash.")
        if self.state is SettlementState.FAILED and not self.failure_digest:
            raise AccountConnectionError("Failed settlement lacks a digest.")

    def begin_submission(self, signature: str, *, now: int) -> "SettlementOperation":
        if self.state is not SettlementState.APPROVED:
            raise AccountConnectionError("Settlement is not approved.")
        if now < self.plan.created_at or now >= self.plan.deadline:
            raise AccountConnectionError("Settlement approval expired.")
        self.plan.relayer_request(signature)
        return replace(
            self, state=SettlementState.SUBMITTING,
            owner_signature_digest=hashlib.sha256(
                bytes.fromhex(signature[2:])
            ).hexdigest(),
        )

    def recover_after_restart(self) -> "SettlementOperation":
        if self.state is SettlementState.SUBMITTING:
            return replace(self, state=SettlementState.UNKNOWN)
        return self

    def record_submission(self, response: Mapping[str, Any]) -> "SettlementOperation":
        if self.state is not SettlementState.SUBMITTING:
            raise AccountConnectionError("Settlement is not submitting.")
        transaction_id = response.get("transaction_id")
        transaction_hash = response.get("transaction_hash")
        if not isinstance(transaction_id, str) or not transaction_id or len(transaction_id) > 128:
            return replace(self, state=SettlementState.UNKNOWN)
        if transaction_hash is not None and (
            not isinstance(transaction_hash, str)
            or re.fullmatch(r"0x[0-9a-fA-F]{64}", transaction_hash) is None
        ):
            return replace(
                self, state=SettlementState.UNKNOWN,
                transaction_id=transaction_id,
            )
        return replace(
            self, state=SettlementState.SUBMITTED,
            transaction_id=transaction_id,
            transaction_hash=transaction_hash.lower() if transaction_hash else None,
        )

    def reconcile(self, response: Mapping[str, Any]) -> "SettlementOperation":
        if self.state not in {SettlementState.SUBMITTED, SettlementState.UNKNOWN}:
            raise AccountConnectionError("Settlement cannot be reconciled.")
        transaction_id = response.get("transaction_id")
        if not isinstance(transaction_id, str) or not transaction_id:
            raise AccountConnectionError("Settlement transaction identity is missing.")
        if self.transaction_id and transaction_id != self.transaction_id:
            raise AccountConnectionError("Settlement transaction identity changed.")
        if any((
            response.get("from") != self.plan.owner_address,
            response.get("to") != POLYMARKET_PRODUCTION_MANIFEST.deposit_wallet_factory.lower(),
            response.get("proxy_address") != self.plan.wallet_address,
            response.get("type") != "WALLET",
        )):
            raise AccountConnectionError("Settlement relayer identity changed.")
        state = response.get("state")
        if state in {"STATE_FAILED", "STATE_INVALID"}:
            reason = str(response.get("error_msg") or state)
            return replace(
                self, state=SettlementState.FAILED,
                transaction_id=transaction_id,
                failure_digest=hashlib.sha256(reason.encode("utf-8")).hexdigest(),
            )
        if state == "STATE_CONFIRMED":
            transaction_hash = response.get("transaction_hash") or self.transaction_hash
            if not isinstance(transaction_hash, str) or re.fullmatch(
                r"0x[0-9a-fA-F]{64}", transaction_hash
            ) is None:
                raise AccountConnectionError(
                    "Confirmed settlement lacks a transaction hash."
                )
            return replace(
                self, state=SettlementState.CONFIRMED,
                transaction_id=transaction_id,
                transaction_hash=transaction_hash.lower(),
            )
        if state not in {"STATE_NEW", "STATE_EXECUTED", "STATE_MINED"}:
            raise AccountConnectionError("Settlement relayer state is invalid.")
        return replace(
            self, state=SettlementState.SUBMITTED,
            transaction_id=transaction_id,
        )

    def to_record(self) -> dict[str, Any]:
        return {
            "plan": self.plan.to_record(), "state": self.state.value,
            "owner_signature_digest": self.owner_signature_digest,
            "transaction_id": self.transaction_id,
            "transaction_hash": self.transaction_hash,
            "failure_digest": self.failure_digest,
        }

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> "SettlementOperation":
        try:
            values = dict(record)
            values["plan"] = SettlementPlan.from_record(values["plan"])
            values["state"] = SettlementState(values["state"])
            return cls(**values)
        except (KeyError, TypeError, ValueError) as exc:
            raise AccountConnectionError("Stored settlement operation is invalid.") from exc
