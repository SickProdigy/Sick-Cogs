import discord
import json
import secrets
import time
import uuid
from urllib.parse import quote
from pathlib import Path
from redbot.core import Config, commands
from redbot.core.bot import Red

from .constants import CONFIG_IDENTIFIER
from .models import TokenDraft
from .validation import normalize_owner_address
from .views import FactoryDeploymentView, TokenFactoryDraftView


class TokenFactory(commands.Cog):
    """Prepare protected, fixed-supply test-token deployment drafts."""

    __author__ = ["SickProdigy"]
    __version__ = "0.3.0"

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(
            self, identifier=CONFIG_IDENTIFIER, force_registration=True
        )
        self.config.register_user(
            deployment_draft=None,
            pending_deployment=None,
            deployed_tokens=[],
        )
        self.config.register_global(
            deployment_enabled=False,
            factory_address=None,
            factory_runtime_code_hash=None,
            factory_version=None,
            pending_factory_operation=None,
            emergency_paused=True,
        )

    async def red_delete_data_for_user(self, *, requester, user_id: int):
        await self.config.user_from_id(user_id).clear()

    async def save_draft(self, user, draft: TokenDraft) -> None:
        await self.config.user(user).deployment_draft.set(draft.to_dict())

    async def resolve_discord_wallet_draft(self, user, draft: TokenDraft) -> TokenDraft:
        context = await self._wallet_context_for_user(user)
        resolved = TokenDraft(
            creator_discord_id=draft.creator_discord_id,
            name=draft.name,
            symbol=draft.symbol,
            decimals=draft.decimals,
            supply_atomic=draft.supply_atomic,
            wallet_profile_id=context["profile_id"],
            owner_address=context["owner_address"],
        )
        await self.save_draft(user, resolved)
        return resolved

    async def create_external_deployment_link(self, user, draft: TokenDraft) -> str:
        if not await self.deployment_available():
            raise RuntimeError("Token deployment is disabled or emergency-paused.")
        user_config = self.config.user(user)
        pending = await user_config.pending_deployment()
        if isinstance(pending, dict):
            same_external_draft = (
                pending.get("route") == "external"
                and pending.get("draft") == draft.to_dict()
                and pending.get("request_id")
            )
            if same_external_draft:
                request_id = str(pending["request_id"])
            elif pending.get("provider_status") not in {
                "complete", "dropped", "failed"
            }:
                raise RuntimeError(
                    "Another token deployment is already active. Verify or finish it first."
                )
            else:
                request_id = "0x" + secrets.token_hex(32)
        else:
            request_id = "0x" + secrets.token_hex(32)
        wallet = self._cryptowallet()
        token, expires_at = await wallet.tokenfactory_create_external_handoff(
            user.id, draft.to_dict(), request_id
        )
        handle = await wallet.register_recovery_handoff(token, expires_at)
        await user_config.pending_deployment.set({
            "route": "external",
            "draft": draft.to_dict(),
            "request_id": request_id,
            "provider_status": "awaiting_external_wallet",
            "submitted_at": int(time.time()),
        })
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

    async def deploy_pinned_factory(self, user, creation_code: str) -> dict:
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
                        "`tokenfactoryset verifyfactory` instead."
                    )
            attempt_id = str(uuid.uuid4())
            result = await self._cryptowallet().tokenfactory_deploy_pinned_factory(
                user, creation_code, attempt_id
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

    async def deployment_available(self) -> bool:
        return bool(
            await self.config.deployment_enabled()
            and not await self.config.emergency_paused()
            and str(await self.config.factory_address() or "").lower()
            == "0xcba30318008035bb5a855a8684cea954d573c2c3"
            and str(await self.config.factory_runtime_code_hash() or "").lower()
            == "0xa4e867671846a61743568f19d897fb5ffe40ad9678c9c06e791ff45dad7136f7"
        )

    async def submit_token_deployment(self, user, draft: TokenDraft) -> dict:
        if not await self.deployment_available():
            raise RuntimeError("Token deployment is disabled or emergency-paused.")
        stored = await self.config.user(user).deployment_draft()
        if not isinstance(stored, dict) or TokenDraft.from_dict(stored) != draft:
            raise RuntimeError("The saved token draft changed; reopen the card and review it.")
        wallet = self._cryptowallet()
        context = await wallet.tokenfactory_wallet_context(user)
        if (
            str(context.get("profile_id")) != draft.wallet_profile_id
            or normalize_owner_address(str(context.get("owner_address"))).lower()
            != draft.owner_address.lower()
        ):
            raise RuntimeError("The wallet profile no longer matches this token draft.")
        user_config = self.config.user(user)
        pending = await user_config.pending_deployment()
        if isinstance(pending, dict) and pending.get("request_id"):
            same_draft = pending.get("draft") == draft.to_dict()
            if same_draft:
                verified = await wallet.tokenfactory_verify_fixed_supply_token(
                    request_id=str(pending["request_id"]),
                    recipient=draft.owner_address,
                    name=draft.name,
                    symbol=draft.symbol,
                    decimals=draft.decimals,
                    supply_atomic=draft.supply_atomic,
                )
                if verified.get("deployed"):
                    return {**verified, "already_deployed": True}
                status = await wallet.tokenfactory_operation_status(
                    user, str(pending["user_operation_hash"])
                )
                pending.update(status)
                await user_config.pending_deployment.set(pending)
                if status["provider_status"] not in {"complete", "dropped", "failed"}:
                    raise RuntimeError(
                        f"Your existing token deployment is {status['provider_status']}. "
                        "Run `tokenfactory deployment` to refresh it."
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
        result = await wallet.tokenfactory_deploy_fixed_supply_token(
            user,
            name=draft.name,
            symbol=draft.symbol,
            decimals=draft.decimals,
            supply_atomic=draft.supply_atomic,
            recipient=draft.owner_address,
            request_id=request_id,
            attempt_id=attempt_id,
        )
        await user_config.pending_deployment.set({
            "draft": draft.to_dict(),
            "request_id": request_id,
            "attempt_id": attempt_id,
            "provider_status": result["provider_status"],
            "user_operation_hash": result["user_operation_hash"],
            "transaction_hash": result.get("transaction_hash"),
            "submitted_at": int(time.time()),
        })
        return result

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
        result = await self._cryptowallet().tokenfactory_verify_external_transaction(
            transaction_hash=transaction_hash,
            request_id=str(pending["request_id"]),
            recipient=recipient,
            name=draft.name,
            symbol=draft.symbol,
            decimals=draft.decimals,
            supply_atomic=draft.supply_atomic,
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

    async def verify_token_deployment(self, user) -> dict:
        user_config = self.config.user(user)
        pending = await user_config.pending_deployment()
        if not isinstance(pending, dict) or not pending.get("request_id"):
            raise RuntimeError("You have no pending token deployment.")
        draft = TokenDraft.from_dict(pending["draft"])
        if draft.creator_discord_id != user.id:
            raise RuntimeError("The pending deployment belongs to another member.")
        wallet = self._cryptowallet()
        verified = await wallet.tokenfactory_verify_fixed_supply_token(
            request_id=str(pending["request_id"]),
            recipient=draft.owner_address,
            name=draft.name,
            symbol=draft.symbol,
            decimals=draft.decimals,
            supply_atomic=draft.supply_atomic,
        )
        if not verified.get("deployed"):
            status = await wallet.tokenfactory_operation_status(
                user, str(pending["user_operation_hash"])
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
        await user_config.pending_deployment.set(None)
        await user_config.deployment_draft.set(None)
        return {"deployed": True, **record}

    async def _wallet_context_for_user(self, user) -> dict:
        wallet = self.bot.get_cog("CryptoWallet")
        integration = getattr(wallet, "tokenfactory_wallet_context", None)
        if integration is None:
            raise RuntimeError("CryptoWallet must be loaded for Discord Wallet deployment.")
        context = await integration(user)
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

    @tokenfactory.command(name="create", aliases=("card",))
    async def tokenfactory_create(self, ctx: commands.Context):
        """Open the interactive fixed-supply token form."""
        stored = await self.config.user(ctx.author).deployment_draft()
        draft = None
        if isinstance(stored, dict):
            try:
                candidate = TokenDraft.from_dict(stored)
                if candidate.creator_discord_id == ctx.author.id:
                    draft = candidate
            except (KeyError, TypeError, ValueError):
                pass
        view = TokenFactoryDraftView(
            self, ctx.author, draft,
            deployment_available=await self.deployment_available(),
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
                    await ctx.send(
                        "After the external wallet submits, use "
                        "`tokenfactory deployment <transaction_hash> <recipient_address>`. "
                        "The protected page provides the exact command."
                    )
                    return
                result = await self.verify_external_deployment(
                    ctx.author, transaction_hash, recipient
                )
            else:
                result = await self.verify_token_deployment(ctx.author)
        except Exception as exc:
            await ctx.send(f"Token deployment verification failed: {exc}")
            return
        if result.get("deployed"):
            await ctx.send(
                f"Verified **{result['name']} ({result['symbol']})** at "
                f"`{result['contract_address']}` on Base Sepolia. It was added to the "
                "community token registry."
            )
            return
        status = result.get("provider_status", "pending")
        transaction = result.get("transaction_hash")
        message = f"Your token deployment operation is **{status}**."
        if transaction:
            message += f" Transaction: `{transaction}`"
        if status in {"complete", "dropped", "failed"}:
            message += " No matching token is on-chain; reopen `tokenfactory create` to retry."
        else:
            message += " No matching token is confirmed yet; check again shortly."
        await ctx.send(message)

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
        embed.add_field(name="Factory", value=f"`{address}`" if address else "Not configured", inline=False)
        embed.add_field(name="Pinned code hash", value=f"`{code_hash}`" if code_hash else "Not configured", inline=False)
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

    @tokenfactoryset.command(name="deployment")
    async def tokenfactoryset_deployment(self, ctx: commands.Context, mode: str):
        """Enable, disable, or emergency-pause member token deployments."""

        choice = mode.strip().lower()
        if choice not in {"enable", "enabled", "disable", "disabled", "pause", "paused"}:
            await ctx.send("Choose `enable`, `disable`, or `pause`.")
            return
        if choice in {"enable", "enabled"}:
            try:
                state = await self._cryptowallet().tokenfactory_deployment_status()
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
            wallet = self._cryptowallet()
            state = await wallet.tokenfactory_deployment_status()
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
        embed.add_field(name="Destination", value=f"`{state['address']}`", inline=False)
        embed.add_field(
            name="Runtime code hash",
            value="`0xa4e867671846a61743568f19d897fb5ffe40ad9678c9c06e791ff45dad7136f7`",
            inline=False,
        )
        embed.set_footer(text="Owner-only · explicit confirmation · no mainnet path")
        view = FactoryDeploymentView(self, ctx.author, str(artifact["bytecode"]))
        await ctx.send(embed=embed, view=view)

    @tokenfactoryset.command(name="verifyfactory")
    async def tokenfactoryset_verify_factory(self, ctx: commands.Context):
        """Verify and record the pinned factory after on-chain confirmation."""

        try:
            state = await self._cryptowallet().tokenfactory_deployment_status()
        except Exception as exc:
            await ctx.send(f"Factory verification failed: {exc}")
            return
        if not state.get("deployed"):
            pending = await self.config.pending_factory_operation()
            if not isinstance(pending, dict) or not pending.get("user_operation_hash"):
                await ctx.send(
                    "The pinned factory is not confirmed and no tracked deployment "
                    "operation exists. Run `tokenfactoryset deployfactory` to start one."
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
