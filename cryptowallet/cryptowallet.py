import secrets
import time
from urllib.parse import urlparse

import discord
from redbot.core import Config, commands

from .models import IntentStatus, TransactionIntent
from .networks import BASE_SEPOLIA, DEFAULT_NETWORK, NETWORKS
from .validation import (
    format_wei_as_eth,
    normalize_evm_address,
    parse_eth_to_wei,
)


INTENT_LIFETIME_SECONDS = 15 * 60
MAX_STORED_INTENTS = 25


class CryptoWallet(commands.Cog):
    """Manage public smart-wallet information through a secure companion service."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=9365048217, force_registration=True)
        self.config.register_global(
            approval_base_url=None,
            provider="unconfigured",
            default_network=DEFAULT_NETWORK,
        )
        self.config.register_user(profile=None, intents={})

    async def red_delete_data_for_user(self, *, requester, user_id: int):
        """Delete the Discord-side wallet profile metadata for a user."""

        await self.config.user_from_id(user_id).clear()

    @commands.group(name="wallet", aliases=("cryptowallet",), invoke_without_command=True)
    async def wallet(self, ctx: commands.Context):
        """Show your wallet profile and prototype status."""

        profile = await self.config.user(ctx.author).profile()
        embed = discord.Embed(
            title="Crypto Wallet",
            color=await ctx.embed_color(),
        )
        embed.add_field(name="Network", value=f"{BASE_SEPOLIA.name} (testnet)", inline=False)
        if profile is None:
            embed.description = (
                "No wallet is linked yet. Enrollment will be enabled after the secure "
                "companion service and CDP user-owned wallet flow are configured."
            )
        else:
            accounts = profile.get("accounts") or []
            embed.description = "Your public wallet profile is linked."
            for account in accounts[:5]:
                address = str(account.get("address") or "Unavailable")
                account_type = str(account.get("account_type") or "unknown")
                embed.add_field(
                    name=account_type.replace("_", " ").title(),
                    value=f"`{address}`",
                    inline=False,
                )
        embed.set_footer(text="Prototype only — do not use with real funds")
        await ctx.send(embed=embed)

    @wallet.command(name="networks")
    async def wallet_networks(self, ctx: commands.Context):
        """List networks enabled for this prototype."""

        lines = [
            f"- **{network.name}** — chain ID `{network.chain_id}` "
            f"({network.native_symbol}, testnet)"
            for network in NETWORKS.values()
        ]
        await ctx.send("**Enabled wallet networks**\n" + "\n".join(lines))

    @wallet.command(name="enroll", aliases=("create",))
    async def wallet_enroll(self, ctx: commands.Context):
        """Begin secure wallet enrollment when the companion service is available."""

        approval_base_url = await self.config.approval_base_url()
        provider = await self.config.provider()
        if not approval_base_url or provider == "unconfigured":
            await ctx.send(
                "Wallet enrollment is not enabled yet. The Base Sepolia companion service "
                "and user-owned wallet provider must be configured first."
            )
            return

        await ctx.send(
            "Enrollment configuration exists, but issuing one-time authorization links is "
            "not implemented in this foundation milestone."
        )

    async def _expire_and_trim_intents(self, user) -> dict:
        """Expire pending intents and retain only the newest bounded history."""

        now = int(time.time())
        async with self.config.user(user).intents() as intents:
            for data in intents.values():
                if (
                    data.get("status") == IntentStatus.PENDING.value
                    and int(data.get("expires_at", 0) or 0) <= now
                ):
                    data["status"] = IntentStatus.EXPIRED.value

            ordered = sorted(
                intents.items(),
                key=lambda item: int(item[1].get("created_at", 0) or 0),
                reverse=True,
            )
            intents.clear()
            intents.update(ordered[:MAX_STORED_INTENTS])
            return dict(intents)

    @staticmethod
    def _account_for_network(profile: dict, network_key: str) -> dict | None:
        for account in profile.get("accounts") or []:
            if account.get("network") == network_key:
                return account
        return None

    @staticmethod
    def _intent_embed(intent: TransactionIntent, network, color) -> discord.Embed:
        embed = discord.Embed(title="Wallet transaction intent", color=color)
        embed.add_field(name="Status", value=intent.status.value.title(), inline=True)
        embed.add_field(
            name="Network", value=f"{network.name} (`{network.chain_id}`)", inline=True
        )
        embed.add_field(
            name="Amount",
            value=f"{format_wei_as_eth(intent.value_wei)} {network.native_symbol}",
            inline=True,
        )
        embed.add_field(name="From", value=f"`{intent.from_address}`", inline=False)
        embed.add_field(name="To", value=f"`{intent.to_address}`", inline=False)
        embed.add_field(name="Intent ID", value=f"`{intent.intent_id}`", inline=False)
        embed.add_field(name="Expires", value=f"<t:{intent.expires_at}:R>", inline=True)
        if intent.transaction_hash:
            embed.add_field(
                name="Transaction",
                value=(
                    f"[{intent.transaction_hash}]"
                    f"({network.explorer_url}/tx/{intent.transaction_hash})"
                ),
                inline=False,
            )
        embed.set_footer(text="Unsigned testnet intent — no transaction has been sent")
        return embed

    @wallet.command(name="send")
    async def wallet_send(self, ctx: commands.Context, to_address: str, amount: str):
        """Prepare an unsigned Base Sepolia ETH transfer intent.

        Nothing is signed or broadcast by this command.
        """

        profile = await self.config.user(ctx.author).profile()
        if profile is None:
            await ctx.send("Link or enroll a wallet before creating a transaction intent.")
            return

        network_key = await self.config.default_network()
        network = NETWORKS.get(network_key)
        if network is None or not network.testnet:
            await ctx.send("Transaction intents are restricted to an enabled test network.")
            return

        account = self._account_for_network(profile, network.key)
        if account is None:
            await ctx.send(f"Your wallet profile has no account for {network.name}.")
            return

        try:
            from_address = normalize_evm_address(str(account.get("address") or ""))
            recipient = normalize_evm_address(to_address)
            value_wei = parse_eth_to_wei(amount)
        except ValueError as exc:
            await ctx.send(str(exc))
            return

        now = int(time.time())
        intent = TransactionIntent(
            intent_id=secrets.token_urlsafe(12),
            profile_id=str(profile.get("profile_id") or ""),
            network=network.key,
            from_address=from_address,
            to_address=recipient,
            value_wei=value_wei,
            created_at=now,
            expires_at=now + INTENT_LIFETIME_SECONDS,
        )
        if not intent.profile_id:
            await ctx.send("Your wallet profile is incomplete and must be linked again.")
            return

        async with self.config.user(ctx.author).intents() as intents:
            intents[intent.intent_id] = intent.to_dict()
        await self._expire_and_trim_intents(ctx.author)

        await ctx.send(embed=self._intent_embed(intent, network, await ctx.embed_color()))

    @wallet.command(name="transaction", aliases=("intent", "tx"))
    async def wallet_transaction(self, ctx: commands.Context, intent_id: str):
        """Show one of your transaction intents by ID."""

        intents = await self._expire_and_trim_intents(ctx.author)
        data = intents.get(intent_id.strip())
        if data is None:
            await ctx.send("No transaction intent with that ID belongs to your wallet profile.")
            return

        try:
            intent = TransactionIntent.from_dict(data)
        except (KeyError, TypeError, ValueError):
            await ctx.send("That stored transaction intent is invalid and cannot be displayed.")
            return
        network = NETWORKS.get(intent.network)
        if network is None:
            await ctx.send("That transaction intent references an unsupported network.")
            return
        await ctx.send(embed=self._intent_embed(intent, network, await ctx.embed_color()))

    @commands.group(name="walletset", invoke_without_command=True)
    @commands.is_owner()
    async def walletset(self, ctx: commands.Context):
        """Configure the global wallet companion integration."""

        await ctx.send_help()

    @walletset.command(name="view")
    @commands.is_owner()
    async def walletset_view(self, ctx: commands.Context):
        """Show non-secret wallet integration settings."""

        approval_base_url = await self.config.approval_base_url()
        provider = await self.config.provider()
        network_key = await self.config.default_network()
        network = NETWORKS.get(network_key, BASE_SEPOLIA)
        await ctx.send(
            "**Wallet integration**\n"
            f"Provider: `{provider}`\n"
            f"Network: `{network.name}` (`{network.chain_id}`)\n"
            f"Companion URL: `{approval_base_url or 'not configured'}`\n"
            "Mainnet: `disabled`"
        )

    @walletset.command(name="approvalurl")
    @commands.is_owner()
    async def walletset_approval_url(self, ctx: commands.Context, url: str):
        """Set the HTTPS origin used for secure wallet approvals."""

        normalized = url.strip().rstrip("/")
        parsed = urlparse(normalized)
        if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
            await ctx.send(
                "Provide an HTTPS origin without embedded credentials, for example "
                "`https://wallet.example.com`."
            )
            return
        await self.config.approval_base_url.set(normalized)
        await ctx.send("Wallet companion URL updated. No credentials were stored.")

    @walletset.command(name="clearapprovalurl")
    @commands.is_owner()
    async def walletset_clear_approval_url(self, ctx: commands.Context):
        """Disable the configured wallet companion origin."""

        await self.config.approval_base_url.set(None)
        await ctx.send("Wallet companion URL cleared; enrollment is disabled.")
