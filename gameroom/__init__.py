from redbot.core.utils import get_end_user_data_statement

from .gameroom import GameRoom


__red_end_user_data_statement__ = get_end_user_data_statement(__file__)

OPTIONAL_ALIASES = {
    "gameroom": ("games",),
    "blackjack": ("21",),
    "higherlower": ("highlow",),
}


def remove_conflicting_aliases(cog, bot):
    """Keep optional shortcuts only when another loaded command does not own them."""
    for command in cog.__cog_commands__:
        candidates = OPTIONAL_ALIASES.get(command.name, ())
        command.aliases = [
            alias for alias in candidates if bot.get_command(alias) is None
        ]


async def setup(bot):
    cog = GameRoom(bot)
    remove_conflicting_aliases(cog, bot)
    await bot.add_cog(cog)
