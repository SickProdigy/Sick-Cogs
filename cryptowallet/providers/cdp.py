import uuid
from dataclasses import dataclass

from ..jwt_auth import JWT_TOKEN_NAMESPACE
from ..models import AccountType, PublicAccount, TransactionIntent, WalletProfile
from ..networks import BASE_SEPOLIA
from ..validation import normalize_evm_address
from .base import WalletProvider, WalletProviderError
from .cdp_api import CdpApiClient, CdpApiCredentials, CdpApiError


CDP_TOKEN_NAMESPACE = "cryptowallet_cdp"
NATIVE_ETH_CONTRACT = "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
MAX_BALANCE_PAGES = 10


@dataclass(frozen=True, slots=True)
class CdpCredentials:
    """Server-only CDP identifiers and secrets loaded from Red's secret store."""

    project_id: str
    api_key_id: str
    api_key_secret: str
    wallet_secret: str
    jwt_kid: str

    @classmethod
    def from_tokens(cls, tokens: dict[str, str]) -> "CdpCredentials | None":
        values = {
            key: str(tokens.get(key) or "").strip()
            for key in (
                "project_id",
                "api_key_id",
                "api_key_secret",
                "wallet_secret",
                "jwt_kid",
            )
        }
        if not all(values.values()):
            return None
        return cls(**values)


class CdpWalletProvider(WalletProvider):
    """CDP provider boundary for end-user smart wallets."""

    name = "cdp"

    def __init__(self, bot):
        self.bot = bot

    async def credentials(self) -> CdpCredentials | None:
        tokens = await self.bot.get_shared_api_tokens(CDP_TOKEN_NAMESPACE)
        jwt_tokens = await self.bot.get_shared_api_tokens(JWT_TOKEN_NAMESPACE)
        combined = dict(tokens)
        combined["jwt_kid"] = jwt_tokens.get("kid")
        return CdpCredentials.from_tokens(combined)

    async def readiness(self) -> dict:
        tokens = await self.bot.get_shared_api_tokens(CDP_TOKEN_NAMESPACE)
        jwt_tokens = await self.bot.get_shared_api_tokens(JWT_TOKEN_NAMESPACE)
        tokens = dict(tokens)
        tokens["jwt_kid"] = jwt_tokens.get("kid")
        required = ("project_id", "api_key_id", "api_key_secret", "wallet_secret")
        missing = [key for key in required if not str(tokens.get(key) or "").strip()]
        if not str(tokens.get("jwt_kid") or "").strip():
            missing.append("generated_jwt_key")
        return {"configured": not missing, "missing": missing}

    @staticmethod
    def _idempotency_key(profile_id: str) -> str:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"sick-cogs:cdp:create:{profile_id}"))

    @staticmethod
    def _profile_from_end_user(
        end_user, profile_id: str, discord_user_id: int
    ) -> WalletProfile:
        smart_accounts = end_user.get("evmSmartAccountObjects") or []
        if not isinstance(smart_accounts, list) or not smart_accounts:
            raise WalletProviderError("CDP did not return the requested EVM smart account.")
        address = normalize_evm_address(str(smart_accounts[0].get("address") or ""))
        provider_user_id = str(end_user.get("userId") or "")
        if not provider_user_id:
            raise WalletProviderError("CDP did not return an end-user identifier.")
        return WalletProfile(
            profile_id=profile_id,
            discord_user_id=discord_user_id,
            provider="cdp",
            provider_user_id=provider_user_id,
            accounts=[
                PublicAccount(
                    address=address,
                    network=BASE_SEPOLIA.key,
                    account_type=AccountType.SMART_ACCOUNT,
                    provider_account_id=address,
                )
            ],
        )

    @staticmethod
    def _api_client(credentials: CdpCredentials) -> CdpApiClient:
        return CdpApiClient(
            CdpApiCredentials(
                api_key_id=credentials.api_key_id,
                api_key_secret=credentials.api_key_secret,
                wallet_secret=credentials.wallet_secret,
            )
        )

    async def _create_end_user(self, credentials: CdpCredentials, profile_id: str) -> dict:
        return await self._api_client(credentials).create_end_user(
            profile_id,
            credentials.jwt_kid,
            self._idempotency_key(profile_id),
        )

    async def get_native_balance(self, address: str, network: str) -> int:
        if network != BASE_SEPOLIA.key:
            raise WalletProviderError("CDP balance lookup is restricted to Base Sepolia.")
        normalized_address = normalize_evm_address(address)
        credentials = await self.credentials()
        if credentials is None:
            raise WalletProviderError("CDP credentials are not completely configured.")
        client = self._api_client(credentials)
        page_token = None
        try:
            for _ in range(MAX_BALANCE_PAGES):
                result = await client.list_token_balances(
                    normalized_address,
                    BASE_SEPOLIA.key,
                    page_size=100,
                    page_token=page_token,
                )
                balances = result.get("balances") or []
                if not isinstance(balances, list):
                    raise WalletProviderError("CDP returned invalid balance data.")
                for balance in balances:
                    token = balance.get("token") or {}
                    amount = balance.get("amount") or {}
                    contract = str(token.get("contractAddress") or "").lower()
                    if contract != NATIVE_ETH_CONTRACT:
                        continue
                    if int(amount.get("decimals", -1)) != 18:
                        raise WalletProviderError(
                            "CDP returned an unexpected decimal count for native ETH."
                        )
                    return int(amount.get("amount", 0))
                page_token = str(result.get("nextPageToken") or "")
                if not page_token:
                    return 0
            raise WalletProviderError("CDP returned too many balance pages to inspect safely.")
        except WalletProviderError:
            raise
        except (CdpApiError, AttributeError, TypeError, ValueError) as exc:
            raise WalletProviderError(
                "CDP could not retrieve the Base Sepolia balance. Try again later."
            ) from exc

    async def validate_wallet_claim(self, access_token: str, profile: dict) -> dict:
        """Validate a browser CDP session against the provisioned profile and account."""
        if not access_token or len(access_token) > 16_384:
            raise WalletProviderError("The CDP access token is missing or invalid.")
        profile_id = str(profile.get("profile_id") or "")
        provider_user_id = str(profile.get("provider_user_id") or "")
        expected_addresses = {
            normalize_evm_address(str(account.get("address") or ""))
            for account in profile.get("accounts") or []
            if account.get("network") == BASE_SEPOLIA.key
        }
        if not profile_id or not provider_user_id or not expected_addresses:
            raise WalletProviderError("The stored wallet profile is incomplete.")
        credentials = await self.credentials()
        if credentials is None:
            raise WalletProviderError("CDP credentials are not completely configured.")
        try:
            end_user = await self._api_client(credentials).validate_access_token(access_token)
            returned_user_id = str(end_user.get("userId") or "")
            smart_accounts = end_user.get("evmSmartAccountObjects") or []
            if not isinstance(smart_accounts, list):
                raise ValueError("Invalid smart account list")
            returned_addresses = {
                normalize_evm_address(str(account.get("address") or ""))
                for account in smart_accounts
            }
        except (CdpApiError, AttributeError, TypeError, ValueError) as exc:
            raise WalletProviderError(
                "CDP rejected the wallet authentication or returned invalid account data."
            ) from exc
        if returned_user_id != provider_user_id or provider_user_id != profile_id:
            raise WalletProviderError("The authenticated CDP user does not match this wallet.")
        matched = expected_addresses.intersection(returned_addresses)
        if not matched:
            raise WalletProviderError("The authenticated CDP account does not match this wallet.")
        return {"provider_user_id": returned_user_id, "address": sorted(matched)[0]}

    @staticmethod
    def _not_connected() -> WalletProviderError:
        return WalletProviderError(
            "This CDP operation is not implemented yet; Base Sepolia only remains enforced."
        )

    async def create_wallet(self, profile_id: str, discord_user_id: int) -> WalletProfile:
        credentials = await self.credentials()
        if credentials is None:
            raise WalletProviderError("CDP credentials are not completely configured.")
        try:
            end_user = await self._create_end_user(credentials, profile_id)
            return self._profile_from_end_user(end_user, profile_id, discord_user_id)
        except WalletProviderError:
            raise
        except Exception as exc:
            raise WalletProviderError(
                "CDP could not provision the Base Sepolia wallet. Try again later."
            ) from exc

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
