"""Persisted, identity-bound approval state for one protected market buy."""

from __future__ import annotations

from dataclasses import dataclass, replace
import re
from typing import Any, Mapping

from .account_connection import AccountConnectionError, normalize_evm_address
from .order_intent import (
    MarketBuyApproval, MarketSellApproval, OrderIntentError,
)
from .security_policy import EligibilityAttestation
from .trade_confirmation import TradeConfirmation, TradeConfirmationError

_IDENTIFIER = re.compile(r"^[A-Za-z0-9_-]{16,128}$")


@dataclass(frozen=True, slots=True)
class TradeApprovalRequest:
    request_id: str
    profile_id: str
    signer_address: str
    account_wallet_address: str
    session_address: str
    approval: MarketBuyApproval | MarketSellApproval
    eligibility: EligibilityAttestation
    confirmation: TradeConfirmation

    def __post_init__(self) -> None:
        if not _IDENTIFIER.fullmatch(self.request_id):
            raise TradeConfirmationError("Trade request identity is invalid.")
        if not isinstance(self.profile_id, str) or not self.profile_id or len(self.profile_id) > 256:
            raise TradeConfirmationError("Trade profile identity is invalid.")
        object.__setattr__(
            self, "signer_address",
            normalize_evm_address(self.signer_address, "trade signer"),
        )
        object.__setattr__(
            self, "account_wallet_address",
            normalize_evm_address(self.account_wallet_address, "trade account wallet"),
        )
        object.__setattr__(
            self, "session_address",
            normalize_evm_address(self.session_address, "trade session signer"),
        )
        if len({self.signer_address, self.account_wallet_address, self.session_address}) != 3:
            raise TradeConfirmationError("Trade signer identities must remain separate.")
        if not isinstance(
            self.approval, (MarketBuyApproval, MarketSellApproval)
        ):
            raise TradeConfirmationError("Trade approval is invalid.")
        if not isinstance(self.eligibility, EligibilityAttestation):
            raise TradeConfirmationError("Trade eligibility is invalid.")
        if not isinstance(self.confirmation, TradeConfirmation):
            raise TradeConfirmationError("Trade confirmation is invalid.")
        if (
            self.eligibility.discord_user_id != self.approval.requester_id
            or self.confirmation.requester_id != self.approval.requester_id
            or self.confirmation.order_fingerprint != self.approval.fingerprint
            or self.confirmation.created_at != self.approval.created_at
            or self.confirmation.expires_at != self.approval.expires_at
            or self.eligibility.checked_at > self.approval.created_at
            or self.eligibility.expires_at < self.approval.expires_at
            or self.eligibility.blocked
        ):
            raise TradeConfirmationError("Trade approval bindings disagree.")

    @classmethod
    def create(
        cls, *, request_id: str, profile_id: str, signer_address: str,
        account_wallet_address: str, session_address: str,
        approval: MarketBuyApproval | MarketSellApproval,
        eligibility: EligibilityAttestation,
        final_confirmation_required: bool,
    ) -> "TradeApprovalRequest":
        confirmation = TradeConfirmation(
            requester_id=approval.requester_id,
            order_fingerprint=approval.fingerprint,
            final_confirmation_required=final_confirmation_required,
            created_at=approval.created_at,
            expires_at=approval.expires_at,
        )
        return cls(
            request_id, profile_id, signer_address, account_wallet_address,
            session_address, approval, eligibility, confirmation,
        )

    def approve_primary(self, *, requester_id: int, now: int) -> "TradeApprovalRequest":
        return replace(self, confirmation=self.confirmation.approve_primary(
            requester_id=requester_id,
            order_fingerprint=self.approval.fingerprint,
            now=now,
        ))

    def decide_final(
        self, approved: bool, *, requester_id: int, now: int,
    ) -> "TradeApprovalRequest":
        return replace(self, confirmation=self.confirmation.decide_final(
            requester_id=requester_id,
            order_fingerprint=self.approval.fingerprint,
            approved=approved,
            now=now,
        ))

    def require_approved(self, *, requester_id: int, now: int) -> None:
        self.eligibility.require_current(discord_user_id=requester_id, now=now)
        self.confirmation.require_approved(
            requester_id=requester_id,
            order_fingerprint=self.approval.fingerprint,
            now=now,
        )

    def to_record(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "profile_id": self.profile_id,
            "signer_address": self.signer_address,
            "account_wallet_address": self.account_wallet_address,
            "session_address": self.session_address,
            "approval": self.approval.to_record(),
            "eligibility": {
                "discord_user_id": self.eligibility.discord_user_id,
                "blocked": self.eligibility.blocked,
                "country": self.eligibility.country,
                "region": self.eligibility.region,
                "checked_at": self.eligibility.checked_at,
                "expires_at": self.eligibility.expires_at,
                "source": self.eligibility.source,
                "endpoint": self.eligibility.endpoint,
            },
            "confirmation": self.confirmation.to_record(),
        }

    @classmethod
    def from_record(cls, record: Any) -> "TradeApprovalRequest":
        expected = {
            "request_id", "profile_id", "signer_address",
            "account_wallet_address", "session_address", "approval",
            "eligibility", "confirmation",
        }
        if not isinstance(record, Mapping) or set(record) != expected:
            raise TradeConfirmationError("Stored trade request has an invalid shape.")
        eligibility = record["eligibility"]
        eligibility_fields = {
            "discord_user_id", "blocked", "country", "region", "checked_at",
            "expires_at", "source", "endpoint",
        }
        if not isinstance(eligibility, Mapping) or set(eligibility) != eligibility_fields:
            raise TradeConfirmationError("Stored trade eligibility has an invalid shape.")
        try:
            return cls(
                request_id=str(record["request_id"]),
                profile_id=str(record["profile_id"]),
                signer_address=str(record["signer_address"]),
                account_wallet_address=str(record["account_wallet_address"]),
                session_address=str(record["session_address"]),
                approval=(
                    MarketBuyApproval.from_record(record["approval"])
                    if isinstance(record["approval"], Mapping)
                    and "max_spend_pusd" in record["approval"]
                    else MarketSellApproval.from_record(record["approval"])
                ),
                eligibility=EligibilityAttestation(
                    discord_user_id=int(eligibility["discord_user_id"]),
                    blocked=eligibility["blocked"],
                    country=str(eligibility["country"]),
                    region=str(eligibility["region"]),
                    checked_at=int(eligibility["checked_at"]),
                    expires_at=int(eligibility["expires_at"]),
                    source=str(eligibility["source"]),
                    endpoint=str(eligibility["endpoint"]),
                ),
                confirmation=TradeConfirmation.from_record(record["confirmation"]),
            )
        except (
            AccountConnectionError, KeyError, OrderIntentError, TypeError, ValueError,
        ) as exc:
            raise TradeConfirmationError("Stored trade request is invalid.") from exc
