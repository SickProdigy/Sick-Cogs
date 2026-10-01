import asyncio
import inspect
import json

import discord
import secrets
import time
import uuid
from urllib.parse import quote
from pathlib import Path
from redbot.core import Config, commands
from redbot.core.bot import Red

from .constants import CONFIG_IDENTIFIER
from .mainnet_approval import (
    MainnetCanaryApproval,
    consume_mainnet_canary_approval,
    create_mainnet_canary_approval,
)
from .mainnet_factory import (
    MainnetFactoryReview,
    verify_mainnet_factory_evidence,
)
from .mainnet_lifecycle import (
    MainnetCanaryLifecycle,
    assert_same_mainnet_lifecycle,
    create_mainnet_canary_lifecycle,
    transition_mainnet_canary_lifecycle,
)
from .mainnet_review import MainnetTokenReview, build_mainnet_token_review
from .mainnet_verification import verify_mainnet_canary_evidence
from .models import TokenDraft
from .network_manifest import mainnet_readiness
from .terms import (
    TOKENFACTORY_MAINNET_TERMS_VERSION,
    TOKENFACTORY_DEPLOYMENT_ACKNOWLEDGEMENT,
    create_tokenfactory_terms_acceptance,
    is_current_tokenfactory_terms_acceptance,
)
from .policy import (
    MAINNET_LIMITS_DEFAULT,
    default_mainnet_limits,
    migrate_mainnet_limits,
    validate_mainnet_limits,
)
from .operations import (
    TOKEN_DEPLOY_GAS_LIMIT,
    TOKEN_FACTORY_DEPLOY_GAS_LIMIT,
    factory_deployment_status,
    factory_operation,
    token_operation,
    verify_external_transaction,
    verify_fixed_supply_token,
)
from .validation import normalize_owner_address
from .views import (
    FactoryDeploymentView,
    MainnetCanaryApprovalView,
    MainnetFactoryApprovalView,
    TokenFactoryDraftView,
    TokenFactoryTermsView,
)


class TokenFactory(commands.Cog):
    """Prepare protected, fixed-supply test-token deployment drafts."""

    __author__ = ["SickProdigy"]
    __version__ = "1.1.9"

    DISCORD_WATCH_INTERVAL = 4
    DISCORD_WATCH_SECONDS = 15 * 60
    TERMINAL_PROVIDER_STATES = {"complete", "dropped", "failed", "ambiguous"}

    def execution_terms(
        self, *, route: str, operation: str = "token", network: str = "base-sepolia"
    ) -> dict:
        if (
            route not in {"discord", "external"}
            or operation not in {"token", "factory"}
            or network not in {"base-sepolia", "base-mainnet"}
        ):
            raise ValueError("Unsupported TokenFactory execution-terms request.")
        if route == "external" and operation != "token":
            raise ValueError("External wallets cannot deploy TokenFactory infrastructure.")
        gas_limit = (
            TOKEN_FACTORY_DEPLOY_GAS_LIMIT
            if operation == "factory"
            else TOKEN_DEPLOY_GAS_LIMIT
        )
        sponsored = route == "discord" and network == "base-sepolia"
        return {
            "gas_limit": gas_limit,
            "native_value_wei": 0,
            "gas_sponsored": sponsored,
            "max_gas_fee_wei": (
                MAINNET_LIMITS_DEFAULT["max_gas_fee_wei"]
                if network == "base-mainnet" else None
            ),
            "gas_payer": (
                "CDP paymaster" if sponsored
                else "creator wallet" if network == "base-mainnet"
                else "connected external wallet"
            ),
        }

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(
            self, identifier=CONFIG_IDENTIFIER, force_registration=True
        )
        self.config.register_user(
            deployment_draft=None,
            pending_deployment=None,
            deployed_tokens=[],
            mainnet_terms_acceptance=None,
            mainnet_pending_review=None,
            mainnet_operation_approval=None,
            mainnet_operation_lifecycle=None,
            mainnet_operation_evidence=None,
        )
        self.deployment_tasks = set()
        self.discord_watchers = {}
        self.deployment_locks = {}
        self.mainnet_lifecycle_lock = asyncio.Lock()
        self.mainnet_factory_lock = asyncio.Lock()
        self.config.register_global(
            deployment_enabled=False,
            factory_address=None,
            factory_runtime_code_hash=None,
            factory_version=None,
            pending_factory_operation=None,
            emergency_paused=True,
            mainnet_deployment_enabled=False,
            mainnet_emergency_paused=True,
            mainnet_manifest_approved=False,
            # Compatibility-only pre-release key; never grants member access.
            mainnet_owner_canary_enabled=False,
            mainnet_limits=default_mainnet_limits(),
            mainnet_pending_review=None,
            mainnet_canary_approval=None,
            mainnet_canary_lifecycle=None,
            mainnet_canary_evidence=None,
            mainnet_factory_pending_review=None,
            mainnet_factory_approval=None,
            mainnet_factory_lifecycle=None,
            mainnet_factory_evidence=None,
        )

    async def accept_mainnet_terms(
        self, user_id: int, *, acceptance_id: str | None = None, now: int | None = None,
    ) -> dict:
        record = create_tokenfactory_terms_acceptance(
            user_id, acceptance_id=acceptance_id, now=now
        )
        await self.config.user_from_id(int(user_id)).mainnet_terms_acceptance.set(record)
        return record

    async def has_current_mainnet_terms(self, user_id: int) -> bool:
        record = await self.config.user_from_id(int(user_id)).mainnet_terms_acceptance()
        return is_current_tokenfactory_terms_acceptance(record, user_id)

    def cog_unload(self):
        for task in self.deployment_tasks:
            task.cancel()

    async def cog_load(self):
        stored_limits = await self.config.mainnet_limits()
        reviewed_limits = migrate_mainnet_limits(stored_limits)
        if reviewed_limits != stored_limits:
            await self.config.mainnet_limits.set(reviewed_limits)
        task = asyncio.create_task(self._restore_discord_watchers())
        self._track_task(task)

    async def stage_mainnet_canary_review(
        self, review: MainnetTokenReview
    ) -> MainnetCanaryApprovalView:
        """Persist one exact review while clearing every prior approval."""

        if review.network != "base-mainnet" or review.chain_id != 8453:
            raise ValueError("Only a Base mainnet deployment review may be staged.")
        scope = self.config.user_from_id(review.owner_discord_id)
        await scope.mainnet_pending_review.set(review.to_dict())
        await scope.mainnet_operation_approval.set(None)
        return MainnetCanaryApprovalView(
            self, review.owner_discord_id, review
        )

    async def stage_member_mainnet_review(
        self, user, draft: TokenDraft, execution_terms: dict
    ) -> tuple[MainnetTokenReview, MainnetCanaryApprovalView]:
        """Build and persist one ordinary-member mainnet review without submitting."""
        if draft.network != "base-mainnet" or draft.chain_id != 8453:
            raise ValueError("A Base mainnet token draft is required.")
        if not await self.deployment_available(draft.network):
            raise RuntimeError(
                "Base mainnet TokenFactory deployment is disabled or not verified."
            )
        stored = await self.config.user(user).deployment_draft()
        if not isinstance(stored, dict) or TokenDraft.from_dict(stored) != draft:
            raise RuntimeError(
                "The saved token draft changed; reopen the card and review it."
            )
        expected_terms = self.execution_terms(
            route="discord", network="base-mainnet"
        )
        if execution_terms != expected_terms:
            raise ValueError("The mainnet TokenFactory fee policy changed.")
        request_id = "0x" + secrets.token_hex(32)
        operation = token_operation(
            draft, request_id, draft.owner_address, network="base-mainnet"
        )
        estimator = getattr(self._cryptowallet(), "estimate_base_mainnet_call_fee", None)
        if not callable(estimator):
            raise RuntimeError("CryptoWallet mainnet fee estimation is unavailable.")
        quote = await estimator(
            from_address=draft.owner_address, to_address=operation["to"],
            value_wei=int(operation["value_wei"]), data=str(operation["data"]),
        )
        estimated_fee = int(quote.get("fee_wei") or 0)
        policy_ceiling = int(execution_terms["max_gas_fee_wei"])
        if estimated_fee <= 0 or estimated_fee > policy_ceiling:
            raise RuntimeError("The current network fee exceeds TokenFactory policy.")
        threshold = min(
            policy_ceiling, max(estimated_fee * 125 // 100, estimated_fee + 10**13)
        )
        review = build_mainnet_token_review(
            draft, request_id, draft.owner_address,
            max_gas_fee_wei=threshold,
            gas_payer=str(execution_terms["gas_payer"]),
            limits=validate_mainnet_limits(await self.config.mainnet_limits()),
            estimated_gas_fee_wei=estimated_fee,
        )
        return review, await self.stage_mainnet_canary_review(review)

    async def approve_mainnet_canary_review(
        self,
        owner_discord_id: int,
        review_fingerprint: str,
        *,
        acknowledgement: str,
    ) -> MainnetCanaryApproval:
        """Record a short-lived protected approval; never submit an operation."""

        if acknowledgement != TOKENFACTORY_DEPLOYMENT_ACKNOWLEDGEMENT:
            raise ValueError(
                "The creator-responsibility acknowledgement did not match. Nothing was approved."
            )
        scope = self.config.user_from_id(int(owner_discord_id))
        data = await scope.mainnet_pending_review()
        try:
            review = MainnetTokenReview.from_dict(data)
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError("The mainnet deployment review is missing or invalid.") from exc
        if (
            int(owner_discord_id) != review.owner_discord_id
            or not secrets.compare_digest(review_fingerprint, review.fingerprint)
        ):
            raise ValueError(
                "The mainnet deployment review changed or belongs to another owner."
            )
        if not await self.has_current_mainnet_terms(owner_discord_id):
            raise RuntimeError(
                "Current TokenFactory mainnet terms must be accepted before this approval."
            )
        current_data = await scope.mainnet_pending_review()
        try:
            current = MainnetTokenReview.from_dict(current_data)
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError(
                "The mainnet deployment review changed during verification."
            ) from exc
        if not secrets.compare_digest(current.fingerprint, review.fingerprint):
            raise RuntimeError(
                "The mainnet deployment review changed during verification."
            )
        approval = create_mainnet_canary_approval(
            current,
            owner_discord_id,
            discord_confirmed=True,
            now=int(time.time()),
        )
        await scope.mainnet_operation_approval.set(approval.to_dict())
        return approval

    async def claim_mainnet_canary_approval(
        self, owner_discord_id: int, review_fingerprint: str
    ) -> MainnetCanaryApproval:
        """Atomically consume one approval before any future provider call."""

        scope = self.config.user_from_id(int(owner_discord_id))
        try:
            review = MainnetTokenReview.from_dict(await scope.mainnet_pending_review())
            if not secrets.compare_digest(review.fingerprint, review_fingerprint):
                raise ValueError("review changed")
            draft = TokenDraft.from_dict(await scope.deployment_draft())
            operation = token_operation(
                draft, review.request_id, review.recipient, network="base-mainnet"
            )
            estimator = getattr(self._cryptowallet(), "estimate_base_mainnet_call_fee", None)
            if not callable(estimator):
                raise RuntimeError("CryptoWallet mainnet fee estimation is unavailable.")
            refreshed = await estimator(
                from_address=review.signer_address, to_address=operation["to"],
                value_wei=int(operation["value_wei"]), data=str(operation["data"]),
            )
            if int(refreshed.get("fee_wei") or 0) > review.max_gas_fee_wei:
                raise RuntimeError(
                    "The network fee rose above the approved threshold; create a new review."
                )
        except RuntimeError:
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError("The mainnet review cannot be revalidated.") from exc
        async with scope.mainnet_operation_approval() as data:
            try:
                approval = MainnetCanaryApproval.from_dict(data)
            except (KeyError, TypeError, ValueError) as exc:
                raise RuntimeError(
                    "No valid protected mainnet deployment approval is available."
                ) from exc
            claimed = consume_mainnet_canary_approval(
                approval, review_fingerprint, now=int(time.time())
            )
            data.clear()
            data.update(claimed.to_dict())
        return claimed

    async def stage_mainnet_canary_lifecycle(
        self,
        review: MainnetTokenReview,
        attempt_id: str,
        *,
        now: int | None = None,
    ) -> MainnetCanaryLifecycle:
        """Persist one immutable deployment attempt without replacing prior history."""

        async with self.mainnet_lifecycle_lock:
            scope = self.config.user_from_id(review.owner_discord_id)
            stored_data = await scope.mainnet_operation_lifecycle()
            if isinstance(stored_data, dict):
                stored = MainnetCanaryLifecycle.from_dict(stored_data)
                assert_same_mainnet_lifecycle(
                    stored, review.fingerprint, attempt_id
                )
                return stored
            lifecycle = create_mainnet_canary_lifecycle(
                review,
                attempt_id,
                now=int(time.time()) if now is None else int(now),
            )
            await scope.mainnet_operation_lifecycle.set(lifecycle.to_dict())
            await scope.mainnet_operation_evidence.set(None)
            return lifecycle

    async def transition_mainnet_canary(
        self,
        owner_discord_id: int,
        review_fingerprint: str,
        attempt_id: str,
        status: str,
        *,
        now: int | None = None,
        **evidence,
    ) -> MainnetCanaryLifecycle:
        """Atomically persist one reviewed mainnet lifecycle transition."""

        async with self.mainnet_lifecycle_lock:
            scope = self.config.user_from_id(int(owner_discord_id))
            data = await scope.mainnet_operation_lifecycle()
            try:
                current = MainnetCanaryLifecycle.from_dict(data)
                assert_same_mainnet_lifecycle(
                    current, review_fingerprint, attempt_id
                )
                updated = transition_mainnet_canary_lifecycle(
                    current,
                    status,
                    now=int(time.time()) if now is None else int(now),
                    **evidence,
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise RuntimeError(
                    "The mainnet deployment lifecycle transition was rejected."
                ) from exc
            await scope.mainnet_operation_lifecycle.set(updated.to_dict())
            return updated

    async def record_mainnet_canary_evidence(
        self,
        review: MainnetTokenReview,
        primary: dict,
        secondary: dict,
    ) -> dict:
        """Persist matching two-RPC verification evidence exactly once."""

        async with self.mainnet_lifecycle_lock:
            scope = self.config.user_from_id(review.owner_discord_id)
            data = await scope.mainnet_operation_lifecycle()
            try:
                lifecycle = MainnetCanaryLifecycle.from_dict(data)
                verified = verify_mainnet_canary_evidence(
                    review, lifecycle, primary, secondary
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise RuntimeError(
                    "The mainnet deployment verification evidence was rejected."
                ) from exc
            existing = await scope.mainnet_operation_evidence()
            if existing is not None and existing != verified:
                raise RuntimeError(
                    "Different mainnet deployment evidence is already recorded."
                )
            if existing is None:
                await scope.mainnet_operation_evidence.set(verified)
            return verified

    async def stage_mainnet_factory_review(
        self, review: MainnetFactoryReview
    ) -> MainnetFactoryApprovalView:
        """Persist one exact factory review while clearing prior approval."""

        if review.network != "base-mainnet" or review.chain_id != 8453:
            raise ValueError("Only a Base mainnet factory review may be staged.")
        await self.config.mainnet_factory_pending_review.set(review.to_dict())
        await self.config.mainnet_factory_approval.set(None)
        return MainnetFactoryApprovalView(
            self, review.owner_discord_id, review
        )

    async def approve_mainnet_factory_review(
        self,
        owner_discord_id: int,
        review_fingerprint: str,
        *,
        acknowledgement: str,
    ) -> MainnetCanaryApproval:
        """Record a protected factory approval without submitting anything."""

        if acknowledgement != "DEPLOY BASE MAINNET FACTORY":
            raise ValueError(
                "The factory permanent-loss acknowledgement did not match. "
                "Nothing was approved."
            )
        data = await self.config.mainnet_factory_pending_review()
        try:
            review = MainnetFactoryReview.from_dict(data)
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError("The mainnet factory review is missing or invalid.") from exc
        if (
            int(owner_discord_id) != review.owner_discord_id
            or not secrets.compare_digest(
                review_fingerprint, review.fingerprint
            )
        ):
            raise ValueError(
                "The mainnet factory review changed or belongs to another owner."
            )
        current_data = await self.config.mainnet_factory_pending_review()
        try:
            current = MainnetFactoryReview.from_dict(current_data)
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError(
                "The mainnet factory review changed during verification."
            ) from exc
        if not secrets.compare_digest(current.fingerprint, review.fingerprint):
            raise RuntimeError(
                "The mainnet factory review changed during verification."
            )
        approval = create_mainnet_canary_approval(
            current,
            owner_discord_id,
            discord_confirmed=True,
            now=int(time.time()),
        )
        await self.config.mainnet_factory_approval.set(approval.to_dict())
        return approval

    async def claim_mainnet_factory_approval(
        self, review_fingerprint: str
    ) -> MainnetCanaryApproval:
        """Atomically consume one factory approval before a future provider call."""

        async with self.config.mainnet_factory_approval() as data:
            try:
                approval = MainnetCanaryApproval.from_dict(data)
            except (KeyError, TypeError, ValueError) as exc:
                raise RuntimeError(
                    "No valid protected mainnet factory approval is available."
                ) from exc
            claimed = consume_mainnet_canary_approval(
                approval, review_fingerprint, now=int(time.time())
            )
            data.clear()
            data.update(claimed.to_dict())
        return claimed

    async def stage_mainnet_factory_lifecycle(
        self,
        review: MainnetFactoryReview,
        attempt_id: str,
        *,
        now: int | None = None,
    ) -> MainnetCanaryLifecycle:
        """Persist the one-time factory attempt separately from token canaries."""

        async with self.mainnet_factory_lock:
            stored_data = await self.config.mainnet_factory_lifecycle()
            if isinstance(stored_data, dict):
                stored = MainnetCanaryLifecycle.from_dict(stored_data)
                assert_same_mainnet_lifecycle(
                    stored, review.fingerprint, attempt_id
                )
                return stored
            lifecycle = create_mainnet_canary_lifecycle(
                review,
                attempt_id,
                now=int(time.time()) if now is None else int(now),
            )
            await self.config.mainnet_factory_lifecycle.set(
                lifecycle.to_dict()
            )
            await self.config.mainnet_factory_evidence.set(None)
            return lifecycle

    async def transition_mainnet_factory(
        self,
        review_fingerprint: str,
        attempt_id: str,
        status: str,
        *,
        now: int | None = None,
        **evidence,
    ) -> MainnetCanaryLifecycle:
        """Atomically persist one factory lifecycle transition."""

        async with self.mainnet_factory_lock:
            data = await self.config.mainnet_factory_lifecycle()
            try:
                current = MainnetCanaryLifecycle.from_dict(data)
                assert_same_mainnet_lifecycle(
                    current, review_fingerprint, attempt_id
                )
                updated = transition_mainnet_canary_lifecycle(
                    current,
                    status,
                    now=int(time.time()) if now is None else int(now),
                    **evidence,
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise RuntimeError(
                    "The mainnet factory lifecycle transition was rejected."
                ) from exc
            await self.config.mainnet_factory_lifecycle.set(updated.to_dict())
            return updated

    async def record_mainnet_factory_evidence(
        self,
        review: MainnetFactoryReview,
        primary: dict,
        secondary: dict,
    ) -> dict:
        """Persist matching two-RPC factory evidence exactly once."""

        async with self.mainnet_factory_lock:
            data = await self.config.mainnet_factory_lifecycle()
            try:
                lifecycle = MainnetCanaryLifecycle.from_dict(data)
                verified = verify_mainnet_factory_evidence(
                    review, lifecycle, primary, secondary
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise RuntimeError(
                    "The mainnet factory verification evidence was rejected."
                ) from exc
            existing = await self.config.mainnet_factory_evidence()
            if existing is not None and existing != verified:
                raise RuntimeError(
                    "Different mainnet factory evidence is already recorded."
                )
            if existing is None:
                await self.config.mainnet_factory_evidence.set(verified)
            return verified

    def _track_task(self, task: asyncio.Task) -> None:
        self.deployment_tasks.add(task)
        task.add_done_callback(self.deployment_tasks.discard)

    async def command_hint(self, command: str, *, ctx=None, guild=None) -> str:
        """Render a live command with a configured text prefix."""
        prefix = str(getattr(ctx, "clean_prefix", "") or "")
        if not prefix:
            prefixes = self.bot.get_valid_prefixes(guild)
            if inspect.isawaitable(prefixes):
                prefixes = await prefixes
            prefix = next(
                (str(item) for item in prefixes
                 if item and not str(item).lstrip().startswith("<@")),
                "!",
            )
        return f"{prefix}{command}"

    def _guild_for_id(self, guild_id):
        if not guild_id:
            return None
        getter = getattr(self.bot, "get_guild", None)
        return getter(int(guild_id)) if callable(getter) else None

    async def _user_for_id(self, user_id: int):
        user = self.bot.get_user(user_id)
        if user is not None:
            return user
        try:
            return await self.bot.fetch_user(user_id)
        except Exception:
            return None

    async def _restore_discord_watchers(self) -> None:
        await self.bot.wait_until_red_ready()
        for user_id, data in (await self.config.all_users()).items():
            pending = data.get("pending_deployment")
            if (
                isinstance(pending, dict)
                and pending.get("route") == "discord"
                and pending.get("request_id")
                and pending.get("watcher_notice_state") != "claimed"
            ):
                self._schedule_discord_watcher(int(user_id))

    def _schedule_discord_watcher(self, user_id: int) -> None:
        current = self.discord_watchers.get(user_id)
        if current is not None and not current.done():
            return
        task = asyncio.create_task(self._watch_discord_deployment(user_id))
        self.discord_watchers[user_id] = task
        self._track_task(task)

        def discard(completed):
            if self.discord_watchers.get(user_id) is completed:
                self.discord_watchers.pop(user_id, None)

        task.add_done_callback(discard)

    async def red_delete_data_for_user(self, *, requester, user_id: int):
        await self.config.user_from_id(user_id).clear()

    async def save_draft(self, user, draft: TokenDraft) -> None:
        await self.config.user(user).deployment_draft.set(draft.to_dict())

    async def resolve_discord_wallet_draft(self, user, draft: TokenDraft) -> TokenDraft:
        network = await self._target_network()
        context = await self._wallet_context_for_user(user, network)
        resolved = TokenDraft(
            creator_discord_id=draft.creator_discord_id,
            name=draft.name,
            symbol=draft.symbol,
            decimals=draft.decimals,
            supply_atomic=draft.supply_atomic,
            wallet_profile_id=context["profile_id"],
            owner_address=context["owner_address"],
            network=context["network"],
            chain_id=int(context["chain_id"]),
        )
        await self.save_draft(user, resolved)
        return resolved

    async def create_external_deployment_link(self, user, draft: TokenDraft) -> str:
        if not await self.deployment_available(draft.network):
            raise RuntimeError("Token deployment is disabled or emergency-paused for this network.")
        user_config = self.config.user(user)
        pending = await user_config.pending_deployment()
        if isinstance(pending, dict):
            external_handoff_expired = (
                pending.get("route") == "external"
                and pending.get("provider_status") == "awaiting_external_wallet"
                and int(pending.get("submitted_at", 0) or 0) + 3 * 60
                <= int(time.time())
            )
            same_external_draft = (
                not external_handoff_expired
                and pending.get("route") == "external"
                and pending.get("draft") == draft.to_dict()
                and pending.get("request_id")
            )
            if same_external_draft:
                request_id = str(pending["request_id"])
            elif (
                not external_handoff_expired
                and pending.get("provider_status") not in {
                    "complete", "dropped", "failed"
                }
            ):
                raise RuntimeError(
                    "Another token deployment is already active. Verify or finish it first."
                )
            else:
                request_id = "0x" + secrets.token_hex(32)
        else:
            request_id = "0x" + secrets.token_hex(32)
        wallet = self._cryptowallet()
        token, expires_at = await wallet.create_external_companion_handoff(
            user.id, "tokenfactory_external",
            {
                **draft.to_dict(),
                "execution_terms": self.execution_terms(route="external", network=draft.network),
                "request_id": request_id,
            },
        )
        handle = await wallet.register_recovery_handoff(token, expires_at)
        await user_config.pending_deployment.set({
            "route": "external",
            "draft": draft.to_dict(),
            "request_id": request_id,
            "provider_status": "awaiting_external_wallet",
            "submitted_at": int(time.time()),
        })
        task = asyncio.create_task(
            self._watch_external_deployment(user, handle, expires_at)
        )
        self._track_task(task)
        base = str(await wallet.config.approval_base_url()).rstrip("/")
        return f"{base}/tokenfactory.html#handoff={quote(handle, safe='')}"

    @staticmethod
    def _factory_artifact() -> dict:
        path = Path(__file__).parent / "contracts" / "artifact" / "SickGamingTokenFactory.json"
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    def _cryptowallet(self):
        wallet = self.bot.get_cog("CryptoWallet")
        if wallet is None:
            raise RuntimeError("CryptoWallet must be loaded for factory deployment.")
        return wallet

    async def _submit_reviewed_call(
        self, user, operation: dict, attempt_id: str, execution_terms: dict
    ) -> dict:
        """Use only the current narrow CryptoWallet signer boundary."""

        signer = getattr(
            self._cryptowallet(), "tokenfactory_submit_reviewed_call", None
        )
        if not callable(signer):
            raise RuntimeError(
                "CryptoWallet is out of date. Ask the bot owner to reload "
                "CryptoWallet, then reload TokenFactory."
            )
        return await signer(user, operation, attempt_id, execution_terms)

    async def deploy_pinned_factory(self, user, creation_code: str, execution_terms: dict) -> dict:
        try:
            pending = await self.config.pending_factory_operation()
            if isinstance(pending, dict) and pending.get("user_operation_hash"):
                status = await self._cryptowallet().tokenfactory_operation_status(
                    user, str(pending["user_operation_hash"])
                )
                pending.update(status)
                await self.config.pending_factory_operation.set(pending)
                if status["provider_status"] not in {"complete", "dropped", "failed"}:
                    raise RuntimeError(
                        "A factory deployment operation is already "
                        f"{status['provider_status']}: "
                        f"`{status['user_operation_hash']}`. Run "
                        f"`{await self.command_hint('tokenfactoryset verifyfactory')}` instead."
                    )
            attempt_id = str(uuid.uuid4())
            operation = factory_operation(creation_code)
            result = await self._submit_reviewed_call(
                user, operation, attempt_id, execution_terms
            )
            if not result.get("already_deployed"):
                await self.config.pending_factory_operation.set(
                    {
                        "attempt_id": attempt_id,
                        "discord_user_id": user.id,
                        "provider_status": result["provider_status"],
                        "user_operation_hash": result["user_operation_hash"],
                        "transaction_hash": result.get("transaction_hash"),
                    }
                )
            return result
        except Exception as exc:
            if isinstance(exc, RuntimeError):
                raise
            raise RuntimeError(f"Pinned factory deployment failed: {exc}") from exc

    async def deployment_available(self, network: str = "base-sepolia") -> bool:
        if network == "base-mainnet":
            status = mainnet_readiness()
            return bool(
                await self.config.mainnet_deployment_enabled()
                and not await self.config.mainnet_emergency_paused()
                and status["member_deployment_authorized"]
                and status["factory_address"]
            )
        if network != "base-sepolia":
            return False
        return bool(
            await self.config.deployment_enabled()
            and not await self.config.emergency_paused()
            and str(await self.config.factory_address() or "").lower()
            == "0xcba30318008035bb5a855a8684cea954d573c2c3"
            and str(await self.config.factory_runtime_code_hash() or "").lower()
            == "0xa4e867671846a61743568f19d897fb5ffe40ad9678c9c06e791ff45dad7136f7"
        )

    async def submit_token_deployment(
        self, user, draft: TokenDraft, execution_terms: dict, *, guild_id=None
    ) -> dict:
        if not await self.deployment_available(draft.network):
            raise RuntimeError("Token deployment is disabled or emergency-paused.")
        stored = await self.config.user(user).deployment_draft()
        if not isinstance(stored, dict) or TokenDraft.from_dict(stored) != draft:
            raise RuntimeError("The saved token draft changed; reopen the card and review it.")
        wallet = self._cryptowallet()
        context = await wallet.tokenfactory_wallet_context(user, draft.network)
        if (
            str(context.get("profile_id")) != draft.wallet_profile_id
            or normalize_owner_address(str(context.get("owner_address"))).lower()
            != draft.owner_address.lower()
        ):
            raise RuntimeError("The wallet profile no longer matches this token draft.")
        user_config = self.config.user(user)
        pending = await user_config.pending_deployment()
        if (
            isinstance(pending, dict)
            and pending.get("route") == "external"
            and pending.get("provider_status") not in {"complete", "dropped", "failed"}
        ):
            raise RuntimeError(
                "An external-wallet token deployment is already active. "
                "Verify or finish it before using Discord Wallet."
            )
        if (
            isinstance(pending, dict)
            and pending.get("request_id")
            and pending.get("route") != "external"
        ):
            same_draft = pending.get("draft") == draft.to_dict()
            if same_draft:
                verified = await verify_fixed_supply_token(
                    draft, str(pending["request_id"]), draft.owner_address
                )
                if verified.get("deployed"):
                    self._schedule_discord_watcher(user.id)
                    return {**verified, "already_deployed": True}
                status = await wallet.tokenfactory_operation_status(
                    user, str(pending["user_operation_hash"]), draft.network
                )
                pending.update(status)
                await user_config.pending_deployment.set(pending)
                if status["provider_status"] not in {"complete", "dropped", "failed"}:
                    deployment = await self.command_hint("tokenfactory deployment")
                    raise RuntimeError(
                        f"Your existing token deployment is {status['provider_status']}. "
                        f"Run `{deployment}` to refresh it."
                    )
            elif pending.get("provider_status") not in {"complete", "dropped", "failed"}:
                raise RuntimeError(
                    "Another token draft already has an active deployment operation."
                )
        if isinstance(pending, dict) and pending.get("draft") == draft.to_dict():
            request_id = str(pending["request_id"])
        else:
            request_id = "0x" + secrets.token_hex(32)
        attempt_id = str(uuid.uuid4())
        operation = token_operation(
            draft, request_id, draft.owner_address, network=draft.network
        )
        result = await self._submit_reviewed_call(
            user, operation, attempt_id, execution_terms
        )
        await user_config.pending_deployment.set({
            "route": "discord",
            "draft": draft.to_dict(),
            "request_id": request_id,
            "attempt_id": attempt_id,
            "provider_status": result["provider_status"],
            "user_operation_hash": result["user_operation_hash"],
            "transaction_hash": result.get("transaction_hash"),
            "submitted_at": int(time.time()),
            "guild_id": guild_id,
        })
        self._schedule_discord_watcher(user.id)
        return result

    async def _claim_watcher_notice(self, user_config, request_id: str, kind: str):
        pending = await user_config.pending_deployment()
        if (
            not isinstance(pending, dict)
            or str(pending.get("request_id")) != request_id
            or pending.get("watcher_notice_state") == "claimed"
        ):
            return None
        pending["watcher_notice_state"] = "claimed"
        pending["watcher_notice_kind"] = kind
        await user_config.pending_deployment.set(pending)
        return pending

    async def _watch_discord_deployment(self, user_id: int) -> None:
        user = await self._user_for_id(user_id)
        if user is None:
            return
        user_config = self.config.user_from_id(user_id)
        pending = await user_config.pending_deployment()
        if not isinstance(pending, dict) or pending.get("route") != "discord":
            return
        request_id = str(pending.get("request_id") or "")
        if not request_id:
            return
        submitted_at = int(pending.get("submitted_at", 0) or time.time())
        deadline = submitted_at + self.DISCORD_WATCH_SECONDS
        last_error = None
        while int(time.time()) <= deadline:
            await asyncio.sleep(self.DISCORD_WATCH_INTERVAL)
            lock = self.deployment_locks.setdefault(user_id, asyncio.Lock())
            async with lock:
                current = await user_config.pending_deployment()
                if (
                    not isinstance(current, dict)
                    or str(current.get("request_id")) != request_id
                    or current.get("watcher_notice_state") == "claimed"
                ):
                    return
                try:
                    result = await self.verify_token_deployment(
                        user, preserve_pending=True
                    )
                    last_error = None
                except Exception as exc:
                    last_error = exc
                    continue
                if result.get("deployed"):
                    claimed = await self._claim_watcher_notice(
                        user_config, request_id, "success"
                    )
                    if claimed is None:
                        return
                    try:
                        await user.send(
                            embed=await self._deployment_success_embed(result, claimed)
                        )
                    except (discord.Forbidden, discord.HTTPException):
                        pass
                    await user_config.pending_deployment.set(None)
                    await user_config.deployment_draft.set(None)
                    return
                status = str(result.get("provider_status") or "pending").lower()
                if status in self.TERMINAL_PROVIDER_STATES:
                    await self._send_watcher_fallback(
                        user, user_config, request_id, current, status
                    )
                    return
        reason = "verification-failed" if last_error is not None else "timed-out"
        async with self.deployment_locks.setdefault(user_id, asyncio.Lock()):
            current = await user_config.pending_deployment()
            await self._send_watcher_fallback(
                user, user_config, request_id, current, reason
            )

    async def _send_watcher_fallback(
        self, user, user_config, request_id: str, pending, reason: str
    ) -> None:
        if not isinstance(pending, dict):
            return
        claimed = await self._claim_watcher_notice(
            user_config, request_id, "fallback"
        )
        if claimed is None:
            return
        guild = self._guild_for_id(claimed.get("guild_id"))
        command = await self.command_hint("tokenfactory deployment", guild=guild)
        try:
            await user.send(
                "Automatic token confirmation could not finish "
                f"(**{reason}**). Your pending deployment is still recoverable; "
                f"run `{command}` to check it safely."
            )
        except (discord.Forbidden, discord.HTTPException):
            pass

    async def _deployment_success_embed(self, result: dict, pending: dict):
        guild = self._guild_for_id(pending.get("guild_id"))
        history = await self.command_hint("tokenfactory tokens", guild=guild)
        scale = 10 ** int(result["decimals"])
        whole, remainder = divmod(int(result["supply_atomic"]), scale)
        supply = str(whole)
        if remainder:
            supply += f".{remainder:0{result['decimals']}d}".rstrip("0")
        contract = str(result["contract_address"])
        operation = str(result.get("user_operation_hash") or "")
        transaction = str(result.get("transaction_hash") or "")
        embed = discord.Embed(
            title="Token deployment confirmed",
            description=(
                "Your token was independently verified and added to TokenFactory "
                "history and CryptoWallet's community registry."
            ),
            color=discord.Color.green(),
        )
        embed.add_field(
            name="Token", value=f"{result['name']} ({result['symbol']})", inline=False
        )
        embed.add_field(name="Network", value="Base Sepolia (`84532`)", inline=True)
        embed.add_field(name="Fixed supply", value=supply, inline=True)
        embed.add_field(name="Decimals", value=str(result["decimals"]), inline=True)
        embed.add_field(
            name="Recipient / owner", value=f"`{result['owner_address']}`", inline=False
        )
        embed.add_field(
            name="Token contract",
            value=f"[`{contract}`](https://sepolia.basescan.org/address/{contract})",
            inline=False,
        )
        if transaction:
            embed.add_field(
                name="Deployment transaction",
                value=f"[`{transaction}`](https://sepolia.basescan.org/tx/{transaction})",
                inline=False,
            )
        elif operation:
            embed.add_field(name="User operation", value=f"`{operation}`", inline=False)
        embed.add_field(
            name="History", value=f"Run `{history}` to view verified deployments.",
            inline=False,
        )
        embed.set_footer(text="Base Sepolia testnet · automatic confirmation")
        return embed

    async def _watch_external_deployment(
        self, user, handle: str, expires_at: int
    ) -> None:
        wallet = self._cryptowallet()
        while int(time.time()) <= expires_at + 30:
            await asyncio.sleep(4)
            try:
                reported = await wallet.poll_tokenfactory_result(handle)
            except Exception:
                continue
            if reported is None:
                continue
            try:
                result = await self.verify_external_deployment(
                    user, reported["transaction_hash"], reported["recipient"]
                )
                if not result.get("deployed"):
                    await asyncio.sleep(4)
                    continue
                await user.send(
                    f"Verified **{result['name']} ({result['symbol']})** at "
                    f"`{result['contract_address']}` on Base Sepolia. It was added "
                    "to the community token registry."
                )
            except Exception as exc:
                deployment = await self.command_hint("tokenfactory deployment")
                await user.send(
                    f"Token deployment verification failed: {exc} "
                    f"Run `{deployment}` to recover it."
                )
            return

    async def verify_external_deployment(
        self, user, transaction_hash: str, recipient: str
    ) -> dict:
        if not await self.deployment_available():
            raise RuntimeError("Token deployment is disabled or emergency-paused.")
        user_config = self.config.user(user)
        pending = await user_config.pending_deployment()
        if not isinstance(pending, dict) or pending.get("route") != "external":
            raise RuntimeError("You have no external-wallet deployment awaiting verification.")
        draft = TokenDraft.from_dict(pending["draft"])
        if draft.creator_discord_id != user.id:
            raise RuntimeError("The pending deployment belongs to another member.")
        recipient = normalize_owner_address(recipient)
        result = await verify_external_transaction(
            draft, str(pending["request_id"]), recipient, transaction_hash
        )
        pending.update({
            "transaction_hash": transaction_hash.lower(),
            "recipient": recipient,
            "provider_status": result.get("provider_status", "pending"),
        })
        await user_config.pending_deployment.set(pending)
        if not result.get("deployed"):
            return result
        resolved = TokenDraft(
            creator_discord_id=draft.creator_discord_id,
            name=draft.name, symbol=draft.symbol, decimals=draft.decimals,
            supply_atomic=draft.supply_atomic, owner_address=recipient,
        )
        record = {
            **resolved.to_dict(),
            "request_id": str(pending["request_id"]),
            "contract_address": result["token_address"],
            "parameters_hash": result["parameters_hash"],
            "transaction_hash": transaction_hash.lower(),
            "signer_address": result["signer_address"],
            "deployment_route": "external",
            "deployed_at": int(time.time()),
        }
        async with user_config.deployed_tokens() as deployments:
            if not any(item.get("request_id") == record["request_id"] for item in deployments):
                deployments.append(record)
        await self._cryptowallet().tokenfactory_register_verified_token(user, {
            "contract_address": record["contract_address"],
            "symbol": draft.symbol, "name": draft.name, "decimals": draft.decimals,
        })
        await user_config.pending_deployment.set(None)
        await user_config.deployment_draft.set(None)
        return {"deployed": True, **record}

    async def verify_token_deployment(
        self, user, *, preserve_pending: bool = False
    ) -> dict:
        user_config = self.config.user(user)
        pending = await user_config.pending_deployment()
        if not isinstance(pending, dict) or not pending.get("request_id"):
            raise RuntimeError("You have no pending token deployment.")
        draft = TokenDraft.from_dict(pending["draft"])
        if draft.creator_discord_id != user.id:
            raise RuntimeError("The pending deployment belongs to another member.")
        wallet = self._cryptowallet()
        verified = await verify_fixed_supply_token(
            draft, str(pending["request_id"]), draft.owner_address
        )
        if not verified.get("deployed"):
            status = await wallet.tokenfactory_operation_status(
                user, str(pending["user_operation_hash"]), draft.network
            )
            pending.update(status)
            await user_config.pending_deployment.set(pending)
            return {"deployed": False, **status}
        record = {
            **draft.to_dict(),
            "request_id": str(pending["request_id"]),
            "contract_address": verified["token_address"],
            "parameters_hash": verified["parameters_hash"],
            "transaction_hash": pending.get("transaction_hash"),
            "user_operation_hash": pending.get("user_operation_hash"),
            "deployment_route": "discord",
            "deployed_at": int(time.time()),
        }
        async with user_config.deployed_tokens() as deployments:
            if not any(item.get("request_id") == record["request_id"] for item in deployments):
                deployments.append(record)
        await wallet.tokenfactory_register_verified_token(user, {
            "contract_address": record["contract_address"],
            "symbol": draft.symbol,
            "name": draft.name,
            "decimals": draft.decimals,
        })
        if preserve_pending:
            pending.update({
                "provider_status": "complete",
                "transaction_hash": record.get("transaction_hash"),
                "verified_at": record["deployed_at"],
            })
            await user_config.pending_deployment.set(pending)
        else:
            await user_config.pending_deployment.set(None)
            await user_config.deployment_draft.set(None)
        return {"deployed": True, **record}

    async def _target_network(self) -> str:
        wallet = self.bot.get_cog("CryptoWallet")
        selector = getattr(wallet, "tokenfactory_default_network", None)
        if not callable(selector):
            return "base-sepolia"
        network = str(await selector())
        if network not in {"base-sepolia", "base-mainnet"}:
            raise RuntimeError("CryptoWallet returned an unsupported TokenFactory network.")
        return network

    async def _wallet_context_for_user(self, user, network: str | None = None) -> dict:
        wallet = self.bot.get_cog("CryptoWallet")
        integration = getattr(wallet, "tokenfactory_wallet_context", None)
        if integration is None:
            raise RuntimeError("CryptoWallet must be loaded for Discord Wallet deployment.")
        context = await integration(user, network or await self._target_network())
        return {
            **context,
            "owner_address": normalize_owner_address(context["owner_address"]),
        }

    @commands.guild_only()
    @commands.group(
        name="tokenfactory", aliases=("tfactory",), invoke_without_command=True
    )
    async def tokenfactory(self, ctx: commands.Context):
        """Create fixed-supply Base Sepolia test-token drafts."""
        await ctx.send_help()

    @tokenfactory.command(name="terms")
    async def tokenfactory_terms(self, ctx: commands.Context):
        """Show and accept TokenFactory-only mainnet terms in Discord."""
        current = await self.has_current_mainnet_terms(ctx.author.id)
        terms_url = None
        wallet = self.bot.get_cog("CryptoWallet")
        if wallet is not None:
            base_url = str(await wallet.config.approval_base_url() or "").rstrip("/")
            if base_url:
                terms_url = f"{base_url}/tokenfactory-terms.html"
        embed = discord.Embed(
            title="TokenFactory Mainnet Terms",
            description=(
                f"Acceptance: **{'Current' if current else 'Not accepted'}**\n"
                "Required once per material terms version before Base mainnet token creation. "
                "Base Sepolia is unchanged."
            ),
            color=discord.Color.green() if current else discord.Color.blurple(),
        )
        embed.add_field(name="Terms version", value=f"`{TOKENFACTORY_MAINNET_TERMS_VERSION}`")
        embed.add_field(
            name="In short",
            value=(
                "You choose and create the token, pay network gas, and are responsible for "
                "its legality and use. Deployment is irreversible. SickGaming charges no "
                "TokenFactory service fee and does not sponsor, market, or provide liquidity."
            ),
            inline=False,
        )
        embed.set_footer(text="Acceptance does not deploy a token or authorize a transaction.")
        await ctx.send(
            embed=embed,
            view=TokenFactoryTermsView(
                self, ctx.author.id, terms_url, current=current
            ),
        )

    @tokenfactory.command(name="create", aliases=("card",))
    async def tokenfactory_create(self, ctx: commands.Context):
        """Open the interactive fixed-supply token form."""
        network = await self._target_network()
        stored = await self.config.user(ctx.author).deployment_draft()
        draft = None
        if isinstance(stored, dict):
            try:
                candidate = TokenDraft.from_dict(stored)
                if (candidate.creator_discord_id == ctx.author.id
                        and candidate.network == network):
                    draft = candidate
            except (KeyError, TypeError, ValueError):
                pass
        view = TokenFactoryDraftView(
            self, ctx.author, draft, network=network,
            deployment_available=await self.deployment_available(network),
        )
        await ctx.send(embed=view.embed(), view=view)

    @tokenfactory.command(name="deployment", aliases=("deploy-status", "verify"))
    async def tokenfactory_deployment(
        self, ctx: commands.Context, transaction_hash: str | None = None,
        recipient: str | None = None,
    ):
        """Verify a Discord-wallet deployment or an external transaction."""

        try:
            pending = await self.config.user(ctx.author).pending_deployment()
            if isinstance(pending, dict) and pending.get("route") == "external":
                if not transaction_hash or not recipient:
                    command = await self.command_hint(
                        "tokenfactory deployment <transaction_hash> <recipient_address>",
                        ctx=ctx,
                    )
                    await ctx.send(
                        f"After the external wallet submits, use `{command}`. "
                        "The protected page provides the exact command."
                    )
                    return
                result = await self.verify_external_deployment(
                    ctx.author, transaction_hash, recipient
                )
            else:
                lock = self.deployment_locks.setdefault(ctx.author.id, asyncio.Lock())
                async with lock:
                    result = await self.verify_token_deployment(ctx.author)
        except Exception as exc:
            await ctx.send(f"Token deployment verification failed: {exc}")
            return
        if result.get("deployed"):
            history = await self.command_hint("tokenfactory tokens", ctx=ctx)
            await ctx.send(
                f"Verified **{result['name']} ({result['symbol']})** at "
                f"`{result['contract_address']}` on Base Sepolia. It was added to the "
                f"community token registry. Run `{history}` to view all of "
                "your verified deployments."
            )
            return
        status = result.get("provider_status", "pending")
        transaction = result.get("transaction_hash")
        message = f"Your token deployment operation is **{status}**."
        if transaction:
            message += f" Transaction: `{transaction}`"
        if status in {"complete", "dropped", "failed"}:
            create = await self.command_hint("tokenfactory create", ctx=ctx)
            message += f" No matching token is on-chain; reopen `{create}` to retry."
        else:
            message += " No matching token is confirmed yet; check again shortly."
        await ctx.send(message)

    @tokenfactory.command(
        name="tokens", aliases=("history", "deployments", "list")
    )
    async def tokenfactory_tokens(self, ctx: commands.Context):
        """List your verified TokenFactory token deployments."""

        deployments = await self.config.user(ctx.author).deployed_tokens()
        if not deployments:
            create = await self.command_hint("tokenfactory create", ctx=ctx)
            await ctx.send(
                f"You have no verified TokenFactory tokens yet. Start with `{create}`."
            )
            return
        valid = [item for item in deployments if isinstance(item, dict)]
        newest = list(reversed(valid[-10:]))
        embed = discord.Embed(
            title="Your TokenFactory tokens",
            description=(
                f"{len(valid)} verified fixed-supply deployment"
                f"{'s' if len(valid) != 1 else ''} on Base Sepolia."
            ),
            color=await ctx.embed_color(),
        )
        for item in newest:
            name = str(item.get("name") or "Unnamed token")
            symbol = str(item.get("symbol") or "TOKEN")
            contract = str(item.get("contract_address") or "Unavailable")
            deployed_at = int(item.get("deployed_at", 0) or 0)
            when = f" · <t:{deployed_at}:R>" if deployed_at > 0 else ""
            embed.add_field(
                name=f"{name} ({symbol})",
                value=f"`{contract}`{when}",
                inline=False,
            )
        if len(valid) > len(newest):
            embed.set_footer(text=f"Showing the latest {len(newest)} deployments")
        else:
            embed.set_footer(text="Testnet tokens only")
        await ctx.send(embed=embed)

    @tokenfactory.command(name="status")
    async def tokenfactory_status(self, ctx: commands.Context):
        """Show the factory safety and deployment state."""
        enabled = await self.config.deployment_enabled()
        paused = await self.config.emergency_paused()
        address = await self.config.factory_address()
        code_hash = await self.config.factory_runtime_code_hash()
        embed = discord.Embed(title="TokenFactory status", color=discord.Color.blue())
        embed.add_field(name="Network", value="Base Sepolia (`84532`)", inline=True)
        embed.add_field(name="Deployment enabled", value=str(bool(enabled)), inline=True)
        embed.add_field(name="Emergency paused", value=str(bool(paused)), inline=True)
        embed.add_field(
            name="Factory",
            value=f"`{address}`" if address else "Not configured",
            inline=False,
        )
        embed.add_field(
            name="Pinned code hash",
            value=f"`{code_hash}`" if code_hash else "Not configured",
            inline=False,
        )
        if enabled and not paused and address and code_hash:
            footer = "Protected Base Sepolia member deployment is enabled"
        elif paused:
            footer = "Draft creation is available; deployment is emergency-paused"
        else:
            footer = "Draft creation is available; deployment is disabled"
        embed.set_footer(text=footer)
        await ctx.send(embed=embed)

    @commands.group(name="tokenfactoryset", invoke_without_command=True)
    @commands.is_owner()
    async def tokenfactoryset(self, ctx: commands.Context):
        """Manage reviewed TokenFactory infrastructure."""

        await ctx.send_help()

    @tokenfactoryset.command(name="mainnetstatus")
    async def tokenfactoryset_mainnet_status(self, ctx: commands.Context):
        """Show the non-executable Base mainnet readiness gates."""

        try:
            status = mainnet_readiness()
        except Exception as exc:
            await ctx.send(f"Base mainnet manifest validation failed: {exc}")
            return
        embed = discord.Embed(
            title="TokenFactory Base mainnet readiness",
            description=(
                "Read-only staging state. No Base mainnet factory or token "
                "deployment path is enabled."
            ),
            color=discord.Color.orange(),
        )
        embed.add_field(name="Network", value="Base mainnet (`8453`)", inline=True)
        embed.add_field(name="Manifest", value=f"`{status['status']}`", inline=True)
        embed.add_field(
            name="Predicted factory",
            value=f"`{status['predicted_factory_address']}`",
            inline=False,
        )
        embed.add_field(
            name="Read-only checks",
            value=(
                f"Singleton matched: **{status['singleton_verified']}**\n"
                f"Destination empty: **{status['destination_empty']}**"
            ),
            inline=False,
        )
        limits = validate_mainnet_limits(await self.config.mainnet_limits())
        enabled = bool(await self.config.mainnet_deployment_enabled())
        paused = bool(await self.config.mainnet_emergency_paused())
        embed.add_field(
            name="Control state",
            value=(
                f"Enabled: **{enabled}**\nPaused: **{paused}**"
            ),
            inline=False,
        )
        def lifecycle_text(lifecycle_data, evidence_data):
            if isinstance(lifecycle_data, dict):
                try:
                    lifecycle = MainnetCanaryLifecycle.from_dict(lifecycle_data)
                    lifecycle_status = lifecycle.status
                    attempt = lifecycle.attempt_id
                except (KeyError, TypeError, ValueError):
                    lifecycle_status = "invalid (fail-closed)"
                    attempt = "unavailable"
            else:
                lifecycle_status = "not started"
                attempt = "none"
            return (
                f"Status: **{lifecycle_status}**\n"
                f"Attempt: `{attempt}`\n"
                f"Independent evidence recorded: **{isinstance(evidence_data, dict)}**"
            )

        embed.add_field(
            name="Factory lifecycle",
            value=lifecycle_text(
                await self.config.mainnet_factory_lifecycle(),
                await self.config.mainnet_factory_evidence(),
            ),
            inline=False,
        )
        embed.add_field(
            name="Token deployment lifecycle",
            value=lifecycle_text(
                await self.config.mainnet_canary_lifecycle(),
                await self.config.mainnet_canary_evidence(),
            ),
            inline=False,
        )
        embed.add_field(
            name="Mainnet ceilings",
            value=(
                f"Factory deployments/day: **{limits['factory_deployments_per_day']}**\n"
                f"Token deployments/day: **{limits['token_deployments_per_day']}**\n"
                f"Token gas: **{limits['token_gas_limit']:,}** | "
                f"Max fee: **{limits['max_gas_fee_wei'] / 10**18:.3f} ETH** | "
                "Native value: **0 ETH**"
            ),
            inline=False,
        )
        embed.add_field(
            name="Fee approval model",
            value=(
                "Live estimate and reapproval threshold are staged. The legacy "
                "provider-level fee gate still blocks submission."
            ),
            inline=False,
        )
        embed.add_field(
            name="Required gates",
            value=(
                f"Independent technical review: **{status['independent_audit']}**\n"
                "Pre-deployment RPC cross-check: **complete**\n"
                "Free-release scope attestation and develop testing approval: **required**\n"
                "Public release acknowledgments: **tracked in issue #229**\n"
                "Monetization or expanded financial services: **outside counsel first (#228)**"
            ),
            inline=False,
        )
        embed.set_footer(text="Mainnet deployment remains unavailable and fail-closed")
        await ctx.send(embed=embed)

    @tokenfactoryset.command(name="mainnetcontrol")
    async def tokenfactoryset_mainnet_control(self, ctx: commands.Context, mode: str):
        """Pause or request installation-wide Base mainnet enablement."""

        choice = str(mode or "").strip().lower()
        if choice not in {"enable", "pause", "disable"}:
            await ctx.send("Choose `enable`, `pause`, or `disable`.")
            return
        if choice == "enable":
            status = mainnet_readiness()
            await ctx.send(
                "Base mainnet TokenFactory enablement was rejected with no state "
                f"change. Manifest: `{status['status']}`; independent technical review: "
                f"**{status['independent_audit']}**. Complete every issue #203 gate "
                "and update the reviewed manifest before requesting develop testing."
            )
            return
        await self.config.mainnet_deployment_enabled.set(False)
        await self.config.mainnet_emergency_paused.set(True)
        await self.config.mainnet_owner_canary_enabled.set(False)
        await self.config.mainnet_pending_review.set(None)
        await self.config.mainnet_canary_approval.set(None)
        await self.config.mainnet_factory_pending_review.set(None)
        await self.config.mainnet_factory_approval.set(None)
        action = "disabled" if choice == "disable" else "emergency-paused"
        await ctx.send(
            f"Base mainnet TokenFactory is **{action}**. No factory or token "
            "deployment can be submitted."
        )

    @tokenfactoryset.command(name="deployment")
    async def tokenfactoryset_deployment(self, ctx: commands.Context, mode: str):
        """Enable, disable, or emergency-pause member token deployments."""

        choice = mode.strip().lower()
        if choice not in {"enable", "enabled", "disable", "disabled", "pause", "paused"}:
            await ctx.send("Choose `enable`, `disable`, or `pause`.")
            return
        if choice in {"enable", "enabled"}:
            try:
                state = await factory_deployment_status()
            except Exception as exc:
                await ctx.send(f"Token deployment enablement failed: {exc}")
                return
            if not state.get("deployed"):
                await ctx.send("The pinned factory is not verified on Base Sepolia.")
                return
            await self.config.factory_address.set(state["address"])
            await self.config.factory_runtime_code_hash.set(
                "0xa4e867671846a61743568f19d897fb5ffe40ad9678c9c06e791ff45dad7136f7"
            )
            await self.config.deployment_enabled.set(True)
            await self.config.emergency_paused.set(False)
            await ctx.send(
                "Base Sepolia member token deployments are **enabled**. The fixed-supply "
                "factory, wallet authorization, and explicit confirmation remain required."
            )
            return
        await self.config.emergency_paused.set(True)
        if choice in {"disable", "disabled"}:
            await self.config.deployment_enabled.set(False)
            await ctx.send("Token deployments are **disabled** and emergency-paused.")
        else:
            await ctx.send("Token deployments are **emergency-paused**.")

    @tokenfactoryset.command(name="deployfactory")
    async def tokenfactoryset_deploy_factory(self, ctx: commands.Context):
        """Preview the one-time pinned Base Sepolia factory deployment."""

        try:
            state = await factory_deployment_status()
            artifact = self._factory_artifact()
        except Exception as exc:
            await ctx.send(f"Factory deployment preflight failed: {exc}")
            return
        if state.get("deployed"):
            await ctx.send(
                f"The pinned factory is already deployed at `{state['address']}`."
            )
            return
        embed = discord.Embed(
            title="Deploy pinned TokenFactory infrastructure",
            description=(
                "This submits one sponsored Base Sepolia operation through the canonical "
                "EIP-2470 singleton. It can deploy only the bundled, hash-pinned factory artifact."
            ),
            color=discord.Color.orange(),
        )
        embed.add_field(name="Network", value="Base Sepolia (`84532`)", inline=True)
        terms = self.execution_terms(route="discord", operation="factory")
        embed.add_field(name="Gas limit", value=f"`{terms['gas_limit']:,}`", inline=True)
        embed.add_field(name="Native value", value="`0.00000000 ETH`", inline=True)
        embed.add_field(
            name="Network gas",
            value="Sponsorship active · paid by CDP paymaster",
            inline=False,
        )
        embed.add_field(name="Destination", value=f"`{state['address']}`", inline=False)
        embed.add_field(
            name="Runtime code hash",
            value="`0xa4e867671846a61743568f19d897fb5ffe40ad9678c9c06e791ff45dad7136f7`",
            inline=False,
        )
        embed.set_footer(text="Owner-only · explicit confirmation · no mainnet path")
        view = FactoryDeploymentView(self, ctx.author, str(artifact["bytecode"]), terms)
        await ctx.send(embed=embed, view=view)

    @tokenfactoryset.command(name="verifyfactory")
    async def tokenfactoryset_verify_factory(self, ctx: commands.Context):
        """Verify and record the pinned factory after on-chain confirmation."""

        try:
            state = await factory_deployment_status()
        except Exception as exc:
            await ctx.send(f"Factory verification failed: {exc}")
            return
        if not state.get("deployed"):
            pending = await self.config.pending_factory_operation()
            if not isinstance(pending, dict) or not pending.get("user_operation_hash"):
                deploy = await self.command_hint("tokenfactoryset deployfactory", ctx=ctx)
                await ctx.send(
                    "The pinned factory is not confirmed and no tracked deployment "
                    f"operation exists. Run `{deploy}` to start one."
                )
                return
            if pending.get("discord_user_id") != ctx.author.id:
                await ctx.send(
                    "The pinned factory is not confirmed. Its tracked operation belongs "
                    "to another bot owner, so its CDP status was not queried."
                )
                return
            try:
                operation = await self._cryptowallet().tokenfactory_operation_status(
                    ctx.author, str(pending["user_operation_hash"])
                )
            except Exception as exc:
                await ctx.send(f"Factory operation status lookup failed: {exc}")
                return
            pending.update(operation)
            await self.config.pending_factory_operation.set(pending)
            status = operation["provider_status"]
            transaction = operation.get("transaction_hash")
            message = (
                f"The pinned factory is not on-chain. CDP reports the tracked "
                f"operation as **{status}**."
            )
            if transaction:
                message += f" Transaction: `{transaction}`"
            if status in {"complete", "dropped", "failed"}:
                message += (
                    " The operation did not install the pinned code; a fresh deployment "
                    "attempt is now allowed."
                )
            else:
                message += " Do not submit another deployment yet."
            await ctx.send(message)
            return
        await self.config.factory_address.set(state["address"])
        await self.config.factory_runtime_code_hash.set(
            "0xa4e867671846a61743568f19d897fb5ffe40ad9678c9c06e791ff45dad7136f7"
        )
        await self.config.factory_version.set("v1")
        await self.config.pending_factory_operation.set(None)
        await ctx.send(
            f"Verified and recorded the pinned factory at `{state['address']}`. "
            "Token deployment remains emergency-paused until its separate flow is complete."
        )
