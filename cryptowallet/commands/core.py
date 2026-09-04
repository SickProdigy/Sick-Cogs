import time

import discord
from redbot.core import commands

from ..core.networks import BASE_SEPOLIA, NETWORKS
from ..providers import WalletProviderError
from ..core.validation import format_wei_as_eth
from .constants import WALLET_SUMMARY_COOLDOWN_SECONDS


class WalletCoreCommands:
    """User-facing wallet commands."""

    async def _wallet_read_allowed(
        self, ctx: commands.Context, key: str, cooldown_seconds: int
    ) -> bool:
        """Rate-limit provider reads for ordinary users while exempting administrators."""
        if await self.bot.is_owner(ctx.author):
            return True
        permissions = getattr(ctx.author, "guild_permissions", None)
        if permissions is not None and permissions.administrator:
            return True

        now = time.monotonic()
        cooldown_key = (ctx.author.id, key)
        allowed_at = self.wallet_read_cooldowns.get(cooldown_key, 0.0)
        if now < allowed_at:
            remaining = max(1, int(allowed_at - now + 0.999))
            await ctx.send(
                f"Please wait {remaining} second(s) before requesting that wallet data again."
            )
            return False
        self.wallet_read_cooldowns[cooldown_key] = now + cooldown_seconds
        return True

    async def _wallet_profile_or_error(self, ctx: commands.Context) -> dict | None:
        if await self.config.provider_paused():
            await ctx.send(
                "CryptoWallet provider processing is paused by the bot owner. "
                "Local wallet settings remain available."
            )
            return None
        try:
            return await self.get_or_create_wallet_profile(ctx.author)
        except WalletProviderError as exc:
            await ctx.send(f"Wallet provisioning is unavailable: {exc}")
        except RuntimeError as exc:
            await ctx.send(str(exc))
        return None

    async def _wallet_embed(
        self, ctx: commands.Context, profile: dict, user=None
    ) -> discord.Embed:
        embed = discord.Embed(title="Crypto Wallet", color=discord.Color.green())
        embed.add_field(name="Network", value=f"{BASE_SEPOLIA.name} (testnet)", inline=False)
        display_name = discord.utils.escape_markdown((user or ctx.author).display_name)
        embed.description = f"{display_name}’s public wallet and balance."
        accounts = profile.get("accounts") or []
        for account in accounts[:5]:
            address = str(account.get("address") or "Unavailable")
            embed.add_field(
                name="Wallet Address",
                value=f"[{address}]({BASE_SEPOLIA.explorer_url}/address/{address})",
                inline=False,
            )
        account = self._account_for_network(profile, BASE_SEPOLIA.key)
        if account is not None:
            try:
                balance_wei = await self.wallet_provider.get_native_balance(
                    str(account.get("address") or ""), BASE_SEPOLIA.key
                )
                balance = f"{format_wei_as_eth(balance_wei)} {BASE_SEPOLIA.native_symbol}"
            except (ValueError, WalletProviderError):
                balance = "Temporarily unavailable"
            embed.add_field(name="Balance", value=balance, inline=False)
        embed.set_footer(text="Prototype only — do not use with real funds")
        return embed

    @commands.group(
        name="wallet", aliases=("wallets", "cryptowallet"), invoke_without_command=True
    )
    async def wallet(self, ctx: commands.Context, member: discord.Member = None):
        """Show your wallet or another member's existing public wallet profile."""
        if not await self._wallet_read_allowed(
            ctx, "summary", WALLET_SUMMARY_COOLDOWN_SECONDS
        ):
            return
        if await self.config.provider_paused():
            await ctx.send(
                "CryptoWallet provider processing is paused by the bot owner. "
                "Local wallet settings remain available."
            )
            return
        target = member or ctx.author
        if target.id == ctx.author.id:
            profile = await self._wallet_profile_or_error(ctx)
            if profile is None:
                return
        else:
            profile = await self.config.user(target).profile()
            if profile is None:
                display_name = discord.utils.escape_markdown(target.display_name)
                await ctx.send(f"{display_name} does not have a public wallet profile yet.")
                return
        await ctx.send(embed=await self._wallet_embed(ctx, profile, target))

    @wallet.command(name="balance", aliases=("funds",))
    async def wallet_balance(self, ctx: commands.Context):
        """Show your Base Sepolia address and native ETH balance."""
        if not await self._wallet_read_allowed(
            ctx, "summary", WALLET_SUMMARY_COOLDOWN_SECONDS
        ):
            return
        profile = await self._wallet_profile_or_error(ctx)
        if profile is None:
            return
        await ctx.send(embed=await self._wallet_embed(ctx, profile))

    @wallet.command(name="notifications", aliases=("notify",))
    async def wallet_notifications(
        self, ctx: commands.Context, enabled: bool = None
    ):
        """Show or change optional wallet transaction confirmation DMs."""
        if enabled is None:
            enabled = await self.config.user(ctx.author).notifications_enabled()
            state = "enabled" if enabled else "disabled"
            await ctx.send(
                f"Wallet transaction DMs are currently **{state}**. "
                "Automatic incoming-deposit alerts are not implemented yet."
            )
            return
        await self.config.user(ctx.author).notifications_enabled.set(enabled)
        state = "enabled" if enabled else "disabled"
        await ctx.send(
            f"Wallet transaction DMs are now **{state}**. "
            "Transaction cards will continue updating either way. Automatic "
            "incoming-deposit alerts are not implemented yet."
        )

    @wallet.command(name="networks")
    async def wallet_networks(self, ctx: commands.Context):
        """List networks enabled for this prototype."""
        lines = [
            f"- **{network.name}** — chain ID `{network.chain_id}` "
            f"({network.native_symbol}, testnet)"
            for network in NETWORKS.values()
        ]
        await ctx.send("**Enabled wallet networks**\n" + "\n".join(lines))


    @staticmethod
    def _account_for_network(profile: dict, network_key: str) -> dict | None:
        """Return the wallet account assigned to a configured network."""
        for account in profile.get("accounts") or []:
            if account.get("network") == network_key:
                return account
        return None
