from dataclasses import dataclass

from ..models import TransactionIntent, WalletProfile
from .base import WalletProvider, WalletProviderError


CDP_TOKEN_NAMESPACE = "cryptowallet_cdp"


@dataclass(frozen=True, slots=True)
class CdpCredentials:
    """Server-only CDP identifiers and secrets loaded from Red's secret store."""

    project_id: str
    api_key_id: str
    api_key_secret: str
    wallet_secret: str

    @classmethod
    def from_tokens(cls, tokens: dict[str, str]) -> "CdpCredentials | None":
        values = {
            key: str(tokens.get(key) or "").strip()
            for key in ("project_id", "api_key_id", "api_key_secret", "wallet_secret")
        }
        if not all(values.values()):
            return None
        return cls(**values)


class CdpWalletProvider(WalletProvider):
    """CDP provider boundary; network operations are added only after credential testing."""

    name = "cdp"

    def __init__(self, bot):
        self.bot = bot

    async def credentials(self) -> CdpCredentials | None:
        tokens = await self.bot.get_shared_api_tokens(CDP_TOKEN_NAMESPACE)
        return CdpCredentials.from_tokens(tokens)

    async def readiness(self) -> dict:
        tokens = await self.bot.get_shared_api_tokens(CDP_TOKEN_NAMESPACE)
        required = ("project_id", "api_key_id", "api_key_secret", "wallet_secret")
        missing = [key for key in required if not str(tokens.get(key) or "").strip()]
        return {"configured": not missing, "missing": missing}

    @staticmethod
    def _not_connected() -> WalletProviderError:
        return WalletProviderError(
            "CDP network operations are not connected yet; Base Sepolia only remains enforced."
        )

    async def create_wallet(self, discord_user_id: int) -> WalletProfile:
        raise self._not_connected()

    async def get_profile(self, profile_id: str) -> WalletProfile:
        raise self._not_connected()

    async def prepare_transaction(self, intent: TransactionIntent) -> TransactionIntent:
        raise self._not_connected()

    async def request_approval(self, intent: TransactionIntent) -> str:
        raise self._not_connected()

    async def get_transaction_status(self, intent_id: str) -> TransactionIntent:
        raise self._not_connected()

    async def revoke_authorization(self, profile_id: str) -> None:
        raise self._not_connected()
