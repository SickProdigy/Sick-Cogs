"""Non-executable design contract for official Deposit Wallet creation."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, replace
from enum import Enum
from typing import Any, Mapping

from .account_connection import AccountConnectionError, WalletType, normalize_evm_address
from .production_manifest import POLYMARKET_PRODUCTION_MANIFEST


FINGERPRINT = re.compile(r"^[0-9a-f]{64}$")
IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9_-]{32,128}$")
HEX_32 = re.compile(r"^0x[0-9a-f]{64}$")
RELAYER_REQUEST_TYPE = "WALLET-CREATE"
PLAN_LIFETIME_SECONDS = 5 * 60


class DepositWalletCreationState(str, Enum):
    APPROVED = "approved"
    SUBMITTING = "submitting"
    SUBMITTED = "submitted"
    CONFIRMED = "confirmed"
    FAILED = "failed"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class DepositWalletTargetEvidence:
    """Read-only proof that the current derived target is not yet deployed."""

    signer_address: str
    deposit_wallet_address: str
    block_number: int
    empty_code_hash: str
    evidence_digest: str
    chain_id: int = 137
    wallet_type: WalletType = WalletType.DEPOSIT_WALLET
    source: str = "polygon_predeployment_read"

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "signer_address",
            normalize_evm_address(self.signer_address, "signer_address"),
        )
        object.__setattr__(
            self, "deposit_wallet_address",
            normalize_evm_address(self.deposit_wallet_address, "deposit_wallet_address"),
        )
        if self.signer_address == self.deposit_wallet_address:
            raise AccountConnectionError("Deposit Wallet target must differ from its owner.")
        if self.block_number < 0 or not HEX_32.fullmatch(self.empty_code_hash):
            raise AccountConnectionError("Deposit Wallet target evidence is invalid.")
        if not FINGERPRINT.fullmatch(self.evidence_digest):
            raise AccountConnectionError("Deposit Wallet target digest is invalid.")
        if (
            self.chain_id != 137
            or self.wallet_type is not WalletType.DEPOSIT_WALLET
            or self.source != "polygon_predeployment_read"
        ):
            raise AccountConnectionError("Deposit Wallet target source is invalid.")


@dataclass(frozen=True, slots=True)
class DepositWalletCreationPlan:
    """Exact user-approved request boundary; submission remains code-disabled."""

    creation_id: str
    discord_user_id: int
    signer_address: str
    deposit_wallet_address: str
    idempotency_key: str
    owner_approval_fingerprint: str
    eligibility_fingerprint: str
    target_evidence_digest: str
    created_at: int
    expires_at: int
    state: DepositWalletCreationState = DepositWalletCreationState.APPROVED
    relayer_transaction_id: str | None = None
    transaction_hash: str | None = None
    failure_digest: str | None = None
    chain_id: int = 137
    request_type: str = RELAYER_REQUEST_TYPE
    request_to: str = POLYMARKET_PRODUCTION_MANIFEST.deposit_wallet_factory
    builder_auth_location: str = "server_only"
    user_controlled_approval: bool = True
    executable: bool = False

    def __post_init__(self) -> None:
        if not IDEMPOTENCY_KEY.fullmatch(self.creation_id):
            raise AccountConnectionError("Deposit Wallet creation ID is invalid.")
        if self.discord_user_id <= 0:
            raise AccountConnectionError("Deposit Wallet creation must bind a Discord user.")
        object.__setattr__(
            self, "signer_address",
            normalize_evm_address(self.signer_address, "signer_address"),
        )
        object.__setattr__(
            self, "deposit_wallet_address",
            normalize_evm_address(self.deposit_wallet_address, "deposit_wallet_address"),
        )
        object.__setattr__(
            self, "request_to", normalize_evm_address(self.request_to, "request_to")
        )
        if self.signer_address == self.deposit_wallet_address:
            raise AccountConnectionError("Deposit Wallet target must differ from its owner.")
        if not IDEMPOTENCY_KEY.fullmatch(self.idempotency_key):
            raise AccountConnectionError("Deposit Wallet idempotency key is invalid.")
        for value in (
            self.owner_approval_fingerprint,
            self.eligibility_fingerprint,
            self.target_evidence_digest,
        ):
            if not FINGERPRINT.fullmatch(value):
                raise AccountConnectionError("Deposit Wallet evidence is invalid.")
        if self.expires_at != self.created_at + PLAN_LIFETIME_SECONDS:
            raise AccountConnectionError("Deposit Wallet approval must expire after five minutes.")
        if not isinstance(self.state, DepositWalletCreationState):
            raise AccountConnectionError("Deposit Wallet creation state is invalid.")
        if (
            self.chain_id != POLYMARKET_PRODUCTION_MANIFEST.chain_id
            or self.request_type != RELAYER_REQUEST_TYPE
            or self.request_to != POLYMARKET_PRODUCTION_MANIFEST.deposit_wallet_factory.lower()
            or self.builder_auth_location != "server_only"
            or not self.user_controlled_approval
            or self.executable
        ):
            raise AccountConnectionError("Deposit Wallet creation boundary has drifted.")
        if self.state is DepositWalletCreationState.APPROVED and any((
            self.relayer_transaction_id, self.transaction_hash, self.failure_digest,
        )):
            raise AccountConnectionError("Approved creation cannot contain provider results.")
        if self.state is DepositWalletCreationState.SUBMITTED and not self.relayer_transaction_id:
            raise AccountConnectionError("Submitted creation requires a relayer transaction ID.")
        if self.state is DepositWalletCreationState.CONFIRMED and (
            not self.relayer_transaction_id or not self.transaction_hash
        ):
            raise AccountConnectionError("Confirmed creation requires both public identifiers.")
        if self.state is DepositWalletCreationState.FAILED and not self.failure_digest:
            raise AccountConnectionError("Failed creation requires a failure digest.")

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(
            json.dumps(self.to_record(), sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    def begin_submission(self, *, now: int) -> "DepositWalletCreationPlan":
        if self.state is not DepositWalletCreationState.APPROVED:
            raise AccountConnectionError("Only an approved creation can begin submission.")
        if now < self.created_at or now >= self.expires_at:
            raise AccountConnectionError("Deposit Wallet creation approval is not current.")
        if self.executable:
            raise AccountConnectionError("Deposit Wallet execution must remain disabled.")
        return replace(self, state=DepositWalletCreationState.SUBMITTING)

    def recover_after_restart(self) -> "DepositWalletCreationPlan":
        if self.state is DepositWalletCreationState.SUBMITTING:
            return replace(self, state=DepositWalletCreationState.UNKNOWN)
        return self

    def record_submission(self, response: Mapping[str, Any]) -> "DepositWalletCreationPlan":
        if self.state is not DepositWalletCreationState.SUBMITTING:
            raise AccountConnectionError("Deposit Wallet creation is not submitting.")
        transaction_id = response.get("transaction_id")
        transaction_hash = response.get("transaction_hash")
        if not isinstance(transaction_id, str) or not transaction_id or len(transaction_id) > 128:
            return replace(self, state=DepositWalletCreationState.UNKNOWN)
        if transaction_hash is not None and (
            not isinstance(transaction_hash, str)
            or re.fullmatch(r"0x[0-9a-fA-F]{64}", transaction_hash) is None
        ):
            return replace(
                self, state=DepositWalletCreationState.UNKNOWN,
                relayer_transaction_id=transaction_id,
            )
        return replace(
            self, state=DepositWalletCreationState.SUBMITTED,
            relayer_transaction_id=transaction_id,
            transaction_hash=transaction_hash.lower() if transaction_hash else None,
        )

    def reconcile(self, response: Mapping[str, Any]) -> "DepositWalletCreationPlan":
        if self.state not in {
            DepositWalletCreationState.SUBMITTED, DepositWalletCreationState.UNKNOWN,
        }:
            raise AccountConnectionError("Deposit Wallet creation cannot be reconciled.")
        state = str(response.get("state") or "")
        transaction_id = response.get("transaction_id")
        if (
            not isinstance(transaction_id, str)
            or not transaction_id
            or len(transaction_id) > 128
        ):
            raise AccountConnectionError("Relayer transaction identity is missing.")
        if self.relayer_transaction_id and transaction_id != self.relayer_transaction_id:
            raise AccountConnectionError("Relayer transaction identity changed.")
        if any((
            response.get("from") != self.signer_address,
            response.get("to") != self.request_to,
            response.get("type") != self.request_type,
            response.get("proxy_address") != self.deposit_wallet_address,
        )):
            raise AccountConnectionError("Relayer deployment identity changed.")
        if state == "STATE_CONFIRMED":
            transaction_hash = response.get("transaction_hash") or self.transaction_hash
            if not isinstance(transaction_hash, str) or re.fullmatch(
                r"0x[0-9a-fA-F]{64}", transaction_hash
            ) is None:
                raise AccountConnectionError("Confirmed deployment lacks a transaction hash.")
            return replace(
                self, state=DepositWalletCreationState.CONFIRMED,
                relayer_transaction_id=transaction_id,
                transaction_hash=transaction_hash.lower(),
            )
        if state in {"STATE_FAILED", "STATE_INVALID"}:
            failure = str(response.get("error_msg") or state)
            return replace(
                self, state=DepositWalletCreationState.FAILED,
                relayer_transaction_id=transaction_id,
                failure_digest=hashlib.sha256(failure.encode("utf-8")).hexdigest(),
            )
        return replace(
            self, state=DepositWalletCreationState.SUBMITTED,
            relayer_transaction_id=transaction_id,
        )

    def relayer_request(self) -> dict[str, str]:
        return {
            "from": self.signer_address,
            "to": self.request_to,
            "type": self.request_type,
        }

    def to_record(self) -> dict[str, Any]:
        record = asdict(self)
        record["state"] = self.state.value
        return record


    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> "DepositWalletCreationPlan":
        try:
            values = dict(record)
            values["state"] = DepositWalletCreationState(values["state"])
            return cls(**values)
        except (KeyError, TypeError, ValueError) as exc:
            raise AccountConnectionError(
                "Stored Deposit Wallet creation is invalid."
            ) from exc
