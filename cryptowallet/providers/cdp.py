import logging
import re
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone

from ..backend.auth import JWT_TOKEN_NAMESPACE
from ..core.models import (
    AccountType,
    IntentStatus,
    PublicAccount,
    TransactionIntent,
    WalletProfile,
)
from ..core.networks import (
    AVALANCHE_FUJI,
    ChainFamily,
    ARBITRUM_SEPOLIA,
    BASE_SEPOLIA,
    ETHEREUM_SEPOLIA,
    POLYGON_AMOY,
    SOLANA_DEVNET,
    KNOWN_NETWORKS,
    NetworkCapability,
)
from ..core.validation import (
    normalize_evm_address,
    normalize_solana_address,
    normalize_solana_signature,
)
from .base import WalletProvider, WalletProviderError
from .base_rpc import (
    BaseRpcError,
    get_erc20_asset,
    get_native_balance as get_rpc_native_balance,
    get_solana_native_balance,
    get_solana_transaction_history,
    get_solana_transaction,
    quote_solana_transfer,
    get_user_operation_receipt,
)
from .cdp_api import CdpApiClient, CdpApiCredentials, CdpApiError


CDP_TOKEN_NAMESPACE = "cryptowallet_cdp"
NATIVE_ETH_CONTRACT = "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
MAX_BALANCE_PAGES = 10
HASH_PATTERN = re.compile(r"^0x[0-9a-fA-F]{64}$")
log = logging.getLogger("red.sickcogs.cryptowallet")


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
    supported_capabilities = {
        BASE_SEPOLIA.key: frozenset({
            NetworkCapability.BALANCE,
            NetworkCapability.TOKEN_DISCOVERY,
            NetworkCapability.SEND,
            NetworkCapability.HISTORY,
            NetworkCapability.DELEGATION,
            NetworkCapability.SPONSORSHIP,
        }),
        ETHEREUM_SEPOLIA.key: frozenset({
            NetworkCapability.BALANCE,
            NetworkCapability.TOKEN_DISCOVERY,
            NetworkCapability.HISTORY,
        }),
        ARBITRUM_SEPOLIA.key: frozenset({NetworkCapability.BALANCE}),
        POLYGON_AMOY.key: frozenset({NetworkCapability.BALANCE}),
        AVALANCHE_FUJI.key: frozenset({NetworkCapability.BALANCE}),
        SOLANA_DEVNET.key: frozenset({
            NetworkCapability.BALANCE, NetworkCapability.HISTORY,
            NetworkCapability.SEND, NetworkCapability.DELEGATION,
        }),
    }

    def __init__(self, bot, *, request_limiter=None, request_observer=None):
        self.bot = bot
        self.request_limiter = request_limiter
        self.request_observer = request_observer

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
        accounts = [
            PublicAccount(
                address=address,
                network=BASE_SEPOLIA.key,
                account_type=AccountType.SMART_ACCOUNT,
                provider_account_id=address,
            )
        ]
        solana_accounts = end_user.get("solanaAccountObjects") or []
        if isinstance(solana_accounts, list) and solana_accounts:
            solana_address = normalize_solana_address(
                str(solana_accounts[0].get("address") or "")
            )
            accounts.append(
                PublicAccount(
                    address=solana_address,
                    network=SOLANA_DEVNET.key,
                    account_type=AccountType.SOLANA_ACCOUNT,
                    provider_account_id=solana_address,
                )
            )
        return WalletProfile(
            profile_id=profile_id,
            discord_user_id=discord_user_id,
            provider="cdp",
            provider_user_id=provider_user_id,
            accounts=accounts,
        )

    def _api_client(self, credentials: CdpCredentials) -> CdpApiClient:
        return CdpApiClient(
            CdpApiCredentials(
                api_key_id=credentials.api_key_id,
                api_key_secret=credentials.api_key_secret,
                wallet_secret=credentials.wallet_secret,
            ),
            request_limiter=self.request_limiter,
            request_observer=self.request_observer,
        )

    async def _create_end_user(self, credentials: CdpCredentials, profile_id: str) -> dict:
        return await self._api_client(credentials).create_end_user(
            profile_id,
            credentials.jwt_kid,
            self._idempotency_key(profile_id),
        )

    async def ensure_network_accounts(self, profile: dict) -> dict:
        """Idempotently add the first Solana account to an existing CDP user."""
        if any(item.get("network") == SOLANA_DEVNET.key for item in profile.get("accounts") or []):
            return profile
        provider_user_id = str(profile.get("provider_user_id") or "")
        profile_id = str(profile.get("profile_id") or "")
        if not provider_user_id or provider_user_id != profile_id:
            raise WalletProviderError("The stored wallet profile is incomplete.")
        credentials = await self.credentials()
        if credentials is None:
            raise WalletProviderError("CDP credentials are not completely configured.")
        idempotency_key = str(
            uuid.uuid5(uuid.NAMESPACE_URL, f"sick-cogs:cdp:solana:{profile_id}")
        )
        try:
            result = await self._api_client(credentials).add_solana_account(
                provider_user_id, idempotency_key
            )
            account = result.get("solanaAccount") or {}
            address = normalize_solana_address(str(account.get("address") or ""))
        except (CdpApiError, AttributeError, TypeError, ValueError) as exc:
            raise WalletProviderError(
                "CDP could not provision the Solana devnet account."
            ) from exc
        updated = dict(profile)
        updated["accounts"] = [dict(item) for item in profile.get("accounts") or []] + [{
            "address": address,
            "network": SOLANA_DEVNET.key,
            "account_type": AccountType.SOLANA_ACCOUNT.value,
            "provider_account_id": address,
        }]
        return updated

    async def get_native_balance(self, address: str, network: str) -> int:
        configured_network = KNOWN_NETWORKS.get(network)
        if (
            configured_network is None
            or not configured_network.supports(NetworkCapability.BALANCE)
            or not self.supports(network, NetworkCapability.BALANCE)
        ):
            raise WalletProviderError("Native balance lookup is unavailable for this network.")
        try:
            if configured_network.family is ChainFamily.SOLANA:
                normalized_address = normalize_solana_address(address)
                return await get_solana_native_balance(normalized_address)
            normalized_address = normalize_evm_address(address)
            return await get_rpc_native_balance(normalized_address, network)
        except BaseRpcError as exc:
            raise WalletProviderError(
                f"{configured_network.name} native balance is temporarily unavailable."
            ) from exc

    async def get_registered_token_asset(
        self, address: str, network: str, contract: str, *, include_metadata: bool = False
    ) -> dict:
        configured_network = KNOWN_NETWORKS.get(network)
        if configured_network is None or not configured_network.supports(NetworkCapability.BALANCE):
            raise WalletProviderError("Token lookup is unavailable for this network.")
        try:
            normalized_address = normalize_evm_address(address)
            normalized_contract = normalize_evm_address(contract)
            return await get_erc20_asset(
                normalized_contract, normalized_address, network, include_metadata=include_metadata
            )
        except (BaseRpcError, ValueError) as exc:
            raise WalletProviderError(str(exc)) from exc

    async def get_token_balances(self, address: str, network: str) -> list[dict]:
        """Return bounded indexed token balances; all contracts remain explicitly identifiable."""
        configured_network = KNOWN_NETWORKS.get(network)
        if (
            configured_network is None
            or not configured_network.supports(NetworkCapability.TOKEN_DISCOVERY)
            or not self.supports(network, NetworkCapability.TOKEN_DISCOVERY)
        ):
            raise WalletProviderError("Automatic token discovery is unavailable for this network.")
        normalized_address = normalize_evm_address(address)
        credentials = await self.credentials()
        if credentials is None:
            raise WalletProviderError("CDP credentials are not completely configured.")
        page_token = None
        assets = []
        try:
            for _ in range(MAX_BALANCE_PAGES):
                result = await self._api_client(credentials).list_token_balances(
                    normalized_address, network, page_size=100, page_token=page_token
                )
                balances = result.get("balances") or []
                if not isinstance(balances, list):
                    raise ValueError("Invalid token balances")
                for balance in balances:
                    if not isinstance(balance, dict):
                        continue
                    token = balance.get("token") or {}
                    amount = balance.get("amount") or {}
                    contract = str(token.get("contractAddress") or "").lower()
                    if contract == NATIVE_ETH_CONTRACT:
                        continue
                    decimals = int(amount.get("decimals", token.get("decimals", -1)))
                    atomic_amount = int(amount.get("amount", 0))
                    if (
                        len(contract) != 42
                        or not contract.startswith("0x")
                        or decimals < 0
                        or decimals > 255
                        or atomic_amount <= 0
                    ):
                        continue
                    int(contract[2:], 16)
                    symbol = str(token.get("symbol") or "TOKEN").strip()[:16] or "TOKEN"
                    assets.append({
                        "symbol": symbol,
                        "contract_address": contract,
                        "amount_atomic": atomic_amount,
                        "decimals": decimals,
                    })
                page_token = str(result.get("nextPageToken") or "")
                if not page_token:
                    return assets[:25]
            raise WalletProviderError("CDP returned too many token pages to inspect safely.")
        except WalletProviderError:
            raise
        except (CdpApiError, AttributeError, TypeError, ValueError) as exc:
            raise WalletProviderError(
                f"{configured_network.name} token discovery is temporarily unavailable."
            ) from exc

    async def get_transaction_history(
        self,
        address: str,
        network: str,
        *,
        page_token: str | None = None,
        limit: int = 10,
    ) -> dict:
        """Return bounded public activity for one capability-approved network."""
        if network == SOLANA_DEVNET.key:
            if page_token is not None:
                raise WalletProviderError("Solana Devnet history does not support pagination.")
            try:
                normalized_address = normalize_solana_address(address)
                return await get_solana_transaction_history(normalized_address, limit)
            except (BaseRpcError, ValueError) as exc:
                raise WalletProviderError(
                    "Solana Devnet activity is temporarily unavailable."
                ) from exc
        configured_network = KNOWN_NETWORKS.get(network)
        if (
            configured_network is None
            or not configured_network.supports(NetworkCapability.HISTORY)
            or not self.supports(network, NetworkCapability.HISTORY)
        ):
            raise WalletProviderError("Transaction history is unavailable for this network.")
        if limit < 1 or limit > 100 or page_token is not None and len(page_token) > 5_000:
            raise WalletProviderError("The transaction history request is invalid.")
        try:
            normalized_address = normalize_evm_address(address)
        except ValueError as exc:
            raise WalletProviderError("The stored wallet address is invalid.") from exc
        credentials = await self.credentials()
        if credentials is None:
            raise WalletProviderError("CDP credentials are not completely configured.")
        try:
            result = await self._api_client(credentials).list_address_transactions(
                normalized_address,
                network,
                limit=limit,
                page_token=page_token,
            )
            transactions = result.get("data")
            has_more = result.get("has_more")
            next_page = result.get("next_page")
            if (
                not isinstance(transactions, list)
                or not isinstance(has_more, bool)
                or has_more and not isinstance(next_page, str)
            ):
                raise ValueError("Invalid address history")
            return {
                "transactions": transactions,
                "has_more": has_more,
                "next_page": str(next_page or ""),
            }
        except (CdpApiError, AttributeError, TypeError, ValueError) as exc:
            raise WalletProviderError(
                f"CDP could not retrieve this wallet's {configured_network.name} activity."
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
        """Read the user-scoped delegation shared by every wallet account."""
        if network not in {BASE_SEPOLIA.key, SOLANA_DEVNET.key}:
            raise WalletProviderError("Delegation lookup uses the wallet profile scope.")
        provider_user_id = str(profile.get("provider_user_id") or "")
        if not provider_user_id:
            raise WalletProviderError("The stored wallet profile is incomplete.")
        credentials = await self.credentials()
        if credentials is None:
            raise WalletProviderError("CDP credentials are not completely configured.")
        try:
            delegation = await self._api_client(credentials).get_user_delegation(
                provider_user_id, credentials.project_id
            )
            if delegation is None:
                return {"active": False, "expires_at": None, "scope": "profile"}
            expires_at = str(delegation.get("expiresAt") or "")
            expiry = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            if expiry.tzinfo is None:
                raise ValueError("Delegation expiry lacks a timezone")
            active = expiry.astimezone(timezone.utc) > datetime.now(timezone.utc)
            return {"active": active, "expires_at": expires_at, "scope": "profile"}
        except (CdpApiError, TypeError, ValueError) as exc:
            raise WalletProviderError(
                "CDP could not retrieve delegation status. Try again later."
            ) from exc

    async def submit_transaction(self, profile: dict, intent: TransactionIntent) -> dict:
        """Submit one sponsored Base Sepolia transfer through delegated signing."""
        if intent.network == SOLANA_DEVNET.key:
            return await self._submit_solana_transaction(profile, intent)
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

    async def _submit_solana_transaction(
        self, profile: dict, intent: TransactionIntent
    ) -> dict:
        provider_user_id = str(profile.get("provider_user_id") or "")
        profile_id = str(profile.get("profile_id") or "")
        account = next((item for item in profile.get("accounts") or []
                        if item.get("network") == SOLANA_DEVNET.key), None)
        try:
            sender = normalize_solana_address(str((account or {}).get("address") or ""))
            recipient = normalize_solana_address(intent.to_address)
            intent_sender = normalize_solana_address(intent.from_address)
        except ValueError as exc:
            raise WalletProviderError("The Solana transaction contains an invalid address.") from exc
        if (not provider_user_id or intent.profile_id != profile_id
                or sender != intent_sender or intent.value_wei <= 0 or intent.gas_sponsored):
            raise WalletProviderError("The Solana transaction does not match this wallet profile.")
        credentials = await self.credentials()
        if credentials is None:
            raise WalletProviderError("CDP credentials are not completely configured.")
        try:
            quote_data = await quote_solana_transfer(sender, recipient, intent.value_wei)
            if int(quote_data["fee_atomic"]) != intent.estimated_gas_fee_wei:
                raise WalletProviderError("The Solana network fee changed; create a new preview.")
            result = await self._api_client(credentials).send_solana_transaction(
                provider_user_id, sender, credentials.project_id, SOLANA_DEVNET.key,
                str(quote_data["transaction"]),
                str(uuid.uuid5(uuid.NAMESPACE_URL,
                    f"sick-cogs:cdp:send:{profile_id}:{intent.intent_id}")),
            )
            signature = normalize_solana_signature(str(
                result.get("signature") or result.get("transactionSignature") or ""
            ))
            return {"provider_status": "broadcast", "user_operation_hash": None,
                    "transaction_hash": signature, "block_number": None}
        except WalletProviderError:
            raise
        except CdpApiError as exc:
            log.warning(
                "Solana transaction submission failed: status=%s error_type=%s "
                "correlation_id=%s",
                exc.status if exc.status is not None else "unavailable",
                exc.error_type or "unavailable",
                exc.correlation_id or "unavailable",
            )
            if exc.status == 400 and exc.error_type == "malformed_transaction":
                return {
                    "provider_status": "failed", "user_operation_hash": None,
                    "transaction_hash": None, "block_number": None,
                }
            raise WalletProviderError(
                "CDP could not safely submit the Solana Devnet transfer."
            ) from exc
        except BaseRpcError as exc:
            log.warning(
                "Solana transaction submission failed before CDP: error_class=%s",
                type(exc).__name__,
            )
            raise WalletProviderError(
                "CDP could not safely submit the Solana Devnet transfer."
            ) from exc
        except (KeyError, TypeError, ValueError) as exc:
            log.warning(
                "Solana transaction submission returned invalid data: error_class=%s",
                type(exc).__name__,
            )
            raise WalletProviderError("CDP could not safely submit the Solana Devnet transfer.") from exc

    async def get_transaction_status(
        self, profile: dict, intent: TransactionIntent
    ) -> dict:
        """Retrieve and validate current CDP state for a submitted user operation."""
        if intent.network == SOLANA_DEVNET.key:
            return await self._get_solana_transaction_status(profile, intent)
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

    async def _get_solana_transaction_status(self, profile: dict, intent: TransactionIntent) -> dict:
        account = next((item for item in profile.get("accounts") or []
                        if item.get("network") == SOLANA_DEVNET.key), None)
        try:
            sender = normalize_solana_address(str((account or {}).get("address") or ""))
            signature = normalize_solana_signature(intent.transaction_hash or "")
            if sender != normalize_solana_address(intent.from_address):
                raise ValueError("sender mismatch")
            transaction = await get_solana_transaction(signature)
        except (BaseRpcError, ValueError) as exc:
            raise WalletProviderError("Solana Devnet could not retrieve this transaction status.") from exc
        if transaction is None:
            return {"provider_status": "broadcast", "transaction_hash": signature,
                    "block_number": None}
        transfers = transaction.get("native_transfers") or []
        if not any(
            item.get("from_address") == sender
            and item.get("to_address") == normalize_solana_address(intent.to_address)
            and int(item.get("value_atomic", 0)) == intent.value_wei
            for item in transfers
        ):
            raise WalletProviderError(
                "The confirmed Solana transaction does not match this transfer intent."
            )
        return {"provider_status": "complete" if transaction["success"] else "failed",
                "transaction_hash": signature, "block_number": int(transaction["slot"])}

    @staticmethod
    def _not_connected() -> WalletProviderError:
        return WalletProviderError(
            "This CDP operation is not implemented for the selected testnet."
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
        """Rebuild the current Base Sepolia quote without signing or submitting."""
        if intent.network == SOLANA_DEVNET.key:
            if (intent.status is not IntentStatus.PENDING or intent.value_wei <= 0
                    or intent.gas_sponsored or intent.provider_status is not None
                    or intent.user_operation_hash is not None
                    or intent.transaction_hash is not None or intent.block_number is not None):
                raise WalletProviderError("Only a clean pending Solana Devnet intent can be quoted.")
            try:
                sender = normalize_solana_address(intent.from_address)
                recipient = normalize_solana_address(intent.to_address)
                quote_data = await quote_solana_transfer(sender, recipient, intent.value_wei)
            except (BaseRpcError, ValueError) as exc:
                raise WalletProviderError("The Solana Devnet fee quote is unavailable.") from exc
            return replace(intent, from_address=sender, to_address=recipient,
                           estimated_gas_fee_wei=int(quote_data["fee_atomic"]),
                           gas_sponsored=False)
        if (
            intent.status is not IntentStatus.PENDING
            or intent.network != BASE_SEPOLIA.key
            or not intent.gas_sponsored
            or intent.estimated_gas_fee_wei != 0
            or intent.value_wei <= 0
            or intent.provider_status is not None
            or intent.user_operation_hash is not None
            or intent.transaction_hash is not None
            or intent.block_number is not None
        ):
            raise WalletProviderError(
                "Only a clean, pending, sponsored Base Sepolia intent can be quoted."
            )
        try:
            from_address = normalize_evm_address(intent.from_address)
            to_address = normalize_evm_address(intent.to_address)
        except ValueError as exc:
            raise WalletProviderError(
                "The transaction quote contains an invalid wallet address."
            ) from exc
        return replace(
            intent,
            from_address=from_address,
            to_address=to_address,
            estimated_gas_fee_wei=0,
            gas_sponsored=True,
        )

    async def request_approval(self, intent: TransactionIntent) -> str:
        raise self._not_connected()

    async def revoke_authorization(self, profile: dict, network: str) -> None:
        """Revoke signing authority for every account in this wallet profile."""
        if network not in {BASE_SEPOLIA.key, SOLANA_DEVNET.key}:
            raise WalletProviderError("Delegation revocation uses the wallet profile scope.")
        provider_user_id = str(profile.get("provider_user_id") or "")
        if not provider_user_id:
            raise WalletProviderError("The stored wallet profile is incomplete.")
        credentials = await self.credentials()
        if credentials is None:
            raise WalletProviderError("CDP credentials are not completely configured.")
        try:
            await self._api_client(credentials).revoke_user_delegation(
                provider_user_id, credentials.project_id
            )
        except CdpApiError as exc:
            raise WalletProviderError(
                "CDP could not revoke this wallet profile signing authorization."
            ) from exc
