from datetime import datetime
from urllib.parse import quote

import discord
from redbot.core import commands

from ..core.networks import BASE_SEPOLIA
from ..providers import WalletProviderError
from .constants import WALLET_PROVIDER_COOLDOWN_SECONDS
from .core import WalletCoreCommands
from .views import WalletAuthorizationView, WalletRevocationView


class WalletAuthorizationCommands:
    """Wallet authorization commands and interaction handlers."""

    @staticmethod
    def _active_authorization_embed(status: dict, expiry: datetime) -> discord.Embed:
        address = str(status.get("address") or "Unavailable")
        embed = discord.Embed(
            title="Wallet authorization active",
            description=(
                "This wallet already permits limited bot signing on Base Sepolia. "
                "No new authorization was created."
            ),
            color=discord.Color.green(),
        )
        embed.add_field(name="Status", value="Active", inline=True)
        embed.add_field(name="Network", value="Base Sepolia", inline=True)
        embed.add_field(
            name="Expires",
            value=f"<t:{int(expiry.timestamp())}:F>\n<t:{int(expiry.timestamp())}:R>",
            inline=False,
        )
        embed.add_field(
            name="Wallet address",
            value=f"[{address}]({BASE_SEPOLIA.explorer_url}/address/{address})",
            inline=False,
        )
        embed.add_field(
            name="Options",
            value=(
                "Leave it active for future sends, deliberately renew it for another "
                "24 hours, or use **Revoke authorization** below. "
                "Revoking does not delete the wallet or move funds."
            ),
            inline=False,
        )
        embed.set_footer(text="Future sends require authorization again after revocation or expiry.")
        return embed

    @WalletCoreCommands.wallet.command(name="authorize", aliases=("auth",))
    async def wallet_authorize(self, ctx: commands.Context):
        """Authorize limited bot actions for your provisioned wallet."""
        if not await self._wallet_read_allowed(
            ctx, "authorization", WALLET_PROVIDER_COOLDOWN_SECONDS
        ):
            return
        profile = await self._wallet_profile_or_error(ctx)
        if profile is None:
            return
        try:
            status = await self.wallet_provider.get_delegation_status(
                profile, BASE_SEPOLIA.key
            )
            if status["active"]:
                expiry = datetime.fromisoformat(
                    status["expires_at"].replace("Z", "+00:00")
                )
                embed = self._active_authorization_embed(status, expiry)
                await ctx.send(
                    embed=embed,
                    view=WalletAuthorizationView(self, ctx.author.id, profile),
                )
                return
            expires_at = await self.send_authorization_link(ctx.author, profile)
        except (RuntimeError, WalletProviderError) as exc:
            await ctx.send(f"Wallet authorization is unavailable: {exc}")
            return
        await ctx.send(f"I sent your wallet authorization link by DM; it expires <t:{expires_at}:R>.")

    async def send_authorization_link(
        self, user, profile: dict, *, renewal: bool = False
    ) -> int:
        """DM a short-lived authorization link and return its expiry."""
        approval_base_url = str(await self.config.approval_base_url() or "").rstrip("/")
        token, expires_at = await self.create_authorization_handoff(user.id, profile)
        link = f"{approval_base_url}/session.html#handoff={quote(token, safe='')}"
        embed = discord.Embed(
            title=(
                "Renew Crypto Wallet Authorization"
                if renewal
                else "Authorize Crypto Wallet"
            ),
            description=(
                "Create a new 24-hour limited signing grant for this Base Sepolia "
                "test wallet. The existing authorization remains unchanged until "
                "you complete this protected approval."
                if renewal
                else "Grant the bot limited signing access to this Base Sepolia test wallet."
            ),
            color=discord.Color.blurple(),
        )
        embed.add_field(name="Link expires", value=f"<t:{expires_at}:R>", inline=True)
        embed.add_field(name="Authorization duration", value="24 hours", inline=True)
        embed.add_field(name="Scope", value="This wallet only", inline=False)
        embed.set_footer(
            text=(
                "Renewal is optional. Do not share or forward this authorization."
                if renewal
                else "Do not share or forward this authorization."
            )
        )
        view = discord.ui.View(timeout=3 * 60)
        view.add_item(
            discord.ui.Button(
                label="Renew authorization" if renewal else "Authorize wallet",
                emoji="🔐",
                style=discord.ButtonStyle.link,
                url=link,
            )
        )
        try:
            await user.send(embed=embed, view=view)
        except discord.Forbidden as exc:
            raise RuntimeError(
                "I could not DM you. Enable direct messages and try again."
            ) from exc
        return expires_at

    @WalletCoreCommands.wallet.command(name="authorization", aliases=("authstatus",))
    async def wallet_authorization(self, ctx: commands.Context):
        """Show whether the bot currently has limited signing authorization."""
        if not await self._wallet_read_allowed(
            ctx, "authorization", WALLET_PROVIDER_COOLDOWN_SECONDS
        ):
            return
        profile = await self._wallet_profile_or_error(ctx)
        if profile is None:
            return
        try:
            status = await self.wallet_provider.get_delegation_status(
                profile, BASE_SEPOLIA.key
            )
        except WalletProviderError as exc:
            await ctx.send(f"Wallet authorization status is unavailable: {exc}")
            return
        if status["active"]:
            expiry = datetime.fromisoformat(
                status["expires_at"].replace("Z", "+00:00")
            )
            await ctx.send(
                embed=self._active_authorization_embed(status, expiry),
                view=WalletAuthorizationView(self, ctx.author.id, profile),
            )
            return
        await ctx.send(
            "No active signing authorization exists. You can still receive funds and view "
            "your wallet; authorization will be requested when you first approve a send."
        )

    @WalletCoreCommands.wallet.command(name="revoke", aliases=("deauthorize",))
    async def wallet_revoke(self, ctx: commands.Context):
        """Revoke limited signing authorization for your Base Sepolia wallet."""
        if not await self._wallet_read_allowed(
            ctx, "authorization", WALLET_PROVIDER_COOLDOWN_SECONDS
        ):
            return
        profile = await self._wallet_profile_or_error(ctx)
        if profile is None:
            return
        try:
            status = await self.wallet_provider.get_delegation_status(
                profile, BASE_SEPOLIA.key
            )
        except WalletProviderError as exc:
            await ctx.send(f"Wallet authorization status is unavailable: {exc}")
            return
        if not status["active"]:
            await ctx.send("No active signing authorization exists for this wallet.")
            return
        expiry = datetime.fromisoformat(status["expires_at"].replace("Z", "+00:00"))
        await ctx.send(
            "Revoke limited signing authorization for "
            f"{status['address']}?\n"
            "This does not delete the wallet or move funds. Future sends will require "
            f"authorization again. The current authorization expires <t:{int(expiry.timestamp())}:R>.",
            view=WalletRevocationView(self, ctx.author.id, profile),
        )

    async def renew_authorization_interaction(
        self, interaction: discord.Interaction, view: WalletAuthorizationView
    ) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            status = await self.wallet_provider.get_delegation_status(
                view.profile, BASE_SEPOLIA.key
            )
            expires_at = await self.send_authorization_link(
                interaction.user, view.profile, renewal=status["active"]
            )
        except (RuntimeError, WalletProviderError) as exc:
            await interaction.followup.send(
                f"Wallet authorization renewal is unavailable: {exc}", ephemeral=True
            )
            return
        view.disable_renewal()
        await interaction.message.edit(view=view)
        if status["active"]:
            message = (
                "I sent a deliberate renewal link by DM. Your current authorization "
                "remains active and unchanged unless you complete it. "
            )
        else:
            message = (
                "The previous authorization has expired, so I sent a new authorization "
                "link by DM. "
            )
        await interaction.followup.send(
            message + f"The link expires <t:{expires_at}:R>.", ephemeral=True
        )

    async def revoke_authorization_interaction(
        self, interaction: discord.Interaction, view: WalletRevocationView
    ) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            await self.wallet_provider.revoke_authorization(view.profile, BASE_SEPOLIA.key)
            status = await self.wallet_provider.get_delegation_status(
                view.profile, BASE_SEPOLIA.key
            )
        except WalletProviderError as exc:
            await interaction.followup.send(
                f"Wallet authorization could not be revoked: {exc}", ephemeral=True
            )
            return
        if status["active"]:
            await interaction.followup.send(
                "CDP still reports this wallet authorization as active; no success was recorded.",
                ephemeral=True,
            )
            return
        view.disable_controls()
        embed = discord.Embed(
            title="Wallet authorization revoked",
            description=(
                "Limited signing authorization is no longer active. Your wallet and funds "
                "were not changed; the next send will require authorization again."
            ),
            color=discord.Color.light_grey(),
        )
        embed.add_field(name="Status", value="Revoked", inline=True)
        embed.add_field(name="Network", value="Base Sepolia", inline=True)
        await interaction.message.edit(content=None, embed=embed, view=view)
        await interaction.followup.send(
            "Limited signing authorization was revoked. Your wallet and funds were not changed. "
            "The next send will require authorization again.",
            ephemeral=True,
        )
