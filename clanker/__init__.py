from .clanker import Clanker

__red_end_user_data_statement__ = (
    "This cog stores guild Clanker API settings and token launch audit records. "
    "It does not store private keys, seed phrases, or user wallet credentials."
)


async def setup(bot):
    await bot.add_cog(Clanker(bot))
