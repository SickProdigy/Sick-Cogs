from redbot.core.utils import get_end_user_data_statement
from .giveaways import Giveaways


__red_end_user_data_statement__ = get_end_user_data_statement(__file__)


async def setup(bot):
    await bot.add_cog(Giveaways(bot))
