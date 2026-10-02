"""Immutable protected approval state for Polymarket session authorization."""

from dataclasses import dataclass, replace
from enum import Enum
import hashlib
import json
import re

from .account_connection import AccountConnectionError
from .security_policy import EligibilityAttestation

IDENTIFIER = re.compile(r"^[A-Za-z0-9_-]{32,128}$")
ADDRESS = re.compile(r"^0x[0-9a-f]{40}$")


class SessionApprovalState(str, Enum):
    AWAITING_ELIGIBILITY = "awaiting_eligibility"
    AWAITING_APPROVAL = "awaiting_approval"
    AWAITING_FINAL_CONFIRMATION = "awaiting_final_confirmation"
    APPROVED = "approved"
    DECLINED = "declined"
    CONSUMED = "consumed"


@dataclass(frozen=True, slots=True)
class SessionApprovalRequest:
    request_id: str
    result_handle: str
    handoff_handle: str
    requester_id: int
    action: str
    signer_address: str
    account_wallet_address: str
    created_at: int
    expires_at: int
    final_confirmation_required: bool
    state: SessionApprovalState = SessionApprovalState.AWAITING_ELIGIBILITY
    eligibility: EligibilityAttestation | None = None
    primary_approved_at: int | None = None
    final_decided_at: int | None = None

    def __post_init__(self) -> None:
        if (not IDENTIFIER.fullmatch(self.request_id)
                or not IDENTIFIER.fullmatch(self.result_handle)
                or not IDENTIFIER.fullmatch(self.handoff_handle)):
            raise AccountConnectionError("Session approval identity is invalid.")
        if self.requester_id <= 0 or self.action not in {"provision", "rotate"}:
            raise AccountConnectionError("Session approval binding is invalid.")
        if (not ADDRESS.fullmatch(self.signer_address)
                or not ADDRESS.fullmatch(self.account_wallet_address)
                or self.signer_address == self.account_wallet_address):
            raise AccountConnectionError("Session approval wallet binding is invalid.")
        if (type(self.created_at) is not int or type(self.expires_at) is not int
                or self.created_at <= 0 or self.expires_at != self.created_at + 300):
            raise AccountConnectionError("Session approval lifetime is invalid.")
        if not isinstance(self.final_confirmation_required, bool):
            raise AccountConnectionError("Session confirmation preference is invalid.")
        for value in (self.primary_approved_at, self.final_decided_at):
            if value is not None and type(value) is not int:
                raise AccountConnectionError("Session approval timestamp is invalid.")
        if (self.primary_approved_at is not None
                and not self.created_at <= self.primary_approved_at < self.expires_at):
            raise AccountConnectionError("Primary approval timestamp is invalid.")
        if (self.final_decided_at is not None
                and (self.primary_approved_at is None
                     or not self.primary_approved_at <= self.final_decided_at < self.expires_at)):
            raise AccountConnectionError("Final approval timestamp is invalid.")
        if self.state is SessionApprovalState.AWAITING_ELIGIBILITY:
            if self.eligibility is not None or self.primary_approved_at is not None:
                raise AccountConnectionError("Pending eligibility state is invalid.")
        elif self.eligibility is None:
            raise AccountConnectionError("Protected eligibility evidence is missing.")
        if self.state is SessionApprovalState.AWAITING_APPROVAL:
            if self.primary_approved_at is not None or self.final_decided_at is not None:
                raise AccountConnectionError("Pending approval timestamps are invalid.")
        elif self.state is SessionApprovalState.AWAITING_FINAL_CONFIRMATION:
            if self.primary_approved_at is None or self.final_decided_at is not None:
                raise AccountConnectionError("Final confirmation timestamps are invalid.")
        elif self.state in {SessionApprovalState.APPROVED, SessionApprovalState.DECLINED, SessionApprovalState.CONSUMED}:
            if self.primary_approved_at is None or self.final_decided_at is None:
                raise AccountConnectionError("Terminal approval timestamps are incomplete.")

    @property
    def fingerprint(self) -> str:
        value = {
            "request_id": self.request_id, "result_handle": self.result_handle,
            "requester_id": self.requester_id, "action": self.action,
            "final_confirmation_required": self.final_confirmation_required,
            "signer_address": self.signer_address,
            "account_wallet_address": self.account_wallet_address,
            "created_at": self.created_at, "expires_at": self.expires_at,
        }
        return hashlib.sha256(json.dumps(
            value, sort_keys=True, separators=(",", ":")
        ).encode("ascii")).hexdigest()

    def _check(self, requester_id: int, fingerprint: str, now: int) -> None:
        if requester_id != self.requester_id or fingerprint != self.fingerprint:
            raise AccountConnectionError("Session approval identity changed.")
        if now < self.created_at or now >= self.expires_at:
            raise AccountConnectionError("Session approval expired; start again.")

    def record_eligibility(
        self, eligibility: EligibilityAttestation, *, requester_id: int,
        fingerprint: str, now: int,
    ) -> "SessionApprovalRequest":
        self._check(requester_id, fingerprint, now)
        if self.state is not SessionApprovalState.AWAITING_ELIGIBILITY:
            raise AccountConnectionError("Eligibility was already decided.")
        eligibility.require_current(discord_user_id=requester_id, now=now)
        return replace(
            self, state=SessionApprovalState.AWAITING_APPROVAL,
            eligibility=eligibility,
        )

    def approve_primary(self, *, requester_id: int, fingerprint: str, now: int) -> "SessionApprovalRequest":
        self._check(requester_id, fingerprint, now)
        if self.state is not SessionApprovalState.AWAITING_APPROVAL:
            raise AccountConnectionError("Session approval was already decided.")
        if self.final_confirmation_required:
            return replace(
                self, state=SessionApprovalState.AWAITING_FINAL_CONFIRMATION,
                primary_approved_at=now,
            )
        return replace(
            self, state=SessionApprovalState.APPROVED, primary_approved_at=now,
            final_decided_at=now,
        )

    def decide_final(
        self, approved: bool, *, requester_id: int, fingerprint: str, now: int,
    ) -> "SessionApprovalRequest":
        self._check(requester_id, fingerprint, now)
        if self.state is not SessionApprovalState.AWAITING_FINAL_CONFIRMATION:
            raise AccountConnectionError("No final session confirmation is pending.")
        return replace(
            self, state=(SessionApprovalState.APPROVED if approved
                         else SessionApprovalState.DECLINED),
            final_decided_at=now,
        )

    def consume(self, *, requester_id: int, fingerprint: str, now: int) -> "SessionApprovalRequest":
        self._check(requester_id, fingerprint, now)
        if self.state is not SessionApprovalState.APPROVED:
            raise AccountConnectionError("Session authorization is not fully approved.")
        return replace(self, state=SessionApprovalState.CONSUMED)

    def to_record(self) -> dict:
        eligibility = None
        if self.eligibility is not None:
            eligibility = {
                "discord_user_id": self.eligibility.discord_user_id,
                "blocked": self.eligibility.blocked, "country": self.eligibility.country,
                "region": self.eligibility.region, "checked_at": self.eligibility.checked_at,
                "expires_at": self.eligibility.expires_at,
            }
        return {
            "request_id": self.request_id, "result_handle": self.result_handle,
            "handoff_handle": self.handoff_handle,
            "requester_id": self.requester_id, "action": self.action,
            "signer_address": self.signer_address,
            "account_wallet_address": self.account_wallet_address,
            "created_at": self.created_at, "expires_at": self.expires_at,
            "final_confirmation_required": self.final_confirmation_required,
            "state": self.state.value, "eligibility": eligibility,
            "primary_approved_at": self.primary_approved_at,
            "final_decided_at": self.final_decided_at,
        }

    @classmethod
    def from_record(cls, record: dict) -> "SessionApprovalRequest":
        expected = {
            "request_id", "result_handle", "handoff_handle", "requester_id", "action",
            "signer_address", "account_wallet_address", "created_at", "expires_at",
            "final_confirmation_required", "state", "eligibility",
            "primary_approved_at", "final_decided_at",
        }
        if not isinstance(record, dict) or set(record) != expected:
            raise AccountConnectionError("Session approval record has an invalid shape.")
        try:
            eligibility = (EligibilityAttestation(**record["eligibility"])
                           if record["eligibility"] is not None else None)
            return cls(
                request_id=record["request_id"], result_handle=record["result_handle"],
                handoff_handle=record["handoff_handle"],
                requester_id=int(record["requester_id"]), action=record["action"],
                signer_address=record["signer_address"],
                account_wallet_address=record["account_wallet_address"],
                created_at=record["created_at"], expires_at=record["expires_at"],
                final_confirmation_required=record["final_confirmation_required"],
                state=SessionApprovalState(record["state"]), eligibility=eligibility,
                primary_approved_at=record["primary_approved_at"],
                final_decided_at=record["final_decided_at"],
            )
        except (TypeError, ValueError) as exc:
            raise AccountConnectionError("Session approval record is invalid.") from exc
