"""Fail-closed models for audited Clanker Base mainnet operations.

This module validates immutable candidates only. It deliberately exposes no submission path.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


MANIFEST_PATH = Path(__file__).with_name("contracts") / "clanker-v4-base-mainnet-candidate.json"
MAINNET_SUBMISSION_ENABLED = False
ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
HEX_DATA_RE = re.compile(r"^0x(?:[0-9a-fA-F]{2})+$")
ID_RE = re.compile(r"^[a-zA-Z0-9._-]{1,128}$")
MAX_NATIVE_VALUE_WEI = 10**18
MAX_GAS_LIMIT = 10_000_000
MAX_FEE_WEI = 10**16


def _manifest() -> dict[str, Any]:
    value = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    if (value.get("status") != "audit-candidate-read-only"
            or value.get("executionEnabled") is not False
            or value.get("chainId") != 8453):
        raise RuntimeError("Clanker mainnet audit manifest is not fail-closed.")
    return value


def _address(value: str, label: str) -> str:
    normalized = str(value or "").strip().lower()
    if not ADDRESS_RE.fullmatch(normalized) or int(normalized[2:], 16) == 0:
        raise ValueError(f"{label} must be a nonzero EVM address.")
    return normalized


def _data(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if not HEX_DATA_RE.fullmatch(normalized) or len(normalized) < 10:
        raise ValueError("Mainnet calldata must contain an ABI selector.")
    return normalized


def _target(manifest: dict[str, Any], target_name: str) -> str:
    if target_name == "factory":
        return str(manifest["factory"]["address"]).lower()
    return str(manifest["contracts"][target_name]["address"]).lower()


@dataclass(frozen=True, slots=True)
class MainnetOperationIntent:
    """One immutable, expiring transaction candidate under the audited allowlist."""

    operation_id: str
    kind: str
    requester_id: int
    signer: str
    to: str
    value: int
    data: str
    created_at: int
    expires_at: int
    gas_limit: int
    max_fee_wei: int
    recipients: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        manifest = _manifest()
        operation = manifest["operationAllowlist"].get(self.kind)
        if not operation or self.kind == "creatorBuyIn":
            raise ValueError("That Clanker mainnet operation is not independently callable.")
        if not ID_RE.fullmatch(str(self.operation_id or "")):
            raise ValueError("Mainnet operation ID is invalid.")
        if not isinstance(self.requester_id, int) or isinstance(self.requester_id, bool) or self.requester_id <= 0:
            raise ValueError("Mainnet requester ID must be positive.")
        object.__setattr__(self, "signer", _address(self.signer, "Mainnet signer"))
        object.__setattr__(self, "to", _address(self.to, "Mainnet target"))
        object.__setattr__(self, "data", _data(self.data))
        object.__setattr__(self, "recipients", tuple(_address(item, "Mainnet recipient") for item in self.recipients))
        expected_target = _target(manifest, str(operation["target"]))
        if self.to != expected_target:
            raise ValueError("Mainnet target does not match the audited operation.")
        if self.data[:10] != str(operation["selector"]).lower():
            raise ValueError("Mainnet selector does not match the audited operation.")
        if not isinstance(self.value, int) or isinstance(self.value, bool) or self.value < 0:
            raise ValueError("Mainnet native value is invalid.")
        if operation["mutability"] != "payable" and self.value != 0:
            raise ValueError("This mainnet operation cannot transfer native value.")
        if self.value > MAX_NATIVE_VALUE_WEI:
            raise ValueError("Mainnet native value exceeds the reviewed ceiling.")
        if self.created_at <= 0 or self.expires_at <= self.created_at or self.expires_at - self.created_at > 900:
            raise ValueError("Mainnet operation expiry is invalid.")
        if not 0 < self.gas_limit <= MAX_GAS_LIMIT:
            raise ValueError("Mainnet gas limit exceeds policy.")
        if not 0 < self.max_fee_wei <= MAX_FEE_WEI:
            raise ValueError("Mainnet maximum fee exceeds policy.")

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "operation_id": self.operation_id,
            "kind": self.kind,
            "chain_id": 8453,
            "requester_id": str(self.requester_id),
            "signer": self.signer,
            "to": self.to,
            "value": str(self.value),
            "data": self.data,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "gas_limit": self.gas_limit,
            "max_fee_wei": str(self.max_fee_wei),
            "recipients": list(self.recipients),
        }

    @property
    def fingerprint(self) -> str:
        encoded = json.dumps(
            self.canonical_payload(), sort_keys=True, separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")
        return "0x" + hashlib.sha256(encoded).hexdigest()


def validate_mainnet_candidate(
    intent: MainnetOperationIntent,
    *,
    chain_id: int,
    signer: str,
    to: str,
    value: int,
    data: str,
    gas_limit: int,
    max_fee_wei: int,
    now: int | None = None,
) -> None:
    """Reject any provider candidate differing from the immutable reviewed intent."""

    current = int(time.time()) if now is None else int(now)
    if current < intent.created_at or current >= intent.expires_at:
        raise ValueError("Mainnet operation is not inside its approval window.")
    supplied = (
        chain_id,
        _address(signer, "Mainnet signer"),
        _address(to, "Mainnet target"),
        value,
        _data(data),
        gas_limit,
        max_fee_wei,
    )
    expected = (
        8453,
        intent.signer,
        intent.to,
        intent.value,
        intent.data,
        intent.gas_limit,
        intent.max_fee_wei,
    )
    if supplied != expected:
        raise ValueError("Mainnet transaction candidate does not match the immutable intent.")


def authorize_mainnet_submission(intent: MainnetOperationIntent) -> None:
    """Keep all real submission unavailable until a later explicit release gate."""

    del intent
    if not MAINNET_SUBMISSION_ENABLED:
        raise RuntimeError("Clanker Base mainnet submission is disabled.")
    raise RuntimeError("No Clanker Base mainnet submitter has been implemented.")
