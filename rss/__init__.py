from redbot.core import commands as red_commands

from .rss import RSS

__red_end_user_data_statement__ = "This cog does not persistently store data or metadata about users."


async def setup(bot: red_commands.Bot):
    n = RSS(bot)
    await bot.add_cog(n)
    n.initialize()
