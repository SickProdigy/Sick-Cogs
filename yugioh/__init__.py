from .yugioh import YuGiOh
async def setup(bot):
    await bot.add_cog(YuGiOh(bot))
