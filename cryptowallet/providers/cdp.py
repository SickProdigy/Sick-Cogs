import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from ..jwt_auth import JWT_TOKEN_NAMESPACE
from ..models import AccountType, PublicAccount, TransactionIntent, WalletProfile
from ..networks import BASE_SEPOLIA
from ..validation import normalize_evm_address
from .base import WalletProvider, WalletProviderError
from .base_rpc import BaseRpcError, get_user_operation_receipt
from .cdp_api import CdpApiClient, CdpApiCredentials, CdpApiError


CDP_TOKEN_NAMESPACE = "cryptowallet_cdp"
NATIVE_ETH_CONTRACT = "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
MAX_BALANCE_PAGES = 10
HASH_PATTERN = re.compile(r"^0x[0-9a-fA-F]{64}$")


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

    async def diagnostics(self) -> dict:
        """Run local credential checks and one non-mutating CDP request."""
        readiness = await self.readiness()
        if not readiness["configured"]:
            return {
                "ready": False,
                "stage": "configuration",
                "missing": readiness["missing"],
            }
        credentials = await self.credentials()
        if credentials is None:
            return {"ready": False, "stage": "configuration", "missing": []}
        client = self._api_client(credentials)
        try:
            await client.check_connection()
        except CdpApiError as exc:
            return {
                "ready": False,
                "stage": "authentication",
                "error": str(exc),
            }
        return {"ready": True, "stage": "complete"}
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

    async def get_delegation_status(self, profile: dict, network: str) -> dict:
        """Read authoritative account-scoped delegation status from CDP."""
        if network != BASE_SEPOLIA.key:
            raise WalletProviderError("Delegation lookup is restricted to Base Sepolia.")
        provider_user_id = str(profile.get("provider_user_id") or "")
        account = next(
            (
                item
                for item in profile.get("accounts") or []
                if item.get("network") == BASE_SEPOLIA.key
            ),
            None,
        )
        if not provider_user_id or account is None:
            raise WalletProviderError("The stored wallet profile is incomplete.")
        try:
            address = normalize_evm_address(str(account.get("address") or ""))
        except ValueError as exc:
            raise WalletProviderError("The stored wallet address is invalid.") from exc
        credentials = await self.credentials()
        if credentials is None:
            raise WalletProviderError("CDP credentials are not completely configured.")
        try:
            delegation = await self._api_client(credentials).get_account_delegation(
                provider_user_id, address, credentials.project_id
            )
            if delegation is None:
                return {"active": False, "address": address, "expires_at": None}
            expires_at = str(delegation.get("expiresAt") or "")
            expiry = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            if expiry.tzinfo is None:
                raise ValueError("Delegation expiry lacks a timezone")
            active = expiry.astimezone(timezone.utc) > datetime.now(timezone.utc)
            return {"active": active, "address": address, "expires_at": expires_at}
        except (CdpApiError, TypeError, ValueError) as exc:
            raise WalletProviderError(
                "CDP could not retrieve delegation status. Try again later."
            ) from exc

    async def submit_transaction(self, profile: dict, intent: TransactionIntent) -> dict:
        """Submit one sponsored Base Sepolia transfer through delegated signing."""
        if intent.network != BASE_SEPOLIA.key or not intent.gas_sponsored:
            raise WalletProviderError(
                "Transaction submission is restricted to sponsored Base Sepolia."
            )
        provider_user_id = str(profile.get("provider_user_id") or "")
        profile_id = str(profile.get("profile_id") or "")
        if not provider_user_id or intent.profile_id != profile_id:
            raise WalletProviderError("The wallet profile does not match this transaction intent.")
        account = next(
            (
                item for item in profile.get("accounts") or []
                if item.get("network") == BASE_SEPOLIA.key
            ),
            None,
        )
        try:
            account_address = normalize_evm_address(
                str((account or {}).get("address") or "")
            )
            to_address = normalize_evm_address(intent.to_address)
        except ValueError as exc:
            raise WalletProviderError("The transaction contains an invalid wallet address.") from exc
        if account_address != normalize_evm_address(intent.from_address):
            raise WalletProviderError("The transaction sender no longer matches the wallet profile.")
        if intent.value_wei <= 0:
            raise WalletProviderError("The transaction amount must be positive.")
        credentials = await self.credentials()
        if credentials is None:
            raise WalletProviderError("CDP credentials are not completely configured.")
        idempotency_key = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"sick-cogs:cdp:send:{profile_id}:{intent.intent_id}",
            )
        )
        try:
            result = await self._api_client(credentials).send_smart_account_user_operation(
                provider_user_id,
                account_address,
                credentials.project_id,
                BASE_SEPOLIA.key,
                to_address,
                intent.value_wei,
                idempotency_key,
            )
            provider_status = str(result.get("status") or "")
            user_op_hash = str(result.get("userOpHash") or "")
            transaction_hash = str(result.get("transactionHash") or "") or None
            returned_calls = result.get("calls") or []
            if (
                str(result.get("network") or "") != BASE_SEPOLIA.key
                or provider_status
                not in {"pending", "signed", "broadcast", "complete", "dropped", "failed"}
                or not HASH_PATTERN.fullmatch(user_op_hash)
                or transaction_hash is not None and not HASH_PATTERN.fullmatch(transaction_hash)
                or not isinstance(returned_calls, list)
                or len(returned_calls) != 1
                or normalize_evm_address(str(returned_calls[0].get("to") or "")) != to_address
                or int(returned_calls[0].get("value", -1)) != intent.value_wei
                or str(returned_calls[0].get("data") or "") != "0x"
            ):
                raise ValueError("CDP returned mismatched user-operation data")
            receipts = result.get("receipts") or []
            block_number = None
            if receipts:
                if not isinstance(receipts, list) or not isinstance(receipts[0], dict):
                    raise ValueError("CDP returned invalid receipt data")
                raw_block_number = receipts[0].get("blockNumber")
                if raw_block_number is not None:
                    block_number = int(raw_block_number)
            return {
                "provider_status": provider_status,
                "user_operation_hash": user_op_hash.lower(),
                "transaction_hash": transaction_hash.lower() if transaction_hash else None,
                "block_number": block_number,
            }
        except WalletProviderError:
            raise
        except (CdpApiError, AttributeError, TypeError, ValueError) as exc:
            raise WalletProviderError(
                "CDP could not safely complete the sponsored Base Sepolia submission."
            ) from exc

    async def get_transaction_status(
        self, profile: dict, intent: TransactionIntent
    ) -> dict:
        """Retrieve and validate current CDP state for a submitted user operation."""
        if intent.network != BASE_SEPOLIA.key or not intent.user_operation_hash:
            raise WalletProviderError("Only submitted Base Sepolia operations can be refreshed.")
        provider_user_id = str(profile.get("provider_user_id") or "")
        account = next(
            (item for item in profile.get("accounts") or [] if item.get("network") == BASE_SEPOLIA.key),
            None,
        )
        try:
            address = normalize_evm_address(str((account or {}).get("address") or ""))
        except ValueError as exc:
            raise WalletProviderError("The stored wallet address is invalid.") from exc
        if not provider_user_id or address != normalize_evm_address(intent.from_address):
            raise WalletProviderError("The wallet profile does not match this operation.")
        credentials = await self.credentials()
        if credentials is None:
            raise WalletProviderError("CDP credentials are not completely configured.")
        try:
            result = await self._api_client(credentials).get_smart_account_user_operation(
                provider_user_id, address, intent.user_operation_hash, credentials.project_id
            )
        except CdpApiError as cdp_exc:
            try:
                result = await get_user_operation_receipt(address, intent.user_operation_hash)
            except BaseRpcError as rpc_exc:
                raise WalletProviderError(
                    "CDP and Base Sepolia could not retrieve the submitted operation status."
                ) from rpc_exc
            if result is None:
                raise WalletProviderError(
                    "CDP could not retrieve the operation and it is not confirmed on Base Sepolia yet."
                ) from cdp_exc
        try:
            provider_status = str(result.get("status") or "")
            user_op_hash = str(result.get("userOpHash") or "")
            transaction_hash = str(result.get("transactionHash") or "") or None
            if (
                provider_status not in {"pending", "signed", "broadcast", "complete", "dropped", "failed"}
                or user_op_hash.lower() != intent.user_operation_hash.lower()
                or not HASH_PATTERN.fullmatch(user_op_hash)
                or transaction_hash is not None and not HASH_PATTERN.fullmatch(transaction_hash)
            ):
                raise ValueError("CDP returned mismatched user-operation status")
            receipts = result.get("receipts") or []
            block_number = None
            if receipts:
                if not isinstance(receipts, list) or not isinstance(receipts[0], dict):
                    raise ValueError("CDP returned invalid receipt data")
                raw_block_number = receipts[0].get("blockNumber")
                if raw_block_number is not None:
                    block_number = int(raw_block_number)
            return {
                "provider_status": provider_status,
                "user_operation_hash": user_op_hash.lower(),
                "transaction_hash": transaction_hash.lower() if transaction_hash else None,
                "block_number": block_number,
            }
        except (AttributeError, TypeError, ValueError) as exc:
            raise WalletProviderError("CDP could not retrieve the submitted operation status.") from exc

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
        except CdpApiError as exc:
            raise WalletProviderError(
                f"CDP could not provision the Base Sepolia wallet: {exc}"
            ) from exc
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

    async def revoke_authorization(self, profile_id: str) -> None:
        raise self._not_connected()
