"""Exact current-SDK settlement calls and immutable approval model."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
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
