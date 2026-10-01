"""Fail-closed recovery and verification for Clanker Base mainnet operations."""
from dataclasses import asdict, dataclass, replace
import re
from typing import Any, Mapping
from .mainnet_operations import MainnetOperationIntent, _manifest

HASH_RE = re.compile(r"^0x[0-9a-f]{64}$")
TRANSITIONS = {
    "prepared": {"processing"},
    "processing": {"submitted", "uncertain"},
    "submitted": {"submitted", "uncertain", "timed_out", "confirmed", "failed", "dropped", "replaced"},
    "uncertain": {"processing", "submitted", "timed_out", "confirmed", "failed", "dropped", "replaced"},
    "timed_out": {"processing", "submitted", "uncertain", "confirmed", "failed", "dropped", "replaced"},
    "confirmed": set(), "failed": set(), "dropped": set(), "replaced": set(),
}

def _hash(value: Any, label: str, required: bool = False) -> str | None:
    value = str(value or "").lower()
    if not value and not required:
        return None
    if not HASH_RE.fullmatch(value):
        raise ValueError(f"Invalid Clanker mainnet {label} hash.")
    return value

@dataclass(frozen=True, slots=True)
class MainnetOperationLifecycle:
    intent_fingerprint: str
    operation_id: str
    attempt_id: str
    status: str
    created_at: int
    updated_at: int
    user_operation_hash: str | None = None
    transaction_hash: str | None = None
    replacement_transaction_hash: str | None = None
    block_number: int | None = None
    failure_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "MainnetOperationLifecycle":
        if data["status"] not in TRANSITIONS:
            raise ValueError("Invalid Clanker lifecycle status.")
        return cls(
            _hash(data["intent_fingerprint"], "intent", True),
            str(data["operation_id"]), str(data["attempt_id"]), str(data["status"]),
            int(data["created_at"]), int(data["updated_at"]),
            _hash(data.get("user_operation_hash"), "operation"),
            _hash(data.get("transaction_hash"), "transaction"),
            _hash(data.get("replacement_transaction_hash"), "replacement"),
            int(data["block_number"]) if data.get("block_number") is not None else None,
            str(data["failure_reason"]) if data.get("failure_reason") else None,
        )

def create_mainnet_lifecycle(intent: MainnetOperationIntent, attempt_id: str, *, now: int) -> MainnetOperationLifecycle:
    if not attempt_id or len(str(attempt_id)) > 128:
        raise ValueError("Invalid mainnet attempt ID.")
    return MainnetOperationLifecycle(
        intent.fingerprint, intent.operation_id, str(attempt_id), "prepared", int(now), int(now)
    )

def assert_same_mainnet_lifecycle(stored: MainnetOperationLifecycle, intent: MainnetOperationIntent, attempt_id: str) -> None:
    if (stored.intent_fingerprint, stored.operation_id, stored.attempt_id) != (
        intent.fingerprint, intent.operation_id, str(attempt_id)
    ):
        raise ValueError("The Clanker mainnet lifecycle binding changed.")

def transition_mainnet_lifecycle(current: MainnetOperationLifecycle, status: str, *, now: int, **evidence: Any) -> MainnetOperationLifecycle:
    if status not in TRANSITIONS[current.status]:
        raise ValueError("Invalid Clanker lifecycle transition.")
    operation_hash = _hash(evidence.get("user_operation_hash"), "operation") or current.user_operation_hash
    transaction_hash = _hash(evidence.get("transaction_hash"), "transaction") or current.transaction_hash
    replacement_hash = _hash(evidence.get("replacement_transaction_hash"), "replacement") or current.replacement_transaction_hash
    if current.user_operation_hash and operation_hash != current.user_operation_hash:
        raise ValueError("Provider operation hash changed.")
    if current.transaction_hash and transaction_hash != current.transaction_hash:
        raise ValueError("Transaction hash changed.")
    block_number = evidence.get("block_number", current.block_number)
    reason = evidence.get("failure_reason") or current.failure_reason
    if status in {"submitted", "confirmed"} and not (operation_hash or transaction_hash):
        raise ValueError("Provider identifier required.")
    if status == "confirmed" and (not transaction_hash or block_number is None):
        raise ValueError("Confirmed receipt required.")
    if status == "replaced" and not replacement_hash:
        raise ValueError("Replacement transaction required.")
    if status in {"failed", "dropped", "replaced"} and not reason:
        raise ValueError("Terminal reason required.")
    if reason and len(str(reason)) > 240:
        raise ValueError("Terminal reason is too long.")
    return replace(
        current, status=status, updated_at=int(now),
        user_operation_hash=operation_hash, transaction_hash=transaction_hash,
        replacement_transaction_hash=replacement_hash,
        block_number=int(block_number) if block_number is not None else None,
        failure_reason=str(reason) if reason else None,
    )

def _snapshot(intent: MainnetOperationIntent, lifecycle: MainnetOperationLifecycle, evidence: Mapping[str, Any]) -> dict[str, Any]:
    if int(evidence.get("chain_id", -1)) != 8453 or evidence.get("receipt_success") is not True:
        raise ValueError("Not a successful Base mainnet receipt.")
    transaction_hash = _hash(evidence.get("transaction_hash"), "transaction", True)
    if transaction_hash != lifecycle.transaction_hash:
        raise ValueError("Receipt does not match lifecycle.")
    exact = (
        str(evidence.get("signer", "")).lower(), str(evidence.get("to", "")).lower(),
        int(evidence.get("value", -1)), str(evidence.get("data", "")).lower(),
    )
    if exact != (intent.signer, intent.to, intent.value, intent.data):
        raise ValueError("Receipt does not match reviewed intent.")
    block_number = int(evidence.get("block_number", -1))
    if block_number != lifecycle.block_number:
        raise ValueError("Receipt block changed.")
    latest_block_number = int(evidence.get("latest_block_number", -1))
    confirmation_depth = latest_block_number - block_number + 1
    if confirmation_depth < 12:
        raise ValueError("Clanker mainnet evidence has not reached 12-block finality.")
    manifest = _manifest()
    operation = manifest["operationAllowlist"][intent.kind]
    target_name = operation["target"]
    pinned_hash = (manifest["factory"] if target_name == "factory" else manifest["contracts"][target_name])["runtimeCodeSha256"]
    expected_runtime_hash = "0x" + str(pinned_hash).removeprefix("0x").lower()
    if _hash(evidence.get("target_runtime_code_hash"), "target runtime", True) != expected_runtime_hash:
        raise ValueError("Target runtime code does not match the audited manifest.")
    result = {
        "chain_id": 8453, "transaction_hash": transaction_hash,
        "block_number": block_number, "block_hash": _hash(evidence.get("block_hash"), "block", True),
        "latest_block_number": latest_block_number, "confirmation_depth": confirmation_depth,
        "signer": intent.signer, "to": intent.to, "value": str(intent.value), "data": intent.data,
    }
    result["target_runtime_code_hash"] = expected_runtime_hash
    topic = operation.get("successTopic")
    if topic:
        if _hash(evidence.get("success_topic"), "success event", True) != topic.lower():
            raise ValueError("Expected success event missing.")
        result["success_topic"] = topic.lower()
    if intent.kind == "launch":
        token_address = str(evidence.get("token_address") or "").lower()
        token_admin = str(evidence.get("token_admin") or "").lower()
        if not re.fullmatch(r"0x[0-9a-f]{40}", token_address) or int(token_address[2:], 16) == 0:
            raise ValueError("Created Clanker token address is invalid.")
        launch = intent.launch_config
        if launch is None or token_admin != launch.token_admin:
            raise ValueError("Created Clanker token administrator changed.")
        if str(evidence.get("name")) != launch.name or str(evidence.get("symbol")) != launch.symbol:
            raise ValueError("Created Clanker token metadata changed.")
        if int(evidence.get("decimals", -1)) != 18 or int(evidence.get("total_supply_atomic", -1)) != launch.supply_tokens * 10**18:
            raise ValueError("Created Clanker token supply changed.")
        expected_rewards = [item.to_dict() for item in launch.rewards]
        if evidence.get("rewards") != expected_rewards:
            raise ValueError("Created Clanker reward or platform attribution changed.")
        expected_vault = launch.vault.to_dict() if launch.vault else None
        if evidence.get("vault") != expected_vault:
            raise ValueError("Created Clanker vault configuration changed.")
        expected_airdrop = launch.airdrop.to_dict() if launch.airdrop else None
        if evidence.get("airdrop") != expected_airdrop:
            raise ValueError("Created Clanker airdrop configuration changed.")
        token_code_sha256 = _hash(evidence.get("token_code_sha256"), "token runtime", True)
        result.update(
            token_address=token_address, token_admin=token_admin, name=launch.name,
            symbol=launch.symbol, decimals=18,
            total_supply_atomic=str(launch.supply_tokens * 10**18),
            rewards=expected_rewards, vault=expected_vault, airdrop=expected_airdrop,
            token_code_sha256=token_code_sha256,
        )
    return result

def verify_mainnet_operation_evidence(
    intent: MainnetOperationIntent, lifecycle: MainnetOperationLifecycle,
    primary: Mapping[str, Any], secondary: Mapping[str, Any],
) -> dict[str, Any]:
    if lifecycle.status != "confirmed":
        raise ValueError("Confirmed lifecycle required.")
    assert_same_mainnet_lifecycle(lifecycle, intent, lifecycle.attempt_id)
    first, second = _snapshot(intent, lifecycle, primary), _snapshot(intent, lifecycle, secondary)
    if first != second:
        raise ValueError("Independent RPC verification results disagree.")
    return {
        **first, "intent_fingerprint": intent.fingerprint, "attempt_id": lifecycle.attempt_id,
        "independent_rpc_verifications": 2, "verified": True,
    }
