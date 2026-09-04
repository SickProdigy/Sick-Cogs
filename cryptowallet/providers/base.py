from abc import ABC, abstractmethod

from ..core.models import TransactionIntent, WalletProfile
from ..core.networks import NetworkCapability


class WalletProviderError(RuntimeError):
    """Raised when a wallet provider cannot complete a requested operation."""


class WalletProvider(ABC):
    """Boundary between the cog and an embedded-wallet implementation."""

    name: str
    supported_capabilities: dict[str, frozenset[NetworkCapability]] = {}

    def supports(self, network: str, capability: NetworkCapability) -> bool:
        """Return whether this adapter implements one reviewed network operation."""

        return capability in self.supported_capabilities.get(network, frozenset())

    @abstractmethod
    async def create_wallet(self, profile_id: str, discord_user_id: int) -> WalletProfile:
        """Create a user-owned wallet profile without exposing secret key material."""

    @abstractmethod
    async def get_profile(self, profile_id: str) -> WalletProfile:
        """Return public wallet profile information."""

    @abstractmethod
    async def get_native_balance(self, address: str, network: str) -> int:
        """Return the native network balance in its smallest unit."""

    @abstractmethod
    async def validate_wallet_claim(self, access_token: str, profile: dict) -> dict:
        """Validate browser control and return matched public provider identity."""

    @abstractmethod
    async def get_delegation_status(self, profile: dict, network: str) -> dict:
        """Return authoritative, non-secret delegated-signing status."""

    @abstractmethod
    async def get_transaction_history(
        self,
        address: str,
        network: str,
        *,
        page_token: str | None = None,
        limit: int = 10,
    ) -> dict:
        """Return one cursor-paginated page of public address activity."""

    @abstractmethod
    async def submit_transaction(self, profile: dict, intent: TransactionIntent) -> dict:
        """Sign and submit an approved transaction with provider idempotency."""

    @abstractmethod
    async def prepare_transaction(self, intent: TransactionIntent) -> TransactionIntent:
        """Validate and prepare an unsigned transaction intent."""

    @abstractmethod
    async def request_approval(self, intent: TransactionIntent) -> str:
        """Return a short-lived HTTPS URL for independent user authorization."""

    @abstractmethod
    async def get_transaction_status(
        self, profile: dict, intent: TransactionIntent
    ) -> dict:
        """Return authoritative provider state for a submitted transaction."""

    @abstractmethod
    async def revoke_authorization(self, profile: dict, network: str) -> None:
        """Revoke account-scoped application authority without signing a transaction."""
