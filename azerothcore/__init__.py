from .azerothcore import AzerothCore


async def setup(bot):
    await bot.add_cog(AzerothCore(bot))