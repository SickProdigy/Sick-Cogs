from redbot.core.utils import get_end_user_data_statement

from .meme import Meme

__red_end_user_data_statement__ = get_end_user_data_statement(__file__)


async def setup(bot):
    # Keep Pillow-backed image generation out of import-time feed tests while
    # still requiring it when Red actually loads the complete package.
    from .memeify import Memeify

    await bot.add_cog(Meme(bot))
    await bot.add_cog(Memeify(bot))
