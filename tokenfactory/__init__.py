from .tokenfactory import TokenFactory


__red_end_user_data_statement__ = (
    "This cog stores public test-token deployment drafts linked to Discord user IDs and "
    "public wallet addresses. It never stores wallet keys, recovery material, or credentials."
)


async def setup(bot):
    await bot.add_cog(TokenFactory(bot))
