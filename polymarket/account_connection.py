"""Secret-free identity and lifecycle records for a future Polymarket connection."""

from dataclasses import asdict, dataclass, replace
from enum import Enum
import re
from typing import Any


EVM_ADDRESS = re.compile(r"^0x[0-9a-fA-F]{40}$")


class AccountConnectionError(ValueError):
    """Raised when an account connection record fails closed."""


class WalletType(str, Enum):
    EOA = "EOA"
    POLY_PROXY = "POLY_PROXY"
    GNOSIS_SAFE = "GNOSIS_SAFE"
    DEPOSIT_WALLET = "DEPOSIT_WALLET"


class ConnectionState(str, Enum):
    PENDING = "pending"
    VERIFIED = "verified"
    DISCONNECTED = "disconnected"


def normalize_evm_address(value: str, field: str) -> str:
    address = str(value or "").strip()
    if not EVM_ADDRESS.fullmatch(address):
        raise AccountConnectionError(f"{field} must be a complete EVM address.")
    return address.lower()


@dataclass(frozen=True, slots=True)
class AccountConnection:
    """Immutable public identity binding; credentials and signatures never belong here."""

    connection_id: str
    discord_user_id: int
    signer_address: str
    account_wallet_address: str
    wallet_type: WalletType
    state: ConnectionState
    created_at: int
    expires_at: int
    verified_at: int | None = None
    disconnected_at: int | None = None

    def __post_init__(self):
        if not self.connection_id or len(self.connection_id) > 128:
            raise AccountConnectionError("A bounded connection ID is required.")
        if self.discord_user_id <= 0:
            raise AccountConnectionError("A positive immutable Discord user ID is required.")
        if not isinstance(self.wallet_type, WalletType):
            raise AccountConnectionError("A recognized wallet type is required.")
        if not isinstance(self.state, ConnectionState):
            raise AccountConnectionError("A recognized connection state is required.")
        signer = normalize_evm_address(self.signer_address, "signer_address")
        wallet = normalize_evm_address(self.account_wallet_address, "account_wallet_address")
        object.__setattr__(self, "signer_address", signer)
        object.__setattr__(self, "account_wallet_address", wallet)
        if self.wallet_type is not WalletType.EOA and signer == wallet:
            raise AccountConnectionError("Smart-wallet signer and account wallet must be distinct.")
        if self.expires_at <= self.created_at:
            raise AccountConnectionError("Connection expiry must be after creation.")
        if self.state is ConnectionState.PENDING and (self.verified_at or self.disconnected_at):
            raise AccountConnectionError("A pending connection cannot contain terminal timestamps.")
        if self.state is ConnectionState.VERIFIED and (
            not self.verified_at or self.disconnected_at
        ):
            raise AccountConnectionError("A verified connection needs only a verification timestamp.")
        if self.state is ConnectionState.DISCONNECTED and (
            not self.verified_at or not self.disconnected_at
            or self.disconnected_at < self.verified_at
        ):
            raise AccountConnectionError("A disconnected connection needs ordered lifecycle timestamps.")

    @classmethod
    def pending(
        cls, *, connection_id: str, discord_user_id: int, signer_address: str,
        account_wallet_address: str, wallet_type: WalletType, created_at: int,
        expires_at: int,
    ) -> "AccountConnection":
        return cls(
            connection_id=connection_id, discord_user_id=discord_user_id,
            signer_address=signer_address, account_wallet_address=account_wallet_address,
            wallet_type=wallet_type, state=ConnectionState.PENDING,
            created_at=created_at, expires_at=expires_at,
        )

    def mark_verified(self, *, discord_user_id: int, now: int) -> "AccountConnection":
        if self.state is not ConnectionState.PENDING:
            raise AccountConnectionError("Only a pending connection can be verified.")
        if discord_user_id != self.discord_user_id:
            raise AccountConnectionError("Connection belongs to a different Discord user.")
        if now < self.created_at:
            raise AccountConnectionError("Verification timestamp precedes connection creation.")
        if now >= self.expires_at:
            raise AccountConnectionError("Connection has expired.")
        return replace(self, state=ConnectionState.VERIFIED, verified_at=now)

    def disconnect(self, *, discord_user_id: int, now: int) -> "AccountConnection":
        if self.state is not ConnectionState.VERIFIED:
            raise AccountConnectionError("Only a verified connection can be disconnected.")
        if discord_user_id != self.discord_user_id:
            raise AccountConnectionError("Connection belongs to a different Discord user.")
        if now < int(self.verified_at or 0):
            raise AccountConnectionError("Disconnect timestamp precedes verification.")
        return replace(self, state=ConnectionState.DISCONNECTED, disconnected_at=now)

    def to_record(self) -> dict[str, Any]:
        record = asdict(self)
        record["wallet_type"] = self.wallet_type.value
        record["state"] = self.state.value
        return record

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> "AccountConnection":
        try:
            values = dict(record)
            values["wallet_type"] = WalletType(values["wallet_type"])
            values["state"] = ConnectionState(values["state"])
            return cls(**values)
        except (KeyError, TypeError, ValueError) as exc:
            raise AccountConnectionError("Stored account connection is invalid.") from exc
