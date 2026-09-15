import logging
import secrets
import time

import discord

from redbot.core import commands

from .backend import JwtAuthMixin, RecoveryRelayMixin
from .backend.clanker_lifecycle import ClankerLifecycleMixin
from .backend.confirmation import ConfirmationProcessorMixin
from .backend.config import WalletConfigMixin, create_config
from .backend.provisioning import WalletProvisioningMixin
from .backend.usage import ProviderUsageMixin
from .commands import WalletAdminCommands, WalletCommands
from .core.clanker import signing_intent_from_clanker_launch
from .core.models import IntentStatus
from .providers.clanker import validate_clanker_deployment_call
from .core.networks import BASE_SEPOLIA
from .providers import CdpWalletProvider
from .providers.cdp import CLANKER_DEPLOY_GAS_LIMIT

log = logging.getLogger("red.Sick-Cogs.CryptoWallet")


class CryptoWallet(
    ProviderUsageMixin,
    ClankerLifecycleMixin,
    ConfirmationProcessorMixin,
    WalletCommands,
    WalletAdminCommands,
    WalletConfigMixin,
    WalletProvisioningMixin,
    JwtAuthMixin,
    RecoveryRelayMixin,
    commands.Cog,
):
    """Manage testnet smart-wallet identity, authorization, and signing."""

    def __init__(self, bot):
        self.bot = bot
        self.config = create_config(self)
        self.wallet_read_cooldowns = {}
        self.initialize_provisioning()
        self.initialize_provider_usage()
        self.wallet_provider = CdpWalletProvider(
            bot,
            request_limiter=self.limit_cdp_request,
            request_observer=self.record_cdp_request,
        )
        self.initialize_confirmation_processor()

    async def initialize(self):
        """Initialize the deployment identity and signed web authorization."""
        if not await self.config.deployment_id():
            await self.config.deployment_id.set(secrets.token_urlsafe(24))
        try:
            await self.initialize_jwt_auth()
        except Exception:
            log.exception("The CryptoWallet custom-auth signing key could not be initialized")

    def cog_unload(self):
        self.confirmation_processor_task.cancel()
        self.usage_flush_task.cancel()
        self.bot.loop.create_task(self.flush_provider_usage())

    async def tokenfactory_wallet_context(self, user) -> dict:
        """Return the narrow public wallet identity needed by TokenFactory."""

        profile = await self.get_or_create_wallet_profile(user)
        account = next(
            (
                item
                for item in profile.get("accounts") or []
                if item.get("network") == BASE_SEPOLIA.key
            ),
            None,
        )
        if not profile.get("profile_id") or not account or not account.get("address"):
            raise RuntimeError("The Base Sepolia wallet profile is incomplete.")
        return {
            "profile_id": str(profile["profile_id"]),
            "owner_address": str(account["address"]),
            "network": BASE_SEPOLIA.key,
            "chain_id": BASE_SEPOLIA.chain_id,
        }

    async def tokenfactory_deployment_status(self) -> dict:
        """Expose only the reviewed TokenFactory deployment state."""

        return await self.wallet_provider.token_factory_deployment_status()

    async def tokenfactory_deploy_pinned_factory(
        self, user, creation_code: str, attempt_id: str
    ) -> dict:
        """Deploy only the provider-pinned TokenFactory artifact for this wallet user."""

        profile = await self.get_or_create_wallet_profile(user)
        return await self.wallet_provider.deploy_token_factory(
            profile, creation_code, attempt_id
        )

    async def tokenfactory_operation_status(
        self, user, user_operation_hash: str
    ) -> dict:
        """Return the CDP state of a submitted factory deployment operation."""

        profile = await self.get_or_create_wallet_profile(user)
        return await self.wallet_provider.token_factory_operation_status(
            profile, user_operation_hash
        )

    async def tokenfactory_deploy_fixed_supply_token(
        self, user, **parameters
    ) -> dict:
        """Submit one structured fixed-supply token deployment."""

        profile = await self.get_or_create_wallet_profile(user)
        return await self.wallet_provider.deploy_fixed_supply_token(
            profile, **parameters
        )

    async def tokenfactory_verify_fixed_supply_token(self, **parameters) -> dict:
        """Verify one fixed-supply token through the pinned factory."""

        return await self.wallet_provider.verify_fixed_supply_token(**parameters)

    async def tokenfactory_verify_external_transaction(self, **parameters) -> dict:
        """Verify one external wallet submitted the exact pinned factory call."""

        return await self.wallet_provider.verify_external_fixed_supply_transaction(
            **parameters
        )

    async def tokenfactory_create_external_handoff(
        self, discord_user_id: int, draft: dict, request_id: str
    ) -> tuple[str, int]:
        """Create a protected handoff that requires no CDP wallet profile."""

        return await self.create_tokenfactory_handoff(
            discord_user_id, draft, request_id
        )

    async def clanker_collect_rewards(
        self, user, *, token: str, token_admin: str, attempt_id: str
    ) -> dict:
        """Collect rewards for one token only after CryptoWallet admin verification."""
        profile = await self.get_or_create_wallet_profile(user)
        return await self.wallet_provider.submit_clanker_reward_collection(
            profile, token, token_admin, attempt_id
        )

    async def clanker_reward_status(
        self, user, *, token: str, token_admin: str, user_operation_hash: str
    ) -> dict:
        """Refresh one previously validated Clanker collection operation."""
        profile = await self.get_or_create_wallet_profile(user)
        return await self.wallet_provider.clanker_reward_operation_status(
            profile, token, token_admin, user_operation_hash
        )

    async def clanker_withdraw_treasuries(
        self, user, *, token_admin: str, creator_treasury: str,
        platform_treasury: str, claims: list[dict], attempt_id: str,
        platform_only: bool = False,
    ) -> dict:
        """Submit only the separately reviewed Clanker treasury withdrawals."""
        profile = await self.get_or_create_wallet_profile(user)
        return await self.wallet_provider.submit_clanker_treasury_withdrawal(
            profile, token_admin=token_admin, creator_treasury=creator_treasury,
            platform_treasury=platform_treasury, claims=claims, attempt_id=attempt_id,
            platform_only=platform_only,
        )

    async def clanker_treasury_status(
        self, user, *, token_admin: str, creator_treasury: str, platform_treasury: str,
        claims: list[dict], user_operation_hash: str, platform_only: bool = False,
    ) -> dict:
        """Refresh a separately reviewed Clanker treasury withdrawal."""
        profile = await self.get_or_create_wallet_profile(user)
        return await self.wallet_provider.clanker_treasury_operation_status(
            profile, token_admin=token_admin, creator_treasury=creator_treasury,
            platform_treasury=platform_treasury, claims=claims,
            user_operation_hash=user_operation_hash, platform_only=platform_only)

    async def clanker_create_external_handoff(
        self, discord_user_id: int, handoff: dict
    ) -> tuple[str, int]:
        """Create a protected companion handoff without wallet signer authority."""

        return await self.create_clanker_external_handoff(discord_user_id, handoff)

    async def tokenfactory_register_verified_token(self, user, token: dict) -> None:
        """Add one factory-verified token to the shared community registry."""

        contract = str(token["contract_address"]).lower()
        async with self.config.token_registry() as registry:
            entries = registry.setdefault(BASE_SEPOLIA.key, {})
            existing = entries.get(contract)
            if existing is not None:
                if (
                    str(existing.get("symbol")) != str(token["symbol"])
                    or int(existing.get("decimals", -1)) != int(token["decimals"])
                ):
                    raise RuntimeError("The existing token registry entry conflicts with deployment.")
                return
            active = sum(
                item.get("status") in {"community", "recognized"}
                for item in entries.values()
            )
            if active >= 25:
                raise RuntimeError("The Base Sepolia community token registry is full.")
            entries[contract] = {
                "contract_address": contract,
                "symbol": str(token["symbol"]),
                "name": str(token["name"]),
                "decimals": int(token["decimals"]),
                "status": "community",
                "submitted_by": user.id,
                "submitted_at": int(time.time()),
                "source": "tokenfactory",
            }

    async def clanker_register_verified_token(self, user, token: dict) -> None:
        """Register one receipt-verified Clanker token for wallet discovery."""
        required = {"contract_address", "symbol", "name", "decimals"}
        if not isinstance(token, dict) or set(token) != required:
            raise ValueError("Clanker returned an invalid verified token record.")
        contract = str(token["contract_address"]).lower()
        if not contract.startswith("0x") or len(contract) != 42:
            raise ValueError("Clanker returned an invalid token contract address.")
        async with self.config.token_registry() as registry:
            entries = registry.setdefault(BASE_SEPOLIA.key, {})
            existing = entries.get(contract)
            if existing is not None:
                if (
                    str(existing.get("symbol")) != str(token["symbol"])
                    or int(existing.get("decimals", -1)) != int(token["decimals"])
                ):
                    raise RuntimeError(
                        "The existing token registry entry conflicts with the Clanker receipt."
                    )
                return
            active = sum(
                item.get("status") in {"community", "recognized"}
                for item in entries.values()
            )
            if active >= 25:
                raise RuntimeError("The Base Sepolia community token registry is full.")
            entries[contract] = {
                "contract_address": contract,
                "symbol": str(token["symbol"]),
                "name": str(token["name"]),
                "decimals": int(token["decimals"]),
                "status": "community",
                "submitted_by": int(user.id),
                "submitted_at": int(time.time()),
                "source": "clanker",
            }

    async def clanker_requester_address(self, user) -> str:
        """Return the requesting user's public Base Sepolia wallet address."""
        profile = await self.get_or_create_wallet_profile(user)
        account = self._account_for_network(profile, BASE_SEPOLIA.key)
        address = str(account.get("address") or "") if account else ""
        if not address:
            raise RuntimeError("CryptoWallet has no Base Sepolia address for this user.")
        return address

    @staticmethod
    def clanker_execution_terms() -> dict:
        """Return the exact bounded spending policy shown on Clanker review cards."""
        return {
            "gas_limit": CLANKER_DEPLOY_GAS_LIMIT,
            "native_value_wei": 0,
            "gas_sponsored": True,
            "gas_payer": "CDP paymaster",
        }

    async def clanker_launch_verified(
        self, user, launch: dict, operation: dict, execution_terms: dict
    ) -> dict:
        """Submit one Discord-reviewed Clanker operation under active delegation."""
        if execution_terms != self.clanker_execution_terms():
            raise ValueError(
                "The reviewed Clanker gas or spending policy no longer matches CryptoWallet."
            )
        if int(operation.get("value", -1)) != execution_terms["native_value_wei"]:
            raise ValueError("The reviewed Clanker native value does not match the operation.")
        if await self.config.provider_paused():
            raise RuntimeError("CryptoWallet provider operations are paused.")
        user_config = self.config.user(user)
        if await user_config.security_locked():
            raise RuntimeError("This CryptoWallet profile is security locked.")
        if int(launch.get("requester_id", 0)) != int(user.id):
            raise ValueError("Clanker requester does not match the signing wallet user.")

        profile = await self.get_or_create_wallet_profile(user)
        account = next(
            (item for item in profile.get("accounts") or []
             if item.get("network") == BASE_SEPOLIA.key), None
        )
        deployment_id = await self.config.deployment_id()
        application_id = self.discord_application_id()
        if not all((profile.get("profile_id"), account,
                    account.get("address") if account else None,
                    deployment_id, application_id)):
            raise RuntimeError("CryptoWallet signing identity is incomplete.")

        intent = signing_intent_from_clanker_launch(
            launch, operation, deployment_id=str(deployment_id),
            discord_application_id=int(application_id),
            profile_id=str(profile["profile_id"]),
            wallet_address=str(account["address"]),
        )
        validate_clanker_deployment_call(
            intent, to=str(operation["to"]), value=int(operation["value"]),
            data=str(operation["data"]),
        )
        delegation = await self.wallet_provider.get_delegation_status(
            profile, BASE_SEPOLIA.key
        )
        if not delegation.get("active"):
            try:
                expires_at = await self.send_authorization_link(user, profile)
            except discord.Forbidden as exc:
                raise RuntimeError(
                    "I could not DM the CryptoWallet authorization link. Enable DMs and try again."
                ) from exc
            return {
                "status": "authorization_required",
                "intent_id": intent.intent_id,
                "payload_hash": intent.payload_hash,
                "authorization_expires_at": int(expires_at),
                "provider_status": None,
                "user_operation_hash": None,
                "transaction_hash": None,
            }

        async with user_config.intents() as intents:
            existing = intents.get(intent.intent_id)
            if existing is not None:
                stored = self._stored_clanker_intent(existing)
                if stored != intent:
                    raise RuntimeError("A different Clanker intent already uses this ID.")
                if existing.get("status") != IntentStatus.PENDING.value:
                    raise RuntimeError("This Clanker launch has already entered its lifecycle.")
            else:
                intents[intent.intent_id] = {
                    **intent.to_dict(), "status": IntentStatus.PENDING.value
                }

        try:
            result = await self.submit_claimed_clanker_intent(
                int(user.id), intent.intent_id, intent.payload_hash,
                secrets.token_urlsafe(24),
            )
        except RuntimeError:
            log.exception(
                "Clanker launch %s entered status recovery after provider submission",
                launch.get("launch_id"),
            )
            lifecycle = await self.clanker_intent_status(
                int(user.id), intent.intent_id, intent.payload_hash
            )
            if lifecycle["status"] != IntentStatus.UNCERTAIN.value:
                raise
            result = lifecycle
        return {
            "status": str(result["status"]),
            "intent_id": intent.intent_id,
            "payload_hash": intent.payload_hash,
            "authorization_expires_at": None,
            "provider_status": result.get("provider_status"),
            "user_operation_hash": result.get("user_operation_hash"),
            "transaction_hash": result.get("transaction_hash"),
        }

    async def clanker_internal_status(
        self, user, signing_intent_id: str, signing_payload_hash: str
    ) -> dict:
        """Return only persisted status for the requesting user exact Clanker intent."""
        profile = await self.get_or_create_wallet_profile(user)
        data = await self.refresh_clanker_intent_status(
            int(user.id), signing_intent_id, signing_payload_hash
        )
        stored = await self.config.user(user).intents.get_raw(
            signing_intent_id, default=None
        )
        intent = self._stored_clanker_intent(stored)
        account = next((item for item in profile.get("accounts") or []
                        if item.get("network") == BASE_SEPOLIA.key), None)
        if (
            intent.profile_id != str(profile.get("profile_id") or "")
            or intent.wallet_address != str((account or {}).get("address") or "").lower()
        ):
            raise RuntimeError("The current wallet profile no longer matches this Clanker intent.")
        return {"route": "internal", "signing_intent_id": signing_intent_id,
                "signing_payload_hash": signing_payload_hash.lower(), **data}

    async def red_delete_data_for_user(self, *, requester, user_id: int):
        """Revoke bot signing authority, then delete all Discord-side user data."""
        user_config = self.config.user_from_id(user_id)
        try:
            profile = await user_config.profile()
            if isinstance(profile, dict) and profile:
                await self.wallet_provider.revoke_authorization(
                    profile, BASE_SEPOLIA.key
                )
        except Exception as exc:
            log.warning(
                "Wallet delegation revocation failed during user-data deletion; "
                "local deletion will continue: error_class=%s",
                type(exc).__name__,
            )
        finally:
            await user_config.clear()

    def discord_application_id(self) -> int | None:
        """Return the immutable Discord application ID for this bot process."""
        application_id = getattr(self.bot, "application_id", None)
        if application_id:
            return int(application_id)
        user = getattr(self.bot, "user", None)
        if user is not None and getattr(user, "id", None):
            return int(user.id)
        return None
