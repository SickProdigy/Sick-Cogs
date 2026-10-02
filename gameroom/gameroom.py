import asyncio
import logging
import secrets
from typing import Dict, List, Optional, Tuple

import discord
from redbot.core import Config, bank, commands

from .games import (
    BlackjackGame,
    HigherLowerGame,
    draw_high_card,
    parse_dice,
    play_pass_line_craps,
)
from .views import BlackjackView, GameMenuView, HigherLowerView, OwnerView
from .wagering import WagerError, WagerManager


log = logging.getLogger("red.sick-cogs.GameRoom")
SessionKey = Tuple[int, int, int]
CONFIG_IDENTIFIER = 726104938105
GUILD_DEFAULTS = {
    "wagering_enabled": False,
    "min_wager": 10,
    "max_wager": 1000,
    "daily_loss_limit": 0,
    "daily_losses": {},
    "ledger": [],
}


class GameRoom(commands.Cog):
    """A unified room for Red's native games and additional free-play games."""

    __author__ = "SickProdigy"
    __version__ = "0.2.0"

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(
            self,
            identifier=CONFIG_IDENTIFIER,
            force_registration=True,
        )
        self.config.register_guild(**GUILD_DEFAULTS)
        self.wagers = WagerManager(self.config)
        self.active_sessions: Dict[SessionKey, OwnerView] = {}
        self._wager_locks: Dict[Tuple[int, int], asyncio.Lock] = {}

    async def cog_load(self) -> None:
        recovered = await self.wagers.recover_incomplete()
        if recovered:
            log.warning(
                "Marked %s interrupted GameRoom wager transactions uncertain.",
                recovered,
            )

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

    def _wager_lock(self, guild_id: int, user_id: int) -> asyncio.Lock:
        return self._wager_locks.setdefault((guild_id, user_id), asyncio.Lock())

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

    @staticmethod
    def build_craps_embed() -> discord.Embed:
        result = play_pass_line_craps()
        lines = [
            f"Roll {index}: {first} + {second} = **{first + second}**"
            for index, (first, second) in enumerate(result.rolls, start=1)
        ]
        embed = discord.Embed(
            title="🎲 Pass-Line Craps",
            description="\n".join(lines),
            color=discord.Color.green() if result.won else discord.Color.red(),
        )
        if result.point is not None:
            embed.add_field(name="Point", value=str(result.point))
        embed.add_field(name="Result", value=result.result, inline=False)
        embed.set_footer(text="Free play • Two six-sided dice")
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

    async def _settle_immediate_wager(
        self,
        ctx: commands.Context,
        game: str,
        amount: int,
        play,
    ):
        async with self._wager_lock(ctx.guild.id, ctx.author.id):
            try:
                record = await self.wagers.reserve(ctx.author, game, amount)
            except WagerError as exc:
                await ctx.send(str(exc))
                return None

            try:
                result = play()
                if result["refund"]:
                    settled = await self.wagers.refund(
                        ctx.author, record["id"], result["outcome"]
                    )
                    capped = False
                else:
                    gross = amount * 2 if result["won"] else 0
                    settled, capped = await self.wagers.settle(
                        ctx.author,
                        record["id"],
                        gross,
                        result["outcome"],
                    )
            except Exception:
                log.exception(
                    "Could not safely settle GameRoom wager %s", record["id"]
                )
                await ctx.send(
                    "The wager could not be safely settled. "
                    f"Transaction: `{record['id']}`. Ask the bot owner to review it."
                )
                return None
            return settled, capped, result

    @commands.group(name="gameroom", aliases=["games"], invoke_without_command=True)
    @commands.guild_only()
    async def gameroom(self, ctx: commands.Context) -> None:
        """Open the unified GameRoom launcher."""
        prefix = ctx.clean_prefix
        embed = discord.Embed(
            title="🎮 GameRoom",
            description=(
                "Choose a free-play game below, or use a command directly. "
                "Wagers use fictional Red bank currency and require server opt-in."
            ),
            color=await ctx.embed_color(),
        )
        embed.add_field(
            name="GameRoom",
            value=(
                f"`{prefix}dice [d20|2d6+3]` — bounded dice notation\n"
                f"`{prefix}coinflip [heads|tails] [amount]`\n"
                f"`{prefix}blackjack` — interactive blackjack\n"
                f"`{prefix}higherlower` — guess the next card\n"
                f"`{prefix}highcard [amount]` — draw against the dealer\n"
                f"`{prefix}craps [amount]` — pass-line craps\n"
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
            "coinflip": (
                "**Coin flip:** Free play uses no arguments. For an enabled wager, "
                "choose heads or tails and an amount. A correct choice pays even money."
            ),
            "blackjack": (
                "**Blackjack:** Get closer to 21 than the dealer without going over. "
                "Aces count as 1 or 11. The dealer stands on all 17s. Double draws "
                "exactly one card and then stands. Wagering is not enabled for blackjack yet."
            ),
            "higherlower": (
                "**Higher or Lower:** Guess whether the next rank is higher or lower. "
                "Aces are high, ties continue without scoring, and ten correct guesses wins."
            ),
            "highcard": (
                "**High Card:** You and the dealer draw once. The higher rank wins; "
                "aces are high and equal ranks tie/refund an enabled wager."
            ),
            "craps": (
                "**Pass-Line Craps:** On the come-out roll, 7 or 11 wins and 2, 3, "
                "or 12 loses. Any other total becomes the point. Roll the point again "
                "before 7 to win. Wagers pay even money."
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
    @commands.guild_only()
    @commands.cooldown(3, 5, commands.BucketType.user)
    async def coinflip(
        self,
        ctx: commands.Context,
        choice: Optional[str] = None,
        amount: Optional[int] = None,
    ) -> None:
        """Flip freely, or wager with coinflip <heads|tails> <amount>."""
        if choice is None and amount is None:
            await ctx.send(self.coinflip_text())
            return
        normalized = (choice or "").lower()
        normalized = {"head": "heads", "tail": "tails"}.get(normalized, normalized)
        if normalized not in {"heads", "tails"} or amount is None:
            await ctx.send(
                f"Use `{ctx.clean_prefix}coinflip <heads|tails> <amount>`, "
                "or omit all arguments for free play."
            )
            return

        result_name = "heads" if secrets.randbelow(2) == 0 else "tails"

        def play():
            won = normalized == result_name
            return {
                "won": won,
                "refund": False,
                "outcome": "win" if won else "loss",
            }

        settled = await self._settle_immediate_wager(
            ctx, "coinflip", amount, play
        )
        if settled is None:
            return
        record, capped, result = settled
        currency = await bank.get_currency_name(ctx.guild)
        if result["won"]:
            text = (
                f"🪙 **{result_name.title()}!** You won. "
                f"Paid **{record['actual_payout']:,} {currency}**."
            )
        else:
            text = f"🪙 **{result_name.title()}!** You lost **{amount:,} {currency}**."
        if capped:
            text += " The bank balance cap limited the payout."
        await ctx.send(text + f" Transaction: `{record['id']}`.")

    @commands.command(name="highcard")
    @commands.guild_only()
    @commands.cooldown(3, 5, commands.BucketType.user)
    async def highcard(
        self, ctx: commands.Context, amount: Optional[int] = None
    ) -> None:
        """Draw against the dealer freely or with an enabled wager."""
        if amount is None:
            embed = self.build_high_card_embed()
            embed.color = await ctx.embed_color()
            await ctx.send(embed=embed)
            return

        draw_holder = {}

        def play():
            draw = draw_high_card()
            draw_holder["draw"] = draw
            if draw.player.high_value == draw.dealer.high_value:
                return {"won": False, "refund": True, "outcome": "tie"}
            won = draw.player.high_value > draw.dealer.high_value
            return {
                "won": won,
                "refund": False,
                "outcome": "win" if won else "loss",
            }

        settled = await self._settle_immediate_wager(
            ctx, "highcard", amount, play
        )
        if settled is None:
            return
        record, capped, result = settled
        draw = draw_holder["draw"]
        currency = await bank.get_currency_name(ctx.guild)
        embed = discord.Embed(
            title="🎴 Wagered High Card",
            description=f"**{draw.result}**",
            color=await ctx.embed_color(),
        )
        embed.add_field(name="Your card", value=draw.player.label)
        embed.add_field(name="Dealer card", value=draw.dealer.label)
        if result["refund"]:
            settlement = f"Refunded **{amount:,} {currency}**."
        elif result["won"]:
            settlement = f"Paid **{record['actual_payout']:,} {currency}**."
        else:
            settlement = f"Lost **{amount:,} {currency}**."
        if capped:
            settlement += " The bank balance cap limited the payout."
        embed.add_field(name="Settlement", value=settlement, inline=False)
        embed.set_footer(text=f"Transaction {record['id']}")
        await ctx.send(embed=embed)

    @commands.command(name="craps")
    @commands.guild_only()
    @commands.cooldown(3, 5, commands.BucketType.user)
    async def craps(
        self, ctx: commands.Context, amount: Optional[int] = None
    ) -> None:
        """Play basic pass-line craps freely or with an enabled wager."""
        if amount is None:
            embed = self.build_craps_embed()
            embed.color = await ctx.embed_color()
            await ctx.send(embed=embed)
            return

        result_holder = {}

        def play():
            result = play_pass_line_craps()
            result_holder["result"] = result
            return {
                "won": result.won,
                "refund": False,
                "outcome": "win" if result.won else "loss",
            }

        settled = await self._settle_immediate_wager(ctx, "craps", amount, play)
        if settled is None:
            return
        record, capped, result_data = settled
        result = result_holder["result"]
        currency = await bank.get_currency_name(ctx.guild)
        lines = [
            f"Roll {index}: {first} + {second} = **{first + second}**"
            for index, (first, second) in enumerate(result.rolls, start=1)
        ]
        embed = discord.Embed(
            title="🎲 Wagered Pass-Line Craps",
            description="\n".join(lines),
            color=discord.Color.green() if result.won else discord.Color.red(),
        )
        if result.point is not None:
            embed.add_field(name="Point", value=str(result.point))
        if result_data["won"]:
            settlement = f"{result.result} Paid **{record['actual_payout']:,} {currency}**."
        else:
            settlement = f"{result.result} Lost **{amount:,} {currency}**."
        if capped:
            settlement += " The bank balance cap limited the payout."
        embed.add_field(name="Settlement", value=settlement, inline=False)
        embed.set_footer(text=f"Transaction {record['id']}")
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

    @commands.group(name="gameroomset", invoke_without_command=True)
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def gameroomset(self, ctx: commands.Context) -> None:
        """Configure GameRoom wagering for this server."""
        settings = await self.config.guild(ctx.guild).all()
        currency = await bank.get_currency_name(ctx.guild)
        uncertain = sum(
            1 for record in settings["ledger"] if record.get("state") == "uncertain"
        )
        await ctx.send(
            f"Wagering: **{'enabled' if settings['wagering_enabled'] else 'disabled'}**\n"
            f"Limits: **{settings['min_wager']:,}–{settings['max_wager']:,} {currency}**\n"
            f"Daily loss limit: **{settings['daily_loss_limit']:,} {currency}** "
            "(0 disables)\n"
            f"Ledger records: **{len(settings['ledger'])}** "
            f"({uncertain} uncertain)"
        )

    @gameroomset.command(name="wagering")
    async def set_wagering(self, ctx: commands.Context, enabled: bool) -> None:
        """Enable or disable fictional-currency wagering."""
        await self.config.guild(ctx.guild).wagering_enabled.set(enabled)
        await ctx.send(f"Wagering is now {'enabled' if enabled else 'disabled'}.")

    @gameroomset.command(name="minimum")
    async def set_minimum(self, ctx: commands.Context, amount: int) -> None:
        """Set the minimum wager."""
        group = self.config.guild(ctx.guild)
        maximum = await group.max_wager()
        if amount <= 0 or amount > maximum:
            await ctx.send(f"Choose an amount from 1 to {maximum:,}.")
            return
        await group.min_wager.set(amount)
        await ctx.send(f"Minimum wager set to {amount:,}.")

    @gameroomset.command(name="maximum")
    async def set_maximum(self, ctx: commands.Context, amount: int) -> None:
        """Set the maximum wager."""
        group = self.config.guild(ctx.guild)
        minimum = await group.min_wager()
        if amount < minimum:
            await ctx.send(f"Choose an amount of at least {minimum:,}.")
            return
        await group.max_wager.set(amount)
        await ctx.send(f"Maximum wager set to {amount:,}.")

    @gameroomset.command(name="dailyloss")
    async def set_daily_loss(self, ctx: commands.Context, amount: int) -> None:
        """Set the per-member daily loss limit; zero disables it."""
        if amount < 0:
            await ctx.send("The daily loss limit cannot be negative.")
            return
        await self.config.guild(ctx.guild).daily_loss_limit.set(amount)
        await ctx.send(f"Daily loss limit set to {amount:,}.")

    @gameroomset.command(name="ledger")
    async def wager_ledger(self, ctx: commands.Context, limit: int = 10) -> None:
        """Show recent wager metadata without game secrets."""
        limit = max(1, min(limit, 20))
        records = (await self.config.guild(ctx.guild).ledger())[-limit:]
        if not records:
            await ctx.send("The GameRoom wager ledger is empty.")
            return
        lines = [
            (
                f"`{record.get('id', '?')}` • {record.get('game', '?')} • "
                f"user {record.get('user_id', 'deleted')} • {record.get('stake', 0):,} • "
                f"{record.get('state', '?')} • {record.get('outcome') or 'pending'}"
            )
            for record in reversed(records)
        ]
        await ctx.send("\n".join(lines))

    async def red_delete_data_for_user(self, *, requester, user_id: int) -> None:
        """Anonymize completed wager records and remove daily-loss data."""
        await self.wagers.anonymize_user(user_id)
