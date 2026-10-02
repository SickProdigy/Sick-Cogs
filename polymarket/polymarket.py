import hashlib
import json
import secrets
import time
from typing import Any
from urllib.parse import quote, urlparse

import aiohttp
import discord
from redbot.core import Config, checks, commands

from .account_binding import BotFirstAccountBinding
from .account_connection import (
    AccountConnection, AccountConnectionError, ConnectionState, WalletType,
)
from .collateral import CollateralPlanError, collateral_plan
from .deposit_wallet import DepositWalletCreationPlan, DepositWalletCreationState
from .handoff import MarketSnapshot, MarketSnapshotError
from .identity_verifier import PolygonAccountIdentityVerifier
from .onboarding import (
    ONBOARDING_LIFETIME_SECONDS, ProtectedOnboardingChallenge,
    complete_protected_onboarding,
)
from .onboarding_verification import finalize_onboarding_evidence
from .order_intent import MarketBuyApproval, OrderBookSnapshot, OrderIntentError
from .relayer import BuilderCredentials, DepositWalletRelayerClient
from .production_manifest import (
    POLYMARKET_PRODUCTION_MANIFEST, validate_polymarket_production_manifest,
)
from .security_policy import (
    ELIGIBILITY_LIFETIME_SECONDS, SESSION_KEY_LIFETIME_SECONDS,
    EligibilityAttestation, validate_session_key_policy,
)
from .signer_proof import (
    clob_auth_digest, recover_signer_address, verify_clob_auth_proof,
)
from .session_authorization import (
    BATCH_LIFETIME_SECONDS, SessionKeyOwnerApproval, generate_session_key,
    verify_session_batch_signature,
)
from .session_credential_store import (
    EncryptedSessionCredentials, protect_session_credentials,
)
from .session_key_store import (
    EncryptedSessionKey, SESSION_KEY_TOKEN_NAMESPACE, decode_wrapping_key,
    encode_wrapping_key, protect_session_private_key, reveal_session_private_key,
)
from .session_lifecycle import (
    SessionKeyLifecycle, SessionKeyOperation, SessionKeyStatus,
    SessionOperationState,
)
from .session_approval import SessionApprovalRequest, SessionApprovalState
from .session_views import SessionApprovalView
from .session_transport import SessionKeyTransport
from cryptowallet.core.polymarket import polymarket_clob_auth_typed_data
from .safety import ProductionLimits, SafetyLimitError
from .terms import (
    POLYMARKET_TERMS_PRODUCT, POLYMARKET_TERMS_VERSION,
    create_polymarket_terms_acceptance, is_current_polymarket_terms_acceptance,
)

CONFIG_IDENTIFIER = 1531372026
PRODUCTION_CAPABILITIES = (
    "account_connect", "deposit_wallet_create", "session", "eligibility",
    "collateral", "order", "cancel", "redeem",
)
ONBOARDING_ENABLE_ACKNOWLEDGEMENT = (
    "I understand protected Polymarket onboarding uses Polygon mainnet "
    "and enables no transactions"
)

GAMMA_API = "https://gamma-api.polymarket.com"
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=10, connect=4)
CATEGORIES = {
    "politics": ("Politics", "2"),
    "crypto": ("Crypto", "21"),
    "sports": ("Sports", "1"),
}


def _json_list(value: Any) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def market_url(market: dict) -> str:
    slug = market.get("slug")
    return f"https://polymarket.com/event/{slug}" if slug else "https://polymarket.com"


def _active_search_markets(payload: Any) -> list[dict]:
    if not isinstance(payload, dict):
        return []
    markets = []
    seen = set()
    for event in payload.get("events", []):
        if not isinstance(event, dict):
            continue
        for market in event.get("markets", []):
            if not isinstance(market, dict) or not market.get("active") or market.get("closed"):
                continue
            market_id = market.get("id")
            if market_id in seen:
                continue
            seen.add(market_id)
            markets.append(market)
    return markets


def future_handoff_reasons(market: dict) -> tuple[str, ...]:
    """Return only objective market-level blockers for a future CLOB V2 handoff."""
    reasons = []
    if not market.get("active") or market.get("closed"):
        reasons.append("market is not active")
    if not market.get("enableOrderBook"):
        reasons.append("no order book")
    if not market.get("acceptingOrders"):
        reasons.append("not accepting orders")
    if not _json_list(market.get("clobTokenIds")):
        reasons.append("no CLOB outcome tokens")
    return tuple(reasons)


def technically_handoff_ready(market: dict) -> bool:
    return isinstance(market, dict) and not future_handoff_reasons(market)


def market_path(reference: str) -> str | None:
    value = reference.strip()
    if value.isdigit():
        return f"/markets/{value}"
    parsed = urlparse(value)
    if parsed.scheme or parsed.netloc:
        if parsed.netloc.casefold() not in {"polymarket.com", "www.polymarket.com"}:
            return None
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) < 2 or parts[0] not in {"event", "market"}:
            return None
        value = parts[1]
    if not value or "/" in value or any(char.isspace() for char in value):
        return None
    return f"/markets/slug/{quote(value, safe='-_')}"


class Polymarket(commands.Cog):
    """Read-only prediction-market discovery and information."""

    __author__ = ["SickProdigy"]
    __version__ = "0.2.31"

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(
            self, identifier=CONFIG_IDENTIFIER, force_registration=True
        )
        self.config.register_global(
            production_enabled=False,
            production_paused=True,
            production_capabilities={name: False for name in PRODUCTION_CAPABILITIES},
            production_limits={
                "per_order_pusd": "0", "per_user_day_pusd": "0",
                "installation_day_pusd": "0",
            },
        )
        self.config.register_user(
            account_connection=None, onboarding_challenge=None, terms_challenge=None,
            terms_acceptance=None, audit_events=[], encrypted_session_key=None,
            encrypted_session_credentials=None, session_lifecycle=None,
            session_operation=None, replacement_session_lifecycle=None,
            replacement_session_operation=None,
            replacement_encrypted_session_key=None,
            replacement_encrypted_session_credentials=None,
            session_lifecycle_history=[], session_approval=None,
            bot_first_account=None, deposit_wallet_creation=None,
            final_confirmation_required=True
        )
        self.deposit_wallet_relayer = DepositWalletRelayerClient()
        self.session_transport = SessionKeyTransport()

    async def initialize(self) -> None:
        """Create the server-side wrapping key without exposing it to users."""

        tokens = await self.bot.get_shared_api_tokens(SESSION_KEY_TOKEN_NAMESPACE)
        encoded = str(tokens.get("wrapping_key") or "")
        if encoded:
            decode_wrapping_key(encoded)
            return
        await self.bot.set_shared_api_tokens(
            SESSION_KEY_TOKEN_NAMESPACE,
            wrapping_key=encode_wrapping_key(secrets.token_bytes(32)),
        )

    async def _session_storage_material(self) -> tuple[bytes, str]:
        tokens = await self.bot.get_shared_api_tokens(SESSION_KEY_TOKEN_NAMESPACE)
        try:
            wrapping_key = decode_wrapping_key(tokens["wrapping_key"])
        except (KeyError, TypeError, ValueError) as exc:
            raise AccountConnectionError(
                "Polymarket session-key storage is unavailable."
            ) from exc
        cryptowallet = self.bot.get_cog("CryptoWallet")
        deployment_config = getattr(cryptowallet, "config", None)
        deployment_value = getattr(deployment_config, "deployment_id", None)
        if not callable(deployment_value):
            raise AccountConnectionError("CryptoWallet deployment identity is unavailable.")
        deployment_id = str(await deployment_value() or "")
        if not deployment_id:
            raise AccountConnectionError("CryptoWallet deployment identity is unavailable.")
        return wrapping_key, deployment_id

    async def _session_capability_allowed(self) -> bool:
        capabilities = await self.config.production_capabilities()
        return (
            bool(await self.config.production_enabled())
            and not bool(await self.config.production_paused())
            and bool(capabilities.get("session"))
            and not validate_polymarket_production_manifest()
        )

    async def _persist_session_provision(
        self, user, *, encrypted_key: dict, lifecycle: SessionKeyLifecycle,
        operation: SessionKeyOperation,
    ) -> None:
        async with self.config.user(user).all() as values:
            if any(values.get(field) for field in (
                "encrypted_session_key", "session_lifecycle", "session_operation",
            )):
                raise AccountConnectionError(
                    "A Polymarket session key already exists for this user."
                )
            values["encrypted_session_key"] = encrypted_key
            values["session_lifecycle"] = lifecycle.to_record()
            values["session_operation"] = operation.to_record()

    def _session_approval_embed(self, request: SessionApprovalRequest) -> discord.Embed:
        titles = {
            SessionApprovalState.AWAITING_ELIGIBILITY: "Check eligibility",
            SessionApprovalState.AWAITING_APPROVAL: "Approve session authorization",
            SessionApprovalState.AWAITING_FINAL_CONFIRMATION: "Are you sure?",
            SessionApprovalState.APPROVED: "Authorization approved",
            SessionApprovalState.DECLINED: "Authorization cancelled",
            SessionApprovalState.CONSUMED: "Authorization submitted",
        }
        embed = discord.Embed(
            title=titles[request.state],
            description=(
                ("Create the user's derived Polymarket Deposit Wallet through the "
                 "official gasless Builder/Relayer path." if request.action == "deploy"
                 else "Authorize a CLOB-only session signer for routine Polymarket "
                      "orders. It cannot withdraw funds and expires after 180 days.")
            ),
            color=discord.Color.blurple(),
        )
        embed.add_field(name="Action", value=(
            "Create Deposit Wallet" if request.action == "deploy"
            else "Renew session authorization" if request.action == "rotate"
            else "Set up session authorization"
        ), inline=False)
        embed.add_field(name="Network", value="Polygon (137)", inline=True)
        embed.add_field(name="Signer", value=f"`{request.signer_address}`", inline=False)
        embed.add_field(
            name="Deposit Wallet", value=f"`{request.account_wallet_address}`",
            inline=False,
        )
        embed.add_field(
            name="Scope",
            value=("Create this Deposit Wallet only" if request.action == "deploy"
                   else "CLOB orders only; no withdrawals"),
            inline=False,
        )
        embed.add_field(name="Expires", value=f"<t:{request.expires_at}:R>", inline=True)
        embed.set_footer(text=f"Request {request.fingerprint[:12]} · Exact details remain unchanged")
        return embed

    async def _stored_session_approval(self, user) -> SessionApprovalRequest | None:
        record = await self.config.user(user).session_approval()
        return SessionApprovalRequest.from_record(record) if record else None

    async def _session_request_for_view(
        self, user, view: SessionApprovalView,
    ) -> SessionApprovalRequest:
        request = await self._stored_session_approval(user)
        if (request is None or request.request_id != view.request_id
                or request.fingerprint != view.fingerprint
                or request.requester_id != user.id):
            raise AccountConnectionError("This session approval card is no longer current.")
        return request

    async def _edit_session_card(
        self, interaction: discord.Interaction, request: SessionApprovalRequest,
        *, eligibility_url: str | None = None, content: str | None = None,
    ) -> None:
        view = None
        if request.state not in {
            SessionApprovalState.DECLINED, SessionApprovalState.CONSUMED,
        }:
            view = SessionApprovalView(self, request, eligibility_url)
            view.message = interaction.message
        await interaction.message.edit(
            embed=self._session_approval_embed(request), view=view
        )
        if content:
            await interaction.followup.send(content, ephemeral=True)

    async def check_session_eligibility_interaction(
        self, interaction: discord.Interaction, view: SessionApprovalView,
    ) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            request = await self._session_request_for_view(interaction.user, view)
            if request.state is not SessionApprovalState.AWAITING_ELIGIBILITY:
                raise AccountConnectionError("Eligibility was already checked.")
            cryptowallet = self.bot.get_cog("CryptoWallet")
            poll = getattr(cryptowallet, "poll_polymarket_eligibility_result", None)
            if not callable(poll):
                raise AccountConnectionError("Protected eligibility is unavailable.")
            result = await poll(request.result_handle)
            if result is None:
                await interaction.followup.send(
                    "Complete the protected eligibility page first, then press "
                    "Eligibility checked again.", ephemeral=True,
                )
                return
            now = int(time.time())
            checked_at = int(result["checked_at"])
            if not request.created_at <= checked_at < request.expires_at:
                raise AccountConnectionError("Eligibility result does not match this request.")
            eligibility = EligibilityAttestation(
                discord_user_id=request.requester_id, blocked=result["blocked"],
                country=result["country"], region=result["region"],
                checked_at=checked_at,
                expires_at=checked_at + ELIGIBILITY_LIFETIME_SECONDS,
            )
            if eligibility.blocked:
                await self.config.user(interaction.user).session_approval.set(None)
                await interaction.message.edit(
                    embed=discord.Embed(
                        title="Polymarket unavailable",
                        description="Polymarket reports this location as unavailable. "
                                    "No wallet signature or transaction was requested.",
                        color=discord.Color.red(),
                    ),
                    view=None,
                )
                return
            request = request.record_eligibility(
                eligibility, requester_id=interaction.user.id,
                fingerprint=view.fingerprint, now=now,
            )
            await self.config.user(interaction.user).session_approval.set(
                request.to_record()
            )
            await self._edit_session_card(
                interaction, request,
                content="Eligibility confirmed. Review the unchanged authorization and approve it.",
            )
        except (AccountConnectionError, KeyError, RuntimeError, TypeError, ValueError):
            await interaction.followup.send(
                "Eligibility could not be verified for this exact request. Nothing was signed.",
                ephemeral=True,
            )

    async def approve_session_interaction(
        self, interaction: discord.Interaction, view: SessionApprovalView,
    ) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            request = await self._session_request_for_view(interaction.user, view)
            if request.state is SessionApprovalState.APPROVED:
                await self._execute_session_approval(interaction, request)
                return
            request = request.approve_primary(
                requester_id=interaction.user.id, fingerprint=view.fingerprint,
                now=int(time.time()),
            )
            await self.config.user(interaction.user).session_approval.set(
                request.to_record()
            )
            if request.state is SessionApprovalState.AWAITING_FINAL_CONFIRMATION:
                await self._edit_session_card(
                    interaction, request,
                    content="Please confirm once more. The authorization details have not changed.",
                )
                return
            await self._execute_session_approval(interaction, request)
        except AccountConnectionError as exc:
            await interaction.followup.send(
                f"Session authorization was not submitted: {exc}", ephemeral=True
            )

    async def confirm_session_interaction(
        self, interaction: discord.Interaction, view: SessionApprovalView,
    ) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            request = await self._session_request_for_view(interaction.user, view)
            request = request.decide_final(
                True, requester_id=interaction.user.id, fingerprint=view.fingerprint,
                now=int(time.time()),
            )
            await self.config.user(interaction.user).session_approval.set(
                request.to_record()
            )
            await self._execute_session_approval(interaction, request)
        except AccountConnectionError as exc:
            await interaction.followup.send(
                f"Session authorization was not submitted: {exc}", ephemeral=True
            )

    async def decline_session_interaction(
        self, interaction: discord.Interaction, view: SessionApprovalView,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            await self._session_request_for_view(interaction.user, view)
        except AccountConnectionError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        await self.config.user(interaction.user).session_approval.set(None)
        await interaction.message.edit(
            embed=discord.Embed(
                title="Session authorization cancelled",
                description="Nothing was signed or submitted.",
                color=discord.Color.red(),
            ),
            view=None,
        )

    async def _execute_deposit_wallet_approval(
        self, user, request: SessionApprovalRequest,
    ) -> DepositWalletCreationPlan:
        """Persist and submit the exact user-approved Deposit Wallet creation."""

        if request.eligibility is None:
            raise AccountConnectionError("Protected eligibility evidence is missing.")
        user_config = self.config.user(user)
        stored = await user_config.deposit_wallet_creation()
        if stored:
            plan = DepositWalletCreationPlan.from_record(stored).recover_after_restart()
            if plan.state in {
                DepositWalletCreationState.SUBMITTED,
                DepositWalletCreationState.UNKNOWN,
            }:
                return await self._reconcile_deposit_wallet_creation(user)
            if plan.state is DepositWalletCreationState.CONFIRMED:
                return plan
            if plan.state is not DepositWalletCreationState.APPROVED:
                raise AccountConnectionError(
                    "The prior Deposit Wallet deployment failed; owner review is required."
                )
        else:
            evidence = await PolygonAccountIdentityVerifier(
                self._polygon_rpc
            ).verify_deposit_wallet_creation_target(request.signer_address)
            if evidence.deposit_wallet_address != request.account_wallet_address:
                raise AccountConnectionError(
                    "Derived Deposit Wallet identity changed before submission."
                )
            eligibility_record = request.to_record()["eligibility"]
            eligibility_fingerprint = hashlib.sha256(json.dumps(
                eligibility_record, sort_keys=True, separators=(",", ":")
            ).encode("ascii")).hexdigest()
            plan = DepositWalletCreationPlan(
                creation_id=secrets.token_urlsafe(32),
                discord_user_id=user.id,
                signer_address=request.signer_address,
                deposit_wallet_address=request.account_wallet_address,
                idempotency_key=secrets.token_urlsafe(32),
                owner_approval_fingerprint=request.fingerprint,
                eligibility_fingerprint=eligibility_fingerprint,
                target_evidence_digest=evidence.evidence_digest,
                created_at=request.created_at, expires_at=request.expires_at,
            )
            await user_config.deposit_wallet_creation.set(plan.to_record())
        if (plan.owner_approval_fingerprint != request.fingerprint
                or plan.discord_user_id != user.id
                or plan.signer_address != request.signer_address
                or plan.deposit_wallet_address != request.account_wallet_address):
            raise AccountConnectionError(
                "Persisted Deposit Wallet approval does not match this request."
            )
        return await self._submit_approved_deposit_wallet_creation(user, plan)

    async def _execute_session_approval(
        self, interaction: discord.Interaction, request: SessionApprovalRequest,
    ) -> None:
        binding = await self._bot_first_account(interaction.user)
        if (binding.signer_address != request.signer_address
                or binding.account_wallet_address != request.account_wallet_address):
            raise AccountConnectionError("CryptoWallet account identity changed.")
        if request.eligibility is None:
            raise AccountConnectionError("Protected eligibility evidence is missing.")
        approved_at = int(time.time())
        request.eligibility.require_current(
            discord_user_id=interaction.user.id, now=approved_at
        )
        if request.action == "deploy":
            await self._execute_deposit_wallet_approval(interaction.user, request)
        elif request.action == "provision":
            await self._provision_session_key(interaction.user, request.eligibility)
        else:
            await self._begin_session_rotation(interaction.user, request.eligibility)
        consumed = request.consume(
            requester_id=interaction.user.id, fingerprint=request.fingerprint,
            now=approved_at,
        )
        await self.config.user(interaction.user).session_approval.set(
            consumed.to_record()
        )
        await self._edit_session_card(
            interaction, consumed,
            content=(
                "Deposit Wallet deployment was submitted and will be reconciled "
                "before session setup." if request.action == "deploy" else
                "Session authorization was submitted and will be reconciled before use."
            ),
        )

    async def _provision_session_key(
        self, user, eligibility: EligibilityAttestation,
    ) -> SessionKeyOperation:
        """Persist and submit one exact owner-approved session authorization."""

        if not await self._session_capability_allowed():
            raise AccountConnectionError("Polymarket session provisioning is disabled.")
        now = int(time.time())
        eligibility.require_current(discord_user_id=user.id, now=now)
        if not is_current_polymarket_terms_acceptance(
            await self.config.user(user).terms_acceptance(), user.id
        ):
            raise AccountConnectionError("Current Polymarket terms are required.")
        binding = await self._bot_first_account(user)
        relationship = await PolygonAccountIdentityVerifier(self._polygon_rpc).verify(
            signer_address=binding.signer_address,
            account_wallet_address=binding.account_wallet_address,
            wallet_type=WalletType.DEPOSIT_WALLET,
        )
        if relationship.block_number < 0:
            raise AccountConnectionError("Deposit Wallet deployment was not verified.")
        wrapping_key, deployment_id = await self._session_storage_material()
        builder_tokens = await self.bot.get_shared_api_tokens("polymarket_builder")
        try:
            builder = BuilderCredentials(
                builder_tokens["api_key"], builder_tokens["secret"],
                builder_tokens["passphrase"],
            )
        except (KeyError, TypeError):
            raise AccountConnectionError(
                "Polymarket Builder credentials are incomplete."
            ) from None
        nonce = await self.session_transport.get_wallet_nonce(binding.signer_address)
        private_key, session_address = generate_session_key()
        approval = SessionKeyOwnerApproval(
            action="authorize", discord_user_id=user.id,
            profile_id=binding.profile_id,
            owner_address=binding.signer_address,
            wallet_address=binding.account_wallet_address,
            session_address=session_address, nonce=nonce, created_at=now,
            deadline=now + BATCH_LIFETIME_SECONDS,
            valid_until=now + SESSION_KEY_LIFETIME_SECONDS,
            idempotency_key=secrets.token_urlsafe(32),
        )
        encrypted = protect_session_private_key(
            wrapping_key, private_key, deployment_id=deployment_id,
            discord_user_id=user.id, profile_id=binding.profile_id,
            signer_address=binding.signer_address,
            account_wallet_address=binding.account_wallet_address,
            session_address=session_address, created_at=now,
            expires_at=approval.valid_until,
        )
        lifecycle = SessionKeyLifecycle(
            discord_user_id=user.id, profile_id=binding.profile_id,
            owner_address=binding.signer_address,
            wallet_address=binding.account_wallet_address,
            session_address=session_address, created_at=now,
            expires_at=approval.valid_until,
        )
        operation = SessionKeyOperation(approval)
        await self._persist_session_provision(
            user, encrypted_key=encrypted.to_record(),
            lifecycle=lifecycle, operation=operation,
        )
        return await self._submit_pending_session_key(user)

    async def _submit_pending_session_key(self, user) -> SessionKeyOperation:
        """Resume owner approval safely until provider submission begins."""

        if not await self._session_capability_allowed():
            raise AccountConnectionError("Polymarket session provisioning is disabled.")
        user_config = self.config.user(user)
        stored = await user_config.session_operation()
        if not stored:
            raise AccountConnectionError("No Polymarket session approval is pending.")
        operation = SessionKeyOperation.from_record(stored).recover_after_restart()
        if operation.state is not SessionOperationState.PENDING_OWNER_APPROVAL:
            raise AccountConnectionError("Polymarket session approval is not pending.")
        approval = operation.approval
        binding = await self._bot_first_account(user)
        if (
            approval.discord_user_id != user.id
            or approval.profile_id != binding.profile_id
            or approval.owner_address != binding.signer_address
            or approval.wallet_address != binding.account_wallet_address
        ):
            raise AccountConnectionError("Polymarket session approval identity changed.")
        await user_config.session_operation.set(operation.to_record())
        builder_tokens = await self.bot.get_shared_api_tokens("polymarket_builder")
        try:
            builder = BuilderCredentials(
                builder_tokens["api_key"], builder_tokens["secret"],
                builder_tokens["passphrase"],
            )
        except (KeyError, TypeError):
            raise AccountConnectionError(
                "Polymarket Builder credentials are incomplete."
            ) from None
        cryptowallet = self.bot.get_cog("CryptoWallet")
        signer = getattr(cryptowallet, "polymarket_sign_session_batch", None)
        if not callable(signer):
            raise AccountConnectionError("CryptoWallet session signing is unavailable.")
        fingerprint = hashlib.sha256(
            json.dumps(
                approval.typed_data(), sort_keys=True, separators=(",", ":")
            ).encode("ascii")
        ).hexdigest()
        try:
            result = await signer(
                user, owner_address=approval.owner_address,
                wallet_address=approval.wallet_address,
                session_address=approval.session_address,
                action=approval.action, valid_until=approval.valid_until,
                typed_data=approval.typed_data(),
                approval_fingerprint=fingerprint,
            )
        except RuntimeError as exc:
            raise AccountConnectionError(
                "CryptoWallet session signing is unavailable."
            ) from exc
        if (
            not isinstance(result, dict)
            or set(result) != {"signature", "signer_address"}
            or result["signer_address"] != approval.owner_address
        ):
            raise AccountConnectionError("CryptoWallet returned an invalid session signer.")
        signature = str(result["signature"])
        verify_session_batch_signature(approval, signature)
        operation = operation.approve(signature, now=int(time.time()))
        await user_config.session_operation.set(operation.to_record())
        submitting = operation.begin_submission(now=int(time.time()))
        await user_config.session_operation.set(submitting.to_record())
        try:
            response = await self.session_transport.submit(
                approval, signature, builder, timestamp=int(time.time())
            )
        except (aiohttp.ClientError, AccountConnectionError, TimeoutError):
            unknown = submitting.recover_after_restart()
            await user_config.session_operation.set(unknown.to_record())
            raise AccountConnectionError(
                "Session authorization outcome is unknown; reconcile before retry."
            ) from None
        submitted = submitting.record_submission(response)
        await user_config.session_operation.set(submitted.to_record())
        return submitted

    async def _maintain_session_key(
        self, user, eligibility: EligibilityAttestation | None = None,
    ) -> str:
        """Advance exactly one persisted lifecycle step without hidden retries."""

        lifecycle_record = await self.config.user(user).session_lifecycle()
        if not lifecycle_record:
            return "none"
        lifecycle = SessionKeyLifecycle.from_record(lifecycle_record)
        action = lifecycle.maintenance_action(now=int(time.time()))
        if action == "reconcile":
            operation_record = await self.config.user(user).session_operation()
            if not operation_record:
                raise AccountConnectionError(
                    "Polymarket session operation is missing."
                )
            operation = SessionKeyOperation.from_record(
                operation_record
            ).recover_after_restart()
            if operation.state is SessionOperationState.PENDING_OWNER_APPROVAL:
                await self._submit_pending_session_key(user)
                return "approval_submitted"
            await self._reconcile_session_key(user)
            return "reconciled"
        if action == "rotate":
            if eligibility is None:
                raise AccountConnectionError(
                    "Fresh protected eligibility is required for session rotation."
                )
            await self._begin_session_rotation(user, eligibility)
            return "rotation_submitted"
        if action == "activate_replacement":
            replacement_record = (
                await self.config.user(user).replacement_session_lifecycle()
            )
            if not replacement_record:
                raise AccountConnectionError(
                    "Polymarket replacement session is missing."
                )
            replacement = SessionKeyLifecycle.from_record(replacement_record)
            if replacement.status is SessionKeyStatus.ACTIVE:
                await self._begin_session_revocation(user)
                return "old_key_revocation_submitted"
            operation_record = await self.config.user(user).session_operation()
            if not operation_record:
                raise AccountConnectionError(
                    "Polymarket replacement operation is missing."
                )
            operation = SessionKeyOperation.from_record(
                operation_record
            ).recover_after_restart()
            if operation.state is SessionOperationState.PENDING_OWNER_APPROVAL:
                await self._submit_pending_session_key(user)
                return "replacement_approval_submitted"
            await self._reconcile_replacement_session_key(user)
            return "replacement_reconciled"
        if action == "revoke_expired":
            await self._begin_session_revocation(user)
            return "expired_key_revocation_submitted"
        return "none"

    async def _begin_session_rotation(
        self, user, eligibility: EligibilityAttestation,
    ) -> SessionKeyOperation:
        """Authorize a replacement while retaining the current active session."""

        if not await self._session_capability_allowed():
            raise AccountConnectionError("Polymarket session rotation is disabled.")
        now = int(time.time())
        eligibility.require_current(discord_user_id=user.id, now=now)
        user_config = self.config.user(user)
        if not is_current_polymarket_terms_acceptance(
            await user_config.terms_acceptance(), user.id
        ):
            raise AccountConnectionError("Current Polymarket terms are required.")
        lifecycle_record = await user_config.session_lifecycle()
        encrypted_record = await user_config.encrypted_session_key()
        credential_record = await user_config.encrypted_session_credentials()
        if not all((lifecycle_record, encrypted_record, credential_record)):
            raise AccountConnectionError("The active Polymarket session is incomplete.")
        lifecycle = SessionKeyLifecycle.from_record(lifecycle_record)
        encrypted = EncryptedSessionKey.from_record(encrypted_record)
        encrypted_credentials = EncryptedSessionCredentials.from_record(
            credential_record
        )
        if lifecycle.maintenance_action(now=now) != "rotate":
            raise AccountConnectionError("Polymarket session rotation is not due.")
        binding = await self._bot_first_account(user)
        if (
            lifecycle.discord_user_id != user.id
            or lifecycle.profile_id != binding.profile_id
            or lifecycle.owner_address != binding.signer_address
            or lifecycle.wallet_address != binding.account_wallet_address
            or encrypted.session_address != lifecycle.session_address
            or encrypted_credentials.profile_id != lifecycle.profile_id
            or encrypted_credentials.signer_address != lifecycle.owner_address
            or encrypted_credentials.account_wallet_address != lifecycle.wallet_address
            or encrypted_credentials.session_address != lifecycle.session_address
        ):
            raise AccountConnectionError("Polymarket session identity changed.")
        wrapping_key, deployment_id = await self._session_storage_material()
        builder_tokens = await self.bot.get_shared_api_tokens("polymarket_builder")
        try:
            BuilderCredentials(
                builder_tokens["api_key"], builder_tokens["secret"],
                builder_tokens["passphrase"],
            )
        except (KeyError, TypeError):
            raise AccountConnectionError(
                "Polymarket Builder credentials are incomplete."
            ) from None
        nonce = await self.session_transport.get_wallet_nonce(binding.signer_address)
        private_key, session_address = generate_session_key()
        approval = SessionKeyOwnerApproval(
            action="authorize", discord_user_id=user.id,
            profile_id=binding.profile_id,
            owner_address=binding.signer_address,
            wallet_address=binding.account_wallet_address,
            session_address=session_address, nonce=nonce, created_at=now,
            deadline=now + BATCH_LIFETIME_SECONDS,
            valid_until=now + SESSION_KEY_LIFETIME_SECONDS,
            idempotency_key=secrets.token_urlsafe(32),
        )
        encrypted_replacement = protect_session_private_key(
            wrapping_key, private_key, deployment_id=deployment_id,
            discord_user_id=user.id, profile_id=binding.profile_id,
            signer_address=binding.signer_address,
            account_wallet_address=binding.account_wallet_address,
            session_address=session_address, created_at=now,
            expires_at=approval.valid_until,
        )
        replacement = SessionKeyLifecycle(
            discord_user_id=user.id, profile_id=binding.profile_id,
            owner_address=binding.signer_address,
            wallet_address=binding.account_wallet_address,
            session_address=session_address, created_at=now,
            expires_at=approval.valid_until,
        )
        operation = SessionKeyOperation(approval)
        rotating = lifecycle.begin_rotation(session_address)
        async with user_config.all() as values:
            current = SessionKeyLifecycle.from_record(values["session_lifecycle"])
            if current != lifecycle or any(values.get(field) for field in (
                "replacement_session_lifecycle",
                "replacement_session_operation",
                "replacement_encrypted_session_key",
                "replacement_encrypted_session_credentials",
            )):
                raise AccountConnectionError("Polymarket session rotation state changed.")
            values["session_lifecycle"] = rotating.to_record()
            values["session_operation"] = operation.to_record()
            values["replacement_session_lifecycle"] = replacement.to_record()
            values["replacement_session_operation"] = operation.to_record()
            values["replacement_encrypted_session_key"] = (
                encrypted_replacement.to_record()
            )
        return await self._submit_pending_session_key(user)

    async def _reconcile_replacement_session_key(
        self, user,
    ) -> SessionKeyLifecycle:
        """Activate a replacement without revoking the current key early."""

        if not await self._session_capability_allowed():
            raise AccountConnectionError("Polymarket session rotation is disabled.")
        user_config = self.config.user(user)
        operation_record = await user_config.session_operation()
        lifecycle_record = await user_config.session_lifecycle()
        replacement_record = await user_config.replacement_session_lifecycle()
        encrypted_record = await user_config.replacement_encrypted_session_key()
        if not all((
            operation_record, lifecycle_record, replacement_record, encrypted_record,
        )):
            raise AccountConnectionError("Polymarket replacement session is incomplete.")
        operation = SessionKeyOperation.from_record(
            operation_record
        ).recover_after_restart()
        lifecycle = SessionKeyLifecycle.from_record(lifecycle_record)
        replacement = SessionKeyLifecycle.from_record(replacement_record)
        encrypted = EncryptedSessionKey.from_record(encrypted_record)
        if (
            lifecycle.status is not SessionKeyStatus.ROTATING
            or lifecycle.replacement_session_address != replacement.session_address
            or operation.approval.session_address != replacement.session_address
            or encrypted.profile_id != replacement.profile_id
            or encrypted.signer_address != replacement.owner_address
            or encrypted.account_wallet_address != replacement.wallet_address
            or encrypted.session_address != replacement.session_address
        ):
            raise AccountConnectionError("Polymarket replacement identity changed.")
        binding = await self._bot_first_account(user)
        if (
            replacement.discord_user_id != user.id
            or replacement.profile_id != binding.profile_id
            or replacement.owner_address != binding.signer_address
            or replacement.wallet_address != binding.account_wallet_address
        ):
            raise AccountConnectionError("Polymarket replacement binding changed.")
        if operation.state is SessionOperationState.UNKNOWN and not operation.transaction_id:
            await user_config.session_operation.set(operation.to_record())
            await user_config.replacement_session_operation.set(operation.to_record())
            raise AccountConnectionError(
                "Replacement authorization has no transaction identity; "
                "manual review is required."
            )
        if operation.state in {
            SessionOperationState.SUBMITTED, SessionOperationState.UNKNOWN,
        }:
            evidence = await self.session_transport.transaction(operation.transaction_id)
            operation = operation.reconcile_transaction(evidence)
            await user_config.session_operation.set(operation.to_record())
            await user_config.replacement_session_operation.set(operation.to_record())
        if operation.state is SessionOperationState.FAILED:
            lifecycle = lifecycle.abort_rotation(operation)
            async with user_config.all() as values:
                values["session_lifecycle"] = lifecycle.to_record()
                values["replacement_session_lifecycle"] = None
                values["replacement_session_operation"] = None
                values["replacement_encrypted_session_key"] = None
                values["replacement_encrypted_session_credentials"] = None
            return lifecycle
        if (
            operation.state is SessionOperationState.COMPLETE
            and replacement.status is SessionKeyStatus.ACTIVE
        ):
            return replacement
        if operation.state is not SessionOperationState.CONFIRMING_REGISTRY:
            return replacement

        wrapping_key, deployment_id = await self._session_storage_material()
        private_key = reveal_session_private_key(
            wrapping_key, encrypted, deployment_id=deployment_id,
            discord_user_id=user.id, profile_id=replacement.profile_id,
            signer_address=replacement.owner_address,
            account_wallet_address=replacement.wallet_address,
            session_address=replacement.session_address,
        )
        now = int(time.time())
        owner_signature = await self._request_cdp_clob_auth_signature(
            user, binding, timestamp=now, nonce=0,
        )
        owner_credentials = await self.session_transport.create_or_derive_clob_credentials(
            address=replacement.owner_address, signature=owner_signature,
            timestamp=now, nonce=0,
        )
        await self.session_transport.require_active(
            operation.approval, credentials=owner_credentials, timestamp=now
        )
        session_credentials = (
            await self.session_transport.create_or_derive_session_credentials(
                private_key, timestamp=now, nonce=0
            )
        )
        encrypted_credentials = protect_session_credentials(
            wrapping_key, session_credentials, deployment_id=deployment_id,
            discord_user_id=user.id, profile_id=replacement.profile_id,
            signer_address=replacement.owner_address,
            account_wallet_address=replacement.wallet_address,
            session_address=replacement.session_address,
            created_at=replacement.created_at, expires_at=replacement.expires_at,
        )
        operation = operation.confirm_registry(active=True, now=now)
        replacement = replacement.activate(operation)
        if not await self._session_capability_allowed():
            raise AccountConnectionError("Polymarket session rotation was paused.")
        async with user_config.all() as values:
            current = SessionKeyOperation.from_record(values["session_operation"])
            if current.state is not SessionOperationState.CONFIRMING_REGISTRY:
                raise AccountConnectionError("Polymarket replacement operation changed.")
            values["session_operation"] = operation.to_record()
            values["replacement_session_operation"] = operation.to_record()
            values["replacement_session_lifecycle"] = replacement.to_record()
            values["replacement_encrypted_session_credentials"] = (
                encrypted_credentials.to_record()
            )
        return replacement

    async def _begin_session_revocation(
        self, user,
    ) -> SessionKeyOperation:
        """Persist and submit one exact revocation for the current session key."""

        if not await self._session_capability_allowed():
            raise AccountConnectionError("Polymarket session revocation is disabled.")
        user_config = self.config.user(user)
        lifecycle_record = await user_config.session_lifecycle()
        encrypted_record = await user_config.encrypted_session_key()
        if not lifecycle_record or not encrypted_record:
            raise AccountConnectionError("No Polymarket session key is available.")
        lifecycle = SessionKeyLifecycle.from_record(lifecycle_record)
        encrypted = EncryptedSessionKey.from_record(encrypted_record)
        binding = await self._bot_first_account(user)
        if (
            lifecycle.discord_user_id != user.id
            or lifecycle.profile_id != binding.profile_id
            or lifecycle.owner_address != binding.signer_address
            or lifecycle.wallet_address != binding.account_wallet_address
            or encrypted.profile_id != lifecycle.profile_id
            or encrypted.signer_address != lifecycle.owner_address
            or encrypted.account_wallet_address != lifecycle.wallet_address
            or encrypted.session_address != lifecycle.session_address
        ):
            raise AccountConnectionError("Polymarket session identity changed.")
        builder_tokens = await self.bot.get_shared_api_tokens("polymarket_builder")
        try:
            BuilderCredentials(
                builder_tokens["api_key"], builder_tokens["secret"],
                builder_tokens["passphrase"],
            )
        except (KeyError, TypeError):
            raise AccountConnectionError(
                "Polymarket Builder credentials are incomplete."
            ) from None
        now = int(time.time())
        nonce = await self.session_transport.get_wallet_nonce(binding.signer_address)
        approval = SessionKeyOwnerApproval(
            action="revoke", discord_user_id=user.id,
            profile_id=binding.profile_id,
            owner_address=binding.signer_address,
            wallet_address=binding.account_wallet_address,
            session_address=lifecycle.session_address,
            nonce=nonce, created_at=now,
            deadline=now + BATCH_LIFETIME_SECONDS,
            idempotency_key=secrets.token_urlsafe(32),
        )
        operation = SessionKeyOperation(approval)
        replacement_active = False
        if lifecycle.status is SessionKeyStatus.ROTATING:
            replacement_record = await user_config.replacement_session_lifecycle()
            replacement_credentials = (
                await user_config.replacement_encrypted_session_credentials()
            )
            if replacement_record and replacement_credentials:
                replacement = SessionKeyLifecycle.from_record(replacement_record)
                encrypted_replacement_credentials = (
                    EncryptedSessionCredentials.from_record(
                        replacement_credentials
                    )
                )
                replacement_active = (
                    replacement.status is SessionKeyStatus.ACTIVE
                    and replacement.session_address
                    == lifecycle.replacement_session_address
                    and encrypted_replacement_credentials.profile_id
                    == replacement.profile_id
                    and encrypted_replacement_credentials.signer_address
                    == replacement.owner_address
                    and encrypted_replacement_credentials.account_wallet_address
                    == replacement.wallet_address
                    and encrypted_replacement_credentials.session_address
                    == replacement.session_address
                )
        lifecycle = lifecycle.begin_revocation(
            replacement_active=replacement_active
        )
        async with user_config.all() as values:
            current = SessionKeyLifecycle.from_record(values["session_lifecycle"])
            if current.status not in {
                SessionKeyStatus.ACTIVE, SessionKeyStatus.ROTATING,
                SessionKeyStatus.EXPIRED,
            }:
                raise AccountConnectionError("Polymarket session lifecycle changed.")
            values["session_lifecycle"] = lifecycle.to_record()
            values["session_operation"] = operation.to_record()
        return await self._submit_pending_session_key(user)

    async def _reconcile_session_key(self, user) -> SessionKeyLifecycle:
        """Reconcile transaction, registry, and encrypted session credentials."""

        if not await self._session_capability_allowed():
            raise AccountConnectionError("Polymarket session reconciliation is disabled.")
        user_config = self.config.user(user)
        operation_record = await user_config.session_operation()
        lifecycle_record = await user_config.session_lifecycle()
        if not operation_record or not lifecycle_record:
            raise AccountConnectionError("Polymarket session provisioning is incomplete.")
        operation = SessionKeyOperation.from_record(operation_record).recover_after_restart()
        lifecycle = SessionKeyLifecycle.from_record(lifecycle_record)
        if (
            lifecycle.status is SessionKeyStatus.REVOKED
            and operation.state is SessionOperationState.COMPLETE
        ):
            return lifecycle
        encrypted_record = await user_config.encrypted_session_key()
        if not encrypted_record:
            raise AccountConnectionError("Polymarket session key material is missing.")
        encrypted = EncryptedSessionKey.from_record(encrypted_record)
        binding = await self._bot_first_account(user)
        identity = (
            user.id, binding.profile_id, binding.signer_address,
            binding.account_wallet_address, lifecycle.session_address,
        )
        if identity != (
            lifecycle.discord_user_id, lifecycle.profile_id,
            lifecycle.owner_address, lifecycle.wallet_address,
            encrypted.session_address,
        ) or (
            encrypted.profile_id, encrypted.signer_address,
            encrypted.account_wallet_address,
        ) != (
            lifecycle.profile_id, lifecycle.owner_address, lifecycle.wallet_address,
        ):
            raise AccountConnectionError("Polymarket session identity changed.")
        if operation.approval.session_address != lifecycle.session_address:
            raise AccountConnectionError("Polymarket session operation identity changed.")
        if operation.state is SessionOperationState.UNKNOWN and not operation.transaction_id:
            await user_config.session_operation.set(operation.to_record())
            raise AccountConnectionError(
                "Session authorization has no transaction identity; manual review is required."
            )
        if operation.state in {
            SessionOperationState.SUBMITTED, SessionOperationState.UNKNOWN,
        }:
            evidence = await self.session_transport.transaction(operation.transaction_id)
            operation = operation.reconcile_transaction(evidence)
            await user_config.session_operation.set(operation.to_record())
        if operation.state is SessionOperationState.FAILED:
            lifecycle = lifecycle.fail(operation)
            await user_config.session_lifecycle.set(lifecycle.to_record())
            return lifecycle
        if operation.state is not SessionOperationState.CONFIRMING_REGISTRY:
            return lifecycle

        wrapping_key, deployment_id = await self._session_storage_material()
        now = int(time.time())
        owner_signature = await self._request_cdp_clob_auth_signature(
            user, binding, timestamp=now, nonce=0,
        )
        owner_credentials = await self.session_transport.create_or_derive_clob_credentials(
            address=lifecycle.owner_address, signature=owner_signature,
            timestamp=now, nonce=0,
        )
        encrypted_credentials = None
        if operation.approval.action == "authorize":
            await self.session_transport.require_active(
                operation.approval, credentials=owner_credentials, timestamp=now
            )
            private_key = reveal_session_private_key(
                wrapping_key, encrypted, deployment_id=deployment_id,
                discord_user_id=user.id, profile_id=lifecycle.profile_id,
                signer_address=lifecycle.owner_address,
                account_wallet_address=lifecycle.wallet_address,
                session_address=lifecycle.session_address,
            )
            session_credentials = (
                await self.session_transport.create_or_derive_session_credentials(
                    private_key, timestamp=now, nonce=0
                )
            )
            encrypted_credentials = protect_session_credentials(
                wrapping_key, session_credentials, deployment_id=deployment_id,
                discord_user_id=user.id, profile_id=lifecycle.profile_id,
                signer_address=lifecycle.owner_address,
                account_wallet_address=lifecycle.wallet_address,
                session_address=lifecycle.session_address,
                created_at=lifecycle.created_at, expires_at=lifecycle.expires_at,
            )
            operation = operation.confirm_registry(active=True, now=now)
            lifecycle = lifecycle.activate(operation)
        else:
            await self.session_transport.require_absent(
                operation.approval, credentials=owner_credentials, timestamp=now
            )
            operation = operation.confirm_registry(active=False, now=now)
            lifecycle = lifecycle.revoke(operation)
        if not await self._session_capability_allowed():
            raise AccountConnectionError("Polymarket session reconciliation was paused.")
        result_lifecycle = lifecycle
        async with user_config.all() as values:
            current = SessionKeyOperation.from_record(values["session_operation"])
            if current.state is not SessionOperationState.CONFIRMING_REGISTRY:
                raise AccountConnectionError("Polymarket session operation changed.")
            values["session_operation"] = operation.to_record()
            values["session_lifecycle"] = lifecycle.to_record()
            if encrypted_credentials is not None:
                values["encrypted_session_credentials"] = encrypted_credentials.to_record()
            elif lifecycle.replacement_session_address is None:
                values["encrypted_session_key"] = None
                values["encrypted_session_credentials"] = None
            else:
                replacement = SessionKeyLifecycle.from_record(
                    values["replacement_session_lifecycle"]
                )
                replacement_operation = SessionKeyOperation.from_record(
                    values["replacement_session_operation"]
                )
                replacement_key = EncryptedSessionKey.from_record(
                    values["replacement_encrypted_session_key"]
                )
                replacement_credentials = EncryptedSessionCredentials.from_record(
                    values["replacement_encrypted_session_credentials"]
                )
                if (
                    replacement.status is not SessionKeyStatus.ACTIVE
                    or replacement.session_address
                    != lifecycle.replacement_session_address
                    or replacement_operation.state
                    is not SessionOperationState.COMPLETE
                    or replacement_operation.approval.session_address
                    != replacement.session_address
                    or replacement_key.session_address
                    != replacement.session_address
                    or replacement_credentials.profile_id != replacement.profile_id
                    or replacement_credentials.signer_address
                    != replacement.owner_address
                    or replacement_credentials.account_wallet_address
                    != replacement.wallet_address
                    or replacement_credentials.session_address
                    != replacement.session_address
                ):
                    raise AccountConnectionError(
                        "Active replacement session evidence is incomplete."
                    )
                history = list(values.get("session_lifecycle_history") or ())
                history.append(lifecycle.to_record())
                values["session_lifecycle_history"] = history[-10:]
                values["session_lifecycle"] = replacement.to_record()
                values["session_operation"] = replacement_operation.to_record()
                values["encrypted_session_key"] = replacement_key.to_record()
                values["encrypted_session_credentials"] = (
                    replacement_credentials.to_record()
                )
                values["replacement_session_lifecycle"] = None
                values["replacement_session_operation"] = None
                values["replacement_encrypted_session_key"] = None
                values["replacement_encrypted_session_credentials"] = None
                result_lifecycle = replacement
        return result_lifecycle

    async def _bot_first_account(self, user) -> BotFirstAccountBinding:
        """Resolve and persist the user's CryptoWallet-owned Deposit Wallet identity."""

        cryptowallet = self.bot.get_cog("CryptoWallet")
        resolver = getattr(cryptowallet, "polymarket_wallet_context", None)
        if not callable(resolver):
            raise AccountConnectionError("CryptoWallet is unavailable.")
        try:
            context = await resolver(user)
        except RuntimeError as exc:
            raise AccountConnectionError(
                "CryptoWallet could not verify the wallet owner."
            ) from exc
        expected = {
            "requester_id", "profile_id", "provider_user_id",
            "smart_account_address", "signer_address", "chain_id", "source",
        }
        if not isinstance(context, dict) or set(context) != expected:
            raise AccountConnectionError("CryptoWallet returned an invalid owner binding.")
        if (
            context["requester_id"] != user.id
            or context["provider_user_id"] != context["profile_id"]
            or context["chain_id"] != 137
            or context["source"] != "cdp_smart_account_owner"
        ):
            raise AccountConnectionError("CryptoWallet owner binding has drifted.")
        signer = str(context["signer_address"])
        deposit_wallet = (
            await PolygonAccountIdentityVerifier(self._polygon_rpc).derive_wallets(signer)
        )[WalletType.DEPOSIT_WALLET][-1]
        user_config = self.config.user(user)
        stored = await user_config.bot_first_account()
        if stored:
            binding = BotFirstAccountBinding.from_record(stored)
            binding.require_same_identity(
                discord_user_id=user.id, profile_id=str(context["profile_id"]),
                signer_address=signer, account_wallet_address=deposit_wallet,
            )
            return binding
        binding = BotFirstAccountBinding(
            discord_user_id=user.id, profile_id=str(context["profile_id"]),
            signer_address=signer, account_wallet_address=deposit_wallet,
            created_at=int(time.time()),
        )
        await user_config.bot_first_account.set(binding.to_record())
        return binding

    async def _request_cdp_clob_auth_signature(
        self, user, binding: BotFirstAccountBinding, *,
        timestamp: int, nonce: int,
    ) -> str:
        """Request and independently recover one exact transient ClobAuth signature."""

        cryptowallet = self.bot.get_cog("CryptoWallet")
        signer = getattr(cryptowallet, "polymarket_sign_clob_auth", None)
        if not callable(signer):
            raise AccountConnectionError("CryptoWallet signing is unavailable.")
        typed_data = polymarket_clob_auth_typed_data(
            binding.signer_address, timestamp=timestamp, nonce=nonce
        )
        approval_fingerprint = hashlib.sha256(
            (
                f"{binding.fingerprint}|{timestamp}|{nonce}|"
                "eip712_clob_auth"
            ).encode("ascii")
        ).hexdigest()
        try:
            result = await signer(
                user, typed_data=typed_data,
                approval_fingerprint=approval_fingerprint,
            )
        except RuntimeError as exc:
            raise AccountConnectionError(
                "CryptoWallet Polymarket signing is unavailable."
            ) from exc
        if (
            not isinstance(result, dict)
            or set(result) != {"signature", "signer_address"}
            or result["signer_address"] != binding.signer_address
        ):
            raise AccountConnectionError("CryptoWallet returned an invalid signer result.")
        digest = clob_auth_digest(
            signer_address=binding.signer_address,
            timestamp=timestamp, nonce=nonce,
        )
        recovered = recover_signer_address(digest, str(result["signature"]))
        if recovered != binding.signer_address:
            raise AccountConnectionError(
                "CDP signature does not match the bound CryptoWallet owner."
            )
        return str(result["signature"])

    async def _submit_approved_deposit_wallet_creation(
        self, user, plan: DepositWalletCreationPlan,
    ) -> DepositWalletCreationPlan:
        """Submit one persisted, exact, currently approved creation plan."""

        capabilities = await self.config.production_capabilities()
        if (
            not bool(await self.config.production_enabled())
            or bool(await self.config.production_paused())
            or not bool(capabilities.get("deposit_wallet_create"))
            or validate_polymarket_production_manifest()
        ):
            raise AccountConnectionError("Deposit Wallet creation is disabled.")
        binding = await self._bot_first_account(user)
        if (
            plan.discord_user_id != user.id
            or plan.signer_address != binding.signer_address
            or plan.deposit_wallet_address != binding.account_wallet_address
        ):
            raise AccountConnectionError("Deposit Wallet creation identity changed.")
        user_config = self.config.user(user)
        stored = await user_config.deposit_wallet_creation()
        if not stored or DepositWalletCreationPlan.from_record(stored) != plan:
            raise AccountConnectionError("Approved Deposit Wallet creation is not persisted.")
        tokens = await self.bot.get_shared_api_tokens("polymarket_builder")
        try:
            credentials = BuilderCredentials(
                tokens["api_key"], tokens["secret"], tokens["passphrase"]
            )
        except (KeyError, TypeError):
            raise AccountConnectionError(
                "Polymarket Builder credentials are incomplete."
            ) from None
        submitting = plan.begin_submission(now=int(time.time()))
        await user_config.deposit_wallet_creation.set(submitting.to_record())
        try:
            response = await self.deposit_wallet_relayer.submit_creation(
                submitting, credentials, timestamp=int(time.time())
            )
        except (aiohttp.ClientError, AccountConnectionError, TimeoutError):
            unknown = submitting.recover_after_restart()
            await user_config.deposit_wallet_creation.set(unknown.to_record())
            raise AccountConnectionError(
                "Deposit Wallet submission outcome is unknown; reconcile before retry."
            ) from None
        submitted = submitting.record_submission(response)
        await user_config.deposit_wallet_creation.set(submitted.to_record())
        return submitted

    async def _reconcile_deposit_wallet_creation(
        self, user,
    ) -> DepositWalletCreationPlan:
        """Reconcile public relayer state and independently verify a confirmed wallet."""

        user_config = self.config.user(user)
        stored = await user_config.deposit_wallet_creation()
        if not stored:
            raise AccountConnectionError("No Deposit Wallet creation is pending.")
        plan = DepositWalletCreationPlan.from_record(stored).recover_after_restart()
        if plan.discord_user_id != user.id:
            raise AccountConnectionError("Deposit Wallet creation belongs to another user.")
        if plan.state is DepositWalletCreationState.SUBMITTING:
            raise AccountConnectionError("Deposit Wallet creation state is invalid.")
        if plan.state not in {
            DepositWalletCreationState.SUBMITTED, DepositWalletCreationState.UNKNOWN,
        }:
            return plan
        evidence = await self.deposit_wallet_relayer.get_creation(plan)
        reconciled = plan.reconcile(evidence)
        if reconciled.state is DepositWalletCreationState.CONFIRMED:
            verified = await PolygonAccountIdentityVerifier(self._polygon_rpc).verify(
                signer_address=plan.signer_address,
                account_wallet_address=plan.deposit_wallet_address,
                wallet_type=WalletType.DEPOSIT_WALLET,
            )
            if verified.block_number < 0:
                raise AccountConnectionError("Deposit Wallet deployment was not verified.")
        await user_config.deposit_wallet_creation.set(reconciled.to_record())
        return reconciled

    async def _get_json(self, path: str, params: dict | None = None):
        async with aiohttp.ClientSession(timeout=REQUEST_TIMEOUT) as session:
            async with session.get(GAMMA_API + path, params=params, headers={"Accept": "application/json"}) as response:
                if response.status != 200:
                    raise RuntimeError(f"Polymarket returned HTTP {response.status}.")
                payload = await response.json(content_type=None)
        return payload

    async def _account_connect_allowed(self) -> bool:
        capabilities = await self.config.production_capabilities()
        return (
            bool(await self.config.production_enabled())
            and not bool(await self.config.production_paused())
            and bool(capabilities.get("account_connect"))
            and bool(capabilities.get("eligibility"))
            and not validate_polymarket_production_manifest()
        )

    async def _append_audit(self, user, event: str, binding: dict[str, Any]) -> None:
        allowed = {
            "terms_started", "terms_accepted", "connect_started",
            "connect_verified", "disconnected",
        }
        if event not in allowed:
            raise AccountConnectionError("Polymarket audit event is invalid.")
        digest = hashlib.sha256(
            json.dumps(binding, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        events = list(await self.config.user(user).audit_events() or [])[-49:]
        events.append({"event": event, "timestamp": int(time.time()), "digest": digest})
        await self.config.user(user).audit_events.set(events)

    async def _polygon_rpc(self, method: str, params: list[Any]):
        payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        async with aiohttp.ClientSession(timeout=REQUEST_TIMEOUT) as session:
            async with session.post(
                POLYMARKET_PRODUCTION_MANIFEST.polygon_rpc, json=payload,
                headers={"Accept": "application/json"},
            ) as response:
                if response.status != 200:
                    raise AccountConnectionError("Polygon RPC is unavailable.")
                result = await response.json(content_type=None)
        if not isinstance(result, dict) or "error" in result or "result" not in result:
            raise AccountConnectionError("Polygon RPC returned an invalid response.")
        return result["result"]

    async def _consume_onboarding_result(self, user, cryptowallet):
        if not await self._account_connect_allowed():
            raise AccountConnectionError("Protected Polymarket connection is disabled.")
        stored = await self.config.user(user).onboarding_challenge()
        if not stored:
            raise AccountConnectionError("No protected Polymarket connection is pending.")
        challenge = ProtectedOnboardingChallenge.from_record(stored)
        if challenge.discord_user_id != user.id:
            raise AccountConnectionError("The pending connection belongs to another user.")
        now = int(time.time())
        if now >= challenge.expires_at:
            await self.config.user(user).onboarding_challenge.set(None)
            raise AccountConnectionError("The protected Polymarket connection expired.")
        result = await cryptowallet.poll_polymarket_onboarding_result(challenge.result_handle)
        if result is None:
            return None
        await self.config.user(user).onboarding_challenge.set(None)
        eligibility = EligibilityAttestation(
            discord_user_id=user.id, blocked=result["blocked"],
            country=result["country"], region=result["region"],
            checked_at=result["checked_at"],
            expires_at=result["checked_at"] + ELIGIBILITY_LIFETIME_SECONDS,
        )
        eligibility.require_current(discord_user_id=user.id, now=now)
        signer_proof = verify_clob_auth_proof(
            challenge, signature=result["signature"], discord_user_id=user.id, now=now,
        )
        relationship = await PolygonAccountIdentityVerifier(self._polygon_rpc).verify(
            signer_address=challenge.signer_address,
            account_wallet_address=challenge.account_wallet_address,
            wallet_type=challenge.wallet_type,
        )
        evidence = finalize_onboarding_evidence(
            challenge, signer_proof=signer_proof, account_relationship=relationship,
            eligibility=eligibility, discord_user_id=user.id, now=now,
        )
        connection = complete_protected_onboarding(
            challenge, evidence, discord_user_id=user.id, now=now
        )
        if not await self._account_connect_allowed():
            raise AccountConnectionError("Protected Polymarket connection was paused.")
        await self.config.user(user).account_connection.set(connection.to_record())
        await self._append_audit(user, "connect_verified", connection.to_record())
        return connection

    async def _get_clob_json(self, path: str, params: dict | None = None):
        base = POLYMARKET_PRODUCTION_MANIFEST.clob_api
        async with aiohttp.ClientSession(timeout=REQUEST_TIMEOUT) as session:
            async with session.get(base + path, params=params, headers={"Accept": "application/json"}) as response:
                if response.status != 200:
                    raise RuntimeError(f"Polymarket CLOB returned HTTP {response.status}.")
                return await response.json(content_type=None)

    @commands.group(aliases=["poly"], invoke_without_command=True)
    async def polymarket(self, ctx: commands.Context):
        """Browse read-only Polymarket market information.

        Production actions remain default-off and emergency-paused.
        """
        prefix = ctx.clean_prefix
        embed = discord.Embed(
            title="Polymarket discovery",
            description="Public market information only. Market-implied probabilities are not financial advice.",
        )
        embed.add_field(name="Choose a category", value=f"`{prefix}poly markets` or `{prefix}poly markets crypto`\nPolitics, crypto, and sports.", inline=False)
        embed.add_field(name="Search market questions", value=f"`{prefix}poly search <words>`\nExamples: bitcoin, ethereum, fed rates, trump.", inline=False)
        embed.add_field(name="Trending", value=f"`{prefix}poly trending`\nActive markets ranked by 24-hour volume.", inline=False)
        embed.add_field(name="One specific market", value=f"`{prefix}poly market <ID, slug, or Polymarket link>`\nProbabilities, rules, resolution source, and link.", inline=False)
        embed.add_field(name="Future compatibility", value=f"`{prefix}poly compatible [words]` and `{prefix}poly readiness <market>`\nTechnical metadata only; trading is disabled.", inline=False)
        embed.add_field(name="Live approval preview", value=f"`{prefix}poly quote <market> <outcome> <max pUSD> [max price]`\nPublic quote only; nothing is signed or submitted.", inline=False)
        embed.add_field(name="Collateral disclosures", value=f"`{prefix}poly collateral <wrap|unwrap|standard|negative-risk> <amount> <account wallet>`", inline=False)
        embed.add_field(name="Safety status", value=f"`{prefix}poly status`", inline=False)
        embed.add_field(name="Protected account", value=f"DM-only `{prefix}poly terms`, `{prefix}poly termsconfirm`, `{prefix}poly disconnect`, and `{prefix}poly audit`\n`{prefix}poly account` automatically derives the CryptoWallet-owned Deposit Wallet. `{prefix}poly session` starts protected session authorization. Existing-account `connect`/`confirm` is compatibility-only. `{prefix}poly confirmations [on|off]` controls the default-on second approval check. Never send secrets in Discord.", inline=False)
        embed.set_footer(text="Read-only: no wallets, deposits, signatures, or trading.")
        await ctx.send(embed=embed)

    @polymarket.command(name="markets", aliases=["browse"])
    @commands.bot_has_permissions(embed_links=True)
    async def polymarket_markets(self, ctx: commands.Context, category: str = ""):
        """Choose politics, crypto, or sports markets."""
        if not category:
            await ctx.invoke(self.polymarket_categories)
            return
        category = category.casefold()
        if category not in CATEGORIES:
            await ctx.send(
                f"Choose **politics**, **crypto**, or **sports**. For keywords, try "
                f"`{ctx.clean_prefix}poly search bitcoin`."
            )
            return
        await ctx.invoke(self.polymarket_category, category=category)

    @polymarket.command(name="search", aliases=["find"])
    @commands.bot_has_permissions(embed_links=True)
    async def polymarket_search(self, ctx: commands.Context, *, query: str):
        """Search active market questions, e.g. bitcoin, ethereum, fed rates, or trump."""
        try:
            markets = _active_search_markets(await self._get_json("/public-search", {"q": query.strip()}))
        except (aiohttp.ClientError, RuntimeError, ValueError):
            await ctx.send("Polymarket market data could not be reached right now.")
            return
        terms = query.casefold().split()
        selected = [
            market for market in markets
            if all(term in str(market.get("question", "")).casefold() for term in terms)
        ][:10]
        if not selected:
            await ctx.send("No active Polymarket markets matched that search.")
            return
        await self._send_market_list(ctx, f"Polymarket search: {query.strip()}", selected)

    async def _send_market_list(self, ctx, title, markets):
        embed = discord.Embed(title=title, description="Read-only market-implied probabilities; not financial advice.")
        for market in markets[:10]:
            outcomes, prices = _json_list(market.get("outcomes")), _json_list(market.get("outcomePrices"))
            probability = " · ".join(
                f"{outcome}: {float(price):.0%}" for outcome, price in zip(outcomes, prices)
                if str(price).replace(".", "", 1).isdigit()
            )
            details = (probability or "Probability unavailable")
            details += "\nID `" + str(market.get("id")) + "` · [Open market](" + market_url(market) + ")"
            embed.add_field(name=str(market.get("question") or "Untitled market")[:256],
                            value=details[:1024], inline=False)
        await ctx.send(embed=embed)

    @polymarket.command(name="categories", aliases=["types"])
    async def polymarket_categories(self, ctx: commands.Context):
        """Show the curated market categories."""
        prefix = ctx.clean_prefix
        lines = [f"**{label}** - `{prefix}poly category {slug}`" for slug, (label, _) in CATEGORIES.items()]
        embed = discord.Embed(
            title="Polymarket categories",
            description="Choose a category instead of browsing unrelated markets.\n\n" + "\n".join(lines),
        )
        embed.set_footer(text=f"Search anything: {prefix}poly search bitcoin")
        await ctx.send(embed=embed)

    @polymarket.command(name="category", aliases=["type"])
    @commands.bot_has_permissions(embed_links=True)
    async def polymarket_category(self, ctx: commands.Context, category: str):
        """List active politics, crypto, or sports markets."""
        selected = CATEGORIES.get(category.casefold())
        if not selected:
            await ctx.send("Choose one of: **politics**, **crypto**, or **sports**.")
            return
        label, tag_id = selected
        try:
            markets = await self._get_json("/markets", {
                "active": "true", "closed": "false", "tag_id": tag_id, "limit": 10,
                "order": "volume24hr", "ascending": "false",
            })
        except (aiohttp.ClientError, RuntimeError, ValueError):
            await ctx.send("Polymarket market data could not be reached right now.")
            return
        if not isinstance(markets, list) or not markets:
            await ctx.send(f"No active {label.lower()} markets were returned.")
            return
        await self._send_market_list(ctx, f"Polymarket: {label}", markets)

    @polymarket.command(name="trending", aliases=["top"])
    @commands.bot_has_permissions(embed_links=True)
    async def polymarket_trending(self, ctx: commands.Context):
        """List active markets ranked by 24-hour volume across all categories."""
        try:
            markets = await self._get_json("/markets", {
                "active": "true", "closed": "false", "limit": 10,
                "order": "volume24hr", "ascending": "false",
            })
        except (aiohttp.ClientError, RuntimeError, ValueError):
            await ctx.send("Polymarket market data could not be reached right now.")
            return
        if not isinstance(markets, list) or not markets:
            await ctx.send("No active Polymarket markets were returned.")
            return
        await self._send_market_list(ctx, "Trending Polymarket markets", markets)

    @polymarket.command(name="compatible", aliases=["ready"])
    @commands.bot_has_permissions(embed_links=True)
    async def polymarket_compatible(self, ctx: commands.Context, *, query: str = ""):
        """List technically order-ready markets for a future Polygon CLOB V2 handoff."""
        try:
            if query.strip():
                markets = _active_search_markets(await self._get_json("/public-search", {"q": query.strip()}))
            else:
                markets = await self._get_json("/markets", {"active": "true", "closed": "false", "limit": 50, "order": "volume24hr", "ascending": "false"})
        except (aiohttp.ClientError, RuntimeError, ValueError):
            await ctx.send("Polymarket market data could not be reached right now.")
            return
        ready = [market for market in markets if technically_handoff_ready(market)][:10] if isinstance(markets, list) else []
        if not ready:
            await ctx.send("No technically order-ready active Polymarket markets matched that search.")
            return
        embed = discord.Embed(
            title="Future CryptoWallet-compatible markets",
            description="Technical CLOB V2 readiness only—not user eligibility or trading availability.",
        )
        for market in ready:
            outcomes, prices = _json_list(market.get("outcomes")), _json_list(market.get("outcomePrices"))
            probability = " · ".join(f"{outcome}: {float(price):.0%}" for outcome, price in zip(outcomes, prices) if str(price).replace(".", "", 1).isdigit())
            value = (probability or "Probability unavailable") + f"\nID `{market.get('id')}` · [Open market]({market_url(market)})"
            embed.add_field(name=str(market.get("question") or "Untitled market")[:256], value=value[:1024], inline=False)
        embed.set_footer(text="Future path: Polygon mainnet · pUSD · user-controlled approval · eligibility required")
        await ctx.send(embed=embed)

    @polymarket.command(name="readiness", aliases=["handoff"])
    @commands.bot_has_permissions(embed_links=True)
    async def polymarket_readiness(self, ctx: commands.Context, reference: str):
        """Show public technical readiness for a future disabled Polygon handoff."""
        path = market_path(reference)
        if not path:
            await ctx.send("Use a Polymarket market ID, slug, or link.")
            return
        try:
            market = await self._get_json(path)
            snapshot = MarketSnapshot.from_market(market, quote_timestamp=int(time.time()))
        except (aiohttp.ClientError, RuntimeError, ValueError, MarketSnapshotError):
            await ctx.send("That market is not technically ready for the staged future handoff.")
            return
        embed = discord.Embed(title="Future handoff readiness", url=market_url(market), description=snapshot.question)
        embed.add_field(name="Technical market state", value="Active CLOB market with accepting order book and outcome tokens.", inline=False)
        embed.add_field(name="Future target", value=f"Polygon mainnet (`137`) · pUSD\nSelected-market minimum size: `{snapshot.minimum_order_size or 'not supplied'}`", inline=False)
        embed.add_field(name="Execution state", value="Disabled. No wallet lookup, account creation, balance check, approval, signature, or order occurs.", inline=False)
        embed.set_footer(text="A later protected approval, eligibility, security, and release review is required.")
        await ctx.send(embed=embed)

    @polymarket.command(name="quote")
    @commands.bot_has_permissions(embed_links=True)
    async def polymarket_quote(self, ctx: commands.Context, reference: str, outcome: str,
                               max_spend_pusd: str, max_price: str | None = None):
        """Preview a bounded live CLOB market buy without signing or submitting it."""
        path = market_path(reference)
        if not path:
            await ctx.send("Use a Polymarket market ID, slug, or link.")
            return
        now = int(time.time())
        try:
            market = await self._get_json(path)
            snapshot = MarketSnapshot.from_market(market, quote_timestamp=now)
            selected = next((index for index, label in enumerate(snapshot.outcomes)
                             if label.casefold() == outcome.casefold()), None)
            if selected is None and outcome.isdigit() and 1 <= int(outcome) <= len(snapshot.outcomes):
                selected = int(outcome) - 1
            if selected is None:
                raise OrderIntentError("Outcome is not part of this market.")
            token_id = snapshot.outcome_token_ids[selected]
            book_payload = await self._get_clob_json("/book", {"token_id": token_id})
            fee_payload = await self._get_clob_json("/fee-rate", {"token_id": token_id})
            quote = OrderBookSnapshot.from_payload(book_payload, captured_at=now)
            base_fee_bps = int(fee_payload["base_fee"])
            approval = MarketBuyApproval.create(
                requester_id=ctx.author.id, market_id=snapshot.market_id,
                condition_id=snapshot.condition_id, outcome=snapshot.outcomes[selected],
                quote=quote, max_price=max_price or format(quote.best_ask, "f"),
                max_spend_pusd=max_spend_pusd, maximum_base_fee_bps=base_fee_bps,
                expires_at=now + 120,
            )
        except (aiohttp.ClientError, RuntimeError, ValueError, KeyError, TypeError,
                MarketSnapshotError, OrderIntentError):
            await ctx.send("A complete bounded live quote could not be produced. Check the market, outcome, amount, and maximum price.")
            return
        embed = discord.Embed(
            title="Polymarket market-buy preview", url=market_url(market),
            description=snapshot.question,
        )
        embed.add_field(name="Outcome", value=approval.outcome, inline=True)
        embed.add_field(name="Best ask / ceiling", value=f"{quote.best_ask} / {approval.max_price}", inline=True)
        embed.add_field(name="All-in cap", value=f"{approval.max_spend_pusd} pUSD", inline=True)
        embed.add_field(name="Maximum notional", value=f"{approval.maximum_notional} pUSD", inline=True)
        embed.add_field(name="Fee reserve", value=f"up to {approval.maximum_fee_pusd} pUSD (base fee {base_fee_bps} bps)", inline=True)
        embed.add_field(name="Market constraints", value=f"Minimum {quote.minimum_order_size} shares · tick {quote.tick_size} · {'negative-risk' if quote.negative_risk else 'standard'} exchange", inline=False)
        embed.add_field(name="Approval fingerprint", value=f"`{approval.fingerprint}`", inline=False)
        embed.add_field(name="Expires", value=f"<t:{approval.expires_at}:R>", inline=True)
        embed.add_field(name="Execution", value="Preview only. No account, balance, allowance, signature, credential, or order was used.", inline=False)
        await ctx.send(embed=embed)

    @polymarket.command(name="collateral")
    @commands.bot_has_permissions(embed_links=True)
    async def polymarket_collateral(self, ctx: commands.Context, mode: str, amount: str,
                                    account_wallet: str):
        """Inspect exact collateral approvals and revocations without executing them."""
        choice = mode.casefold()
        action = "trading" if choice in {"standard", "negative-risk"} else choice
        try:
            plan = collateral_plan(
                action, amount, account_wallet, negative_risk=choice == "negative-risk"
            )
        except (CollateralPlanError, ValueError):
            await ctx.send(
                "Choose wrap, unwrap, standard, or negative-risk; provide a positive amount "
                "with at most six decimals and a complete Polygon account-wallet address."
            )
            return
        embed = discord.Embed(
            title="Polymarket collateral disclosure",
            description=f"{plan.action.title()} {plan.display_amount} {plan.source_asset} to {plan.destination_asset}",
        )
        embed.add_field(name="Account wallet", value=plan.account_wallet, inline=False)
        embed.add_field(name="Action contract", value=f"{plan.contract}\n{plan.function}", inline=False)
        for index, approval in enumerate(plan.approvals, 1):
            amount_text = (
                f"exactly {approval.amount_base_units} base units ({plan.display_amount} {approval.asset})"
                if approval.amount_base_units is not None else "operator access: true"
            )
            embed.add_field(
                name=f"Approval {index}: {approval.asset}",
                value=(
                    f"Token: {approval.token}\nSpender: {approval.spender}\n"
                    f"Permission: {amount_text}\nPurpose: {approval.purpose}\n"
                    f"Revoke: set value to {approval.revoke_value}"
                ),
                inline=False,
            )
        embed.add_field(
            name="Execution",
            value="Disclosure only. No allowance, operator permission, wrap, unwrap, transfer, or transaction occurs.",
            inline=False,
        )
        await ctx.send(embed=embed)

    @polymarket.command(name="market", aliases=["info"])
    @commands.bot_has_permissions(embed_links=True)
    async def polymarket_market(self, ctx: commands.Context, reference: str):
        """Show outcomes, probabilities, rules, and links from a market ID, slug, or Polymarket link."""
        reference_key = reference.casefold()
        if reference_key in CATEGORIES:
            await ctx.send(
                f"**{reference_key}** is a category. Use "
                f"`{ctx.clean_prefix}poly markets {reference_key}`. "
                f"For keyword matching, use `{ctx.clean_prefix}poly search {reference_key}`."
            )
            return
        path = market_path(reference)
        if not path:
            await ctx.send("Use a Polymarket market ID, slug, or `polymarket.com/event/...` link.")
            return
        try:
            market = await self._get_json(path)
        except (aiohttp.ClientError, RuntimeError, ValueError):
            await ctx.send(
                f"That exact market could not be reached. Use an ID, slug, or Polymarket link, "
                f"or try `{ctx.clean_prefix}poly search {reference}`."
            )
            return
        if not isinstance(market, dict):
            await ctx.send("Polymarket returned an unexpected market response.")
            return
        embed = discord.Embed(title=str(market.get("question") or "Polymarket market"), url=market_url(market), description=str(market.get("description") or "No market description was supplied.")[:4096])
        outcomes, prices = _json_list(market.get("outcomes")), _json_list(market.get("outcomePrices"))
        lines = []
        for outcome, price in zip(outcomes, prices):
            try:
                lines.append(f"{outcome}: **{float(price):.1%}**")
            except (TypeError, ValueError):
                lines.append(f"{outcome}: unavailable")
        embed.add_field(name="Market-implied probabilities", value="\n".join(lines) or "Unavailable", inline=False)
        if market.get("resolutionSource"):
            embed.add_field(name="Resolution source", value=str(market["resolutionSource"])[:1024], inline=False)
        details = []
        if market.get("endDateIso") or market.get("endDate"):
            details.append("Closes: " + str(market.get("endDateIso") or market.get("endDate")))
        if market.get("volume"):
            details.append("Volume: " + str(market["volume"]))
        if market.get("liquidity"):
            details.append("Liquidity: " + str(market["liquidity"]))
        embed.set_footer(text=" · ".join(details) or "Read-only Polymarket data")
        await ctx.send(embed=embed)

    @polymarket.command(name="status")
    async def polymarket_status(self, ctx: commands.Context):
        """Show the reviewed, default-off production integration boundary."""
        manifest = POLYMARKET_PRODUCTION_MANIFEST
        drift = validate_polymarket_production_manifest(manifest)
        enabled = bool(await self.config.production_enabled())
        paused = bool(await self.config.production_paused())
        embed = discord.Embed(
            title="Polymarket integration status",
            description="Public discovery is available. Production execution remains disabled.",
        )
        embed.add_field(
            name="Production target",
            value=f"Polygon mainnet (`{manifest.chain_id}`) · {manifest.collateral_symbol} ({manifest.collateral_decimals} decimals)",
            inline=False,
        )
        embed.add_field(
            name="Wallet model",
            value="CryptoWallet's verified CDP EOA is the signer. Its separately derived Deposit Wallet is the Polymarket account; legacy Proxy and Safe connections remain compatibility-only.",
            inline=False,
        )
        embed.add_field(
            name="Reviewed boundary",
            value=(
                f"Manifest: {'valid' if not drift else 'drift detected'} · "
                f"installation: {'enabled' if enabled else 'disabled'} · "
                f"pause: {'active' if paused else 'inactive'} · execution disabled"
            ),
            inline=False,
        )
        embed.set_footer(text="No wallet creation, credentials, approvals, signatures, deposits, or orders")
        await ctx.send(embed=embed)

    @polymarket.command(name="terms")
    @commands.dm_only()
    async def polymarket_terms(self, ctx: commands.Context):
        """Open the protected, product-specific Polymarket terms acceptance."""
        cryptowallet = self.bot.get_cog("CryptoWallet")
        required = (
            "recovery_relay_status", "create_external_companion_handoff",
            "register_recovery_handoff", "poll_polymarket_terms_result",
        )
        if cryptowallet is None or not all(
            callable(getattr(cryptowallet, name, None)) for name in required
        ):
            await ctx.send("The protected CryptoWallet companion is unavailable.")
            return
        try:
            status = await cryptowallet.recovery_relay_status()
            base_url = status.get("approval_base_url")
            if not status.get("configured") or not base_url:
                raise RuntimeError("companion unavailable")
            result_handle = secrets.token_urlsafe(32)
            payload = {
                "product": POLYMARKET_TERMS_PRODUCT,
                "version": POLYMARKET_TERMS_VERSION,
                "result_handle": result_handle,
            }
            token, expires_at = await cryptowallet.create_external_companion_handoff(
                ctx.author.id, "polymarket_terms", payload
            )
            handoff = await cryptowallet.register_recovery_handoff(
                token, expires_at, purpose="polymarket_terms"
            )
            challenge = {
                "result_handle": result_handle, "expires_at": expires_at,
                "version": POLYMARKET_TERMS_VERSION,
            }
            await self.config.user(ctx.author).terms_challenge.set(challenge)
            await self._append_audit(ctx.author, "terms_started", challenge)
            encoded_handoff = quote(handoff, safe="")
            link = f"{base_url}/polymarket-terms.html#handoff={encoded_handoff}"
        except (KeyError, RuntimeError, ValueError):
            await ctx.send("Protected Polymarket terms acceptance could not be prepared.")
            return
        await ctx.send(
            f"Open this one-time protected link: {link}\n"
            f"It expires <t:{expires_at}:R>. Then run "
            f"`{ctx.clean_prefix}poly termsconfirm`."
        )

    @polymarket.command(name="termsconfirm")
    @commands.dm_only()
    async def polymarket_terms_confirm(self, ctx: commands.Context):
        """Consume one protected Polymarket terms acceptance."""
        user_config = self.config.user(ctx.author)
        challenge = await user_config.terms_challenge()
        if (
            not isinstance(challenge, dict)
            or set(challenge) != {"result_handle", "expires_at", "version"}
            or challenge.get("version") != POLYMARKET_TERMS_VERSION
            or int(challenge.get("expires_at", 0)) <= int(time.time())
        ):
            await user_config.terms_challenge.set(None)
            await ctx.send("No current protected Polymarket terms acceptance is pending.")
            return
        cryptowallet = self.bot.get_cog("CryptoWallet")
        poll = getattr(cryptowallet, "poll_polymarket_terms_result", None)
        if not callable(poll):
            await ctx.send("The protected CryptoWallet companion is unavailable.")
            return
        try:
            result = await poll(challenge["result_handle"])
        except RuntimeError:
            await ctx.send("The protected terms result could not be verified.")
            return
        if result is None:
            await ctx.send("Complete the protected page first, then run this command again.")
            return
        try:
            acceptance = create_polymarket_terms_acceptance(
                ctx.author.id, now=int(time.time()),
                acceptance_id=result["acceptance_id"],
            )
        except (KeyError, TypeError, ValueError):
            await user_config.terms_challenge.set(None)
            await ctx.send("The protected terms result had an invalid binding.")
            return
        await user_config.terms_challenge.set(None)
        await user_config.terms_acceptance.set(acceptance)
        await self._append_audit(ctx.author, "terms_accepted", acceptance)
        await ctx.send(
            f"Polymarket terms **{POLYMARKET_TERMS_VERSION}** accepted. "
            "No account was connected and no transaction was submitted."
        )

    @polymarket.command(name="audit")
    @commands.dm_only()
    async def polymarket_audit(self, ctx: commands.Context):
        """Show the caller their bounded, digest-only Polymarket safety audit."""
        events = list(await self.config.user(ctx.author).audit_events() or [])[-10:]
        lines = [
            "<t:{}:f> `{}` `{}...`".format(
                int(item.get("timestamp")), item.get("event"),
                item.get("digest", "")[:12],
            )
            for item in events if isinstance(item, dict)
            and set(item) == {"event", "timestamp", "digest"}
        ]
        await ctx.send(
            "**Polymarket safety audit (latest 10)**\n" + "\n".join(lines)
            if lines else "No Polymarket safety events are recorded."
        )

    @polymarket.command(name="account")
    async def polymarket_account(self, ctx: commands.Context):
        """Automatically prepare and show the caller's CryptoWallet-owned account."""
        try:
            binding = await self._bot_first_account(ctx.author)
        except AccountConnectionError:
            record = await self.config.user(ctx.author).account_connection()
            if not record:
                await ctx.send(
                    "The Polymarket account could not be prepared from CryptoWallet. "
                    "No wallet was connected and no transaction was submitted."
                )
                return
            try:
                connection = AccountConnection.from_record(record)
            except AccountConnectionError:
                await ctx.send(
                    "The stored Polymarket connection is invalid and cannot be used."
                )
                return
            await ctx.send(
                "**Polymarket account (existing-account compatibility)**\n"
                f"State: **{connection.state.value}**\n"
                f"Wallet type: **{connection.wallet_type.value}**\n"
                f"Signer: `{connection.signer_address}`\n"
                f"Account wallet: `{connection.account_wallet_address}`\n"
                "Order execution remains disabled."
            )
            return
        await ctx.send(
            "**Polymarket account**\n"
            "Source: **CryptoWallet CDP owner**\n"
            "State: **derived; deployment not submitted**\n"
            f"Wallet type: **{binding.wallet_type.value}**\n"
            f"Signer: `{binding.signer_address}`\n"
            f"Deposit Wallet: `{binding.account_wallet_address}`\n"
            "No separate Polymarket setup is required. No funds moved, signature "
            "was requested, or transaction was submitted."
        )

    @polymarket.command(name="session")
    @commands.dm_only()
    async def polymarket_session(self, ctx: commands.Context):
        """Start or resume protected CLOB session authorization."""
        capabilities = await self.config.production_capabilities()
        if (not await self._session_capability_allowed()
                or not bool(capabilities.get("eligibility"))):
            await ctx.send(
                "Protected Polymarket session setup is disabled or emergency-paused."
            )
            return
        user_config = self.config.user(ctx.author)
        if not is_current_polymarket_terms_acceptance(
            await user_config.terms_acceptance(), ctx.author.id
        ):
            await ctx.send(
                f"Accept the current Polymarket terms first with "
                f"`{ctx.clean_prefix}poly terms`."
            )
            return
        now = int(time.time())
        try:
            binding = await self._bot_first_account(ctx.author)
            request = await self._stored_session_approval(ctx.author)
            if request is not None and now >= request.expires_at:
                await user_config.session_approval.set(None)
                request = None
            if (request is not None
                    and request.state is SessionApprovalState.CONSUMED
                    and request.action == "deploy"):
                deployment = await self._reconcile_deposit_wallet_creation(ctx.author)
                if deployment.state is DepositWalletCreationState.CONFIRMED:
                    await user_config.session_approval.set(None)
                    request = None
                elif deployment.state is DepositWalletCreationState.FAILED:
                    await ctx.send(
                        "Deposit Wallet deployment failed. No retry was submitted; "
                        "owner review is required."
                    )
                    return
                else:
                    await ctx.send(
                        "Deposit Wallet deployment is still being reconciled. "
                        "No duplicate submission was made."
                    )
                    return
            if request is not None and (
                request.signer_address != binding.signer_address
                or request.account_wallet_address != binding.account_wallet_address
            ):
                raise AccountConnectionError(
                    "The pending session request no longer matches CryptoWallet."
                )
            if request is None:
                lifecycle_record = await user_config.session_lifecycle()
                action = "provision"
                if lifecycle_record:
                    lifecycle = SessionKeyLifecycle.from_record(lifecycle_record)
                    maintenance = lifecycle.maintenance_action(now=now)
                    if maintenance != "rotate":
                        await ctx.send(
                            "Your Polymarket session authorization already exists. "
                            f"Current state: **{lifecycle.status.value}**."
                        )
                        return
                    action = "rotate"
                else:
                    verifier = PolygonAccountIdentityVerifier(self._polygon_rpc)
                    try:
                        await verifier.verify(
                            signer_address=binding.signer_address,
                            account_wallet_address=binding.account_wallet_address,
                            wallet_type=WalletType.DEPOSIT_WALLET,
                        )
                    except AccountConnectionError:
                        target = await verifier.verify_deposit_wallet_creation_target(
                            binding.signer_address
                        )
                        if target.deposit_wallet_address != binding.account_wallet_address:
                            raise AccountConnectionError(
                                "Derived Deposit Wallet identity changed."
                            )
                        action = "deploy"
                cryptowallet = self.bot.get_cog("CryptoWallet")
                required = (
                    "recovery_relay_status", "create_external_companion_handoff",
                    "register_recovery_handoff",
                    "poll_polymarket_eligibility_result",
                )
                if cryptowallet is None or not all(
                    callable(getattr(cryptowallet, name, None)) for name in required
                ):
                    raise AccountConnectionError(
                        "The protected CryptoWallet companion is unavailable."
                    )
                status = await cryptowallet.recovery_relay_status()
                if not status.get("configured"):
                    raise AccountConnectionError(
                        "The protected CryptoWallet companion is unavailable."
                    )
                payload = {
                    "request_id": secrets.token_urlsafe(32),
                    "result_handle": secrets.token_urlsafe(32),
                    "discord_user_id": ctx.author.id, "action": action,
                    "signer_address": binding.signer_address,
                    "account_wallet_address": binding.account_wallet_address,
                    "created_at": now, "expires_at": now + 300,
                    "chain_id": 137, "purpose": "polymarket_eligibility",
                }
                token, expires_at = await cryptowallet.create_external_companion_handoff(
                    ctx.author.id, "polymarket_eligibility", payload
                )
                if expires_at != payload["expires_at"]:
                    raise AccountConnectionError(
                        "Protected eligibility expiry changed unexpectedly."
                    )
                handoff = await cryptowallet.register_recovery_handoff(
                    token, expires_at, purpose="polymarket_eligibility"
                )
                request = SessionApprovalRequest(
                    request_id=payload["request_id"],
                    result_handle=payload["result_handle"],
                    handoff_handle=handoff, requester_id=ctx.author.id,
                    action=action, signer_address=binding.signer_address,
                    account_wallet_address=binding.account_wallet_address,
                    created_at=now, expires_at=expires_at,
                    final_confirmation_required=bool(
                        await user_config.final_confirmation_required()
                    ),
                )
                await user_config.session_approval.set(request.to_record())
            status = await self.bot.get_cog("CryptoWallet").recovery_relay_status()
            eligibility_url = None
            if request.state is SessionApprovalState.AWAITING_ELIGIBILITY:
                eligibility_url = (
                    f"{status['approval_base_url']}/polymarket-eligibility.html"
                    f"#handoff={quote(request.handoff_handle, safe='')}"
                )
            view = SessionApprovalView(self, request, eligibility_url)
            message = await ctx.send(
                embed=self._session_approval_embed(request), view=view
            )
            view.message = message
        except (AccountConnectionError, KeyError, RuntimeError, TypeError, ValueError):
            await ctx.send(
                "Protected session setup could not be prepared. Nothing was signed "
                "or submitted."
            )

    @polymarket.command(name="confirmations", aliases=["doublecheck"])
    async def polymarket_confirmations(self, ctx: commands.Context, mode: str = ""):
        """Show or change the optional second trade confirmation."""
        user_config = self.config.user(ctx.author)
        current = bool(await user_config.final_confirmation_required())
        choice = str(mode or "").strip().casefold()
        if not choice:
            await ctx.send(
                "Second trade confirmation is **{}**. Use `{}poly confirmations on` "
                "or `{}poly confirmations off`.".format(
                    "on" if current else "off", ctx.clean_prefix, ctx.clean_prefix
                )
            )
            return
        if choice not in {"on", "off"}:
            await ctx.send("Use `poly confirmations on` or `poly confirmations off`.")
            return
        enabled = choice == "on"
        await user_config.final_confirmation_required.set(enabled)
        await ctx.send(
            "Second trade confirmation is now **{}**. "
            "The first approval always remains required.".format(
                "on" if enabled else "off"
            )
        )

    @polymarket.command(name="connect")
    @commands.dm_only()
    async def polymarket_connect(
        self, ctx: commands.Context, signer_address: str,
        account_wallet_address: str, wallet_type: str,
    ):
        """Start protected existing-account verification in DM."""
        if not await self._account_connect_allowed():
            await ctx.send("Protected Polymarket connection is disabled or emergency-paused.")
            return
        if not is_current_polymarket_terms_acceptance(
            await self.config.user(ctx.author).terms_acceptance(), ctx.author.id
        ):
            await ctx.send(
                f"Accept the current Polymarket terms first with "
                f"`{ctx.clean_prefix}poly terms`."
            )
            return
        existing_record = await self.config.user(ctx.author).account_connection()
        if existing_record:
            try:
                existing = AccountConnection.from_record(existing_record)
            except AccountConnectionError:
                await ctx.send("The stored Polymarket connection is invalid and must be cleared.")
                return
            if existing.state is ConnectionState.VERIFIED:
                await ctx.send("Disconnect the current Polymarket account before replacing it.")
                return
        cryptowallet = self.bot.get_cog("CryptoWallet")
        required = (
            "recovery_relay_status", "create_external_companion_handoff",
            "register_recovery_handoff", "poll_polymarket_onboarding_result",
        )
        if cryptowallet is None or not all(
            callable(getattr(cryptowallet, name, None)) for name in required
        ):
            await ctx.send("The protected CryptoWallet companion is unavailable.")
            return
        try:
            kind = WalletType(str(wallet_type).strip().upper())
            now = int(time.time())
            challenge = ProtectedOnboardingChallenge(
                connection_id=secrets.token_urlsafe(24),
                result_handle=secrets.token_urlsafe(32),
                discord_user_id=ctx.author.id, signer_address=signer_address,
                account_wallet_address=account_wallet_address, wallet_type=kind,
                challenge=secrets.token_urlsafe(32), created_at=now,
                expires_at=now + ONBOARDING_LIFETIME_SECONDS,
            )
            status = await cryptowallet.recovery_relay_status()
            if not status.get("configured"):
                raise RuntimeError("companion unavailable")
            token, expires_at = await cryptowallet.create_external_companion_handoff(
                ctx.author.id, "polymarket_connect", challenge.to_record()
            )
            if expires_at != challenge.expires_at:
                raise RuntimeError("companion expiry drift")
            handoff = await cryptowallet.register_recovery_handoff(
                token, expires_at, purpose="polymarket_connect"
            )
            await self.config.user(ctx.author).onboarding_challenge.set(
                challenge.to_record()
            )
            await self._append_audit(ctx.author, "connect_started", challenge.to_record())
            link = (
                f"{status['approval_base_url']}/polymarket-connect.html"
                f"#handoff={quote(handoff, safe='')}"
            )
        except (AccountConnectionError, KeyError, RuntimeError, ValueError):
            await ctx.send(
                "Protected Polymarket connection could not be prepared. Nothing was connected."
            )
            return
        await ctx.send(
            f"Open this one-time protected link: {link}\n"
            f"It expires <t:{expires_at}:R>. Then run `{ctx.clean_prefix}poly confirm`. "
            "The page will check eligibility before requesting a signer proof."
        )

    @polymarket.command(name="confirm")
    @commands.dm_only()
    async def polymarket_confirm(self, ctx: commands.Context):
        """Consume and independently verify one protected connection result."""
        if not await self._account_connect_allowed():
            await ctx.send("Protected Polymarket connection is disabled or emergency-paused.")
            return
        cryptowallet = self.bot.get_cog("CryptoWallet")
        if cryptowallet is None or not callable(
            getattr(cryptowallet, "poll_polymarket_onboarding_result", None)
        ):
            await ctx.send("The protected CryptoWallet companion is unavailable.")
            return
        try:
            connection = await self._consume_onboarding_result(ctx.author, cryptowallet)
        except (aiohttp.ClientError, AccountConnectionError, KeyError, RuntimeError, ValueError):
            await ctx.send(
                "The protected result could not be verified. No Polymarket account was connected."
            )
            return
        if connection is None:
            await ctx.send("Complete the protected page first, then run this command again.")
            return
        await ctx.send(
            "Polymarket account connected with independent signer, wallet-derivation, "
            "Polygon deployment, and eligibility checks. No funds moved and no order was placed."
        )

    @polymarket.command(name="disconnect")
    @commands.dm_only()
    async def polymarket_disconnect(self, ctx: commands.Context):
        """Disconnect the caller's public Polymarket account binding."""
        user_config = self.config.user(ctx.author)
        await user_config.onboarding_challenge.set(None)
        record = await user_config.account_connection()
        if not record:
            await ctx.send("No Polymarket account is connected.")
            return
        try:
            connection = AccountConnection.from_record(record)
        except AccountConnectionError:
            await user_config.account_connection.set(None)
            await ctx.send("The invalid Polymarket account record was cleared.")
            return
        if connection.state is ConnectionState.DISCONNECTED:
            await ctx.send("The Polymarket account is already disconnected.")
            return
        disconnected = connection.disconnect(
            discord_user_id=ctx.author.id, now=int(time.time())
        )
        await user_config.account_connection.set(disconnected.to_record())
        await self._append_audit(ctx.author, "disconnected", disconnected.to_record())
        await ctx.send(
            "Polymarket account disconnected. No session key or trading credential "
            "was created by this release, and no transaction was submitted."
        )

    async def red_delete_data_for_user(self, *, requester, user_id: int) -> None:
        """Delete the user's Polymarket connection, challenge, and audit records."""
        await self.config.user_from_id(user_id).clear()

    @commands.group(name="polyset")
    @checks.is_owner()
    async def polymarketset(self, ctx: commands.Context):
        """Owner-only Polymarket production controls."""
        pass

    @polymarketset.command(name="productionstatus")
    async def polymarketset_production_status(self, ctx: commands.Context):
        """Show the default-off Polygon production control state."""
        capabilities = await self.config.production_capabilities()
        enabled = [name for name in PRODUCTION_CAPABILITIES if capabilities.get(name)]
        await ctx.send(
            "**Polymarket Polygon production**\n"
            f"Manifest: **{'valid' if not validate_polymarket_production_manifest() else 'drift detected'}**\n"
            f"Installation enabled: **{bool(await self.config.production_enabled())}**\n"
            f"Emergency paused: **{bool(await self.config.production_paused())}**\n"
            f"Enabled capabilities: **{', '.join(enabled) if enabled else 'none'}**\n"
            f"Session-key policy: **{'valid, beta, non-executable' if not validate_session_key_policy() else 'drift detected'}**\n"
            f"Limits: **{(await self.config.production_limits())['per_order_pusd']} / "
            f"{(await self.config.production_limits())['per_user_day_pusd']} / "
            f"{(await self.config.production_limits())['installation_day_pusd']} pUSD**\n"
            "Order execution: **code-disabled**"
        )

    @polymarketset.command(name="onboardingcontrol")
    async def polymarketset_onboarding_control(
        self, ctx: commands.Context, mode: str, *, acknowledgement: str = "",
    ):
        """Enable only protected, non-transactional existing-account onboarding."""
        choice = str(mode or "").strip().lower()
        if choice in {"pause", "disable"}:
            capabilities = await self.config.production_capabilities()
            capabilities["account_connect"] = False
            capabilities["eligibility"] = False
            await self.config.production_capabilities.set(capabilities)
            await self.config.production_enabled.set(False)
            await self.config.production_paused.set(True)
            await ctx.send("Protected Polymarket onboarding is disabled and emergency-paused.")
            return
        if choice != "enable":
            await ctx.send("Use `onboardingcontrol enable`, `pause`, or `disable`.")
            return
        if acknowledgement != ONBOARDING_ENABLE_ACKNOWLEDGEMENT:
            await ctx.send(
                "Onboarding remains disabled. Repeat the command with the exact "
                f"acknowledgment: `{ONBOARDING_ENABLE_ACKNOWLEDGEMENT}`"
            )
            return
        if validate_polymarket_production_manifest():
            await ctx.send("Onboarding remains disabled because the manifest has drifted.")
            return
        capabilities = {name: False for name in PRODUCTION_CAPABILITIES}
        capabilities["account_connect"] = True
        capabilities["eligibility"] = True
        await self.config.production_capabilities.set(capabilities)
        await self.config.production_enabled.set(True)
        await self.config.production_paused.set(False)
        await ctx.send(
            "Protected existing-account onboarding is enabled. Transaction, collateral, "
            "wallet-creation, order, cancel, and redeem capabilities remain disabled."
        )

    @polymarketset.command(name="limits")
    async def polymarketset_limits(
        self, ctx: commands.Context, per_order_pusd: str,
        per_user_day_pusd: str, installation_day_pusd: str,
    ):
        """Set bounded pUSD limits without enabling any capability."""
        try:
            limits = ProductionLimits(
                per_order_pusd, per_user_day_pusd, installation_day_pusd
            )
        except SafetyLimitError as exc:
            await ctx.send(f"Limits were not changed: {exc}.")
            return
        await self.config.production_limits.set(limits.to_record())
        await ctx.send(
            "Polymarket limits stored: "
            f"{limits.per_order_pusd} per order, "
            f"{limits.per_user_day_pusd} per user/day, "
            f"{limits.installation_day_pusd} installation/day pUSD. "
            "No production capability was enabled."
        )

    @polymarketset.command(name="productioncontrol")
    async def polymarketset_production_control(self, ctx: commands.Context, mode: str):
        """Pause the production boundary; enablement fails closed until reviewed."""
        choice = str(mode or "").strip().lower()
        if choice in {"pause", "disable"}:
            await self.config.production_enabled.set(False)
            await self.config.production_paused.set(True)
            await ctx.send("Polymarket production is disabled and emergency-paused.")
            return
        if choice != "enable":
            await ctx.send("Use `productioncontrol enable`, `pause`, or `disable`.")
            return
        if (
            validate_polymarket_production_manifest()
            or not POLYMARKET_PRODUCTION_MANIFEST.execution_enabled
            or not POLYMARKET_PRODUCTION_MANIFEST.executable_capabilities
        ):
            await ctx.send(
                "Polymarket production remains code-disabled. No state changed."
            )
            return
        await ctx.send("Polymarket production cannot be enabled by this release.")
