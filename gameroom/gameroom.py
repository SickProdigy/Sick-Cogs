import secrets
from typing import List

import discord
from redbot.core import commands

from .games import BlackjackGame, HigherLowerGame, parse_dice
from .views import BlackjackView, GameMenuView, HigherLowerView


class GameRoom(commands.Cog):
    """A unified room for Red's native games and additional free-play games."""

    __author__ = "SickProdigy"
    __version__ = "0.1.0"

    def __init__(self, bot):
        self.bot = bot

    def _native_games(self, prefix: str) -> List[str]:
        games = []
        for command_name, label in (
            ("roll", "Roll"),
            ("flip", "Flip"),
            ("rps", "Rock Paper Scissors"),
            ("slot", "Slots"),
        ):
            if self.bot.get_command(command_name) is not None:
                games.append(f"`{prefix}{command_name}` — {label}")
        return games

    @commands.group(
        name="gameroom",
        aliases=["games"],
        invoke_without_command=True,
    )
    @commands.guild_only()
    async def gameroom(self, ctx: commands.Context) -> None:
        """Open the unified GameRoom launcher."""
        prefix = ctx.clean_prefix
        embed = discord.Embed(
            title="🎮 GameRoom",
            description=(
                "Pick a free-play game below, or use one of the commands directly. "
                "Bank wagering is not part of this initial release."
            ),
            color=await ctx.embed_color(),
        )
        embed.add_field(
            name="GameRoom",
            value=(
                f"`{prefix}dice [d20|2d6+3]` — bounded dice notation\n"
                f"`{prefix}coinflip` — heads or tails\n"
                f"`{prefix}blackjack` — interactive blackjack\n"
                f"`{prefix}higherlower` — guess the next card"
            ),
            inline=False,
        )
        native = self._native_games(prefix)
        if native:
            embed.add_field(
                name="Available Red games",
                value="\n".join(native),
                inline=False,
            )
        embed.set_footer(text="Free play • More games can be added later")
        view = GameMenuView(ctx.author.id, prefix)
        view.message = await ctx.send(embed=embed, view=view)

    @commands.command(name="dice")
    @commands.cooldown(3, 5, commands.BucketType.user)
    async def dice(self, ctx: commands.Context, notation: str = "d6") -> None:
        """Roll bounded dice notation such as d20, 2d6, or 2d8+3."""
        try:
            spec = parse_dice(notation)
        except ValueError as exc:
            await ctx.send(str(exc))
            return

        rolls, total = spec.roll()
        shown = ", ".join(str(value) for value in rolls)
        modifier = ""
        if spec.modifier:
            modifier = f" {spec.modifier:+d}"
        embed = discord.Embed(
            title=f"🎲 {spec.notation}",
            description=f"**Rolls:** {shown}\n**Total:** {sum(rolls)}{modifier} = **{total}**",
            color=await ctx.embed_color(),
        )
        await ctx.send(embed=embed)

    @commands.command(name="coinflip")
    @commands.cooldown(3, 5, commands.BucketType.user)
    async def coinflip(self, ctx: commands.Context) -> None:
        """Flip a fair coin using cryptographic randomness."""
        result = "Heads" if secrets.randbelow(2) == 0 else "Tails"
        await ctx.send(f"🪙 **{result}!**")

    @commands.command(name="blackjack", aliases=["21"])
    @commands.cooldown(2, 10, commands.BucketType.user)
    async def blackjack(self, ctx: commands.Context) -> None:
        """Start a requester-controlled free-play blackjack hand."""
        game = BlackjackGame()
        view = BlackjackView(ctx.author.id, game)
        if game.finished:
            await ctx.send(embed=view.embed())
            return
        view.message = await ctx.send(embed=view.embed(), view=view)

    @commands.command(name="higherlower", aliases=["highlow"])
    @commands.cooldown(2, 10, commands.BucketType.user)
    async def higherlower(self, ctx: commands.Context) -> None:
        """Guess whether the next card is higher or lower."""
        game = HigherLowerGame()
        view = HigherLowerView(ctx.author.id, game)
        view.message = await ctx.send(embed=view.embed(), view=view)

    async def red_delete_data_for_user(self, **kwargs) -> None:
        """GameRoom stores no end-user data."""
        return
