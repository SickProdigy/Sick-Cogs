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


class ApprovalPurpose(str, Enum):
    CLAIM = "claim"
    RECOVERY = "recovery"
    SECURITY = "security"
    TRANSACTION = "transaction"


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    IDENTITY_VERIFIED = "identity_verified"
    EXPIRED = "expired"


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
    from_address: str
    to_address: str
    value_wei: int
    created_at: int
    expires_at: int
    estimated_gas_fee_wei: int = 0
    gas_sponsored: bool = False
    status: IntentStatus = IntentStatus.PENDING
    transaction_hash: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent_id": self.intent_id,
            "profile_id": self.profile_id,
            "network": self.network,
            "from_address": self.from_address,
            "to_address": self.to_address,
            "value_wei": self.value_wei,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "estimated_gas_fee_wei": self.estimated_gas_fee_wei,
            "gas_sponsored": self.gas_sponsored,
            "status": self.status.value,
            "transaction_hash": self.transaction_hash,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TransactionIntent":
        return cls(
            intent_id=str(data["intent_id"]),
            profile_id=str(data["profile_id"]),
            network=str(data["network"]),
            from_address=str(data["from_address"]),
            to_address=str(data["to_address"]),
            value_wei=int(data["value_wei"]),
            created_at=int(data["created_at"]),
            expires_at=int(data["expires_at"]),
            estimated_gas_fee_wei=int(data.get("estimated_gas_fee_wei", 0)),
            gas_sponsored=bool(data.get("gas_sponsored", False)),
            status=IntentStatus(data.get("status", IntentStatus.PENDING.value)),
            transaction_hash=data.get("transaction_hash"),
        )


@dataclass(slots=True)
class ApprovalSession:
    """One-time browser handoff state stored without its bearer token."""

    token_digest: str
    deployment_id: str
    discord_application_id: int
    discord_user_id: int
    purpose: ApprovalPurpose
    created_at: int
    expires_at: int
    status: ApprovalStatus = ApprovalStatus.PENDING
    intent_id: str | None = None
    consumed_at: int | None = None
    browser_token_digest: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "token_digest": self.token_digest,
            "deployment_id": self.deployment_id,
            "discord_application_id": self.discord_application_id,
            "discord_user_id": self.discord_user_id,
            "purpose": self.purpose.value,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "status": self.status.value,
            "intent_id": self.intent_id,
            "consumed_at": self.consumed_at,
            "browser_token_digest": self.browser_token_digest,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ApprovalSession":
        purpose = data["purpose"]
        if purpose == "enrollment":
            purpose = ApprovalPurpose.CLAIM.value
        return cls(
            token_digest=str(data["token_digest"]),
            deployment_id=str(data["deployment_id"]),
            discord_application_id=int(data["discord_application_id"]),
            discord_user_id=int(data["discord_user_id"]),
            purpose=ApprovalPurpose(purpose),
            created_at=int(data["created_at"]),
            expires_at=int(data["expires_at"]),
            status=ApprovalStatus(data.get("status", ApprovalStatus.PENDING.value)),
            intent_id=data.get("intent_id"),
            consumed_at=data.get("consumed_at"),
            browser_token_digest=data.get("browser_token_digest"),
        )
