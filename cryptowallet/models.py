from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class AccountType(str, Enum):
    EOA = "eoa"
    SMART_ACCOUNT = "smart_account"


class IntentStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    SUBMITTED = "submitted"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    EXPIRED = "expired"
    FAILED = "failed"


@dataclass(slots=True)
class PublicAccount:
    """Non-secret account metadata safe for persistent cog storage."""

    address: str
    network: str
    account_type: AccountType
    provider_account_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "address": self.address,
            "network": self.network,
            "account_type": self.account_type.value,
            "provider_account_id": self.provider_account_id,
        }


@dataclass(slots=True)
class WalletProfile:
    """Provider-neutral wallet profile linked to an immutable Discord ID."""

    profile_id: str
    discord_user_id: int
    provider: str
    provider_user_id: str | None = None
    accounts: list[PublicAccount] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "discord_user_id": self.discord_user_id,
            "provider": self.provider,
            "provider_user_id": self.provider_user_id,
            "accounts": [account.to_dict() for account in self.accounts],
        }


@dataclass(slots=True)
class TransactionIntent:
    """Public transaction request awaiting an independent user approval."""

    intent_id: str
    profile_id: str
    network: str
    to_address: str
    value_wei: int
    status: IntentStatus = IntentStatus.PENDING
    transaction_hash: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent_id": self.intent_id,
            "profile_id": self.profile_id,
            "network": self.network,
            "to_address": self.to_address,
            "value_wei": self.value_wei,
            "status": self.status.value,
            "transaction_hash": self.transaction_hash,
        }
