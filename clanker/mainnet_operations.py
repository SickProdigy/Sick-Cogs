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

from .models import ClankerLaunchIntent
from .operation import clanker_deployment_calldata


MANIFEST_PATH = Path(__file__).with_name("contracts") / "clanker-v4-base-mainnet-candidate.json"
MAINNET_SUBMISSION_ENABLED = False
ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
HEX_DATA_RE = re.compile(r"^0x(?:[0-9a-fA-F]{2})+$")
ID_RE = re.compile(r"^[a-zA-Z0-9._-]{1,128}$")
MAX_NATIVE_VALUE_WEI = 10**18
MAX_GAS_LIMIT = 10_000_000
DEFAULT_LAUNCH_GAS_LIMIT = 8_000_000


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


def _word(value: int) -> str:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0 or value >= 1 << 256:
        raise ValueError("Mainnet ABI integer is invalid.")
    return value.to_bytes(32, "big").hex()


def _address_word(value: str, label: str) -> str:
    return _address(value, label)[2:].rjust(64, "0")


def _proof_word(value: str) -> str:
    normalized = str(value or "").lower()
    if not re.fullmatch(r"0x[0-9a-f]{64}", normalized):
        raise ValueError("Airdrop proof entries must be bytes32 values.")
    return normalized[2:]


def _mainnet_launch_calldata(manifest: dict[str, Any], launch: ClankerLaunchIntent) -> str:
    contracts = manifest["contracts"]
    return clanker_deployment_calldata(
        launch, chain_id=8453,
        locker=contracts["locker"]["address"],
        vault=contracts["vault"]["address"],
        airdrop=contracts["airdrop"]["address"],
        devbuy=contracts["devbuy"]["address"],
        mev_module=contracts["mevModuleV2"]["address"],
        static_fee_hook_v2=contracts["feeStaticHookV2"]["address"],
    ).lower()


def _semantic_calldata(
    kind: str, selector: str, *, token: str | None, fee_owner: str | None,
    recipient: str | None, allocated_amount: int | None, proof: tuple[str, ...],
    launch_config: ClankerLaunchIntent | None,
) -> str | None:
    if kind == "launch":
        if launch_config is None:
            raise ValueError("Mainnet launch semantics are required.")
        return _mainnet_launch_calldata(_manifest(), launch_config)
    if not token:
        raise ValueError("This mainnet operation requires a token address.")
    token_word = _address_word(token, "Mainnet token")
    if kind in {"rewardCollection", "rewardConfiguration", "vaultDiscovery", "vaultClaim"}:
        return selector + token_word
    if kind in {"rewardDiscovery", "treasuryClaim"}:
        if not fee_owner:
            raise ValueError("This mainnet operation requires a fee owner.")
        return selector + _address_word(fee_owner, "Mainnet fee owner") + token_word
    if kind in {"airdropDiscovery", "airdropClaim"}:
        if not recipient:
            raise ValueError("This mainnet operation requires an airdrop recipient.")
        if allocated_amount is None:
            raise ValueError("This mainnet operation requires an allocated amount.")
        head = token_word + _address_word(recipient, "Airdrop recipient") + _word(allocated_amount)
        if kind == "airdropDiscovery":
            return selector + head
        proof_data = _word(len(proof)) + "".join(_proof_word(item) for item in proof)
        return selector + head + _word(128) + proof_data
    raise ValueError("That mainnet operation has no semantic calldata policy.")


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
    token: str | None = None
    fee_owner: str | None = None
    allocated_amount: int | None = None
    proof: tuple[str, ...] = ()
    launch_config: ClankerLaunchIntent | None = None

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
        object.__setattr__(self, "token", _address(self.token, "Mainnet token") if self.token else None)
        object.__setattr__(self, "fee_owner", _address(self.fee_owner, "Mainnet fee owner") if self.fee_owner else None)
        object.__setattr__(self, "proof", tuple(str(item).lower() for item in self.proof))
        if self.kind == "launch":
            launch = self.launch_config
            if launch is None:
                raise ValueError("Mainnet launch semantics are required.")
            if (self.operation_id != launch.launch_id or self.requester_id != launch.requester_id
                    or self.signer != launch.token_admin or self.value != launch.expected_native_value_wei
                    or self.created_at != launch.created_at or self.expires_at != launch.expires_at):
                raise ValueError("Mainnet launch identity does not match its immutable semantics.")
            expected_recipients = tuple(item.recipient for item in launch.rewards)
            if self.recipients != expected_recipients:
                raise ValueError("Mainnet launch recipients do not match reward semantics.")
        elif self.launch_config is not None:
            raise ValueError("Only a launch operation may carry launch semantics.")
        expected_target = _target(manifest, str(operation["target"]))
        if self.to != expected_target:
            raise ValueError("Mainnet target does not match the audited operation.")
        if self.data[:10] != str(operation["selector"]).lower():
            raise ValueError("Mainnet selector does not match the audited operation.")
        semantic_recipient = self.recipients[0] if len(self.recipients) == 1 else None
        if self.kind == "treasuryClaim" and self.recipients != (self.fee_owner,):
            raise ValueError("Treasury claim recipient must equal the recorded fee owner.")
        expected_data = _semantic_calldata(
            self.kind, str(operation["selector"]).lower(), token=self.token,
            fee_owner=self.fee_owner, recipient=semantic_recipient,
            allocated_amount=self.allocated_amount, proof=self.proof,
            launch_config=self.launch_config,
        )
        if expected_data is not None and self.data != expected_data:
            raise ValueError("Mainnet calldata arguments do not match the recorded operation fields.")
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
        if self.max_fee_wei <= 0:
            raise ValueError("Mainnet reapproval threshold must be positive.")

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
            "token": self.token,
            "fee_owner": self.fee_owner,
            "allocated_amount": str(self.allocated_amount) if self.allocated_amount is not None else None,
            "proof": list(self.proof),
            "launch_payload_hash": self.launch_config.payload_hash if self.launch_config else None,
        }

    @property
    def fingerprint(self) -> str:
        encoded = json.dumps(
            self.canonical_payload(), sort_keys=True, separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")
        return "0x" + hashlib.sha256(encoded).hexdigest()


def build_mainnet_launch_operation(
    launch: ClankerLaunchIntent, *, gas_limit: int, max_fee_wei: int,
) -> MainnetOperationIntent:
    """Convert one Base-mainnet semantic launch into its audited operation."""

    if launch.network != "base-mainnet" or launch.chain_id != 8453:
        raise ValueError("A Base mainnet launch intent is required.")
    manifest = _manifest()
    return MainnetOperationIntent(
        operation_id=launch.launch_id, kind="launch",
        requester_id=launch.requester_id, signer=launch.token_admin,
        to=str(manifest["factory"]["address"]),
        value=launch.expected_native_value_wei,
        data=_mainnet_launch_calldata(manifest, launch),
        created_at=launch.created_at, expires_at=launch.expires_at,
        gas_limit=int(gas_limit), max_fee_wei=int(max_fee_wei),
        recipients=tuple(item.recipient for item in launch.rewards),
        launch_config=launch,
    )


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


def revalidate_mainnet_pre_submission(
    intent: MainnetOperationIntent, *, chain_id: int, signer: str,
    to: str, value: int, data: str, gas_limit: int, max_fee_wei: int,
    live_target_runtime_sha256: str, authorization_active: bool,
    signer_balance_wei: int, operation_state: str,
    quoted_gas_limit: int, quoted_max_fee_wei: int, now: int | None = None,
) -> dict[str, Any]:
    """Recheck live bindings immediately before any future provider call."""
    validate_mainnet_candidate(
        intent, chain_id=chain_id, signer=signer, to=to, value=value, data=data,
        gas_limit=gas_limit, max_fee_wei=max_fee_wei, now=now,
    )
    manifest = _manifest()
    operation = manifest["operationAllowlist"][intent.kind]
    target_name = operation["target"]
    target = manifest["factory"] if target_name == "factory" else manifest["contracts"][target_name]
    expected_hash = "0x" + str(target["runtimeCodeSha256"]).removeprefix("0x").lower()
    if str(live_target_runtime_sha256 or "").lower() != expected_hash:
        raise ValueError("The live Clanker target runtime does not match the audited manifest.")
    if authorization_active is not True:
        raise ValueError("The protected wallet authorization is not active.")
    if operation_state != "not-created":
        raise ValueError("A provider operation or public transaction already exists.")
    if quoted_gas_limit != intent.gas_limit or quoted_max_fee_wei != intent.max_fee_wei:
        raise ValueError("The live Clanker gas quote changed after approval.")
    required_balance = intent.value + intent.max_fee_wei
    if int(signer_balance_wei) < required_balance:
        raise ValueError("The signer balance cannot cover value plus the approved reapproval threshold.")
    return {
        "intent_fingerprint": intent.fingerprint,
        "target_runtime_sha256": expected_hash,
        "operation_state": "not-created",
        "required_balance_wei": required_balance,
        "approval_expires_at": intent.expires_at,
    }
