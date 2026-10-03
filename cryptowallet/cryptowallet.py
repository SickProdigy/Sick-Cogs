import hashlib
import logging
import re
import secrets
import time

import discord

from redbot.core import commands

from .backend import JwtAuthMixin, RecoveryRelayMixin, TotpSecurityMixin
from .backend.clanker_lifecycle import ClankerLifecycleMixin
from .backend.confirmation import ConfirmationProcessorMixin
from .backend.config import WalletConfigMixin, create_config
from .backend.history import WalletHistoryMixin
from .backend.provisioning import WalletProvisioningMixin
from .backend.usage import ProviderUsageMixin
from .commands import WalletAdminCommands, WalletCommands
from .core.clanker import signing_intent_from_clanker_launch
from .core.models import IntentStatus, TransactionIntent
from .core.polymarket import (
    validate_polymarket_clob_auth_typed_data,
    validate_polymarket_session_batch_typed_data,
    validate_polymarket_settlement_batch_typed_data,
    validate_polymarket_withdrawal_batch_typed_data,
)
from .core.validation import normalize_evm_address
from .providers.clanker import validate_clanker_deployment_call
from .core.networks import BASE_MAINNET, BASE_SEPOLIA, KNOWN_NETWORKS, NetworkCapability
from .providers import CdpWalletProvider
from .commands.views import WalletIntentView
from .providers.base_rpc import get_chain_id, get_contract_code
from .providers.cdp import CLANKER_DEPLOY_GAS_LIMIT

log = logging.getLogger("red.Sick-Cogs.CryptoWallet")


class CryptoWallet(
    ProviderUsageMixin,
    WalletHistoryMixin,
    ClankerLifecycleMixin,
    ConfirmationProcessorMixin,
    WalletCommands,
    WalletAdminCommands,
    WalletConfigMixin,
    WalletProvisioningMixin,
    TotpSecurityMixin,
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
        self.initialize_wallet_history()
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
        try:
            await self.initialize_totp_security()
        except Exception:
            log.exception("The CryptoWallet TOTP encryption key could not be initialized")

    def cog_unload(self):
        self.confirmation_processor_task.cancel()
        self.usage_flush_task.cancel()
        self.bot.loop.create_task(self.flush_provider_usage())

    async def tokenfactory_default_network(self) -> str:
        """Return the TokenFactory network selected by the wallet operating mode."""
        mode = str(await self.config.operating_mode() or "testnet").lower()
        return BASE_MAINNET.key if mode in {"mainnet", "mainnet-only"} else BASE_SEPOLIA.key

    async def clanker_default_network(self) -> str:
        """Return the Clanker network selected by the wallet operating mode."""
        mode = str(await self.config.operating_mode() or "testnet").lower()
        return BASE_MAINNET.key if mode in {"mainnet", "mainnet-only"} else BASE_SEPOLIA.key

    async def tokenfactory_wallet_context(
        self, user, network: str = BASE_SEPOLIA.key
    ) -> dict:
        """Return the narrow public wallet identity needed by TokenFactory."""

        if network == BASE_MAINNET.key:
            mode = str(await self.config.operating_mode() or "testnet").lower()
            policy = await self.config.base_mainnet_policy()
            if (mode not in {"mainnet", "mainnet-only"}
                    or policy.get("enabled") is not True
                    or policy.get("paused", True) is not False):
                raise RuntimeError(
                    "Enable CryptoWallet Base mainnet before using an integrated mainnet product."
                )
        profile = await self.get_or_create_wallet_profile(user)
        if network == BASE_MAINNET.key:
            profile = await self.ensure_mainnet_wallet_profile(user, profile)
        account = next(
            (
                item
                for item in profile.get("accounts") or []
                if item.get("network") == network
            ),
            None,
        )
        selected = KNOWN_NETWORKS.get(network)
        if network not in {BASE_SEPOLIA.key, BASE_MAINNET.key} or selected is None:
            raise RuntimeError("TokenFactory requires a reviewed Base network.")
        if not profile.get("profile_id") or not account or not account.get("address"):
            raise RuntimeError("The selected Base wallet profile is incomplete.")
        return {
            "profile_id": str(profile["profile_id"]),
            "owner_address": str(account["address"]),
            "network": network,
            "chain_id": selected.chain_id,
        }

    async def polymarket_wallet_context(self, user) -> dict:
        """Return the user's verified public CDP owner for Polygon derivation."""

        from .core.polymarket import PolymarketSignerContext

        profile = await self.get_or_create_wallet_profile(user)
        context = PolymarketSignerContext(**(
            await self.wallet_provider.polymarket_signer_context(profile, user.id)
        ))
        return context.to_dict()


    async def polymarket_withdrawal_destination(self, user, chain_id: int) -> dict:
        """Return an existing CryptoWallet EVM address without provisioning it."""

        if chain_id != BASE_MAINNET.chain_id:
            raise RuntimeError("CryptoWallet does not support that withdrawal chain.")
        profile = await self.get_or_create_wallet_profile(user)
        accounts = tuple(profile.get("accounts") or ())
        account = next(
            (item for item in accounts if item.get("network") == BASE_MAINNET.key),
            None,
        ) or next(
            (item for item in accounts if item.get("network") == BASE_SEPOLIA.key),
            None,
        )
        if not profile.get("profile_id") or not account or not account.get("address"):
            raise RuntimeError("CryptoWallet has no EVM address for this user.")
        return {
            "profile_id": str(profile["profile_id"]),
            "network": BASE_MAINNET.key, "chain_id": BASE_MAINNET.chain_id,
            "address": normalize_evm_address(str(account["address"])).lower(),
        }


    async def polymarket_sign_clob_auth(
        self, user, *, typed_data: dict, approval_fingerprint: str,
    ) -> dict:
        """Use the verified CDP EOA for one exact, user-approved ClobAuth proof."""

        if not bool(await self.config.polymarket_typed_signing_enabled()):
            raise RuntimeError("CryptoWallet Polymarket signing remains disabled.")
        if await self.config.provider_paused():
            raise RuntimeError("CryptoWallet provider operations are paused.")
        if await self.config.user(user).security_locked():
            raise RuntimeError("This CryptoWallet profile is security locked.")
        if not re.fullmatch(r"[0-9a-f]{64}", approval_fingerprint):
            raise RuntimeError("Polymarket signing approval is invalid.")
        try:
            message = typed_data.get("message") if isinstance(typed_data, dict) else None
            signer_address = normalize_evm_address(
                str((message or {}).get("address") or "")
            ).lower()
            validate_polymarket_clob_auth_typed_data(typed_data, signer_address)
        except (AttributeError, TypeError, ValueError) as exc:
            raise RuntimeError("Polymarket ClobAuth typed data is invalid.") from exc
        profile = await self.get_or_create_wallet_profile(user)
        return await self.wallet_provider.sign_polymarket_clob_auth(
            profile, user.id, signer_address, typed_data,
            f"polymarket-clob-auth-{approval_fingerprint}",
        )

    async def polymarket_sign_session_batch(
        self, user, *, owner_address: str, wallet_address: str,
        session_address: str, action: str, valid_until: int | None,
        typed_data: dict, approval_fingerprint: str,
    ) -> dict:
        """Sign one exact user-approved Deposit Wallet session-key Batch."""

        if not bool(await self.config.polymarket_typed_signing_enabled()):
            raise RuntimeError("CryptoWallet Polymarket signing remains disabled.")
        if await self.config.provider_paused():
            raise RuntimeError("CryptoWallet provider operations are paused.")
        if await self.config.user(user).security_locked():
            raise RuntimeError("This CryptoWallet profile is security locked.")
        if not re.fullmatch(r"[0-9a-f]{64}", approval_fingerprint):
            raise RuntimeError("Polymarket signing approval is invalid.")
        try:
            owner = normalize_evm_address(owner_address).lower()
            wallet = normalize_evm_address(wallet_address).lower()
            session = normalize_evm_address(session_address).lower()
            if len({owner, wallet, session}) != 3:
                raise ValueError("Polymarket session identities must remain separate.")
            _, deadline = validate_polymarket_session_batch_typed_data(
                typed_data, wallet_address=wallet, session_address=session,
                action=action, valid_until=valid_until,
            )
            now = int(time.time())
            if not now < deadline <= now + 5 * 60:
                raise ValueError("Polymarket session approval is not current.")
        except (AttributeError, TypeError, ValueError) as exc:
            raise RuntimeError("Polymarket session Batch typed data is invalid.") from exc
        profile = await self.get_or_create_wallet_profile(user)
        return await self.wallet_provider.sign_polymarket_session_batch(
            profile, user.id, owner, wallet, session, action, valid_until,
            typed_data, f"polymarket-session-{action}-{approval_fingerprint}",
        )

    async def polymarket_sign_settlement_batch(
        self, user, *, owner_address: str, wallet_address: str,
        typed_data: dict, approval_fingerprint: str,
    ) -> dict:
        """Sign one exact user-approved Deposit Wallet settlement Batch."""

        if not bool(await self.config.polymarket_typed_signing_enabled()):
            raise RuntimeError("CryptoWallet Polymarket signing remains disabled.")
        if await self.config.provider_paused():
            raise RuntimeError("CryptoWallet provider operations are paused.")
        if await self.config.user(user).security_locked():
            raise RuntimeError("This CryptoWallet profile is security locked.")
        if not re.fullmatch(r"[0-9a-f]{64}", approval_fingerprint):
            raise RuntimeError("Polymarket settlement approval is invalid.")
        try:
            owner = normalize_evm_address(owner_address).lower()
            wallet = normalize_evm_address(wallet_address).lower()
            if owner == wallet:
                raise ValueError("Polymarket owner and wallet must remain separate.")
            _nonce, deadline, _calls = (
                validate_polymarket_settlement_batch_typed_data(
                    typed_data, wallet_address=wallet,
                )
            )
            now = int(time.time())
            if not now < deadline <= now + 10 * 60:
                raise ValueError("Polymarket settlement approval is not current.")
        except (AttributeError, TypeError, ValueError) as exc:
            raise RuntimeError(
                "Polymarket settlement Batch typed data is invalid."
            ) from exc
        profile = await self.get_or_create_wallet_profile(user)
        return await self.wallet_provider.sign_polymarket_settlement_batch(
            profile, user.id, owner, wallet, typed_data,
            f"polymarket-settlement-{approval_fingerprint}",
        )


    async def polymarket_sign_withdrawal_batch(
        self, user, *, owner_address: str, wallet_address: str,
        bridge_address: str, amount_atomic: int, typed_data: dict,
        approval_fingerprint: str,
    ) -> dict:
        """Sign one exact user-approved Deposit Wallet withdrawal Batch."""

        if not bool(await self.config.polymarket_typed_signing_enabled()):
            raise RuntimeError("CryptoWallet Polymarket signing remains disabled.")
        if await self.config.provider_paused():
            raise RuntimeError("CryptoWallet provider operations are paused.")
        if await self.config.user(user).security_locked():
            raise RuntimeError("This CryptoWallet profile is security locked.")
        if not re.fullmatch(r"[0-9a-f]{64}", approval_fingerprint):
            raise RuntimeError("Polymarket withdrawal approval is invalid.")
        try:
            owner = normalize_evm_address(owner_address).lower()
            wallet = normalize_evm_address(wallet_address).lower()
            bridge = normalize_evm_address(bridge_address).lower()
            if len({owner, wallet, bridge}) != 3:
                raise ValueError("Withdrawal identities must remain separate.")
            _nonce, deadline = validate_polymarket_withdrawal_batch_typed_data(
                typed_data, wallet_address=wallet, bridge_address=bridge,
                amount_atomic=amount_atomic,
            )
            now = int(time.time())
            if not now < deadline <= now + 10 * 60:
                raise ValueError("Polymarket withdrawal approval is not current.")
        except (AttributeError, TypeError, ValueError) as exc:
            raise RuntimeError("Polymarket withdrawal Batch typed data is invalid.") from exc
        profile = await self.get_or_create_wallet_profile(user)
        return await self.wallet_provider.sign_polymarket_withdrawal_batch(
            profile, user.id, owner, wallet, bridge, amount_atomic, typed_data,
            f"polymarket-withdrawal-{approval_fingerprint}",
        )

    @staticmethod
    def _polymarket_deposit_intent_evidence(intent) -> dict:
        return {
            "intent_id": intent.intent_id, "profile_id": intent.profile_id,
            "network": intent.network, "from_address": intent.from_address,
            "to_address": intent.to_address, "value_atomic": intent.value_atomic,
            "asset_kind": intent.asset_kind, "asset_symbol": intent.asset_symbol,
            "asset_decimals": intent.asset_decimals,
            "approval_fingerprint": intent.approval_fingerprint(),
            "status": intent.status.value,
            "transaction_hash": intent.transaction_hash,
            "expires_at": intent.expires_at,
            "estimated_fee_atomic": intent.estimated_fee_atomic,
            "max_gas_fee_wei": intent.max_gas_fee_wei,
        }

    async def polymarket_prepare_deposit_intent(
        self, user, *, deposit_id: str, binding_fingerprint: str,
        recipient: str, amount_atomic: int,
    ) -> dict:
        """Create one idempotent protected Base ETH transfer for Polymarket funding."""

        if (re.fullmatch(r"[A-Za-z0-9_-]{1,80}", str(deposit_id or "")) is None
                or re.fullmatch(r"[0-9a-f]{64}", str(binding_fingerprint or "")) is None):
            raise RuntimeError("Polymarket deposit binding is invalid.")
        try:
            amount = int(amount_atomic)
            destination = normalize_evm_address(recipient).lower()
        except (TypeError, ValueError) as exc:
            raise RuntimeError("Polymarket deposit transfer is invalid.") from exc
        if amount <= 0:
            raise RuntimeError("Polymarket deposit amount is invalid.")
        if await self.config.provider_paused():
            raise RuntimeError("CryptoWallet provider operations are paused.")
        if await self.config.user(user).security_locked():
            raise RuntimeError("This CryptoWallet profile is security locked.")
        mode = str(await self.config.operating_mode() or "testnet").lower()
        policy = await self.config.base_mainnet_policy()
        if (mode not in {"mainnet", "mainnet-only"} or not isinstance(policy, dict)
                or policy.get("enabled") is not True
                or policy.get("paused", True) is not False
                or (policy.get("capabilities") or {}).get("send") is not True):
            raise RuntimeError("CryptoWallet Base mainnet sending is disabled or paused.")
        if not BASE_MAINNET.supports(NetworkCapability.SEND) or not self.wallet_provider.supports(
            BASE_MAINNET.key, NetworkCapability.SEND
        ):
            raise RuntimeError("CryptoWallet Base mainnet sending is unavailable.")
        if not await self.has_current_cryptowallet_mainnet_terms(user.id):
            raise RuntimeError("Current CryptoWallet mainnet terms are required.")

        profile = await self.get_or_create_wallet_profile(user)
        profile = await self.ensure_mainnet_wallet_profile(user, profile)
        account = self._account_for_network(profile, BASE_MAINNET.key)
        try:
            sender = normalize_evm_address(str((account or {}).get("address") or "")).lower()
        except ValueError as exc:
            raise RuntimeError("The Base mainnet wallet account is invalid.") from exc
        profile_id = str(profile.get("profile_id") or "")
        if not profile_id:
            raise RuntimeError("The CryptoWallet profile is incomplete.")

        intent_id = f"poly-{deposit_id}"
        existing = await self._stored_intent(user.id, intent_id)
        integration = await self.config.user(user).integration_intents.get_raw(
            intent_id, default=None
        )
        if existing is not None or integration is not None:
            expected = {
                "product": "polymarket_deposit",
                "binding_fingerprint": binding_fingerprint,
                "intent_fingerprint": (
                    existing.approval_fingerprint() if existing is not None else ""
                ),
            }
            if (existing is None or integration != expected
                    or existing.profile_id != profile_id
                    or existing.network != BASE_MAINNET.key
                    or existing.from_address.lower() != sender
                    or existing.to_address.lower() != destination
                    or existing.value_atomic != amount
                    or existing.asset_kind != "native"
                    or existing.asset_symbol != "ETH"
                    or existing.asset_decimals != 18):
                raise RuntimeError("Stored Polymarket deposit intent binding changed.")
            return self._polymarket_deposit_intent_evidence(existing)

        balance = int(await self.wallet_provider.get_native_balance(
            sender, BASE_MAINNET.key
        ))
        if amount > balance:
            raise RuntimeError("Insufficient Base ETH balance for this deposit.")
        now = int(time.time())
        intent = TransactionIntent(
            intent_id=intent_id, profile_id=profile_id,
            network=BASE_MAINNET.key, from_address=sender,
            to_address=destination, value_wei=amount,
            created_at=now, expires_at=now + 120,
            asset_kind="native", asset_symbol="ETH", asset_decimals=18,
            estimated_gas_fee_wei=0, gas_sponsored=False,
        )
        intent = await self.wallet_provider.prepare_transaction(intent)
        disclosure = self._mainnet_intent_disclosure_error(intent, BASE_MAINNET)
        if disclosure is not None:
            raise RuntimeError(f"Polymarket deposit preview is incomplete: {disclosure}")
        if intent.value_atomic + intent.estimated_fee_atomic > balance:
            raise RuntimeError("Insufficient Base ETH balance for the amount and fee.")
        limits = policy.get("limits_atomic") or {}
        try:
            transaction_limit = int(limits.get("per_transaction", 0))
        except (TypeError, ValueError):
            transaction_limit = 0
        reserved = intent.value_atomic + intent.max_gas_fee_wei
        if transaction_limit <= 0 or reserved > transaction_limit:
            raise RuntimeError(
                "This deposit plus its fee threshold exceeds the Base mainnet limit."
            )

        async with self.config.user(user).all() as values:
            intents = values.get("intents")
            integrations = values.get("integration_intents")
            if not isinstance(intents, dict) or not isinstance(integrations, dict):
                raise RuntimeError("CryptoWallet intent storage is unavailable.")
            if intent_id in intents or intent_id in integrations:
                raise RuntimeError("Polymarket deposit intent changed while it was prepared.")
            intents[intent_id] = intent.to_dict()
            integrations[intent_id] = {
                "product": "polymarket_deposit",
                "binding_fingerprint": binding_fingerprint,
                "intent_fingerprint": intent.approval_fingerprint(),
            }
        await self.expire_and_trim_intents(user)
        return self._polymarket_deposit_intent_evidence(intent)

    async def polymarket_deposit_intent_evidence(
        self, user, *, intent_id: str, binding_fingerprint: str,
        reconcile: bool = False,
    ) -> dict:
        """Return authenticated public status for one bound Polymarket transfer."""

        integration = await self.config.user(user).integration_intents.get_raw(
            intent_id, default=None
        )
        if (not isinstance(integration, dict)
                or integration.get("product") != "polymarket_deposit"
                or integration.get("binding_fingerprint") != binding_fingerprint):
            raise RuntimeError("Polymarket deposit intent binding is unavailable.")
        intent = await self._stored_intent(user.id, intent_id)
        if intent is None:
            raise RuntimeError("The CryptoWallet deposit intent is unavailable.")
        if reconcile and intent.status in {IntentStatus.SUBMITTED, IntentStatus.UNCERTAIN}:
            intent = await self._refresh_submitted_intent(user.id, intent_id)
        evidence = self._polymarket_deposit_intent_evidence(intent)
        if integration.get("intent_fingerprint") != evidence["approval_fingerprint"]:
            raise RuntimeError("The CryptoWallet deposit intent changed.")
        return evidence

    async def polymarket_deposit_approval_card(
        self, user, *, intent_id: str, binding_fingerprint: str,
        quoted_pusd_atomic: int, minimum_received_usd: str, color,
    ):
        """Build the standard CryptoWallet approval card for a bound deposit intent."""

        evidence = await self.polymarket_deposit_intent_evidence(
            user, intent_id=intent_id, binding_fingerprint=binding_fingerprint
        )
        intent = await self._stored_intent(user.id, intent_id)
        if intent is None or intent.status is not IntentStatus.PENDING:
            raise RuntimeError("The CryptoWallet deposit intent is not pending.")
        embed = self._intent_embed(intent, BASE_MAINNET, color)
        embed.title = "Fund Polymarket account"
        embed.add_field(
            name="Estimated Polymarket credit",
            value=(f"{int(quoted_pusd_atomic) // 1_000_000}."
                   f"{int(quoted_pusd_atomic) % 1_000_000:06d} pUSD"),
            inline=True,
        )
        embed.add_field(
            name="Bridge minimum received", value=f"${minimum_received_usd}",
            inline=True,
        )
        embed.add_field(
            name="Credit status",
            value="Estimate only; credited only after Bridge completion evidence.",
            inline=False,
        )
        return embed, WalletIntentView(self, user.id, intent), evidence

    async def estimate_base_mainnet_call_fee(
        self, *, from_address: str, to_address: str, value_wei: int, data: str
    ) -> dict:
        """Return a non-signing Base mainnet call-fee estimate for integrated cogs."""
        return await self.wallet_provider.estimate_base_mainnet_call_fee(
            from_address=from_address, to_address=to_address,
            value_wei=value_wei, data=data,
        )

    async def base_mainnet_call_snapshot(
        self, user, *, to_address: str, value_wei: int, data: str,
        reviewed_gas_limit: int, reviewed_fee_threshold_wei: int,
        empty_destination_address: str | None = None,
    ) -> dict:
        """Return trusted read-only state for one reviewed Base mainnet call."""
        target = normalize_evm_address(to_address).lower()
        value = int(value_wei)
        gas_limit = int(reviewed_gas_limit)
        threshold = int(reviewed_fee_threshold_wei)
        if value < 0 or gas_limit <= 0 or threshold <= 0:
            raise RuntimeError("The reviewed Base mainnet call policy is invalid.")
        profile = await self.get_or_create_wallet_profile(user)
        profile = await self.ensure_mainnet_wallet_profile(user, profile)
        account = self._account_for_network(profile, BASE_MAINNET.key)
        signer = normalize_evm_address(str((account or {}).get("address") or "")).lower()
        chain_id = await get_chain_id(BASE_MAINNET.key)
        if chain_id != BASE_MAINNET.chain_id:
            raise RuntimeError("The live chain is not Base mainnet.")
        code = await get_contract_code(target, BASE_MAINNET.key)
        runtime_hash = "0x" + hashlib.sha256(bytes.fromhex(code[2:])).hexdigest()
        destination_empty = None
        if empty_destination_address is not None:
            destination = normalize_evm_address(empty_destination_address).lower()
            destination_empty = (
                await get_contract_code(destination, BASE_MAINNET.key)
            ) == "0x"
        balance = await self.wallet_provider.get_native_balance(signer, BASE_MAINNET.key)
        authorization = await self.wallet_provider.get_delegation_status(
            profile, BASE_MAINNET.key
        )
        quote = await self.estimate_base_mainnet_call_fee(
            from_address=signer, to_address=target, value_wei=value, data=data
        )
        fee = int(quote.get("fee_wei") or 0)
        if fee <= 0 or fee > threshold:
            raise RuntimeError(
                "The live network fee exceeds the approved reapproval threshold."
            )
        return {
            "chain_id": chain_id, "signer": signer, "to": target,
            "value": value, "data": str(data), "gas_limit": gas_limit,
            "max_fee_wei": threshold, "target_runtime_sha256": runtime_hash,
            "authorization_active": authorization.get("active") is True,
            "signer_balance_wei": int(balance), "operation_state": "not-created",
            "quoted_gas_limit": gas_limit, "quoted_max_fee_wei": threshold,
            "estimated_fee_wei": fee, "destination_empty": destination_empty,
        }

    async def tokenfactory_submit_reviewed_call(
        self, user, operation: dict, attempt_id: str, execution_terms: dict
    ) -> dict:
        """Submit one immutable TokenFactory-owned call through the narrow signer."""

        try:
            expected_terms = {
                "gas_limit": int(operation["gas_limit"]),
                "native_value_wei": int(operation["value_wei"]),
                "gas_sponsored": operation.get("network") != BASE_MAINNET.key,
                "gas_payer": (
                    "creator wallet" if operation.get("network") == BASE_MAINNET.key
                    else "CDP paymaster"
                ),
            }
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError("The reviewed TokenFactory operation is incomplete.") from exc
        if execution_terms != expected_terms:
            raise RuntimeError(
                "The reviewed TokenFactory gas or spending policy no longer matches "
                "CryptoWallet. Review it again."
            )
        if await self.config.provider_paused():
            raise RuntimeError("CryptoWallet provider operations are paused.")
        if await self.config.user(user).security_locked():
            raise RuntimeError("This CryptoWallet profile is security locked.")
        profile = await self.get_or_create_wallet_profile(user)
        if operation.get("network") == BASE_MAINNET.key:
            profile = await self.ensure_mainnet_wallet_profile(user, profile)
        return await self.wallet_provider.submit_reviewed_tokenfactory_call(
            profile, operation, attempt_id
        )

    async def tokenfactory_operation_status(
        self, user, user_operation_hash: str, network: str = BASE_SEPOLIA.key
    ) -> dict:
        """Return the CDP state of a submitted factory deployment operation."""

        profile = await self.get_or_create_wallet_profile(user)
        return await self.wallet_provider.token_factory_operation_status(
            profile, user_operation_hash, network
        )

    async def clanker_submit_mainnet_operation(
        self, user, *, envelope: dict, attempt_id: str
    ) -> dict:
        """Submit one exact Clanker mainnet envelope through the narrow signer."""
        if await self.config.provider_paused():
            raise RuntimeError("CryptoWallet provider operations are paused.")
        if await self.config.user(user).security_locked():
            raise RuntimeError("This CryptoWallet profile is security locked.")
        profile = await self.get_or_create_wallet_profile(user)
        profile = await self.ensure_mainnet_wallet_profile(user, profile)
        return await self.wallet_provider.submit_reviewed_clanker_mainnet_operation(
            profile, envelope, attempt_id
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

    async def clanker_spendable_balance(self, user) -> dict:
        """Return the exact Base Sepolia signer and its current native balance."""
        profile = await self.get_or_create_wallet_profile(user)
        account = self._account_for_network(profile, BASE_SEPOLIA.key)
        address = str(account.get("address") or "") if account else ""
        if not address:
            raise RuntimeError("CryptoWallet has no Base Sepolia address for this user.")
        balance_wei = await self.wallet_provider.get_native_balance(
            address, BASE_SEPOLIA.key
        )
        return {"address": address, "balance_wei": int(balance_wei)}

    @staticmethod
    def clanker_execution_terms(native_value_wei: int = 0) -> dict:
        """Return the exact bounded spending policy shown on Clanker review cards."""
        native_value = int(native_value_wei)
        if not 0 <= native_value <= 10**18:
            raise ValueError("Clanker creator buy-in is outside the reviewed 0-1 ETH limit.")
        return {
            "gas_limit": CLANKER_DEPLOY_GAS_LIMIT,
            "native_value_wei": native_value,
            "gas_sponsored": True,
            "gas_payer": "CDP paymaster",
        }

    async def clanker_launch_verified(
        self, user, launch: dict, operation: dict, execution_terms: dict
    ) -> dict:
        """Submit one Discord-reviewed Clanker operation under active delegation."""
        if execution_terms != self.clanker_execution_terms(int(operation.get("value", -1))):
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

        available_wei = await self.wallet_provider.get_native_balance(
            str(account["address"]), BASE_SEPOLIA.key
        )
        required_wei = int(execution_terms["native_value_wei"])
        if int(available_wei) < required_wei:
            raise RuntimeError(
                "CryptoWallet has insufficient Base Sepolia ETH for the reviewed "
                "creator buy-in."
            )

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
