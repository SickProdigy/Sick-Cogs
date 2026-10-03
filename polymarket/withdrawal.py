"""Immutable approval and restart-safe lifecycle for Polymarket withdrawals."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import hashlib
import re
from typing import Any, Mapping

from .account_connection import AccountConnectionError
from .bridge import BridgeWithdrawalPlan
from .production_manifest import POLYMARKET_PRODUCTION_MANIFEST
from .security_policy import EligibilityAttestation
from .settlement import SettlementState
from .trade_confirmation import TradeConfirmation, TradeConfirmationError

_ID = re.compile(r"^[A-Za-z0-9_-]{32,128}$")


@dataclass(frozen=True, slots=True)
class WithdrawalApprovalRequest:
    request_id: str
    plan: BridgeWithdrawalPlan
    eligibility: EligibilityAttestation
    confirmation: TradeConfirmation

    def __post_init__(self) -> None:
        if (
            not isinstance(self.request_id, str) or _ID.fullmatch(self.request_id) is None
            or not isinstance(self.plan, BridgeWithdrawalPlan)
            or not isinstance(self.eligibility, EligibilityAttestation)
            or not isinstance(self.confirmation, TradeConfirmation)
            or self.eligibility.discord_user_id != self.plan.discord_user_id
            or self.eligibility.blocked
            or self.eligibility.checked_at > self.plan.created_at
            or self.eligibility.expires_at < self.confirmation.expires_at
            or self.confirmation.requester_id != self.plan.discord_user_id
            or self.confirmation.order_fingerprint != self.plan.fingerprint
            or self.confirmation.created_at != self.plan.created_at
            or self.confirmation.expires_at > self.plan.deadline
        ):
            raise TradeConfirmationError("Withdrawal approval bindings disagree.")

    @classmethod
    def create(
        cls, *, request_id: str, plan: BridgeWithdrawalPlan,
        eligibility: EligibilityAttestation, final_confirmation_required: bool,
    ) -> "WithdrawalApprovalRequest":
        expires_at = min(plan.created_at + 120, plan.deadline, eligibility.expires_at)
        return cls(
            request_id, plan, eligibility,
            TradeConfirmation(
                requester_id=plan.discord_user_id,
                order_fingerprint=plan.fingerprint,
                final_confirmation_required=final_confirmation_required,
                created_at=plan.created_at, expires_at=expires_at,
            ),
        )

    def approve_primary(self, *, requester_id: int, now: int) -> "WithdrawalApprovalRequest":
        return replace(self, confirmation=self.confirmation.approve_primary(
            requester_id=requester_id, order_fingerprint=self.plan.fingerprint, now=now,
        ))

    def decide_final(
        self, approved: bool, *, requester_id: int, now: int,
    ) -> "WithdrawalApprovalRequest":
        return replace(self, confirmation=self.confirmation.decide_final(
            requester_id=requester_id, order_fingerprint=self.plan.fingerprint,
            approved=approved, now=now,
        ))

    def require_approved(self, *, requester_id: int, now: int) -> None:
        self.eligibility.require_current(discord_user_id=requester_id, now=now)
        self.confirmation.require_approved(
            requester_id=requester_id, order_fingerprint=self.plan.fingerprint, now=now,
        )

    def to_record(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id, "plan": self.plan.to_record(),
            "eligibility": asdict(self.eligibility),
            "confirmation": self.confirmation.to_record(),
        }

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> "WithdrawalApprovalRequest":
        if not isinstance(record, Mapping) or set(record) != {
            "request_id", "plan", "eligibility", "confirmation"
        }:
            raise TradeConfirmationError("Stored withdrawal request has an invalid shape.")
        try:
            return cls(
                request_id=str(record["request_id"]),
                plan=BridgeWithdrawalPlan.from_record(record["plan"]),
                eligibility=EligibilityAttestation(**dict(record["eligibility"])),
                confirmation=TradeConfirmation.from_record(record["confirmation"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise TradeConfirmationError("Stored withdrawal request is invalid.") from exc


@dataclass(frozen=True, slots=True)
class WithdrawalOperation:
    """Restart-safe lifecycle that never persists the raw owner signature."""

    plan: BridgeWithdrawalPlan
    state: SettlementState = SettlementState.APPROVED
    owner_signature_digest: str | None = None
    transaction_id: str | None = None
    transaction_hash: str | None = None
    failure_digest: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.plan, BridgeWithdrawalPlan) or not isinstance(self.state, SettlementState):
            raise AccountConnectionError("Withdrawal operation is invalid.")
        for value in (self.owner_signature_digest, self.failure_digest):
            if value is not None and re.fullmatch(r"[0-9a-f]{64}", value) is None:
                raise AccountConnectionError("Withdrawal operation digest is invalid.")
        if self.transaction_id is not None and (
            not isinstance(self.transaction_id, str) or not self.transaction_id
            or len(self.transaction_id) > 128
        ):
            raise AccountConnectionError("Withdrawal transaction ID is invalid.")
        if self.transaction_hash is not None and re.fullmatch(
            r"0x[0-9a-f]{64}", self.transaction_hash
        ) is None:
            raise AccountConnectionError("Withdrawal transaction hash is invalid.")
        if self.state is SettlementState.APPROVED and any((
            self.owner_signature_digest, self.transaction_id,
            self.transaction_hash, self.failure_digest,
        )):
            raise AccountConnectionError("Approved withdrawal contains results.")
        if self.state is SettlementState.SUBMITTING and not self.owner_signature_digest:
            raise AccountConnectionError("Submitting withdrawal lacks owner approval.")
        if self.state in {SettlementState.SUBMITTED, SettlementState.CONFIRMED} and (
            not self.owner_signature_digest or not self.transaction_id
        ):
            raise AccountConnectionError("Submitted withdrawal lacks identity.")
        if self.state is SettlementState.CONFIRMED and not self.transaction_hash:
            raise AccountConnectionError("Confirmed withdrawal lacks a transaction hash.")
        if self.state is SettlementState.FAILED and not self.failure_digest:
            raise AccountConnectionError("Failed withdrawal lacks a digest.")

    def begin_submission(self, signature: str, *, now: int) -> "WithdrawalOperation":
        if self.state is not SettlementState.APPROVED:
            raise AccountConnectionError("Withdrawal is not approved.")
        if now < self.plan.created_at or now >= self.plan.deadline:
            raise AccountConnectionError("Withdrawal approval expired.")
        self.plan.relayer_request(signature)
        return replace(
            self, state=SettlementState.SUBMITTING,
            owner_signature_digest=hashlib.sha256(bytes.fromhex(signature[2:])).hexdigest(),
        )

    def recover_after_restart(self) -> "WithdrawalOperation":
        return replace(self, state=SettlementState.UNKNOWN) if self.state is SettlementState.SUBMITTING else self

    def record_submission(self, response: Mapping[str, Any]) -> "WithdrawalOperation":
        if self.state is not SettlementState.SUBMITTING:
            raise AccountConnectionError("Withdrawal is not submitting.")
        transaction_id = response.get("transaction_id")
        transaction_hash = response.get("transaction_hash")
        if not isinstance(transaction_id, str) or not transaction_id or len(transaction_id) > 128:
            return replace(self, state=SettlementState.UNKNOWN)
        if transaction_hash is not None and (
            not isinstance(transaction_hash, str)
            or re.fullmatch(r"0x[0-9a-fA-F]{64}", transaction_hash) is None
        ):
            return replace(self, state=SettlementState.UNKNOWN, transaction_id=transaction_id)
        return replace(
            self, state=SettlementState.SUBMITTED, transaction_id=transaction_id,
            transaction_hash=transaction_hash.lower() if transaction_hash else None,
        )

    def reconcile(self, response: Mapping[str, Any]) -> "WithdrawalOperation":
        if self.state not in {SettlementState.SUBMITTED, SettlementState.UNKNOWN}:
            raise AccountConnectionError("Withdrawal cannot be reconciled.")
        transaction_id = response.get("transaction_id")
        if not isinstance(transaction_id, str) or not transaction_id:
            raise AccountConnectionError("Withdrawal transaction identity is missing.")
        if self.transaction_id and transaction_id != self.transaction_id:
            raise AccountConnectionError("Withdrawal transaction identity changed.")
        if any((
            response.get("from") != self.plan.owner_address,
            response.get("to") != POLYMARKET_PRODUCTION_MANIFEST.deposit_wallet_factory.lower(),
            response.get("proxy_address") != self.plan.wallet_address,
            response.get("type") != "WALLET",
        )):
            raise AccountConnectionError("Withdrawal relayer identity changed.")
        state = response.get("state")
        if state in {"STATE_FAILED", "STATE_INVALID"}:
            reason = str(response.get("error_msg") or state)
            return replace(
                self, state=SettlementState.FAILED, transaction_id=transaction_id,
                failure_digest=hashlib.sha256(reason.encode()).hexdigest(),
            )
        if state == "STATE_CONFIRMED":
            transaction_hash = response.get("transaction_hash") or self.transaction_hash
            if not isinstance(transaction_hash, str) or re.fullmatch(
                r"0x[0-9a-fA-F]{64}", transaction_hash
            ) is None:
                raise AccountConnectionError("Confirmed withdrawal lacks a transaction hash.")
            return replace(
                self, state=SettlementState.CONFIRMED, transaction_id=transaction_id,
                transaction_hash=transaction_hash.lower(),
            )
        if state not in {"STATE_NEW", "STATE_EXECUTED", "STATE_MINED"}:
            raise AccountConnectionError("Withdrawal relayer state is invalid.")
        return replace(self, state=SettlementState.SUBMITTED, transaction_id=transaction_id)

    def to_record(self) -> dict[str, Any]:
        return {
            "plan": self.plan.to_record(), "state": self.state.value,
            "owner_signature_digest": self.owner_signature_digest,
            "transaction_id": self.transaction_id,
            "transaction_hash": self.transaction_hash,
            "failure_digest": self.failure_digest,
        }

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> "WithdrawalOperation":
        try:
            values = dict(record)
            values["plan"] = BridgeWithdrawalPlan.from_record(values["plan"])
            values["state"] = SettlementState(values["state"])
            return cls(**values)
        except (KeyError, TypeError, ValueError) as exc:
            raise AccountConnectionError("Stored withdrawal operation is invalid.") from exc
