from redbot.core.utils import get_end_user_data_statement

from .gmod import GMod


__red_end_user_data_statement__ = get_end_user_data_statement(__file__)


async def setup(bot):
    await bot.add_cog(GMod(bot))
