"""Immutable two-step confirmation state for one exact Polymarket order."""

from dataclasses import dataclass
from enum import Enum
import re

FINGERPRINT = re.compile(r"^[0-9a-f]{64}$")


class TradeConfirmationError(ValueError):
    """Raised when a confirmation transition or binding is invalid."""


class TradeConfirmationState(str, Enum):
    AWAITING_APPROVAL = "awaiting_approval"
    AWAITING_FINAL_CONFIRMATION = "awaiting_final_confirmation"
    APPROVED = "approved"
    DECLINED = "declined"


@dataclass(frozen=True, slots=True)
class TradeConfirmation:
    requester_id: int
    order_fingerprint: str
    final_confirmation_required: bool
    created_at: int
    expires_at: int
    state: TradeConfirmationState = TradeConfirmationState.AWAITING_APPROVAL
    primary_approved_at: int | None = None
    final_decided_at: int | None = None

    def __post_init__(self):
        if self.requester_id <= 0 or not FINGERPRINT.fullmatch(self.order_fingerprint):
            raise TradeConfirmationError("Trade confirmation identity is invalid.")
        if not isinstance(self.final_confirmation_required, bool):
            raise TradeConfirmationError("Final-confirmation preference must be explicit.")
        if self.created_at <= 0 or self.expires_at <= self.created_at:
            raise TradeConfirmationError("Trade confirmation lifetime is invalid.")
        if self.state is TradeConfirmationState.AWAITING_APPROVAL:
            if self.primary_approved_at is not None or self.final_decided_at is not None:
                raise TradeConfirmationError("Unapproved confirmation has decision timestamps.")
        elif self.state is TradeConfirmationState.AWAITING_FINAL_CONFIRMATION:
            if self.primary_approved_at is None or self.final_decided_at is not None:
                raise TradeConfirmationError("Final confirmation timestamps are invalid.")
        elif self.primary_approved_at is None or self.final_decided_at is None:
            raise TradeConfirmationError("Terminal confirmation timestamps are incomplete.")

    def _check(self, *, requester_id: int, order_fingerprint: str, now: int) -> None:
        if requester_id != self.requester_id:
            raise TradeConfirmationError("Trade confirmation belongs to another user.")
        if order_fingerprint != self.order_fingerprint:
            raise TradeConfirmationError("Trade confirmation does not match this exact order.")
        if now < self.created_at or now >= self.expires_at:
            raise TradeConfirmationError("Trade confirmation expired; request a fresh quote.")

    def approve_primary(self, *, requester_id: int, order_fingerprint: str,
                        now: int) -> "TradeConfirmation":
        self._check(requester_id=requester_id,
                    order_fingerprint=order_fingerprint, now=now)
        if self.state is not TradeConfirmationState.AWAITING_APPROVAL:
            raise TradeConfirmationError("Primary approval was already decided.")
        needs_final = self.final_confirmation_required
        return TradeConfirmation(
            self.requester_id, self.order_fingerprint, needs_final,
            self.created_at, self.expires_at,
            TradeConfirmationState.AWAITING_FINAL_CONFIRMATION
            if needs_final else TradeConfirmationState.APPROVED,
            now, None if needs_final else now,
        )

    def decide_final(self, *, requester_id: int, order_fingerprint: str,
                     approved: bool, now: int) -> "TradeConfirmation":
        self._check(requester_id=requester_id,
                    order_fingerprint=order_fingerprint, now=now)
        if self.state is not TradeConfirmationState.AWAITING_FINAL_CONFIRMATION:
            raise TradeConfirmationError("No final confirmation is pending.")
        return TradeConfirmation(
            self.requester_id, self.order_fingerprint,
            self.final_confirmation_required, self.created_at, self.expires_at,
            TradeConfirmationState.APPROVED if approved
            else TradeConfirmationState.DECLINED,
            self.primary_approved_at, now,
        )

    def require_approved(self, *, requester_id: int, order_fingerprint: str,
                         now: int) -> None:
        self._check(requester_id=requester_id,
                    order_fingerprint=order_fingerprint, now=now)
        if self.state is not TradeConfirmationState.APPROVED:
            raise TradeConfirmationError("The exact order is not fully approved.")

    def to_record(self) -> dict:
        return {
            "requester_id": self.requester_id,
            "order_fingerprint": self.order_fingerprint,
            "final_confirmation_required": self.final_confirmation_required,
            "created_at": self.created_at, "expires_at": self.expires_at,
            "state": self.state.value,
            "primary_approved_at": self.primary_approved_at,
            "final_decided_at": self.final_decided_at,
        }

    @classmethod
    def from_record(cls, record: dict) -> "TradeConfirmation":
        expected = {
            "requester_id", "order_fingerprint", "final_confirmation_required",
            "created_at", "expires_at", "state", "primary_approved_at",
            "final_decided_at",
        }
        if not isinstance(record, dict) or set(record) != expected:
            raise TradeConfirmationError("Trade confirmation record has an invalid shape.")
        try:
            return cls(
                int(record["requester_id"]), str(record["order_fingerprint"]),
                record["final_confirmation_required"], int(record["created_at"]),
                int(record["expires_at"]), TradeConfirmationState(record["state"]),
                record["primary_approved_at"], record["final_decided_at"],
            )
        except (TypeError, ValueError) as exc:
            raise TradeConfirmationError("Trade confirmation record is invalid.") from exc
