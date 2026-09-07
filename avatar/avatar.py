from typing import Optional, Union

import discord
from redbot.core import commands


class Avatar(commands.Cog):
    """Get user's avatar URL."""

    __author__ = ["SickProdigy", "skeith"]
    __version__ = "1.0.1"

    async def red_delete_data_for_user(self, **kwargs):
        """Nothing to delete."""
        return

    @commands.command()
    async def avatar(
        self,
        ctx: commands.Context,
        *,
        user: Optional[Union[discord.Member, discord.User]] = None,
    ):
        """Return a user's avatar URL.

        User argument can be user mention, nickname, username, user ID.
        Defaults to yourself when no argument is supplied.
        """
        user = user or ctx.author

        url = user.display_avatar.url

        await ctx.send(f"{user}'s avatar URL: {url}")
