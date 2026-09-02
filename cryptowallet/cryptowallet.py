from urllib.parse import urlparse

import discord
from redbot.core import Config, commands

from .networks import BASE_SEPOLIA, DEFAULT_NETWORK, NETWORKS


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
        self.config.register_user(profile=None)

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
