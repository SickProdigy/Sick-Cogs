from urllib.parse import quote

import discord
from redbot.core import commands

from .constants import WALLET_PROVIDER_COOLDOWN_SECONDS
from .core import WalletCoreCommands


class WalletAccountCommands:
    """Protected wallet signer-backup commands."""

    @WalletCoreCommands.wallet.command(name="recovery", aliases=("backup",))
    async def wallet_recovery(self, ctx: commands.Context):
        """DM a protected link for backing up the wallet profile's account keys."""
        if not await self._wallet_sensitive_allowed(ctx):
            return
        if not await self._wallet_read_allowed(
            ctx, "recovery", WALLET_PROVIDER_COOLDOWN_SECONDS
        ):
            return
        profile = await self._wallet_profile_or_error(ctx)
        if profile is None:
            return
        account = self._account_for_network(profile, "base-sepolia")
        if account is None:
            await ctx.send("Your wallet profile has no Base Sepolia account.")
            return
        approval_base_url = str(
            await self.config.approval_base_url() or ""
        ).rstrip("/")
        if not approval_base_url:
            await ctx.send(
                "Wallet recovery is unavailable because the protected website is not configured."
            )
            return
        try:
            token, expires_at = await self.create_recovery_handoff(
                ctx.author.id, profile
            )
            link = f"{approval_base_url}/recovery.html#handoff={quote(token, safe='')}"
            link_message = f"🛟 [Open protected recovery page]({link})"
            if len(link_message) > 2000:
                raise RuntimeError(
                    "The protected wallet link is too long for Discord delivery."
                )
            embed = discord.Embed(
                title="Back Up Your Wallet Keys",
                description=(
                    "Securely export an EVM signer or Solana account private key "
                    "without exposing it to Discord or the bot."
                ),
                color=discord.Color.blurple(),
            )
            embed.add_field(
                name="Link expires", value=f"<t:{expires_at}:R>", inline=True
            )
            embed.add_field(
                name="Base smart account", value=f"`{account['address']}`", inline=False
            )
            embed.add_field(
                name="Private-key safety",
                value="Choose the account on the protected page. Coinbase displays its key only inside the isolated secure export frame.",
                inline=False,
            )
            await ctx.author.send(content=link_message, embed=embed)
        except discord.HTTPException:
            await ctx.send(
                "Discord could not deliver the protected wallet link. "
                "Enable direct messages and try again."
            )
            return
        except (KeyError, RuntimeError) as exc:
            await ctx.send(f"Wallet recovery is unavailable: {exc}")
            return
        await ctx.send(
            f"I sent your protected wallet recovery link by DM; it expires <t:{expires_at}:R>."
        )
