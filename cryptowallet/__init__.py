from .cryptowallet import CryptoWallet


__red_end_user_data_statement__ = (
    "This cog stores wallet profile identifiers, linked platform identifiers, public wallet "
    "addresses, and public transaction status metadata. It never stores private keys, recovery "
    "phrases, wallet passwords, or one-time authentication codes."
)


async def setup(bot):
    cog = CryptoWallet(bot)
    await bot.add_cog(cog)
    await cog.initialize()
