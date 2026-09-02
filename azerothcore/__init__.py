from .azerothcore import AzerothCore


__red_end_user_data_statement__ = (
    "This cog stores guild configuration such as API endpoint settings and allowed role IDs. "
    "It does not persist user-provided account data."
)


async def setup(bot):
    await bot.add_cog(AzerothCore(bot))
