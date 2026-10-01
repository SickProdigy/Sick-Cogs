from datetime import datetime
import secrets
from urllib.parse import quote

import discord
from redbot.core import commands

from ..core.environment import WalletEnvironment, parse_wallet_environment
from ..core.networks import BASE_SEPOLIA
from ..backend.terms import (
    CRYPTOWALLET_MAINNET_TERMS_VERSION,
    CRYPTOWALLET_TERMS_PRODUCT,
)
from ..providers import WalletProviderError
from .constants import WALLET_PROVIDER_COOLDOWN_SECONDS
from .core import WalletCoreCommands
from .views import (
    WalletAuthorizationView, WalletMainnetSetupView, WalletRevocationView,
)


class WalletAuthorizationCommands:
    """Wallet authorization commands and interaction handlers."""

    @staticmethod
    def _active_authorization_embed(status: dict, expiry: datetime) -> discord.Embed:
        embed = discord.Embed(
            title="Wallet authorization active",
            description=(
                "This authorization permits limited bot signing for every account in "
                "your Crypto Wallet profile. No new authorization was created."
            ),
            color=discord.Color.green(),
        )
        embed.add_field(name="Status", value="Active", inline=True)
        embed.add_field(
            name="Scope",
            value=(
                "All wallet accounts (account-specific grants)"
                if status.get("scope") == "accounts"
                else "All wallet accounts"
            ),
            inline=True,
        )
        embed.add_field(
            name="Expires",
            value=f"<t:{int(expiry.timestamp())}:F>\n<t:{int(expiry.timestamp())}:R>",
            inline=False,
        )
        embed.add_field(
            name="Options",
            value=(
                "Leave it active for future sends, or use **Revoke authorization** below before choosing a different duration. "
                "Revoking does not delete the wallet or move funds."
            ),
            inline=False,
        )
        embed.set_footer(text="Future sends require authorization again after revocation or expiry.")
        return embed

    @WalletCoreCommands.wallet.command(name="authorize", aliases=("auth",))
    async def wallet_authorize(self, ctx: commands.Context, days: int = None):
        """Authorize protected signing.

        Authorizes limited bot actions for your provisioned wallet.
        """
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
                mode_setting = getattr(getattr(self, "config", None), "operating_mode", None)
                mode_value = await mode_setting() if mode_setting is not None else "testnet"
                environment = parse_wallet_environment(mode_value) or WalletEnvironment.TESTNET
                if (environment is not WalletEnvironment.TESTNET
                        and not await self.has_current_cryptowallet_mainnet_terms(ctx.author.id)):
                    expires_at = await self._send_wallet_terms_acceptance(ctx.author)
                    await ctx.send(
                        "Your limited wallet authorization is already active. I sent the "
                        f"remaining protected mainnet terms step by DM; it expires <t:{expires_at}:R>."
                    )
                    return
                expiry = datetime.fromisoformat(
                    status["expires_at"].replace("Z", "+00:00")
                )
                embed = self._active_authorization_embed(status, expiry)
                if days is not None:
                    embed.add_field(
                        name="Duration not changed",
                        value=(
                            "CDP allows only one active authorization. Revoke the current "
                            f"authorization first, then use `{ctx.clean_prefix}wallet auth {days}` again."
                        ),
                        inline=False,
                    )
                await ctx.send(
                    embed=embed,
                    view=WalletAuthorizationView(self, ctx.author.id, profile),
                )
                return
            if status.get("partial"):
                await ctx.send(
                    f"Wallet authorization is incomplete. Use `{ctx.clean_prefix}wallet revoke` to clear "
                    "the partial grant before authorizing again."
                )
                return
            expires_at = await self.send_authorization_link(
                ctx.author, profile, renewal=status["active"], requested_days=days
            )
        except (RuntimeError, WalletProviderError) as exc:
            await ctx.send(f"Wallet authorization is unavailable: {exc}")
            return
        await ctx.send(f"I sent your wallet authorization link by DM; it expires <t:{expires_at}:R>.")

    async def send_authorization_link(
        self, user, profile: dict, *, renewal: bool = False,
        requested_days: int | None = None,
    ) -> int:
        """DM the existing authorization flow or one guided mainnet setup."""
        if await self.config.user_from_id(user.id).security_locked():
            raise RuntimeError(
                "This wallet is emergency-locked; new authorization is blocked until "
                "the bot owner completes an identity review and unlocks it."
            )
        approval_base_url = str(await self.config.approval_base_url() or "").rstrip("/")
        configured_days = int(await self.config.delegation_duration_days() or 0)
        maximum_days = int(await self.config.delegation_max_duration_days() or 0)
        recommended_days = configured_days if requested_days is None else int(requested_days)
        if not 1 <= recommended_days <= maximum_days:
            raise RuntimeError(
                f"Authorization duration must be from 1 through {maximum_days} days."
            )
        mode_setting = getattr(getattr(self, "config", None), "operating_mode", None)
        mode_value = await mode_setting() if mode_setting is not None else "testnet"
        environment = parse_wallet_environment(mode_value) or WalletEnvironment.TESTNET
        needs_terms = (
            environment is not WalletEnvironment.TESTNET
            and not await self.has_current_cryptowallet_mainnet_terms(user.id)
        )
        result_handle = secrets.token_urlsafe(32) if needs_terms else None
        terms = ({
            "product": CRYPTOWALLET_TERMS_PRODUCT,
            "version": CRYPTOWALLET_MAINNET_TERMS_VERSION,
            "result_handle": result_handle,
        } if needs_terms else None)
        if needs_terms:
            token, expires_at = await self.create_authorization_handoff(
                user.id, profile, delegation_days=recommended_days, terms=terms
            )
        else:
            token, expires_at = await self.create_authorization_handoff(
                user.id, profile, delegation_days=recommended_days
            )
        link = f"{approval_base_url}/session.html#handoff={quote(token, safe='')}"
        if needs_terms:
            embed = discord.Embed(
                title="Set Up Mainnet Wallet",
                description=(
                    "Use the protected page to review and accept the CryptoWallet terms, "
                    "then create the existing time-limited wallet authorization. Return "
                    "here and press **Confirm setup** when both steps are complete."
                ),
                color=discord.Color.blurple(),
            )
            embed.add_field(
                name="What this does",
                value=(
                    "Records CryptoWallet terms acceptance and enables limited signing for "
                    "your wallet profile. It does not send funds or approve a transaction."
                ),
                inline=False,
            )
            embed.add_field(name="Terms version", value=f"`{CRYPTOWALLET_MAINNET_TERMS_VERSION}`")
            embed.add_field(name="Link expires", value=f"<t:{expires_at}:R>")
            embed.add_field(
                name="Authorization duration",
                value=f"Defaults to {recommended_days} days; maximum {maximum_days} days.",
                inline=False,
            )
            embed.set_footer(text="Do not share or forward this protected setup.")
            view = WalletMainnetSetupView(
                self, user.id, profile, link, result_handle, expires_at
            )
            try:
                message = await user.send(embed=embed, view=view)
                view.message = message
            except discord.HTTPException as exc:
                raise RuntimeError(
                    "Discord could not deliver the protected wallet link. "
                    "Enable direct messages and try again."
                ) from exc
            return expires_at
        embed = discord.Embed(
            title="Renew Crypto Wallet Authorization" if renewal else "Authorize Crypto Wallet",
            description=(
                "Create a new time-limited signing grant for every account in this wallet "
                "profile. The existing authorization remains unchanged until you complete "
                "this protected approval."
                if renewal else
                "Grant the bot limited signing access to every account in this wallet profile."
            ),
            color=discord.Color.blurple(),
        )
        embed.description += f"\n\n🔐 **[Open protected authorization page]({link})**"
        if len(embed.description) > 4096:
            raise RuntimeError("The protected wallet link is too long for Discord delivery.")
        embed.add_field(name="Link expires", value=f"<t:{expires_at}:R>", inline=True)
        embed.add_field(
            name="Authorization duration",
            value=(
                f"The protected page defaults to {recommended_days} days. "
                f"You may enter any whole number from 1 through {maximum_days}."
            ),
            inline=True,
        )
        embed.add_field(name="Scope", value="All current wallet accounts", inline=False)
        embed.set_footer(text=(
            "Renewal is optional. Do not share or forward this authorization."
            if renewal else "Do not share or forward this authorization."
        ))
        try:
            await user.send(embed=embed)
        except discord.HTTPException as exc:
            raise RuntimeError(
                "Discord could not deliver the protected wallet link. "
                "Enable direct messages and try again."
            ) from exc
        return expires_at

    @WalletCoreCommands.wallet.command(name="authorization", aliases=("authstatus",))
    async def wallet_authorization(self, ctx: commands.Context):
        """Show authorization status.

        Shows whether the bot currently has limited signing authorization.
        """
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
        if status.get("partial"):
            await ctx.send(
                "Wallet authorization is incomplete: only "
                f"`{status.get('active_accounts', 0)}` of "
                f"`{status.get('required_accounts', 0)}` accounts are authorized. "
                f"Use `{ctx.clean_prefix}wallet revoke` to clear the partial grant before trying again."
            )
            return
        await ctx.send(
            "No active signing authorization exists. You can still receive funds and view "
            "your wallet; authorization will be requested when you first approve a send."
        )

    @WalletCoreCommands.wallet.command(name="revoke", aliases=("deauthorize", "de-auth", "deauth"))
    async def wallet_revoke(self, ctx: commands.Context):
        """Revoke signing access.

        Revokes limited signing authorization for every account in your wallet profile.
        """
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
        if not status["active"] and not status.get("partial"):
            await ctx.send("No active signing authorization exists for this wallet profile.")
            return
        expiry = datetime.fromisoformat(status["expires_at"].replace("Z", "+00:00"))
        await ctx.send(
            "Revoke limited signing authorization for every account in your wallet profile?\n"
            "This does not delete the wallet or move funds. Future sends will require "
            f"authorization again. The current authorization expires <t:{int(expiry.timestamp())}:R>.",
            view=WalletRevocationView(self, ctx.author.id, profile),
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
        if status["active"] or status.get("partial"):
            await interaction.followup.send(
                "CDP still reports an active or partial wallet authorization; no success was recorded.",
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
        embed.add_field(name="Scope", value="All wallet accounts", inline=True)
        await interaction.message.edit(content=None, embed=embed, view=view)
        await interaction.followup.send(
            "Limited signing authorization was revoked for every wallet account. "
            "Your wallet and funds were not changed. "
            "The next send will require authorization again.",
            ephemeral=True,
        )
