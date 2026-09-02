from .donate import Donate


__red_end_user_data_statement__ = (
    "This cog stores guild donation display settings such as payment method labels, links, "
    "addresses, and public notes. It does not store donor records or payment history."
)


async def setup(bot):
    await bot.add_cog(Donate(bot))
