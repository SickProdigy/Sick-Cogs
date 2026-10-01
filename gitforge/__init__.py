from .gitforge import GitForge


async def setup(bot):
    await bot.add_cog(GitForge(bot))
