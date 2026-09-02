from abc import ABC, abstractmethod

from ..models import TransactionIntent, WalletProfile


class WalletProviderError(RuntimeError):
    """Raised when a wallet provider cannot complete a requested operation."""


class WalletProvider(ABC):
    """Boundary between the cog and an embedded-wallet implementation."""

    name: str

    @abstractmethod
    async def create_wallet(self, discord_user_id: int) -> WalletProfile:
        """Create a user-owned wallet profile without exposing secret key material."""

    @abstractmethod
    async def get_profile(self, profile_id: str) -> WalletProfile:
        """Return public wallet profile information."""

    @abstractmethod
    async def prepare_transaction(self, intent: TransactionIntent) -> TransactionIntent:
        """Validate and prepare an unsigned transaction intent."""

    @abstractmethod
    async def request_approval(self, intent: TransactionIntent) -> str:
        """Return a short-lived HTTPS URL for independent user authorization."""

    @abstractmethod
    async def get_transaction_status(self, intent_id: str) -> TransactionIntent:
        """Return the current public state of a transaction intent."""

    @abstractmethod
    async def revoke_authorization(self, profile_id: str) -> None:
        """Revoke application authority without requiring the Discord bot to sign."""
