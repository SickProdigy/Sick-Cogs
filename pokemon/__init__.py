from .pokemon import Pokemon

async def setup(bot):
    await bot.add_cog(Pokemon(bot))
