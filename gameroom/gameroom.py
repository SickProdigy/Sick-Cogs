import secrets
from typing import Dict, List, Optional, Tuple

import discord
from redbot.core import commands

from .games import BlackjackGame, HigherLowerGame, draw_high_card, parse_dice
from .views import BlackjackView, GameMenuView, HigherLowerView, OwnerView


SessionKey = Tuple[int, int, int]


class GameRoom(commands.Cog):
    """A unified room for Red's native games and additional free-play games."""

    __author__ = "SickProdigy"
    __version__ = "0.1.1"

    def __init__(self, bot):
        self.bot = bot
        self.active_sessions: Dict[SessionKey, OwnerView] = {}

    def cog_unload(self) -> None:
        for view in self.active_sessions.values():
            view.stop()
        self.active_sessions.clear()

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

    @staticmethod
    def _session_key(
        guild_id: Optional[int], channel_id: int, user_id: int
    ) -> SessionKey:
        return (guild_id or 0, channel_id, user_id)

    def _release_session(self, key: SessionKey, view: OwnerView) -> None:
        if self.active_sessions.get(key) is view:
            self.active_sessions.pop(key, None)

    def _claim_session(self, key: SessionKey) -> bool:
        return key not in self.active_sessions

    @staticmethod
    def coinflip_text() -> str:
        result = "Heads" if secrets.randbelow(2) == 0 else "Tails"
        return f"🪙 **{result}!**"

    @staticmethod
    def build_dice_embed(notation: str) -> discord.Embed:
        spec = parse_dice(notation)
        rolls, total = spec.roll()
        shown = ", ".join(str(value) for value in rolls)
        modifier = f" {spec.modifier:+d}" if spec.modifier else ""
        return discord.Embed(
            title=f"🎲 {spec.notation}",
            description=(
                f"**Rolls:** {shown}\n"
                f"**Total:** {sum(rolls)}{modifier} = **{total}**"
            ),
            color=discord.Color.blurple(),
        )

    @staticmethod
    def build_high_card_embed() -> discord.Embed:
        draw = draw_high_card()
        embed = discord.Embed(
            title="🎴 High Card",
            description=f"**{draw.result}**",
            color=discord.Color.dark_gold(),
        )
        embed.add_field(name="Your card", value=draw.player.label)
        embed.add_field(name="Dealer card", value=draw.dealer.label)
        embed.set_footer(text="Free play • Aces are high")
        return embed

    def _new_blackjack_view(self, key: SessionKey, user_id: int) -> BlackjackView:
        view = BlackjackView(user_id, BlackjackGame())
        view._on_release = lambda: self._release_session(key, view)
        return view

    def _new_higher_lower_view(self, key: SessionKey, user_id: int) -> HigherLowerView:
        view = HigherLowerView(user_id, HigherLowerGame())
        view._on_release = lambda: self._release_session(key, view)
        return view

    async def _start_blackjack_context(self, ctx: commands.Context) -> None:
        key = self._session_key(
            ctx.guild.id if ctx.guild else None, ctx.channel.id, ctx.author.id
        )
        if not self._claim_session(key):
            await ctx.send("Finish or quit your active game in this channel first.")
            return
        view = self._new_blackjack_view(key, ctx.author.id)
        self.active_sessions[key] = view
        try:
            view.message = await ctx.send(embed=view.embed(), view=view)
        except Exception:
            self._release_session(key, view)
            raise

    async def _start_higher_lower_context(self, ctx: commands.Context) -> None:
        key = self._session_key(
            ctx.guild.id if ctx.guild else None, ctx.channel.id, ctx.author.id
        )
        if not self._claim_session(key):
            await ctx.send("Finish or quit your active game in this channel first.")
            return
        view = self._new_higher_lower_view(key, ctx.author.id)
        self.active_sessions[key] = view
        try:
            view.message = await ctx.send(embed=view.embed(), view=view)
        except Exception:
            self._release_session(key, view)
            raise

    async def start_blackjack_interaction(
        self, interaction: discord.Interaction
    ) -> bool:
        key = self._session_key(
            interaction.guild_id, interaction.channel_id, interaction.user.id
        )
        if not self._claim_session(key):
            await interaction.response.send_message(
                "Finish or quit your active game in this channel first.",
                ephemeral=True,
            )
            return False
        view = self._new_blackjack_view(key, interaction.user.id)
        self.active_sessions[key] = view
        view.message = interaction.message
        try:
            await interaction.response.edit_message(
                content=None, embed=view.embed(), view=view
            )
        except Exception:
            self._release_session(key, view)
            raise
        return True

    async def start_higher_lower_interaction(
        self, interaction: discord.Interaction
    ) -> bool:
        key = self._session_key(
            interaction.guild_id, interaction.channel_id, interaction.user.id
        )
        if not self._claim_session(key):
            await interaction.response.send_message(
                "Finish or quit your active game in this channel first.",
                ephemeral=True,
            )
            return False
        view = self._new_higher_lower_view(key, interaction.user.id)
        self.active_sessions[key] = view
        view.message = interaction.message
        try:
            await interaction.response.edit_message(
                content=None, embed=view.embed(), view=view
            )
        except Exception:
            self._release_session(key, view)
            raise
        return True

    @commands.group(name="gameroom", aliases=["games"], invoke_without_command=True)
    @commands.guild_only()
    async def gameroom(self, ctx: commands.Context) -> None:
        """Open the unified GameRoom launcher."""
        prefix = ctx.clean_prefix
        embed = discord.Embed(
            title="🎮 GameRoom",
            description=(
                "Choose a free-play game below, or use a command directly. "
                "Bank wagering is not part of this release."
            ),
            color=await ctx.embed_color(),
        )
        embed.add_field(
            name="GameRoom",
            value=(
                f"`{prefix}dice [d20|2d6+3]` — bounded dice notation\n"
                f"`{prefix}coinflip` — heads or tails\n"
                f"`{prefix}blackjack` — interactive blackjack\n"
                f"`{prefix}higherlower` — guess the next card\n"
                f"`{prefix}highcard` — draw against the dealer\n"
                f"`{prefix}games rules [game]` — concise rules"
            ),
            inline=False,
        )
        native = self._native_games(prefix)
        if native:
            embed.add_field(
                name="Available Red games", value="\n".join(native), inline=False
            )
        embed.set_footer(text="Free play • Buttons are locked to the requester")
        view = GameMenuView(ctx.author.id, prefix, self)
        view.message = await ctx.send(embed=embed, view=view)

    @gameroom.command(name="rules")
    async def rules(self, ctx: commands.Context, game: Optional[str] = None) -> None:
        """Show concise rules for a GameRoom game."""
        rules = {
            "dice": (
                "**Dice:** Use d20, 2d6, or 2d8+3. Rolls are limited to "
                "20 dice, 2–1,000 sides, and modifiers from -10,000 to 10,000."
            ),
            "coinflip": "**Coin flip:** Produces heads or tails with equal probability.",
            "blackjack": (
                "**Blackjack:** Get closer to 21 than the dealer without going over. "
                "Aces count as 1 or 11. The dealer stands on all 17s. Double draws "
                "exactly one card and then stands. Split, insurance, and surrender "
                "are not included."
            ),
            "higherlower": (
                "**Higher or Lower:** Guess whether the next rank is higher or lower. "
                "Aces are high, ties continue without scoring, and ten correct guesses wins."
            ),
            "highcard": (
                "**High Card:** You and the dealer draw once. The higher rank wins; "
                "aces are high and equal ranks tie."
            ),
        }
        aliases = {"21": "blackjack", "highlow": "higherlower", "coin": "coinflip"}
        if game is None:
            await ctx.send(
                "Available rules: " + ", ".join(f"`{name}`" for name in rules)
            )
            return
        selected = aliases.get(game.lower(), game.lower())
        text = rules.get(selected)
        if text is None:
            await ctx.send("Unknown game. Choose: " + ", ".join(rules))
            return
        await ctx.send(text)

    @commands.command(name="dice")
    @commands.cooldown(3, 5, commands.BucketType.user)
    async def dice(self, ctx: commands.Context, notation: str = "d6") -> None:
        """Roll bounded dice notation such as d20, 2d6, or 2d8+3."""
        try:
            embed = self.build_dice_embed(notation)
        except ValueError as exc:
            await ctx.send(str(exc))
            return
        embed.color = await ctx.embed_color()
        await ctx.send(embed=embed)

    @commands.command(name="coinflip")
    @commands.cooldown(3, 5, commands.BucketType.user)
    async def coinflip(self, ctx: commands.Context) -> None:
        """Flip a fair coin using cryptographic randomness."""
        await ctx.send(self.coinflip_text())

    @commands.command(name="highcard")
    @commands.cooldown(3, 5, commands.BucketType.user)
    async def highcard(self, ctx: commands.Context) -> None:
        """Draw one card against the dealer."""
        embed = self.build_high_card_embed()
        embed.color = await ctx.embed_color()
        await ctx.send(embed=embed)

    @commands.command(name="blackjack", aliases=["21"])
    @commands.cooldown(2, 10, commands.BucketType.user)
    async def blackjack(self, ctx: commands.Context) -> None:
        """Start a requester-controlled free-play blackjack hand."""
        await self._start_blackjack_context(ctx)

    @commands.command(name="higherlower", aliases=["highlow"])
    @commands.cooldown(2, 10, commands.BucketType.user)
    async def higherlower(self, ctx: commands.Context) -> None:
        """Guess whether the next card is higher or lower."""
        await self._start_higher_lower_context(ctx)

    async def red_delete_data_for_user(self, **kwargs) -> None:
        """GameRoom stores no end-user data."""
        return
