import json
import logging
import re
import time
import uuid
from hashlib import sha256
from dataclasses import dataclass, replace
from datetime import datetime, timezone

from ..backend.auth import JWT_TOKEN_NAMESPACE
from ..core.clanker import ClankerDeploymentIntent
from ..core.provider_manifest import (
    BASE_MAINNET_PROVIDER_MANIFEST,
    validate_base_mainnet_provider_manifest,
)
from ..core.polymarket import (
    validate_polymarket_clob_auth_typed_data,
    validate_polymarket_session_batch_typed_data,
)
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
    BASE_MAINNET,
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
from .clanker import (
    clanker_claim_call, clanker_collect_rewards_call, clanker_deployment_calldata,
    validate_clanker_claim_call, validate_clanker_collect_rewards_call,
    validate_clanker_deployment_call,
)
from .base_rpc import (
    BaseRpcError,
    get_chain_id,
    get_contract_code,
    get_erc20_asset,
    get_evm_block_number,
    get_factory_token_deployment,
    get_native_balance as get_rpc_native_balance,
    get_solana_native_balance,
    get_solana_transaction_history,
    get_solana_transaction,
    get_transaction,
    quote_solana_transfer,
    quote_evm_call_fee,
    get_user_operation_receipt,
)
from .cdp_api import CdpApiClient, CdpApiCredentials, CdpApiError


CDP_TOKEN_NAMESPACE = "cryptowallet_cdp"
BASE_FINALITY_CONFIRMATIONS = 12
NATIVE_ETH_CONTRACT = "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
MAX_BALANCE_PAGES = 10
PROVISIONING_IDEMPOTENCY_VERSION = 3
HASH_PATTERN = re.compile(r"^0x[0-9a-fA-F]{64}$")
log = logging.getLogger("red.sickcogs.cryptowallet")
TOKEN_FACTORY_SINGLETON = "0xce0042b868300000d44a59004da54a005ffdcf9f"
TOKEN_FACTORY_ADDRESS = "0xcba30318008035bb5a855a8684cea954d573c2c3"
TOKEN_FACTORY_DEPLOYED_TOPIC = (
    "0x8fdcf262da18a046c6f85d4fd10e822a07e071a567fa391c6b3d1fe6d91a1c5f"
)
TOKEN_FACTORY_CREATION_SHA256 = "8f8f4cd23e799be527a98bc723aa77695aa1addff5a805f7c0971bfb8251dd45"
TOKEN_FACTORY_RUNTIME_SHA256 = "d9cdd1effe5aac3b2bab44d78897fb27d4527f6ac964cdbc517e812da2bf20fb"
TOKEN_FACTORY_SINGLETON_SHA256 = "687bc888d213f8eff1e6a982da794f24b835191feb99dd2cacfcd33a9e58fdea"
TOKEN_FACTORY_DEPLOY_GAS_LIMIT = 2_000_000
TOKEN_DEPLOY_GAS_LIMIT = 1_500_000
CLANKER_DEPLOY_GAS_LIMIT = 8_000_000
TOKEN_CREATE_SELECTOR = "8b08cf96"
CLANKER_MAINNET_CALLS = {
    "launch": ("0xe85a59c628f7d2787aceb4bf3b35733630083a9", "0xdf40224a", True),
    "rewardCollection": ("0xffa37784d619f228d8b379d287a4d7282e500762", "0x5763dbd0", False),
    "treasuryClaim": ("0xf3622742b1e446d92e45e22923ef11c2fcd55d68", "0x21c0b342", False),
    "vaultClaim": ("0x8e845ead15737bf71904a30bddd3aee76d6adf6c", "0x1e83409a", False),
    "airdropClaim": ("0xf652b3610d75d81871bf96db50825d9af28391e0", "0xfabed412", False),
}

def _erc20_transfer_data(recipient: str, amount_atomic: int) -> str:
    """Encode only ERC-20 transfer(address,uint256); arbitrary calldata is forbidden."""

    address = normalize_evm_address(recipient)
    if amount_atomic <= 0 or amount_atomic >= 2 ** 256:
        raise ValueError("ERC-20 transfer amount is invalid")
    return "0xa9059cbb" + address[2:].lower().rjust(64, "0") + format(amount_atomic, "064x")




def _sha256_bytecode(value: str) -> str:
    if not isinstance(value, str) or not value.startswith("0x") or len(value) % 2:
        raise ValueError("Invalid EVM bytecode")
    return sha256(bytes.fromhex(value[2:])).hexdigest()


def _singleton_deploy_data(creation_code: str) -> str:
    """Encode only EIP-2470 deploy(bytes,bytes32) with the fixed zero salt."""

    if _sha256_bytecode(creation_code) != TOKEN_FACTORY_CREATION_SHA256:
        raise ValueError("Unrecognized TokenFactory creation bytecode")
    raw = creation_code[2:].lower()
    length = len(raw) // 2
    padded = raw.ljust(((length + 31) // 32) * 64, "0")
    return "0x4af63f02" + format(64, "064x") + "0" * 64 + format(length, "064x") + padded



def _validate_tokenfactory_operation(
    operation: dict,
) -> tuple[str, int, str, int, str, str]:
    """Independently constrain one TokenFactory-owned call before signing."""

    if not isinstance(operation, dict):
        raise ValueError("Invalid TokenFactory operation")
    kind = str(operation.get("kind") or "")
    network = str(operation.get("network") or "")
    expected_chain_ids = {
        BASE_SEPOLIA.key: BASE_SEPOLIA.chain_id,
        BASE_MAINNET.key: BASE_MAINNET.chain_id,
    }
    if network not in expected_chain_ids:
        raise ValueError("TokenFactory operation targets an unsupported network")
    if int(operation.get("chain_id", 0)) != expected_chain_ids[network]:
        raise ValueError("TokenFactory operation targets the wrong chain")
    target = normalize_evm_address(str(operation.get("to") or ""))
    value_wei = int(operation.get("value_wei", -1))
    gas_limit = int(operation.get("gas_limit", 0))
    calldata = str(operation.get("data") or "").lower()
    if value_wei != 0 or not re.fullmatch(r"0x[0-9a-f]+", calldata) or len(calldata) % 2:
        raise ValueError("TokenFactory operation has invalid call data or value")

    if kind == "factory":
        expected_keys = {
            "kind", "network", "chain_id", "to", "value_wei", "data", "gas_limit"
        }
        if set(operation) != expected_keys:
            raise ValueError("TokenFactory infrastructure operation has unexpected fields")
        if target != TOKEN_FACTORY_SINGLETON or gas_limit != TOKEN_FACTORY_DEPLOY_GAS_LIMIT:
            raise ValueError("TokenFactory infrastructure operation is outside its allowlist")
        if not calldata.startswith("0x4af63f02") or len(calldata) < 10 + 64 * 3:
            raise ValueError("TokenFactory infrastructure calldata is invalid")
        byte_length = int(calldata[10 + 128:10 + 192], 16)
        creation_start = 10 + 192
        creation_end = creation_start + byte_length * 2
        creation_code = "0x" + calldata[creation_start:creation_end]
        if _singleton_deploy_data(creation_code).lower() != calldata:
            raise ValueError("TokenFactory infrastructure calldata does not match its pin")
        return target, value_wei, calldata, gas_limit, kind, network

    if kind == "fixed_supply_token":
        expected_keys = {
            "kind", "network", "chain_id", "to", "value_wei", "data", "gas_limit",
            "recipient", "request_id",
        }
        if set(operation) != expected_keys:
            raise ValueError("TokenFactory token operation has unexpected fields")
        if target != TOKEN_FACTORY_ADDRESS or gas_limit != TOKEN_DEPLOY_GAS_LIMIT:
            raise ValueError("TokenFactory token operation is outside its allowlist")
        if not calldata.startswith("0x" + TOKEN_CREATE_SELECTOR) or len(calldata) < 10 + 64 * 6:
            raise ValueError("TokenFactory token calldata is invalid")
        recipient = normalize_evm_address(str(operation.get("recipient") or ""))
        request_id = str(operation.get("request_id") or "").lower()
        encoded_recipient = normalize_evm_address(
            "0x" + calldata[10 + 64 * 4 + 24:10 + 64 * 5]
        )
        encoded_request_id = "0x" + calldata[10 + 64 * 5:10 + 64 * 6]
        if (
            recipient.lower() != encoded_recipient.lower()
            or not HASH_PATTERN.fullmatch(request_id)
            or request_id != encoded_request_id
        ):
            raise ValueError("TokenFactory token bindings do not match its calldata")
        return target, value_wei, calldata, gas_limit, kind, network

    raise ValueError("Unsupported TokenFactory operation kind")


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
        BASE_MAINNET.key: frozenset({
            NetworkCapability.BALANCE,
            NetworkCapability.TOKEN_DISCOVERY,
            NetworkCapability.SEND,
            NetworkCapability.HISTORY,
            NetworkCapability.DELEGATION,
        }),
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

    async def _credentials_from_namespace(
        self, namespace: str
    ) -> CdpCredentials | None:
        tokens = await self.bot.get_shared_api_tokens(namespace)
        jwt_tokens = await self.bot.get_shared_api_tokens(JWT_TOKEN_NAMESPACE)
        combined = dict(tokens)
        combined["jwt_kid"] = jwt_tokens.get("kid")
        return CdpCredentials.from_tokens(combined)

    async def credentials(self) -> CdpCredentials | None:
        return await self._credentials_from_namespace(CDP_TOKEN_NAMESPACE)

    async def credentials_for_network(
        self, network: str
    ) -> CdpCredentials | None:
        """Use the installation CDP project while binding every call to its network."""
        return await self.credentials()

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

    async def mainnet_readiness(self) -> dict:
        """Report shared CDP project readiness for explicit Base-mainnet calls."""
        return await self.readiness()

    async def mainnet_diagnostics(self) -> dict:
        """Validate the pinned provider contract and shared CDP credentials read-only."""
        manifest_errors = validate_base_mainnet_provider_manifest()
        if manifest_errors:
            return {
                "ready": False,
                "stage": "provider_contract",
                "error": "; ".join(manifest_errors),
                "manifest": BASE_MAINNET_PROVIDER_MANIFEST.fingerprint,
            }
        readiness = await self.mainnet_readiness()
        if not readiness["configured"]:
            return {"ready": False, "stage": "configuration", "missing": readiness["missing"]}
        credentials = await self.credentials()
        if credentials is None:
            return {"ready": False, "stage": "configuration", "missing": []}
        try:
            await self._api_client(credentials).check_connection()
        except CdpApiError as exc:
            return {"ready": False, "stage": "authentication", "error": str(exc)}
        return {
            "ready": True,
            "stage": "complete",
            "manifest": BASE_MAINNET_PROVIDER_MANIFEST.fingerprint,
        }

    @staticmethod
    def _idempotency_key(profile_id: str) -> str:
        return str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"sick-cogs:cdp:create:v{PROVISIONING_IDEMPOTENCY_VERSION}:{profile_id}",
            )
        )

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

    async def ensure_mainnet_account(self, profile: dict) -> dict:
        """Attach Base mainnet only after the shared project proves the same smart account."""
        accounts = [dict(item) for item in profile.get("accounts") or []]
        test_account = next(
            (item for item in accounts if item.get("network") == BASE_SEPOLIA.key), None
        )
        try:
            expected_address = normalize_evm_address(
                str((test_account or {}).get("address") or "")
            ).lower()
        except ValueError as exc:
            raise WalletProviderError(
                "The stored Base Sepolia smart account is invalid."
            ) from exc
        existing = next(
            (item for item in accounts if item.get("network") == BASE_MAINNET.key), None
        )
        if existing is not None:
            try:
                if normalize_evm_address(str(existing.get("address") or "")) != expected_address:
                    raise ValueError("mainnet address mismatch")
            except ValueError as exc:
                raise WalletProviderError(
                    "The stored Base mainnet account does not match this wallet."
                ) from exc
            return profile
        profile_id = str(profile.get("profile_id") or "")
        provider_user_id = str(profile.get("provider_user_id") or "")
        if not profile_id or provider_user_id != profile_id:
            raise WalletProviderError("The stored wallet profile is incomplete.")
        credentials = await self.credentials_for_network(BASE_MAINNET.key)
        if credentials is None:
            raise WalletProviderError(
                "Base mainnet CDP credentials are not completely configured."
            )
        try:
            end_user = await self._api_client(credentials).get_end_user(provider_user_id)
            if str(end_user.get("userId") or "") != provider_user_id:
                raise ValueError("CDP returned a different end user")
            smart_accounts = end_user.get("evmSmartAccountObjects") or []
            match = next(
                (item for item in smart_accounts
                 if normalize_evm_address(str(item.get("address") or "")).lower() == expected_address),
                None,
            )
            if match is None:
                raise ValueError("CDP project does not contain the stored smart account")
            # Reuse the normal owner-relationship validator against the isolated response.
            self._delegation_addresses(
                end_user,
                {**profile, "accounts": [{"network": BASE_MAINNET.key,
                                           "address": expected_address}]},
            )
        except (CdpApiError, TypeError, ValueError) as exc:
            raise WalletProviderError(
                "The shared CDP project does not verify this Base mainnet wallet account."
            ) from exc
        updated = dict(profile)
        updated["accounts"] = accounts + [{
            "address": expected_address,
            "network": BASE_MAINNET.key,
            "account_type": AccountType.SMART_ACCOUNT.value,
            "provider_account_id": expected_address,
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
        except CdpApiError as exc:
            log.warning(
                "CDP address history failed for network=%s status=%s type=%s correlation=%s reason=%s",
                configured_network.key,
                exc.status if exc.status is not None else "none",
                exc.error_type or "none",
                exc.correlation_id or "none",
                str(exc),
            )
            raise WalletProviderError(
                f"CDP could not retrieve this wallet's {configured_network.name} activity."
            ) from exc
        except (AttributeError, TypeError, ValueError) as exc:
            log.warning(
                "CDP address history returned an invalid shape for network=%s error=%s",
                configured_network.key,
                type(exc).__name__,
            )
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


    @staticmethod
    def _delegation_addresses(end_user: dict, profile: dict) -> list[str]:
        """Resolve stored public accounts to the addresses that own signing authority."""
        smart_accounts = end_user.get("evmSmartAccountObjects") or []
        eoa_addresses = {
            normalize_evm_address(str(item.get("address") or "")).lower()
            for item in end_user.get("evmAccountObjects") or []
        }
        solana_addresses = {
            normalize_solana_address(str(item.get("address") or ""))
            for item in end_user.get("solanaAccountObjects") or []
        }
        resolved = []
        for account in profile.get("accounts") or []:
            network = account.get("network")
            address = str(account.get("address") or "")
            if network in {BASE_SEPOLIA.key, BASE_MAINNET.key}:
                stored = normalize_evm_address(address).lower()
                matched = next(
                    (item for item in smart_accounts
                     if normalize_evm_address(str(item.get("address") or "")).lower() == stored),
                    None,
                )
                owners = {
                    normalize_evm_address(str(owner)).lower()
                    for owner in (matched or {}).get("ownerAddresses") or []
                }
                eligible = owners.intersection(eoa_addresses)
                if len(eligible) != 1:
                    raise ValueError("CDP did not return one EOA owner for the smart account")
                resolved.append(next(iter(eligible)))
            elif network == SOLANA_DEVNET.key:
                stored = normalize_solana_address(address)
                if stored not in solana_addresses:
                    raise ValueError("CDP did not return the stored Solana account")
                resolved.append(stored)
        if not resolved:
            raise ValueError("No delegation accounts were resolved")
        return resolved


    async def polymarket_signer_context(
        self, profile: dict, discord_user_id: int
    ) -> dict:
        """Resolve one stored CDP smart account to its unique public EOA owner."""

        profile_id = str(profile.get("profile_id") or "")
        provider_user_id = str(profile.get("provider_user_id") or "")
        if (
            int(discord_user_id) <= 0
            or not profile_id
            or provider_user_id != profile_id
        ):
            raise WalletProviderError("The CryptoWallet profile identity is incomplete.")
        try:
            stored_smart_accounts = {
                normalize_evm_address(str(item.get("address") or "")).lower()
                for item in profile.get("accounts") or []
                if item.get("network") in {BASE_SEPOLIA.key, BASE_MAINNET.key}
            }
        except (AttributeError, TypeError, ValueError) as exc:
            raise WalletProviderError(
                "CryptoWallet contains an invalid EVM smart account."
            ) from exc
        if len(stored_smart_accounts) != 1:
            raise WalletProviderError(
                "CryptoWallet must contain exactly one consistent EVM smart account."
            )
        credentials = await self.credentials_for_network(BASE_SEPOLIA.key)
        if credentials is None:
            raise WalletProviderError("CDP credentials are not completely configured.")
        try:
            end_user = await self._api_client(credentials).get_end_user(provider_user_id)
            if str(end_user.get("userId") or "") != provider_user_id:
                raise ValueError("CDP returned a different end user")
            eoa_addresses = {
                normalize_evm_address(str(item.get("address") or "")).lower()
                for item in end_user.get("evmAccountObjects") or []
            }
            smart_address = next(iter(stored_smart_accounts))
            matching = [
                item for item in end_user.get("evmSmartAccountObjects") or []
                if normalize_evm_address(str(item.get("address") or "")).lower()
                == smart_address
            ]
            if len(matching) != 1:
                raise ValueError("CDP did not return the stored smart account")
            eligible_owners = {
                normalize_evm_address(str(owner)).lower()
                for owner in matching[0].get("ownerAddresses") or []
            }.intersection(eoa_addresses)
            if len(eligible_owners) != 1:
                raise ValueError("CDP did not return one EOA owner for the smart account")
            signer_address = next(iter(eligible_owners))
        except (CdpApiError, KeyError, TypeError, ValueError) as exc:
            raise WalletProviderError(
                "CDP could not verify the CryptoWallet owner signer."
            ) from exc
        return {
            "requester_id": int(discord_user_id),
            "profile_id": profile_id,
            "provider_user_id": provider_user_id,
            "smart_account_address": smart_address,
            "signer_address": signer_address,
            "chain_id": 137,
            "source": "cdp_smart_account_owner",
        }

    async def sign_polymarket_clob_auth(
        self, profile: dict, discord_user_id: int, signer_address: str,
        typed_data: dict, idempotency_key: str,
    ) -> dict:
        """Sign only the exact Polymarket ClobAuth proof with the verified CDP EOA."""

        signer = normalize_evm_address(signer_address).lower()
        validate_polymarket_clob_auth_typed_data(typed_data, signer)
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", idempotency_key):
            raise WalletProviderError("Polymarket signing idempotency key is invalid.")
        context = await self.polymarket_signer_context(profile, discord_user_id)
        if context["signer_address"] != signer:
            raise WalletProviderError("Polymarket signer no longer matches CryptoWallet.")
        delegation = await self.get_delegation_status(profile, BASE_SEPOLIA.key)
        if delegation.get("active") is not True:
            raise WalletProviderError(
                "CryptoWallet delegated signing is not active for this profile."
            )
        credentials = await self.credentials_for_network(BASE_SEPOLIA.key)
        if credentials is None:
            raise WalletProviderError("CDP credentials are not completely configured.")
        try:
            response = await self._api_client(credentials).sign_end_user_evm_typed_data(
                context["provider_user_id"], signer, credentials.project_id,
                typed_data, idempotency_key,
            )
        except (CdpApiError, AttributeError, TypeError, ValueError) as exc:
            raise WalletProviderError(
                "CDP could not sign the Polymarket ownership proof."
            ) from exc
        if (
            not isinstance(response, dict)
            or set(response) != {"signature"}
            or re.fullmatch(r"0x[0-9a-fA-F]{130}", str(response.get("signature") or ""))
            is None
        ):
            raise WalletProviderError("CDP returned an invalid EIP-712 signature.")
        return {
            "signature": str(response["signature"]).lower(),
            "signer_address": signer,
        }

    async def sign_polymarket_session_batch(
        self, profile: dict, discord_user_id: int, owner_address: str,
        wallet_address: str, session_address: str, action: str,
        valid_until: int | None, typed_data: dict, idempotency_key: str,
    ) -> dict:
        """Sign only one exact Deposit Wallet session authorization or revocation."""

        owner = normalize_evm_address(owner_address).lower()
        wallet = normalize_evm_address(wallet_address).lower()
        session = normalize_evm_address(session_address).lower()
        if len({owner, wallet, session}) != 3:
            raise WalletProviderError("Polymarket session identities must remain separate.")
        _, deadline = validate_polymarket_session_batch_typed_data(
            typed_data, wallet_address=wallet_address,
            session_address=session_address, action=action,
            valid_until=valid_until,
        )
        now = int(time.time())
        if not now < deadline <= now + 5 * 60:
            raise WalletProviderError("Polymarket session approval is not current.")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", idempotency_key):
            raise WalletProviderError("Polymarket signing idempotency key is invalid.")
        context = await self.polymarket_signer_context(profile, discord_user_id)
        if context["signer_address"] != owner:
            raise WalletProviderError("Polymarket signer no longer matches CryptoWallet.")
        delegation = await self.get_delegation_status(profile, BASE_SEPOLIA.key)
        if delegation.get("active") is not True:
            raise WalletProviderError(
                "CryptoWallet delegated signing is not active for this profile."
            )
        credentials = await self.credentials_for_network(BASE_SEPOLIA.key)
        if credentials is None:
            raise WalletProviderError("CDP credentials are not completely configured.")
        try:
            response = await self._api_client(credentials).sign_end_user_evm_typed_data(
                context["provider_user_id"], owner, credentials.project_id,
                typed_data, idempotency_key,
            )
        except (CdpApiError, AttributeError, TypeError, ValueError) as exc:
            raise WalletProviderError(
                "CDP could not sign the Polymarket session authorization."
            ) from exc
        if (
            not isinstance(response, dict)
            or set(response) != {"signature"}
            or re.fullmatch(r"0x[0-9a-fA-F]{130}", str(response.get("signature") or ""))
            is None
        ):
            raise WalletProviderError("CDP returned an invalid EIP-712 signature.")
        return {"signature": str(response["signature"]).lower(), "signer_address": owner}

    async def get_delegation_status(self, profile: dict, network: str) -> dict:
        """Read a legacy profile grant or the complete account-scoped grant set."""
        if network not in {BASE_SEPOLIA.key, BASE_MAINNET.key, SOLANA_DEVNET.key}:
            raise WalletProviderError("Delegation lookup uses the wallet profile scope.")
        provider_user_id = str(profile.get("provider_user_id") or "")
        environment_networks = (
            {BASE_MAINNET.key}
            if network == BASE_MAINNET.key
            else {BASE_SEPOLIA.key, SOLANA_DEVNET.key}
        )
        scoped_profile = {
            **profile,
            "accounts": [
                item for item in profile.get("accounts") or []
                if item.get("network") in environment_networks
            ],
        }
        accounts = [
            str(item.get("address") or "")
            for item in scoped_profile["accounts"]
        ]
        if not provider_user_id or not accounts or any(not address for address in accounts):
            raise WalletProviderError("The stored wallet profile is incomplete.")
        credentials = await self.credentials_for_network(network)
        if credentials is None:
            raise WalletProviderError("CDP credentials are not completely configured.")
        try:
            client = self._api_client(credentials)
            end_user = await client.get_end_user(provider_user_id)
            if str(end_user.get("userId") or "") != provider_user_id:
                raise ValueError("CDP returned a different end user")
            accounts = self._delegation_addresses(end_user, scoped_profile)
            delegation = await client.get_user_delegation(
                provider_user_id, credentials.project_id
            )
            scope = "profile"
            delegations = [delegation] if delegation is not None else []
            if delegation is None:
                scope = "accounts"
                for address in accounts:
                    item = await client.get_account_delegation(
                        provider_user_id, address, credentials.project_id
                    )
                    if item is not None:
                        delegations.append(item)
            expiries = []
            for item in delegations:
                expires_at = str(item.get("expiresAt") or "")
                expiry = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
                if expiry.tzinfo is None:
                    raise ValueError("Delegation expiry lacks a timezone")
                expiries.append((expiry.astimezone(timezone.utc), expires_at))
            now = datetime.now(timezone.utc)
            active_count = sum(expiry > now for expiry, _ in expiries)
            required_count = 1 if scope == "profile" else len(accounts)
            active = active_count == required_count
            earliest = min(expiries, default=(None, None), key=lambda item: item[0])[1]
            return {
                "active": active, "expires_at": earliest, "scope": scope,
                "partial": scope == "accounts" and 0 < active_count < required_count,
                "active_accounts": active_count, "required_accounts": required_count,
            }
        except (CdpApiError, TypeError, ValueError) as exc:
            raise WalletProviderError(
                "CDP could not retrieve delegation status. Try again later."
            ) from exc

    async def estimate_base_mainnet_call_fee(
        self, *, from_address: str, to_address: str, value_wei: int, data: str
    ) -> dict:
        """Estimate one exact Base mainnet call without signing or submitting."""
        try:
            sender = normalize_evm_address(from_address)
            target = normalize_evm_address(to_address)
            value = int(value_wei)
            calldata = str(data or "0x")
            if value < 0 or not re.fullmatch(r"0x(?:[0-9a-fA-F]{2})*", calldata):
                raise ValueError("invalid call")
            return await quote_evm_call_fee(
                BASE_MAINNET.key, sender, target, value, calldata
            )
        except (BaseRpcError, TypeError, ValueError) as exc:
            raise WalletProviderError("The Base mainnet fee estimate is unavailable.") from exc

    async def token_factory_deployment_status(self, network: str = BASE_SEPOLIA.key) -> dict:
        """Verify the pinned singleton and deterministic TokenFactory destination."""

        if network not in {BASE_SEPOLIA.key, BASE_MAINNET.key}:
            raise WalletProviderError("TokenFactory deployment status requires a reviewed Base network.")
        try:
            singleton_code = await get_contract_code(
                TOKEN_FACTORY_SINGLETON, network
            )
            factory_code = await get_contract_code(
                TOKEN_FACTORY_ADDRESS, network
            )
            if _sha256_bytecode(singleton_code) != TOKEN_FACTORY_SINGLETON_SHA256:
                raise WalletProviderError(
                    "The Base Sepolia singleton deployer code does not match its pin."
                )
            if factory_code == "0x":
                return {"deployed": False, "address": TOKEN_FACTORY_ADDRESS}
            if _sha256_bytecode(factory_code) != TOKEN_FACTORY_RUNTIME_SHA256:
                raise WalletProviderError(
                    "Unexpected code exists at the deterministic TokenFactory address."
                )
            return {"deployed": True, "address": TOKEN_FACTORY_ADDRESS}
        except BaseRpcError as exc:
            raise WalletProviderError(
                "The selected Base network could not verify the TokenFactory deployment state."
            ) from exc

    async def submit_reviewed_tokenfactory_call(
        self, profile: dict, operation: dict, attempt_id: str
    ) -> dict:
        """Sign and submit one independently allowlisted TokenFactory-owned call."""

        try:
            target, value_wei, calldata, gas_limit, kind, network = (
                _validate_tokenfactory_operation(operation)
            )
        except (TypeError, ValueError) as exc:
            raise WalletProviderError(
                "The reviewed TokenFactory operation is invalid."
            ) from exc
        state = await self.token_factory_deployment_status(network)
        if kind == "factory" and state["deployed"]:
            return {**state, "provider_status": "complete", "already_deployed": True}
        if kind == "fixed_supply_token" and not state["deployed"]:
            raise WalletProviderError("The pinned TokenFactory is not deployed.")

        provider_user_id = str(profile.get("provider_user_id") or "")
        profile_id = str(profile.get("profile_id") or "")
        account = next(
            (
                item
                for item in profile.get("accounts") or []
                if item.get("network") == network
            ),
            None,
        )
        try:
            sender = normalize_evm_address(str((account or {}).get("address") or "")).lower()
            if kind == "fixed_supply_token":
                recipient = normalize_evm_address(str(operation["recipient"])).lower()
                if sender != recipient:
                    raise ValueError("Token recipient does not match the signing wallet")
        except ValueError as exc:
            raise WalletProviderError(
                "The wallet profile does not match this TokenFactory operation."
            ) from exc
        if not provider_user_id or not profile_id:
            raise WalletProviderError("The wallet profile is incomplete.")
        delegation = await self.get_delegation_status(profile, network)
        if not delegation.get("active"):
            raise WalletProviderError(
                "An active wallet authorization is required for TokenFactory deployment."
            )
        credentials = await self.credentials_for_network(network)
        if credentials is None:
            raise WalletProviderError("CDP credentials are not completely configured.")

        namespace = "factory:v1" if kind == "factory" else "token"
        try:
            result = await self._api_client(credentials).send_smart_account_user_operation(
                provider_user_id,
                sender,
                credentials.project_id,
                "base" if network == BASE_MAINNET.key else BASE_SEPOLIA.key,
                target,
                value_wei,
                str(
                    uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        f"sick-cogs:tokenfactory:{namespace}:{attempt_id}",
                    )
                ),
                calldata,
                override_gas_limit=gas_limit,
                use_cdp_paymaster=network != BASE_MAINNET.key,
            )
            status = str(result.get("status") or "")
            user_op_hash = str(result.get("userOpHash") or "")
            calls = result.get("calls") or []
            if (
                status not in {"pending", "signed", "broadcast", "complete"}
                or not HASH_PATTERN.fullmatch(user_op_hash)
                or not isinstance(calls, list)
                or len(calls) != 1
                or normalize_evm_address(str(calls[0].get("to") or "")).lower() != target.lower()
                or int(calls[0].get("value", -1)) != value_wei
                or str(calls[0].get("data") or "").lower() != calldata
            ):
                raise ValueError("CDP returned mismatched TokenFactory operation data")
            transaction_hash = str(result.get("transactionHash") or "") or None
            if transaction_hash and not HASH_PATTERN.fullmatch(transaction_hash):
                raise ValueError("CDP returned an invalid transaction hash")
            response = {
                "provider_status": status,
                "user_operation_hash": user_op_hash.lower(),
                "transaction_hash": transaction_hash.lower() if transaction_hash else None,
            }
            if kind == "factory":
                response.update(
                    deployed=False,
                    already_deployed=False,
                    address=TOKEN_FACTORY_ADDRESS,
                )
            else:
                response["request_id"] = str(operation["request_id"]).lower()
            return response
        except (CdpApiError, TypeError, ValueError) as exc:
            raise WalletProviderError(
                "CDP could not safely submit the reviewed TokenFactory operation."
            ) from exc

    async def token_factory_operation_status(
        self, profile: dict, user_operation_hash: str, network: str = BASE_SEPOLIA.key
    ) -> dict:
        """Retrieve one pinned-factory deployment operation from CDP."""

        if not HASH_PATTERN.fullmatch(user_operation_hash or ""):
            raise WalletProviderError("The stored factory operation hash is invalid.")
        provider_user_id = str(profile.get("provider_user_id") or "")
        account = next(
            (
                item
                for item in profile.get("accounts") or []
                if item.get("network") == network
            ),
            None,
        )
        try:
            address = normalize_evm_address(str((account or {}).get("address") or ""))
        except ValueError as exc:
            raise WalletProviderError("The stored wallet address is invalid.") from exc
        if not provider_user_id:
            raise WalletProviderError("The wallet profile is incomplete.")
        credentials = await self.credentials_for_network(network)
        if credentials is None:
            raise WalletProviderError("CDP credentials are not completely configured.")
        try:
            result = await self._api_client(credentials).get_smart_account_user_operation(
                provider_user_id,
                address,
                user_operation_hash,
                credentials.project_id,
            )
            status = str(result.get("status") or "")
            returned_hash = str(result.get("userOpHash") or "")
            transaction_hash = str(result.get("transactionHash") or "") or None
            if (
                status
                not in {"pending", "signed", "broadcast", "complete", "dropped", "failed"}
                or returned_hash.lower() != user_operation_hash.lower()
                or not HASH_PATTERN.fullmatch(returned_hash)
                or transaction_hash is not None
                and not HASH_PATTERN.fullmatch(transaction_hash)
            ):
                raise ValueError("CDP returned mismatched factory operation data")
            return {
                "provider_status": status,
                "user_operation_hash": returned_hash.lower(),
                "transaction_hash": (
                    transaction_hash.lower() if transaction_hash else None
                ),
            }
        except (CdpApiError, AttributeError, TypeError, ValueError) as exc:
            raise WalletProviderError(
                "CDP could not retrieve the factory deployment operation."
            ) from exc

    async def submit_reviewed_clanker_mainnet_operation(
        self, profile: dict, envelope: dict, attempt_id: str
    ) -> dict:
        """Submit one fingerprint-bound Clanker call from the reviewed mainnet allowlist."""
        if not isinstance(envelope, dict) or set(envelope) != {"payload", "fingerprint"}:
            raise WalletProviderError("The reviewed Clanker envelope is invalid.")
        payload = envelope.get("payload")
        if not isinstance(payload, dict):
            raise WalletProviderError("The reviewed Clanker payload is invalid.")
        encoded = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
        fingerprint = "0x" + sha256(encoded).hexdigest()
        if fingerprint != str(envelope.get("fingerprint") or "").lower():
            raise WalletProviderError("The reviewed Clanker fingerprint changed.")
        try:
            kind = str(payload["kind"])
            target, selector, payable = CLANKER_MAINNET_CALLS[kind]
            signer = normalize_evm_address(str(payload["signer"])).lower()
            to = normalize_evm_address(str(payload["to"])).lower()
            value = int(payload["value"])
            data = str(payload["data"]).lower()
            gas_limit = int(payload["gas_limit"])
            max_fee = int(payload["max_fee_wei"])
            requester_id = int(payload["requester_id"])
            if (int(payload["chain_id"]) != BASE_MAINNET.chain_id
                    or to != target or not data.startswith(selector)
                    or value < 0 or (not payable and value != 0)
                    or not 0 < gas_limit <= CLANKER_DEPLOY_GAS_LIMIT
                    or max_fee <= 0 or requester_id <= 0
                    or not attempt_id):
                raise ValueError("Clanker mainnet operation exceeds its allowlist")
        except (KeyError, TypeError, ValueError) as exc:
            raise WalletProviderError("The reviewed Clanker operation is invalid.") from exc
        account = next((item for item in profile.get("accounts") or []
                        if item.get("network") == BASE_MAINNET.key), None)
        try:
            address = normalize_evm_address(str((account or {}).get("address") or "")).lower()
        except ValueError as exc:
            raise WalletProviderError("The Base mainnet wallet account is invalid.") from exc
        if (not profile.get("provider_user_id") or signer != address):
            raise WalletProviderError("The Clanker signer does not match this wallet.")
        delegation = await self.get_delegation_status(profile, BASE_MAINNET.key)
        if not delegation.get("active"):
            raise WalletProviderError("Active Base mainnet wallet authorization is required.")
        credentials = await self.credentials_for_network(BASE_MAINNET.key)
        if credentials is None:
            raise WalletProviderError("Base mainnet CDP credentials are incomplete.")
        key = str(uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"sick-cogs:clanker:mainnet:{fingerprint}:{attempt_id}",
        ))
        try:
            result = await self._api_client(credentials).send_smart_account_user_operation(
                str(profile["provider_user_id"]), signer, credentials.project_id,
                "base", to, value, key, data, override_gas_limit=gas_limit,
                use_cdp_paymaster=False,
            )
            status = str(result.get("status") or "")
            operation_hash = str(result.get("userOpHash") or "")
            transaction_hash = str(result.get("transactionHash") or "") or None
            calls = result.get("calls") or []
            if (status not in {"pending", "signed", "broadcast", "complete"}
                    or not HASH_PATTERN.fullmatch(operation_hash)
                    or transaction_hash is not None and not HASH_PATTERN.fullmatch(transaction_hash)
                    or not isinstance(calls, list) or len(calls) != 1
                    or normalize_evm_address(str(calls[0].get("to") or "")).lower() != to
                    or int(calls[0].get("value", -1)) != value
                    or str(calls[0].get("data") or "").lower() != data):
                raise ValueError("CDP returned mismatched Clanker mainnet data")
            return {"provider_status": status, "user_operation_hash": operation_hash.lower(),
                    "transaction_hash": transaction_hash.lower() if transaction_hash else None,
                    "fingerprint": fingerprint}
        except (CdpApiError, AttributeError, TypeError, ValueError) as exc:
            raise WalletProviderError(
                "CDP could not safely submit the reviewed Clanker mainnet operation."
            ) from exc

    async def submit_clanker_reward_collection(
        self, profile: dict, token: str, token_admin: str, attempt_id: str
    ) -> dict:
        """Submit one reviewed, per-token Clanker reward collection."""
        provider_user_id = str(profile.get("provider_user_id") or "")
        account = next((item for item in profile.get("accounts") or []
                        if item.get("network") == BASE_SEPOLIA.key), None)
        try:
            sender = normalize_evm_address(str((account or {}).get("address") or ""))
            admin = normalize_evm_address(token_admin)
            call = clanker_collect_rewards_call(normalize_evm_address(token))
        except ValueError as exc:
            raise WalletProviderError("The Clanker reward request is invalid.") from exc
        if not provider_user_id or sender.lower() != admin.lower():
            raise WalletProviderError("The CryptoWallet signer is not this token administrator.")
        if not (await self.get_delegation_status(profile, BASE_SEPOLIA.key)).get("active"):
            raise WalletProviderError("An active wallet authorization is required to collect rewards.")
        if not attempt_id:
            raise WalletProviderError("A reward collection attempt ID is required.")
        credentials = await self.credentials()
        if credentials is None:
            raise WalletProviderError("CDP credentials are not completely configured.")
        key = str(uuid.uuid5(uuid.NAMESPACE_URL, f"sick-cogs:clanker:collect:{sender}:{token.lower()}:{attempt_id}"))
        try:
            result = await self._api_client(credentials).send_smart_account_user_operation(
                provider_user_id, sender, credentials.project_id, BASE_SEPOLIA.key,
                str(call["to"]), 0, key, str(call["data"]),
            )
            status = str(result.get("status") or "")
            operation_hash = str(result.get("userOpHash") or "")
            transaction_hash = str(result.get("transactionHash") or "") or None
            calls = result.get("calls") or []
            if (status not in {"pending", "signed", "broadcast", "complete"}
                    or not HASH_PATTERN.fullmatch(operation_hash)
                    or transaction_hash is not None and not HASH_PATTERN.fullmatch(transaction_hash)
                    or not isinstance(calls, list) or len(calls) != 1):
                raise ValueError("invalid reward collection acknowledgement")
            validate_clanker_collect_rewards_call(
                token, to=str(calls[0].get("to") or ""),
                value=int(calls[0].get("value", -1)),
                data=str(calls[0].get("data") or ""),
            )
            return {"provider_status": status, "user_operation_hash": operation_hash.lower(),
                    "transaction_hash": transaction_hash.lower() if transaction_hash else None}
        except (CdpApiError, AttributeError, TypeError, ValueError) as exc:
            raise WalletProviderError("CDP could not safely collect Clanker rewards.") from exc

    async def clanker_reward_operation_status(
        self, profile: dict, token: str, token_admin: str, user_operation_hash: str
    ) -> dict:
        """Refresh one token-scoped collection and validate any echoed call."""
        if not HASH_PATTERN.fullmatch(user_operation_hash or ""):
            raise WalletProviderError("The stored reward operation hash is invalid.")
        provider_user_id = str(profile.get("provider_user_id") or "")
        account = next((item for item in profile.get("accounts") or []
                        if item.get("network") == BASE_SEPOLIA.key), None)
        try:
            sender = normalize_evm_address(str((account or {}).get("address") or ""))
            admin = normalize_evm_address(token_admin)
            normalize_evm_address(token)
        except ValueError as exc:
            raise WalletProviderError("The stored reward operation is invalid.") from exc
        if not provider_user_id or sender.lower() != admin.lower():
            raise WalletProviderError("The CryptoWallet signer is not this token administrator.")
        credentials = await self.credentials()
        if credentials is None:
            raise WalletProviderError("CDP credentials are not completely configured.")
        try:
            result = await self._api_client(credentials).get_smart_account_user_operation(
                provider_user_id, sender, user_operation_hash, credentials.project_id
            )
            status = str(result.get("status") or "")
            returned = str(result.get("userOpHash") or "")
            transaction_hash = str(result.get("transactionHash") or "") or None
            calls = result.get("calls")
            if (status not in {"pending", "signed", "broadcast", "complete", "dropped", "failed"}
                    or returned.lower() != user_operation_hash.lower()
                    or not HASH_PATTERN.fullmatch(returned)
                    or transaction_hash is not None and not HASH_PATTERN.fullmatch(transaction_hash)):
                raise ValueError("invalid reward operation status")
            if calls is not None:
                if not isinstance(calls, list) or len(calls) != 1:
                    raise ValueError("invalid reward operation calls")
                validate_clanker_collect_rewards_call(
                    token, to=str(calls[0].get("to") or ""),
                    value=int(calls[0].get("value", -1)),
                    data=str(calls[0].get("data") or ""),
                )
            return {"provider_status": status, "user_operation_hash": returned.lower(),
                    "transaction_hash": transaction_hash.lower() if transaction_hash else None}
        except (CdpApiError, AttributeError, TypeError, ValueError) as exc:
            raise WalletProviderError("CDP could not refresh the Clanker reward operation.") from exc

    async def submit_clanker_treasury_withdrawal(
        self, profile: dict, *, token_admin: str, creator_treasury: str,
        platform_treasury: str, claims: list[dict], attempt_id: str,
        platform_only: bool = False,
    ) -> dict:
        """Atomically submit a reviewed treasury withdrawal under asymmetric policy."""
        provider_user_id = str(profile.get("provider_user_id") or "")
        account = next((item for item in profile.get("accounts") or []
                        if item.get("network") == BASE_SEPOLIA.key), None)
        try:
            sender = normalize_evm_address(str((account or {}).get("address") or "")).lower()
            admin = normalize_evm_address(token_admin).lower()
            creator = normalize_evm_address(creator_treasury).lower()
            platform = normalize_evm_address(platform_treasury).lower()
            if not 1 <= len(claims) <= 32:
                raise ValueError("invalid claim count")
            normalized = []
            for item in claims:
                if set(item) != {"owner", "asset"}:
                    raise ValueError("invalid claim fields")
                owner = normalize_evm_address(str(item["owner"])).lower()
                asset = normalize_evm_address(str(item["asset"])).lower()
                if owner not in {creator, platform}:
                    raise ValueError("unbound treasury")
                if platform_only:
                    if sender != platform or owner != platform:
                        raise ValueError("platform-only withdrawal cannot include creator rewards")
                elif sender != admin or owner not in {creator, platform}:
                    raise ValueError("token-admin withdrawal has invalid authority")
                normalized.append(clanker_claim_call(owner, asset))
            if len({(item["owner"].lower(), item["asset"].lower()) for item in claims}) != len(claims):
                raise ValueError("duplicate treasury claim")
        except ValueError as exc:
            raise WalletProviderError("The Clanker treasury withdrawal violates its review policy.") from exc
        if not provider_user_id or not attempt_id:
            raise WalletProviderError("The treasury withdrawal identity is incomplete.")
        if not (await self.get_delegation_status(profile, BASE_SEPOLIA.key)).get("active"):
            raise WalletProviderError("An active wallet authorization is required to withdraw rewards.")
        credentials = await self.credentials()
        if credentials is None:
            raise WalletProviderError("CDP credentials are not completely configured.")
        key = str(uuid.uuid5(uuid.NAMESPACE_URL, f"sick-cogs:clanker:withdraw:{sender}:{attempt_id}"))
        try:
            result = await self._api_client(credentials).send_smart_account_calls(
                provider_user_id, sender, credentials.project_id, BASE_SEPOLIA.key, normalized, key
            )
            status = str(result.get("status") or "")
            operation_hash = str(result.get("userOpHash") or "")
            transaction_hash = str(result.get("transactionHash") or "") or None
            echoed = result.get("calls") or []
            if (str(result.get("network") or "") != BASE_SEPOLIA.key
                    or status not in {"pending", "signed", "broadcast", "complete"}
                    or not HASH_PATTERN.fullmatch(operation_hash)
                    or transaction_hash is not None and not HASH_PATTERN.fullmatch(transaction_hash)
                    or not isinstance(echoed, list) or len(echoed) != len(claims)):
                raise ValueError("invalid treasury acknowledgement")
            for item, call in zip(claims, echoed):
                validate_clanker_claim_call(str(item["owner"]), str(item["asset"]),
                    to=str(call.get("to") or ""), value=int(call.get("value", -1)),
                    data=str(call.get("data") or ""))
            return {"provider_status": status, "user_operation_hash": operation_hash.lower(),
                    "transaction_hash": transaction_hash.lower() if transaction_hash else None}
        except (CdpApiError, AttributeError, TypeError, ValueError) as exc:
            raise WalletProviderError("CDP could not safely withdraw Clanker rewards.") from exc

    async def clanker_treasury_operation_status(
        self, profile: dict, *, token_admin: str, creator_treasury: str, platform_treasury: str,
        claims: list[dict], user_operation_hash: str, platform_only: bool = False,
    ) -> dict:
        """Refresh an exact treasury batch and revalidate every echoed call."""
        if not HASH_PATTERN.fullmatch(user_operation_hash or ""):
            raise WalletProviderError("The stored treasury operation hash is invalid.")
        provider_user_id = str(profile.get("provider_user_id") or "")
        account = next((item for item in profile.get("accounts") or [] if item.get("network") == BASE_SEPOLIA.key), None)
        try:
            sender = normalize_evm_address(str((account or {}).get("address") or "")).lower()
            admin = normalize_evm_address(token_admin).lower()
            creator = normalize_evm_address(creator_treasury).lower()
            platform = normalize_evm_address(platform_treasury).lower()
            for item in claims:
                owner = normalize_evm_address(str(item["owner"])).lower()
                if (platform_only and (sender != platform or owner != platform)
                        or not platform_only and (sender != admin or owner not in {creator, platform})):
                    raise ValueError
        except (KeyError, TypeError, ValueError) as exc:
            raise WalletProviderError("The stored treasury operation violates its authority policy.") from exc
        credentials = await self.credentials()
        if credentials is None or not provider_user_id:
            raise WalletProviderError("The treasury operation identity is incomplete.")
        try:
            result = await self._api_client(credentials).get_smart_account_user_operation(
                provider_user_id, sender, user_operation_hash, credentials.project_id)
            status = str(result.get("status") or "")
            returned = str(result.get("userOpHash") or "")
            transaction_hash = str(result.get("transactionHash") or "") or None
            calls = result.get("calls")
            if (status not in {"pending", "signed", "broadcast", "complete", "dropped", "failed"}
                    or returned.lower() != user_operation_hash.lower() or not HASH_PATTERN.fullmatch(returned)
                    or transaction_hash is not None and not HASH_PATTERN.fullmatch(transaction_hash)):
                raise ValueError
            if calls is not None:
                if not isinstance(calls, list) or len(calls) != len(claims):
                    raise ValueError
                for item, call in zip(claims, calls):
                    validate_clanker_claim_call(str(item["owner"]), str(item["asset"]),
                        to=str(call.get("to") or ""), value=int(call.get("value", -1)), data=str(call.get("data") or ""))
            return {"provider_status": status, "user_operation_hash": returned.lower(),
                    "transaction_hash": transaction_hash.lower() if transaction_hash else None}
        except (CdpApiError, AttributeError, TypeError, ValueError) as exc:
            raise WalletProviderError("CDP could not refresh the Clanker treasury operation.") from exc

    async def prepare_clanker_deployment(
        self, profile: dict, intent: ClankerDeploymentIntent, attempt_id: str
    ) -> dict:
        """Prepare, but never submit, one allowlisted Clanker deployment call."""

        provider_user_id = str(profile.get("provider_user_id") or "")
        profile_id = str(profile.get("profile_id") or "")
        account = next(
            (
                item for item in profile.get("accounts") or []
                if item.get("network") == BASE_SEPOLIA.key
            ),
            None,
        )
        try:
            sender = normalize_evm_address(
                str((account or {}).get("address") or "")
            )
            calldata = clanker_deployment_calldata(intent)
            validate_clanker_deployment_call(
                intent, to=intent.factory, value=intent.expected_native_value_wei, data=calldata
            )
        except ValueError as exc:
            raise WalletProviderError("The Clanker deployment intent is invalid.") from exc
        if (
            not provider_user_id
            or profile_id != intent.profile_id
            or sender.lower() != intent.wallet_address.lower()
        ):
            raise WalletProviderError(
                "The wallet profile does not match this Clanker deployment."
            )
        delegation = await self.get_delegation_status(profile, BASE_SEPOLIA.key)
        if not delegation.get("active"):
            raise WalletProviderError(
                "An active wallet authorization is required for Clanker deployment."
            )
        if not attempt_id:
            raise WalletProviderError("A Clanker deployment attempt ID is required.")
        return {
            "network": BASE_SEPOLIA.key,
            "from": sender,
            "to": intent.factory,
            "value": intent.expected_native_value_wei,
            "data": calldata,
            "idempotency_key": str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"sick-cogs:clanker:v1:{profile_id}:{intent.payload_hash}:{attempt_id}",
                )
            ),
            "intent_id": intent.intent_id.lower(),
            "payload_hash": intent.payload_hash,
        }

    async def submit_clanker_deployment(
        self, profile: dict, intent: ClankerDeploymentIntent, attempt_id: str
    ) -> dict:
        """Submit one prepared Clanker call and reject changed provider output."""

        prepared = await self.prepare_clanker_deployment(profile, intent, attempt_id)
        credentials = await self.credentials()
        if credentials is None:
            raise WalletProviderError("CDP credentials are not completely configured.")
        try:
            result = await self._api_client(credentials).send_smart_account_user_operation(
                str(profile["provider_user_id"]),
                prepared["from"],
                credentials.project_id,
                prepared["network"],
                prepared["to"],
                prepared["value"],
                prepared["idempotency_key"],
                prepared["data"],
                override_gas_limit=CLANKER_DEPLOY_GAS_LIMIT,
            )
            status = str(result.get("status") or "")
            user_op_hash = str(result.get("userOpHash") or "")
            transaction_hash = str(result.get("transactionHash") or "") or None
            calls = result.get("calls") or []
            if (
                str(result.get("network") or "") != BASE_SEPOLIA.key
                or status not in {"pending", "signed", "broadcast", "complete"}
                or not HASH_PATTERN.fullmatch(user_op_hash)
                or transaction_hash is not None
                and not HASH_PATTERN.fullmatch(transaction_hash)
                or not isinstance(calls, list)
                or len(calls) != 1
            ):
                raise ValueError("CDP returned invalid Clanker deployment metadata")
            validate_clanker_deployment_call(
                intent,
                to=str(calls[0].get("to") or ""),
                value=int(calls[0].get("value", -1)),
                data=str(calls[0].get("data") or ""),
            )
            return {
                "provider_status": status,
                "user_operation_hash": user_op_hash.lower(),
                "transaction_hash": transaction_hash.lower() if transaction_hash else None,
                "intent_id": intent.intent_id.lower(),
                "payload_hash": intent.payload_hash,
            }
        except (CdpApiError, AttributeError, TypeError, ValueError) as exc:
            log.exception(
                "CDP Clanker submission failed safe validation for intent %s",
                intent.intent_id,
            )
            raise WalletProviderError(
                "CDP could not safely submit the Clanker deployment."
            ) from exc

    async def clanker_operation_status(
        self, profile: dict, intent: ClankerDeploymentIntent, user_operation_hash: str
    ) -> dict:
        """Refresh one Clanker operation and reject changed provider call data."""
        if not HASH_PATTERN.fullmatch(user_operation_hash or ""):
            raise WalletProviderError("The stored Clanker operation hash is invalid.")
        provider_user_id = str(profile.get("provider_user_id") or "")
        account = next((item for item in profile.get("accounts") or []
                        if item.get("network") == BASE_SEPOLIA.key), None)
        try:
            address = normalize_evm_address(str((account or {}).get("address") or ""))
        except ValueError as exc:
            raise WalletProviderError("The stored Clanker wallet address is invalid.") from exc
        if (
            not provider_user_id
            or address.lower() != intent.wallet_address.lower()
        ):
            raise WalletProviderError("The wallet profile no longer matches the Clanker intent.")
        credentials = await self.credentials()
        if credentials is None:
            raise WalletProviderError("CDP credentials are not completely configured.")
        try:
            result = await self._api_client(credentials).get_smart_account_user_operation(
                provider_user_id, address, user_operation_hash, credentials.project_id
            )
            status = str(result.get("status") or "")
            returned_hash = str(result.get("userOpHash") or "")
            transaction_hash = str(result.get("transactionHash") or "") or None
            calls = result.get("calls")
            if (status not in {"pending", "signed", "broadcast", "complete", "dropped", "failed"}
                    or returned_hash.lower() != user_operation_hash.lower()
                    or not HASH_PATTERN.fullmatch(returned_hash)
                    or transaction_hash is not None and not HASH_PATTERN.fullmatch(transaction_hash)):
                raise ValueError("mismatched Clanker operation status")
            if calls is not None:
                if not isinstance(calls, list) or len(calls) != 1:
                    raise ValueError("mismatched Clanker operation calls")
                validate_clanker_deployment_call(intent, to=str(calls[0].get("to") or ""),
                    value=int(calls[0].get("value", -1)), data=str(calls[0].get("data") or ""))
            receipts = result.get("receipts") or []
            block_number = int(receipts[0]["blockNumber"]) if receipts and receipts[0].get("blockNumber") is not None else None
            return {"provider_status": status, "user_operation_hash": returned_hash.lower(),
                    "transaction_hash": transaction_hash.lower() if transaction_hash else None,
                    "block_number": block_number}
        except (CdpApiError, AttributeError, KeyError, TypeError, ValueError) as exc:
            raise WalletProviderError("CDP could not refresh the Clanker operation.") from exc

    async def validate_pre_submission(
        self, profile: dict, intent: TransactionIntent
    ) -> dict:
        """Fail closed immediately before CDP creates the provider-managed operation."""
        network = KNOWN_NETWORKS.get(intent.network)
        if (
            network is None
            or not self.supports(network.key, NetworkCapability.SEND)
            or intent.status is not IntentStatus.PENDING
            or intent.provider_status is not None
            or intent.user_operation_hash is not None
            or intent.transaction_hash is not None
            or intent.block_number is not None
        ):
            raise WalletProviderError(
                "The transaction is not a clean pending provider operation."
            )
        profile_id = str(profile.get("profile_id") or "")
        provider_user_id = str(profile.get("provider_user_id") or "")
        account = next(
            (item for item in profile.get("accounts") or []
             if item.get("network") == network.key),
            None,
        )
        try:
            if network.family is ChainFamily.EVM:
                account_address = normalize_evm_address(
                    str((account or {}).get("address") or "")
                )
                sender = normalize_evm_address(intent.from_address)
            else:
                account_address = normalize_solana_address(
                    str((account or {}).get("address") or "")
                )
                sender = normalize_solana_address(intent.from_address)
        except ValueError as exc:
            raise WalletProviderError(
                "The final wallet account binding is invalid."
            ) from exc
        if (
            not profile_id
            or provider_user_id != profile_id
            or intent.profile_id != profile_id
            or account_address != sender
        ):
            raise WalletProviderError(
                "The final wallet profile or sender binding changed."
            )
        if network.key == BASE_MAINNET.key:
            manifest_errors = validate_base_mainnet_provider_manifest()
            if manifest_errors:
                raise WalletProviderError(
                    "The reviewed Base mainnet provider contract changed."
                )
        credentials = await self.credentials_for_network(network.key)
        if credentials is None:
            raise WalletProviderError(
                "CDP credentials are not completely configured for this environment."
            )
        chain_id = None
        if network.family is ChainFamily.EVM:
            try:
                chain_id = await get_chain_id(network.key)
            except BaseRpcError as exc:
                raise WalletProviderError(
                    "The final network identity could not be verified."
                ) from exc
            if chain_id != network.chain_id:
                raise WalletProviderError(
                    "The final network identity does not match the approved chain."
                )
        authorization = await self.get_delegation_status(profile, network.key)
        if not authorization.get("active"):
            raise WalletProviderError(
                "The wallet authorization is no longer active."
            )
        if network.family is ChainFamily.SOLANA:
            if intent.gas_sponsored or intent.estimated_gas_fee_wei < 0:
                raise WalletProviderError(
                    "The final Solana fee policy no longer matches the quote."
                )
        elif network.testnet:
            if not intent.gas_sponsored or intent.estimated_gas_fee_wei != 0:
                raise WalletProviderError(
                    "The final sponsored-gas policy no longer matches the quote."
                )
        elif (
            intent.gas_sponsored
            or intent.max_gas_fee_wei <= 0
            or intent.estimated_gas_fee_wei < 0
            or intent.estimated_gas_fee_wei > intent.max_gas_fee_wei
        ):
            raise WalletProviderError(
                "The final mainnet gas policy no longer matches the quote."
            )
        return {
            "network": network.key,
            "chain_id": chain_id,
            "network_reference": network.reference,
            "provider": self.name,
            "authorization_active": True,
            "operation_state": "not-created",
            "nonce_strategy": "provider-managed-at-idempotent-submission",
        }

    async def submit_transaction(self, profile: dict, intent: TransactionIntent) -> dict:
        """Submit one sponsored Base Sepolia transfer through delegated signing."""
        if intent.network == SOLANA_DEVNET.key:
            return await self._submit_solana_transaction(profile, intent)
        if intent.network == BASE_MAINNET.key:
            return await self._submit_base_mainnet_transaction(profile, intent)
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
        call_to = to_address
        call_value = intent.value_wei
        call_data = "0x"
        if intent.asset_kind == "erc20":
            try:
                call_to = normalize_evm_address(intent.asset_contract or "").lower()
                if (
                    not intent.asset_symbol
                    or intent.asset_decimals is None
                    or intent.asset_decimals < 0
                    or intent.asset_decimals > 255
                ):
                    raise ValueError("missing token metadata")
                call_data = _erc20_transfer_data(to_address, intent.value_wei)
            except ValueError as exc:
                raise WalletProviderError(
                    "The token intent contains invalid immutable fields."
                ) from exc
            call_value = 0
        elif intent.asset_kind != "native":
            raise WalletProviderError("That transaction asset type is unsupported.")
        elif (
            intent.asset_contract is not None
            or intent.asset_symbol not in {None, BASE_SEPOLIA.native_symbol}
            or intent.asset_decimals not in {None, BASE_SEPOLIA.native_decimals}
        ):
            raise WalletProviderError(
                "The native transaction contains invalid immutable asset fields."
            )
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
                call_to,
                call_value,
                idempotency_key,
                call_data,
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
                or normalize_evm_address(str(returned_calls[0].get("to") or "")) != call_to
                or int(returned_calls[0].get("value", -1)) != call_value
                or str(returned_calls[0].get("data") or "").lower() != call_data.lower()
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

    async def _submit_base_mainnet_transaction(
        self, profile: dict, intent: TransactionIntent
    ) -> dict:
        """Submit one user-funded Base mainnet transfer through the shared project."""
        provider_user_id = str(profile.get("provider_user_id") or "")
        profile_id = str(profile.get("profile_id") or "")
        account = next(
            (item for item in profile.get("accounts") or []
             if item.get("network") == BASE_MAINNET.key),
            None,
        )
        try:
            sender = normalize_evm_address(str((account or {}).get("address") or "")).lower()
            recipient = normalize_evm_address(intent.to_address).lower()
            intent_sender = normalize_evm_address(intent.from_address).lower()
        except ValueError as exc:
            raise WalletProviderError(
                "The Base mainnet transaction contains an invalid wallet address."
            ) from exc
        if (not provider_user_id or provider_user_id != profile_id
                or intent.profile_id != profile_id or sender != intent_sender
                or intent.value_wei <= 0 or intent.gas_sponsored
                or intent.max_gas_fee_wei <= 0
                or intent.estimated_gas_fee_wei < 0
                or intent.estimated_gas_fee_wei > intent.max_gas_fee_wei):
            raise WalletProviderError(
                "The Base mainnet transaction does not match this wallet or fee policy."
            )
        call_to = recipient
        call_value = intent.value_wei
        call_data = "0x"
        if intent.asset_kind == "erc20":
            try:
                call_to = normalize_evm_address(intent.asset_contract or "").lower()
                if (not intent.asset_symbol or intent.asset_decimals is None
                        or not 0 <= intent.asset_decimals <= 255):
                    raise ValueError("missing token metadata")
                call_data = _erc20_transfer_data(recipient, intent.value_wei)
            except ValueError as exc:
                raise WalletProviderError(
                    "The Base mainnet token intent is invalid."
                ) from exc
            call_value = 0
        elif (intent.asset_kind != "native" or intent.asset_contract is not None
              or intent.asset_symbol not in {None, BASE_MAINNET.native_symbol}
              or intent.asset_decimals not in {None, BASE_MAINNET.native_decimals}):
            raise WalletProviderError(
                "The Base mainnet native asset binding is invalid."
            )
        credentials = await self.credentials_for_network(BASE_MAINNET.key)
        if credentials is None:
            raise WalletProviderError(
                "Base mainnet CDP credentials are not completely configured."
            )
        key = str(uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"sick-cogs:cdp:send:{BASE_MAINNET.key}:{profile_id}:{intent.intent_id}",
        ))
        try:
            result = await self._api_client(credentials).send_smart_account_user_operation(
                provider_user_id, sender, credentials.project_id, "base", call_to,
                call_value, key, call_data, use_cdp_paymaster=False,
            )
            status = str(result.get("status") or "")
            user_op_hash = str(result.get("userOpHash") or "")
            transaction_hash = str(result.get("transactionHash") or "") or None
            calls = result.get("calls") or []
            if (str(result.get("network") or "") != "base"
                    or status not in {"pending", "signed", "broadcast", "complete",
                                      "dropped", "failed"}
                    or not HASH_PATTERN.fullmatch(user_op_hash)
                    or transaction_hash is not None
                    and not HASH_PATTERN.fullmatch(transaction_hash)
                    or not isinstance(calls, list) or len(calls) != 1
                    or normalize_evm_address(str(calls[0].get("to") or "")).lower() != call_to
                    or int(calls[0].get("value", -1)) != call_value
                    or str(calls[0].get("data") or "").lower() != call_data.lower()):
                raise ValueError("CDP returned mismatched Base mainnet operation data")
            return {
                "provider_status": status,
                "user_operation_hash": user_op_hash.lower(),
                "transaction_hash": transaction_hash.lower() if transaction_hash else None,
                "block_number": None,
            }
        except (CdpApiError, AttributeError, TypeError, ValueError) as exc:
            raise WalletProviderError(
                "CDP could not safely submit the user-funded Base mainnet transfer."
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
                or sender != intent_sender or intent.value_wei <= 0 or intent.gas_sponsored
                or intent.asset_kind != "native" or intent.asset_contract is not None):
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
        if (intent.network not in {BASE_SEPOLIA.key, BASE_MAINNET.key}
                or not intent.user_operation_hash):
            raise WalletProviderError("Only submitted Base operations can be refreshed.")
        provider_user_id = str(profile.get("provider_user_id") or "")
        account = next(
            (item for item in profile.get("accounts") or [] if item.get("network") == intent.network),
            None,
        )
        try:
            address = normalize_evm_address(str((account or {}).get("address") or ""))
        except ValueError as exc:
            raise WalletProviderError("The stored wallet address is invalid.") from exc
        if not provider_user_id or address != normalize_evm_address(intent.from_address):
            raise WalletProviderError("The wallet profile does not match this operation.")
        credentials = await self.credentials_for_network(intent.network)
        if credentials is None:
            raise WalletProviderError("CDP credentials are not completely configured.")
        try:
            result = await self._api_client(credentials).get_smart_account_user_operation(
                provider_user_id, address, intent.user_operation_hash, credentials.project_id
            )
        except CdpApiError as cdp_exc:
            try:
                result = await get_user_operation_receipt(address, intent.user_operation_hash, intent.network)
            except BaseRpcError as rpc_exc:
                raise WalletProviderError(
                    "CDP and the selected Base network could not retrieve the submitted operation status."
                ) from rpc_exc
            if result is None:
                raise WalletProviderError(
                    "CDP could not retrieve the operation and it is not confirmed on the selected Base network yet."
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
            if provider_status == "complete":
                public = await get_user_operation_receipt(
                    address, intent.user_operation_hash, intent.network
                )
                if public is None:
                    return {
                        "provider_status": (
                            "reorged" if intent.block_number is not None else "broadcast"
                        ),
                        "user_operation_hash": user_op_hash.lower(),
                        "transaction_hash": transaction_hash.lower() if transaction_hash else None,
                        "block_number": None,
                    }
                public_hash = str(public.get("transactionHash") or "").lower()
                public_receipts = public.get("receipts") or []
                public_block = int(public_receipts[0]["blockNumber"])
                if (
                    public.get("status") not in {"complete", "failed"}
                    or not HASH_PATTERN.fullmatch(public_hash)
                    or transaction_hash is not None
                    and public_hash != transaction_hash.lower()
                    or intent.transaction_hash is not None
                    and public_hash != intent.transaction_hash.lower()
                    or block_number is not None
                    and public_block != block_number
                    or intent.block_number is not None
                    and public_block != intent.block_number
                ):
                    return {
                        "provider_status": "reorged",
                        "user_operation_hash": user_op_hash.lower(),
                        "transaction_hash": public_hash or None,
                        "block_number": public_block,
                    }
                if public["status"] == "failed":
                    provider_status = "failed"
                else:
                    latest_block = await get_evm_block_number(intent.network)
                    confirmations = max(0, latest_block - public_block + 1)
                    provider_status = (
                        "complete"
                        if confirmations >= BASE_FINALITY_CONFIRMATIONS
                        else "confirming"
                    )
                transaction_hash = public_hash
                block_number = public_block
            return {
                "provider_status": provider_status,
                "user_operation_hash": user_op_hash.lower(),
                "transaction_hash": transaction_hash.lower() if transaction_hash else None,
                "block_number": block_number,
            }
        except (AttributeError, IndexError, TypeError, ValueError) as exc:
            raise WalletProviderError("CDP could not retrieve the submitted operation status.") from exc

    async def _get_solana_transaction_status(self, profile: dict, intent: TransactionIntent) -> dict:
        account = next((item for item in profile.get("accounts") or []
                        if item.get("network") == SOLANA_DEVNET.key), None)
        try:
            sender = normalize_solana_address(str((account or {}).get("address") or ""))
            signature = normalize_solana_signature(intent.transaction_hash or "")
            if sender != normalize_solana_address(intent.from_address):
                raise ValueError("sender mismatch")
            transaction = await get_solana_transaction(signature, commitment="finalized")
        except (BaseRpcError, ValueError) as exc:
            raise WalletProviderError("Solana Devnet could not retrieve this transaction status.") from exc
        if transaction is None:
            return {"provider_status": (
                        "reorged" if intent.block_number is not None else "broadcast"
                    ), "transaction_hash": signature, "block_number": None}
        if intent.block_number is not None and int(transaction["slot"]) != intent.block_number:
            return {"provider_status": "reorged", "transaction_hash": signature,
                    "block_number": int(transaction["slot"])}
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
            if (
                intent.status is not IntentStatus.PENDING
                or intent.value_wei <= 0
                or intent.asset_kind != "native"
                or intent.asset_contract is not None
                or intent.asset_symbol not in {None, SOLANA_DEVNET.native_symbol}
                or intent.asset_decimals not in {None, SOLANA_DEVNET.native_decimals}
                or intent.gas_sponsored
                or intent.provider_status is not None
                or intent.user_operation_hash is not None
                or intent.transaction_hash is not None
                or intent.block_number is not None
            ):
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
        if intent.network == BASE_MAINNET.key:
            if (intent.status is not IntentStatus.PENDING or intent.value_wei <= 0
                    or intent.gas_sponsored or intent.provider_status is not None
                    or intent.user_operation_hash is not None
                    or intent.transaction_hash is not None or intent.block_number is not None):
                raise WalletProviderError("Only a clean pending Base mainnet intent can be quoted.")
            try:
                sender = normalize_evm_address(intent.from_address)
                recipient = normalize_evm_address(intent.to_address)
                call_to, call_value, call_data = recipient, intent.value_wei, "0x"
                if intent.asset_kind == "erc20":
                    call_to = normalize_evm_address(intent.asset_contract or "")
                    call_value = 0
                    call_data = _erc20_transfer_data(recipient, intent.value_wei)
                elif intent.asset_kind != "native" or intent.asset_contract is not None:
                    raise ValueError("unsupported asset")
                quote = await quote_evm_call_fee(
                    BASE_MAINNET.key, sender, call_to, call_value, call_data
                )
                estimate = int(quote["fee_wei"])
            except (BaseRpcError, TypeError, ValueError) as exc:
                raise WalletProviderError("The Base mainnet fee estimate is unavailable.") from exc
            if intent.max_gas_fee_wei > 0 and estimate <= intent.max_gas_fee_wei:
                return replace(intent, from_address=sender, to_address=recipient)
            tolerance = max(estimate * 125 // 100, estimate + 10**13)
            return replace(
                intent, from_address=sender, to_address=recipient,
                estimated_gas_fee_wei=estimate, max_gas_fee_wei=tolerance,
                gas_sponsored=False,
            )
        if intent.asset_kind == "erc20":
            if (
                intent.status is not IntentStatus.PENDING
                or intent.network != BASE_SEPOLIA.key
                or not intent.gas_sponsored
                or intent.estimated_gas_fee_wei != 0
                or intent.value_wei <= 0
                or not intent.asset_symbol
                or intent.asset_decimals is None
                or intent.asset_decimals < 0
                or intent.asset_decimals > 255
                or intent.provider_status is not None
                or intent.user_operation_hash is not None
                or intent.transaction_hash is not None
                or intent.block_number is not None
            ):
                raise WalletProviderError(
                    "Only a clean, pending, sponsored Base Sepolia token intent can be quoted."
                )
            try:
                from_address = normalize_evm_address(intent.from_address)
                to_address = normalize_evm_address(intent.to_address)
                contract = normalize_evm_address(intent.asset_contract or "")
                _erc20_transfer_data(to_address, intent.value_wei)
            except ValueError as exc:
                raise WalletProviderError(
                    "The token transaction quote contains invalid immutable fields."
                ) from exc
            return replace(
                intent,
                from_address=from_address,
                to_address=to_address,
                asset_contract=contract.lower(),
                estimated_gas_fee_wei=0,
                gas_sponsored=True,
            )
        if intent.asset_kind != "native":
            raise WalletProviderError("That transaction asset type is unsupported.")
        if (
            intent.status is not IntentStatus.PENDING
            or intent.network != BASE_SEPOLIA.key
            or not intent.gas_sponsored
            or intent.estimated_gas_fee_wei != 0
            or intent.value_wei <= 0
            or intent.asset_contract is not None
            or intent.asset_symbol not in {None, BASE_SEPOLIA.native_symbol}
            or intent.asset_decimals not in {None, BASE_SEPOLIA.native_decimals}
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
            client = self._api_client(credentials)
            end_user = await client.get_end_user(provider_user_id)
            if str(end_user.get("userId") or "") != provider_user_id:
                raise ValueError("CDP returned a different end user")
            accounts = self._delegation_addresses(end_user, profile)
            await client.revoke_user_delegation(
                provider_user_id, credentials.project_id
            )
            for address in accounts:
                await client.revoke_account_delegation(
                    provider_user_id, address, credentials.project_id
                )
        except (CdpApiError, TypeError, ValueError) as exc:
            raise WalletProviderError(
                "CDP could not revoke this wallet profile signing authorization."
            ) from exc
