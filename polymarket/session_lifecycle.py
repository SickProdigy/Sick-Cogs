"""Persistent, secret-free lifecycle for Polymarket session-key operations."""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, replace
from enum import Enum
from typing import Mapping

from .account_connection import AccountConnectionError, normalize_evm_address
from .security_policy import SESSION_KEY_LIFETIME_SECONDS
from .session_authorization import SessionKeyOwnerApproval


HEX_32 = re.compile(r"^[0-9a-f]{64}$")
TX_HASH = re.compile(r"^0x[0-9a-f]{64}$")
ROTATION_LEAD_SECONDS = 7 * 24 * 60 * 60


class SessionOperationState(str, Enum):
    PENDING_OWNER_APPROVAL = "pending_owner_approval"
    APPROVED = "approved"
    SUBMITTING = "submitting"
    SUBMITTED = "submitted"
    CONFIRMING_REGISTRY = "confirming_registry"
    COMPLETE = "complete"
    FAILED = "failed"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class SessionKeyOperation:
    """One restart-safe authorization or revocation; raw signatures never persist."""

    approval: SessionKeyOwnerApproval
    state: SessionOperationState = SessionOperationState.PENDING_OWNER_APPROVAL
    owner_signature_digest: str | None = None
    operation_id: str | None = None
    transaction_id: str | None = None
    transaction_hash: str | None = None
    failure_digest: str | None = None
    completed_at: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.approval, SessionKeyOwnerApproval):
            raise AccountConnectionError("Session operation approval is invalid.")
        if not isinstance(self.state, SessionOperationState):
            raise AccountConnectionError("Session operation state is invalid.")
        for value in (self.owner_signature_digest, self.failure_digest):
            if value is not None and not HEX_32.fullmatch(value):
                raise AccountConnectionError("Session operation digest is invalid.")
        if self.transaction_hash is not None and not TX_HASH.fullmatch(
            self.transaction_hash
        ):
            raise AccountConnectionError("Session operation transaction hash is invalid.")
        if self.state is SessionOperationState.PENDING_OWNER_APPROVAL:
            if any((
                self.owner_signature_digest, self.operation_id,
                self.transaction_id, self.transaction_hash,
                self.failure_digest, self.completed_at,
            )):
                raise AccountConnectionError("Pending session operation contains results.")
        if self.state in {
            SessionOperationState.APPROVED, SessionOperationState.SUBMITTING,
        } and not self.owner_signature_digest:
            raise AccountConnectionError("Session operation lacks owner approval.")
        if self.state in {
            SessionOperationState.SUBMITTED,
            SessionOperationState.CONFIRMING_REGISTRY,
            SessionOperationState.COMPLETE,
        } and (
            not self.owner_signature_digest
            or not self.operation_id or not self.transaction_id
        ):
            raise AccountConnectionError("Submitted session operation lacks identity.")
        if self.state is SessionOperationState.COMPLETE and (
            self.completed_at is None or self.completed_at < self.approval.created_at
        ):
            raise AccountConnectionError("Completed session operation timing is invalid.")
        if self.state is SessionOperationState.FAILED and not self.failure_digest:
            raise AccountConnectionError("Failed session operation lacks a digest.")

    def approve(self, signature: str, *, now: int) -> "SessionKeyOperation":
        if self.state is not SessionOperationState.PENDING_OWNER_APPROVAL:
            raise AccountConnectionError("Session operation is not awaiting approval.")
        if now < self.approval.created_at or now >= self.approval.deadline:
            raise AccountConnectionError("Session operation approval expired.")
        self.approval.request_body(signature)
        digest = hashlib.sha256(bytes.fromhex(signature[2:])).hexdigest()
        return replace(
            self, state=SessionOperationState.APPROVED,
            owner_signature_digest=digest,
        )

    def begin_submission(self, *, now: int) -> "SessionKeyOperation":
        if self.state is not SessionOperationState.APPROVED:
            raise AccountConnectionError("Session operation is not approved.")
        if now < self.approval.created_at or now >= self.approval.deadline:
            raise AccountConnectionError("Session operation approval expired.")
        return replace(self, state=SessionOperationState.SUBMITTING)

    def recover_after_restart(self) -> "SessionKeyOperation":
        if self.state is SessionOperationState.SUBMITTING:
            return replace(self, state=SessionOperationState.UNKNOWN)
        return self

    def record_submission(self, response: Mapping) -> "SessionKeyOperation":
        if self.state is not SessionOperationState.SUBMITTING:
            raise AccountConnectionError("Session operation is not submitting.")
        operation_id = response.get("operation_id")
        transaction_id = response.get("transaction_id")
        transaction_hash = response.get("transaction_hash")
        if not all(isinstance(value, str) and value for value in (
            operation_id, transaction_id,
        )):
            return replace(self, state=SessionOperationState.UNKNOWN)
        if transaction_hash is not None and (
            not isinstance(transaction_hash, str)
            or not TX_HASH.fullmatch(transaction_hash.lower())
        ):
            return replace(self, state=SessionOperationState.UNKNOWN)
        return replace(
            self, state=SessionOperationState.SUBMITTED,
            operation_id=operation_id, transaction_id=transaction_id,
            transaction_hash=transaction_hash.lower() if transaction_hash else None,
        )

    def reconcile_transaction(self, response: Mapping) -> "SessionKeyOperation":
        if self.state not in {
            SessionOperationState.SUBMITTED, SessionOperationState.UNKNOWN,
        }:
            raise AccountConnectionError("Session operation cannot be reconciled.")
        if response.get("transaction_id") != self.transaction_id:
            raise AccountConnectionError("Session operation transaction identity changed.")
        state = response.get("state")
        if state in {"STATE_FAILED", "STATE_INVALID"}:
            reason = str(response.get("error_msg") or state)
            return replace(
                self, state=SessionOperationState.FAILED,
                failure_digest=hashlib.sha256(reason.encode("utf-8")).hexdigest(),
            )
        if state == "STATE_CONFIRMED":
            transaction_hash = response.get("transaction_hash") or self.transaction_hash
            if not isinstance(transaction_hash, str) or not TX_HASH.fullmatch(
                transaction_hash.lower()
            ):
                raise AccountConnectionError(
                    "Confirmed session operation lacks a transaction hash."
                )
            return replace(
                self, state=SessionOperationState.CONFIRMING_REGISTRY,
                transaction_hash=transaction_hash.lower(),
            )
        return replace(self, state=SessionOperationState.SUBMITTED)

    def confirm_registry(self, *, active: bool, now: int) -> "SessionKeyOperation":
        if self.state is not SessionOperationState.CONFIRMING_REGISTRY:
            raise AccountConnectionError("Session operation is not registry-confirmable.")
        expected_active = self.approval.action == "authorize"
        if active is not expected_active:
            raise AccountConnectionError("Session registry has not applied the operation.")
        return replace(
            self, state=SessionOperationState.COMPLETE, completed_at=now
        )

    def to_record(self) -> dict:
        record = asdict(self)
        record["state"] = self.state.value
        return record

    @classmethod
    def from_record(cls, record: Mapping) -> "SessionKeyOperation":
        try:
            values = dict(record)
            approval = dict(values["approval"])
            approval["scopes"] = tuple(approval.get("scopes", ()))
            values["approval"] = SessionKeyOwnerApproval(**approval)
            values["state"] = SessionOperationState(values["state"])
            return cls(**values)
        except (KeyError, TypeError, ValueError) as exc:
            raise AccountConnectionError(
                "Stored session operation is invalid."
            ) from exc


class SessionKeyStatus(str, Enum):
    PROVISIONING = "provisioning"
    ACTIVE = "active"
    ROTATING = "rotating"
    REVOKING = "revoking"
    REVOKED = "revoked"
    EXPIRED = "expired"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class SessionKeyLifecycle:
    """Public lifecycle metadata for one encrypted CLOB-only session key."""

    discord_user_id: int
    profile_id: str
    owner_address: str
    wallet_address: str
    session_address: str
    created_at: int
    expires_at: int
    status: SessionKeyStatus = SessionKeyStatus.PROVISIONING
    replacement_session_address: str | None = None
    activated_at: int | None = None
    revoked_at: int | None = None

    def __post_init__(self) -> None:
        for field in ("owner_address", "wallet_address", "session_address"):
            object.__setattr__(self, field, normalize_evm_address(getattr(self, field), field))
        if self.replacement_session_address is not None:
            object.__setattr__(
                self, "replacement_session_address",
                normalize_evm_address(
                    self.replacement_session_address, "replacement session signer"
                ),
            )
        if (
            self.discord_user_id <= 0 or not self.profile_id
            or self.created_at <= 0
            or self.expires_at != self.created_at + SESSION_KEY_LIFETIME_SECONDS
            or not isinstance(self.status, SessionKeyStatus)
            or len({self.owner_address, self.wallet_address, self.session_address}) != 3
        ):
            raise AccountConnectionError("Session-key lifecycle binding is invalid.")
        if self.replacement_session_address in {
            self.owner_address, self.wallet_address, self.session_address,
        }:
            raise AccountConnectionError("Replacement session identity is invalid.")
        if self.status in {
            SessionKeyStatus.ACTIVE, SessionKeyStatus.ROTATING,
            SessionKeyStatus.REVOKING, SessionKeyStatus.REVOKED,
        } and self.activated_at is None:
            raise AccountConnectionError("Session key was never activated.")
        if self.status is SessionKeyStatus.ROTATING and not self.replacement_session_address:
            raise AccountConnectionError("Rotating session key lacks a replacement.")
        if self.status is SessionKeyStatus.REVOKED and self.revoked_at is None:
            raise AccountConnectionError("Revoked session key lacks a timestamp.")

    def activate(self, operation: SessionKeyOperation) -> "SessionKeyLifecycle":
        if (
            self.status is not SessionKeyStatus.PROVISIONING
            or operation.approval.action != "authorize"
            or operation.approval.session_address != self.session_address
            or operation.state is not SessionOperationState.COMPLETE
        ):
            raise AccountConnectionError("Session activation evidence is invalid.")
        return replace(
            self, status=SessionKeyStatus.ACTIVE,
            activated_at=operation.completed_at,
        )

    def maintenance_action(self, *, now: int) -> str:
        if self.status in {SessionKeyStatus.PROVISIONING, SessionKeyStatus.REVOKING}:
            return "reconcile"
        if self.status is SessionKeyStatus.ROTATING:
            return "activate_replacement"
        if self.status is not SessionKeyStatus.ACTIVE:
            return "none"
        if now >= self.expires_at:
            return "revoke_expired"
        if now >= self.expires_at - ROTATION_LEAD_SECONDS:
            return "rotate"
        return "none"

    def begin_rotation(self, replacement_session_address: str) -> "SessionKeyLifecycle":
        if self.status is not SessionKeyStatus.ACTIVE:
            raise AccountConnectionError("Only an active session key can rotate.")
        return replace(
            self, status=SessionKeyStatus.ROTATING,
            replacement_session_address=replacement_session_address,
        )

    def begin_revocation(
        self, *, replacement_active: bool = False,
    ) -> "SessionKeyLifecycle":
        if self.status is SessionKeyStatus.ROTATING and not replacement_active:
            raise AccountConnectionError(
                "Replacement must be active before revoking the old session key."
            )
        if self.status not in {
            SessionKeyStatus.ACTIVE, SessionKeyStatus.ROTATING,
            SessionKeyStatus.EXPIRED,
        }:
            raise AccountConnectionError("Session key cannot begin revocation.")
        return replace(self, status=SessionKeyStatus.REVOKING)

    def revoke(self, operation: SessionKeyOperation) -> "SessionKeyLifecycle":
        if (
            self.status is not SessionKeyStatus.REVOKING
            or operation.approval.action != "revoke"
            or operation.approval.session_address != self.session_address
            or operation.state is not SessionOperationState.COMPLETE
        ):
            raise AccountConnectionError("Session revocation evidence is invalid.")
        return replace(
            self, status=SessionKeyStatus.REVOKED,
            revoked_at=operation.completed_at,
        )

    def to_record(self) -> dict:
        record = asdict(self)
        record["status"] = self.status.value
        return record

    @classmethod
    def from_record(cls, record: Mapping) -> "SessionKeyLifecycle":
        try:
            values = dict(record)
            values["status"] = SessionKeyStatus(values["status"])
            return cls(**values)
        except (KeyError, TypeError, ValueError) as exc:
            raise AccountConnectionError(
                "Stored session-key lifecycle is invalid."
            ) from exc
