from .mybb import MyBB


async def setup(bot):
    await bot.add_cog(MyBB(bot))
