from redbot.core.utils import get_end_user_data_statement
from redbot.core import commands as red_commands

from .rss import RSS


__red_end_user_data_statement__ = get_end_user_data_statement(__file__)


async def setup(bot: red_commands.Bot):
    n = RSS(bot)
    await bot.add_cog(n)
    n.initialize()
