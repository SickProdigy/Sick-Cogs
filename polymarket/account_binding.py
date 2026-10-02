"""Bot-first binding from one CryptoWallet profile to one Polymarket account."""

from dataclasses import asdict, dataclass
import hashlib
import json
import re

from .account_connection import AccountConnectionError, WalletType, normalize_evm_address

PROFILE_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
FINGERPRINT = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class BotFirstAccountBinding:
    """Public identity binding only; it contains no provider or signing secret."""

    discord_user_id: int
    profile_id: str
    signer_address: str
    account_wallet_address: str
    created_at: int
    source: str = "cryptowallet_cdp_eoa"
    chain_id: int = 137
    wallet_type: WalletType = WalletType.DEPOSIT_WALLET

    def __post_init__(self):
        if self.discord_user_id <= 0 or not PROFILE_ID.fullmatch(self.profile_id):
            raise AccountConnectionError("CryptoWallet profile binding is invalid.")
        object.__setattr__(
            self, "signer_address",
            normalize_evm_address(self.signer_address, "signer_address"),
        )
        object.__setattr__(
            self, "account_wallet_address",
            normalize_evm_address(self.account_wallet_address, "account_wallet_address"),
        )
        if self.signer_address == self.account_wallet_address:
            raise AccountConnectionError("Deposit Wallet must differ from its owner signer.")
        if self.created_at <= 0:
            raise AccountConnectionError("CryptoWallet profile binding timestamp is invalid.")
        if (
            self.source != "cryptowallet_cdp_eoa"
            or self.chain_id != 137
            or self.wallet_type is not WalletType.DEPOSIT_WALLET
        ):
            raise AccountConnectionError("CryptoWallet profile binding has drifted.")

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(
            json.dumps(self.to_record(), sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    def require_same_identity(
        self, *, discord_user_id: int, profile_id: str, signer_address: str,
        account_wallet_address: str,
    ) -> None:
        candidate = BotFirstAccountBinding(
            discord_user_id=discord_user_id, profile_id=profile_id,
            signer_address=signer_address,
            account_wallet_address=account_wallet_address,
            created_at=self.created_at,
        )
        if candidate != self:
            raise AccountConnectionError(
                "CryptoWallet or Polymarket identity changed; automatic replacement is blocked."
            )

    def to_record(self) -> dict:
        record = asdict(self)
        record["wallet_type"] = self.wallet_type.value
        return record

    @classmethod
    def from_record(cls, record: dict) -> "BotFirstAccountBinding":
        expected = {
            "discord_user_id", "profile_id", "signer_address",
            "account_wallet_address", "created_at", "source", "chain_id",
            "wallet_type",
        }
        if not isinstance(record, dict) or set(record) != expected:
            raise AccountConnectionError("CryptoWallet profile binding record is invalid.")
        try:
            return cls(
                discord_user_id=int(record["discord_user_id"]),
                profile_id=str(record["profile_id"]),
                signer_address=str(record["signer_address"]),
                account_wallet_address=str(record["account_wallet_address"]),
                created_at=int(record["created_at"]),
                source=str(record["source"]),
                chain_id=int(record["chain_id"]),
                wallet_type=WalletType(record["wallet_type"]),
            )
        except (TypeError, ValueError) as exc:
            raise AccountConnectionError(
                "CryptoWallet profile binding record is invalid."
            ) from exc
