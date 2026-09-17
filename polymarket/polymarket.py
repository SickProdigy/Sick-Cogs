from redbot.core import commands


class Polymarket(commands.Cog):
    """Prediction-market information and future wallet handoff tools."""

    __author__ = ["SickProdigy"]
    __version__ = "0.1.0"

    @commands.group(invoke_without_command=True)
    async def polymarket(self, ctx: commands.Context):
        """Browse prediction-market information.

        The initial release is a read-only foundation. It does not create wallets,
        custody funds, accept deposits, sign transactions, or place market orders.
        """
        await ctx.send(
            "**Prediction markets are being prepared.**\n"
            "This cog will begin with market discovery and information. Wallet handoff "
            "and any testnet-only transaction review require separate design and approval."
        )

    @polymarket.command(name="status")
    async def polymarket_status(self, ctx: commands.Context):
        """Show the current implementation boundary."""
        await ctx.send(
            "**Polymarket cog status**\n"
            "Phase: foundation\n"
            "Available: command surface and safety boundary\n"
            "Not available: provider requests, wallets, deposits, signatures, or trading."
        )
