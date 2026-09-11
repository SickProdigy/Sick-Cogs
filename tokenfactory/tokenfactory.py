import discord
import json
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
    __version__ = "0.1.0"

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(
            self, identifier=CONFIG_IDENTIFIER, force_registration=True
        )
        self.config.register_user(deployment_draft=None)
        self.config.register_global(
            deployment_enabled=False,
            factory_address=None,
            factory_runtime_code_hash=None,
            factory_version=None,
            emergency_paused=True,
        )

    async def red_delete_data_for_user(self, *, requester, user_id: int):
        await self.config.user_from_id(user_id).clear()

    async def save_draft(self, user, draft: TokenDraft) -> None:
        await self.config.user(user).deployment_draft.set(draft.to_dict())

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
            return await self._cryptowallet().tokenfactory_deploy_pinned_factory(
                user, creation_code
            )
        except Exception as exc:
            raise RuntimeError(f"Pinned factory deployment failed: {exc}") from exc

    async def _wallet_context(self, ctx: commands.Context) -> dict | None:
        wallet = self.bot.get_cog("CryptoWallet")
        integration = getattr(wallet, "tokenfactory_wallet_context", None)
        if integration is None:
            await ctx.send("CryptoWallet must be loaded before creating a token draft.")
            return None
        try:
            context = await integration(ctx.author)
            return {
                **context,
                "owner_address": normalize_owner_address(context["owner_address"]),
            }
        except Exception:
            await ctx.send(
                "Your Base Sepolia wallet could not be prepared. Try `wallet` first, then retry."
            )
            return None

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
        wallet_context = await self._wallet_context(ctx)
        if wallet_context is None:
            return
        stored = await self.config.user(ctx.author).deployment_draft()
        draft = None
        if isinstance(stored, dict):
            try:
                candidate = TokenDraft.from_dict(stored)
                if (
                    candidate.creator_discord_id == ctx.author.id
                    and candidate.wallet_profile_id == wallet_context["profile_id"]
                    and candidate.owner_address.lower()
                    == wallet_context["owner_address"].lower()
                ):
                    draft = candidate
            except (KeyError, TypeError, ValueError):
                pass
        view = TokenFactoryDraftView(self, ctx.author, wallet_context, draft)
        await ctx.send(embed=view.embed(), view=view)

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
        embed.set_footer(text="Draft creation is safe; deployment remains unavailable")
        await ctx.send(embed=embed)

    @commands.group(name="tokenfactoryset", invoke_without_command=True)
    @commands.is_owner()
    async def tokenfactoryset(self, ctx: commands.Context):
        """Manage reviewed TokenFactory infrastructure."""

        await ctx.send_help()

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
            await ctx.send("The pinned factory is not confirmed on Base Sepolia yet.")
            return
        await self.config.factory_address.set(state["address"])
        await self.config.factory_runtime_code_hash.set(
            "0xa4e867671846a61743568f19d897fb5ffe40ad9678c9c06e791ff45dad7136f7"
        )
        await self.config.factory_version.set("v1")
        await ctx.send(
            f"Verified and recorded the pinned factory at `{state['address']}`. "
            "Token deployment remains emergency-paused until its separate flow is complete."
        )
