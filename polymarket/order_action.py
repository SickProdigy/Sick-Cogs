"""Immutable user confirmation for one exact active-order cancellation."""

from dataclasses import dataclass, replace
import hashlib
import json
import re
from typing import Any, Mapping

from .trade_confirmation import TradeConfirmation, TradeConfirmationError

_IDENTIFIER = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_HEX = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class CancelApprovalRequest:
    request_id: str
    requester_id: int
    order_id: str
    order_binding_key: str
    order_revision: int
    created_at: int
    expires_at: int
    confirmation: TradeConfirmation

    def __post_init__(self) -> None:
        if not _IDENTIFIER.fullmatch(self.request_id):
            raise TradeConfirmationError("Cancel request identity is invalid.")
        if self.requester_id <= 0 or not _IDENTIFIER.fullmatch(self.order_id):
            raise TradeConfirmationError("Cancel order identity is invalid.")
        if not _HEX.fullmatch(self.order_binding_key):
            raise TradeConfirmationError("Cancel order binding is invalid.")
        if self.order_revision < 0:
            raise TradeConfirmationError("Cancel order revision is invalid.")
        if self.created_at <= 0 or self.expires_at <= self.created_at:
            raise TradeConfirmationError("Cancel request lifetime is invalid.")
        if (
            self.confirmation.requester_id != self.requester_id
            or self.confirmation.order_fingerprint != self.fingerprint
            or self.confirmation.created_at != self.created_at
            or self.confirmation.expires_at != self.expires_at
        ):
            raise TradeConfirmationError("Cancel confirmation binding changed.")

    @property
    def fingerprint(self) -> str:
        payload = {
            "request_id": self.request_id,
            "requester_id": self.requester_id,
            "order_id": self.order_id,
            "order_binding_key": self.order_binding_key,
            "order_revision": self.order_revision,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
        }
        return hashlib.sha256(json.dumps(
            payload, sort_keys=True, separators=(",", ":")
        ).encode("ascii")).hexdigest()

    @classmethod
    def create(
        cls, *, request_id: str, requester_id: int, order_id: str,
        order_binding_key: str, order_revision: int, created_at: int,
        expires_at: int, final_confirmation_required: bool,
    ) -> "CancelApprovalRequest":
        provisional = {
            "request_id": request_id, "requester_id": requester_id,
            "order_id": order_id, "order_binding_key": order_binding_key,
            "order_revision": order_revision, "created_at": created_at,
            "expires_at": expires_at,
        }
        fingerprint = hashlib.sha256(json.dumps(
            provisional, sort_keys=True, separators=(",", ":")
        ).encode("ascii")).hexdigest()
        return cls(
            **provisional,
            confirmation=TradeConfirmation(
                requester_id, fingerprint, final_confirmation_required,
                created_at, expires_at,
            ),
        )

    def approve_primary(
        self, *, requester_id: int, now: int,
    ) -> "CancelApprovalRequest":
        return replace(self, confirmation=self.confirmation.approve_primary(
            requester_id=requester_id,
            order_fingerprint=self.fingerprint,
            now=now,
        ))

    def decide_final(
        self, approved: bool, *, requester_id: int, now: int,
    ) -> "CancelApprovalRequest":
        return replace(self, confirmation=self.confirmation.decide_final(
            requester_id=requester_id,
            order_fingerprint=self.fingerprint,
            approved=approved, now=now,
        ))

    def require_approved(self, *, requester_id: int, now: int) -> None:
        self.confirmation.require_approved(
            requester_id=requester_id,
            order_fingerprint=self.fingerprint,
            now=now,
        )

    def to_record(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "requester_id": self.requester_id,
            "order_id": self.order_id,
            "order_binding_key": self.order_binding_key,
            "order_revision": self.order_revision,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "confirmation": self.confirmation.to_record(),
        }

    @classmethod
    def from_record(cls, record: Any) -> "CancelApprovalRequest":
        expected = {
            "request_id", "requester_id", "order_id", "order_binding_key",
            "order_revision", "created_at", "expires_at", "confirmation",
        }
        if not isinstance(record, Mapping) or set(record) != expected:
            raise TradeConfirmationError(
                "Stored cancel request has an invalid shape."
            )
        try:
            return cls(
                request_id=str(record["request_id"]),
                requester_id=int(record["requester_id"]),
                order_id=str(record["order_id"]),
                order_binding_key=str(record["order_binding_key"]),
                order_revision=int(record["order_revision"]),
                created_at=int(record["created_at"]),
                expires_at=int(record["expires_at"]),
                confirmation=TradeConfirmation.from_record(
                    record["confirmation"]
                ),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise TradeConfirmationError(
                "Stored cancel request is invalid."
            ) from exc
