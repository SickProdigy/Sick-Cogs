"""Server-local prediction games with optional fictional Red Bank stakes."""

import asyncio
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Optional

import discord
from redbot.core import Config, bank, commands
from redbot.core.errors import BalanceTooHigh

from .models import PredictionMarket, calculate_payouts
from .views import PredictionEntryView, PredictionSetupView, StakeConfirmView


GUILD_DEFAULTS = {
    "markets": {}, "next_market_id": 1, "channel_id": None, "scores": {},
    "bank_enabled": False, "stake_min": 10, "stake_max": 10000,
    "exposure_limit": 50000, "house_cut_bps": 0, "treasury_user_id": None,
}
DURATION_RE = re.compile(r"^(?P<count>[1-9][0-9]*)(?P<unit>[mhdw])$", re.IGNORECASE)
DURATION_UNITS = {"m": 60, "h": 3600, "d": 86400, "w": 604800}


class Predictions(commands.Cog):
    """Create and settle free or optional play-credit prediction games."""

    __author__ = ["SickProdigy"]
    __version__ = "0.2.0"
    CONFIG_IDENTIFIER = 4471154655686372714528845515891787089043720058314721103476628161340921

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=self.CONFIG_IDENTIFIER, force_registration=True)
        self.config.register_guild(**GUILD_DEFAULTS)
        self._guild_locks = {}

    async def cog_load(self):
        for guild_id, settings in (await self.config.all_guilds()).items():
            for raw in settings.get("markets", {}).values():
                market = self._market_from_raw(raw)
                if market and market.is_open():
                    self.bot.add_view(PredictionEntryView(self, int(guild_id), market))

    def _guild_lock(self, guild_id: int):
        return self._guild_locks.setdefault(guild_id, asyncio.Lock())

    async def send_stake_confirmation(self, interaction, market_id: int, choice: int, amount: int):
        markets = await self.config.guild(interaction.guild).markets()
        market = self._market_from_raw(markets.get(str(market_id)))
        if market is None or not market.is_open() or not market.uses_bank:
            await interaction.response.send_message("Voting is closed for this prediction.", ephemeral=True)
            return
        if not market.stake_min <= amount <= market.stake_max:
            await interaction.response.send_message("That stake is outside this prediction's limits.", ephemeral=True)
            return
        currency = await bank.get_currency_name(interaction.guild)
        view = StakeConfirmView(self, interaction.user.id, market_id, choice, amount)
        await interaction.response.send_message(
            f"Confirm **{amount} {currency}** on **{market.outcomes[choice]}**?\n"
            "This is fictional server currency. It is refunded if the prediction is "
            "cancelled or has no winner; otherwise the losing pool is shared by winners.",
            view=view, ephemeral=True,
        )

    @staticmethod
    def parse_stake(value: str):
        value = value.strip()
        if value.isdigit():
            amount = int(value)
            return ("fixed", amount, amount) if amount > 0 else None
        parts = value.split("-", 1)
        if len(parts) == 2 and all(part.strip().isdigit() for part in parts):
            minimum, maximum = (int(part.strip()) for part in parts)
            if 0 < minimum <= maximum:
                return "range", minimum, maximum
        return None

    @staticmethod
    def _audit(market: PredictionMarket, event: str, **details):
        market.audit.append({
            "event": event, "at": datetime.now(timezone.utc).isoformat(), **details
        })
        market.audit = market.audit[-100:]

    async def red_delete_data_for_user(self, *, requester, user_id):
        for guild_id, settings in (await self.config.all_guilds()).items():
            changed = False
            markets = settings.get("markets", {})
            for raw in markets.values():
                if not isinstance(raw, dict):
                    continue
                votes = raw.get("votes", {})
                if str(user_id) in votes:
                    del votes[str(user_id)]
                    changed = True
            scores = settings.get("scores", {})
            if str(user_id) in scores:
                del scores[str(user_id)]
                changed = True
            if changed:
                await self.config.guild_from_id(guild_id).markets.set(markets)
                await self.config.guild_from_id(guild_id).scores.set(scores)

    @staticmethod
    def parse_duration(value: str) -> Optional[timedelta]:
        match = DURATION_RE.fullmatch(value.strip())
        if not match:
            return None
        duration = timedelta(seconds=int(match.group("count")) * DURATION_UNITS[match.group("unit").lower()])
        return duration if timedelta(minutes=1) <= duration <= timedelta(days=30) else None

    @staticmethod
    def parse_definition(value: str):
        parts = [part.strip() for part in value.split("|")]
        if len(parts) < 3:
            return None
        question, outcomes = parts[0], parts[1:]
        if not question or len(question) > 240 or not 2 <= len(outcomes) <= 5:
            return None
        if any(not outcome or len(outcome) > 80 for outcome in outcomes):
            return None
        if len({outcome.casefold() for outcome in outcomes}) != len(outcomes):
            return None
        return question, outcomes

    @staticmethod
    def _market_from_raw(raw) -> Optional[PredictionMarket]:
        try:
            return PredictionMarket.from_raw(raw)
        except ValueError:
            return None

    @staticmethod
    def outcome_index(market: PredictionMarket, choice: str) -> Optional[int]:
        if choice.isdigit():
            index = int(choice) - 1
            return index if 0 <= index < len(market.outcomes) else None
        normalized = choice.strip().casefold()
        matches = [index for index, outcome in enumerate(market.outcomes) if outcome.casefold() == normalized]
        return matches[0] if len(matches) == 1 else None

    @staticmethod
    def market_embed(market: PredictionMarket) -> discord.Embed:
        state = "Resolved" if market.is_resolved else ("Open" if market.is_open() else "Voting closed")
        lines = [f"{index + 1}. **{outcome}** — {count} vote(s)" for index, (outcome, count) in enumerate(zip(market.outcomes, market.vote_counts()))]
        embed = discord.Embed(title=f"Prediction #{market.market_id}", description=market.question, color=discord.Color.blurple())
        embed.add_field(name="Outcomes", value="\n".join(lines), inline=False)
        if market.is_resolved:
            embed.add_field(name="Result", value=market.outcomes[market.resolved_outcome], inline=False)
        else:
            embed.add_field(name="Voting closes", value=f"<t:{int(market.closes_at.timestamp())}:F>", inline=False)
        embed.set_footer(text=f"{state} • No money, tokens, or prizes")
        return embed

    @staticmethod
    async def market_embed_with_currency(market: PredictionMarket, guild) -> discord.Embed:
        embed = Predictions.market_embed(market)
        currency = await bank.get_currency_name(guild)
        stake = (
            str(market.stake_min) if market.stake_mode == "fixed"
            else f"{market.stake_min}–{market.stake_max}"
        )
        embed.add_field(
            name="Play-credit stake",
            value=(
                f"**{stake} {currency}** · losing pool is shared proportionally by winners. "
                "Cancelled or no-winner predictions refund accepted stakes."
            ), inline=False,
        )
        cut = market.house_cut_bps / 100
        embed.set_footer(text=f"Open · Fictional {currency} only · House cut {cut:g}%")
        return embed

    async def _save_market(self, guild, markets: dict, market: PredictionMarket):
        markets[str(market.market_id)] = market.to_raw()
        await self.config.guild(guild).markets.set(markets)

    async def _bank_account(self, guild, user_id: int):
        member = guild.get_member(user_id)
        if member is not None:
            return member
        if await bank.is_global():
            return self.bot.get_user(user_id)
        return None

    async def _apply_credit_operation(
        self, guild, markets: dict, market: PredictionMarket,
        operation_id: str, user_id: int, amount: int, kind: str,
    ) -> bool:
        operations = market.settlement.setdefault("operations", {})
        operation = operations.get(operation_id)
        if operation and operation.get("state") == "applied":
            return True
        account = await self._bank_account(guild, user_id)
        if account is None:
            operations[operation_id] = {
                "state": "blocked", "kind": kind, "user_id": user_id,
                "amount": amount, "reason": "account unavailable",
            }
            market.state = "frozen"
            self._audit(market, "credit_blocked", operation_id=operation_id, reason="account unavailable")
            await self._save_market(guild, markets, market)
            return False
        current = await bank.get_balance(account)
        if operation and operation.get("state") == "prepared":
            before = int(operation["balance_before"])
            if current == before + amount:
                operation["state"] = "applied"
                self._audit(market, "credit_reconciled", operation_id=operation_id)
                await self._save_market(guild, markets, market)
                return True
            if current != before:
                operation["state"] = "ambiguous"
                operation["observed_balance"] = current
                market.state = "frozen"
                self._audit(market, "credit_ambiguous", operation_id=operation_id)
                await self._save_market(guild, markets, market)
                return False
        else:
            operation = operations[operation_id] = {
                "state": "prepared", "kind": kind, "user_id": user_id,
                "amount": amount, "balance_before": current,
            }
            self._audit(market, "credit_prepared", operation_id=operation_id, amount=amount)
            await self._save_market(guild, markets, market)
        try:
            await bank.deposit_credits(account, amount)
        except BalanceTooHigh:
            operation["state"] = "blocked"
            operation["reason"] = "maximum balance"
            market.state = "frozen"
            self._audit(market, "credit_blocked", operation_id=operation_id, reason="maximum balance")
            await self._save_market(guild, markets, market)
            return False
        operation["state"] = "applied"
        self._audit(market, "credit_applied", operation_id=operation_id, amount=amount)
        await self._save_market(guild, markets, market)
        return True

    async def _finalize_bank_market(self, guild, market_id: int, winning_choice=None, *, cancelled=False):
        async with self._guild_lock(guild.id):
            markets = await self.config.guild(guild).markets()
            market = self._market_from_raw(markets.get(str(market_id)))
            if market is None or not market.uses_bank:
                return None, "That bank-backed prediction does not exist."
            if market.settlement.get("state") == "complete":
                return market, "This prediction's credits were already finalized."
            if not market.settlement:
                payouts, cut, refunded = calculate_payouts(
                    market.entries, None if cancelled else winning_choice, market.house_cut_bps
                )
                market.settlement = {
                    "id": f"{guild.id}:{market_id}:{'cancel' if cancelled else 'resolve'}",
                    "state": "planned", "winning_choice": winning_choice,
                    "cancelled": cancelled, "refunded": refunded,
                    "payouts": payouts, "house_cut": cut, "operations": {},
                }
                market.state = "frozen"
                self._audit(market, "settlement_planned", payouts=sum(payouts.values()), house_cut=cut)
                await self._save_market(guild, markets, market)
            plan = market.settlement
            for user_id, amount in plan.get("payouts", {}).items():
                operation_id = f"{plan['id']}:credit:{user_id}"
                if not await self._apply_credit_operation(
                    guild, markets, market, operation_id, int(user_id), int(amount),
                    "refund" if plan.get("refunded") or cancelled else "payout",
                ):
                    return market, "Finalization is frozen; review the prediction audit before retrying."
            cut = int(plan.get("house_cut", 0))
            if cut:
                treasury_id = market.treasury_user_id
                if not treasury_id or not await self._apply_credit_operation(
                    guild, markets, market, f"{plan['id']}:treasury", int(treasury_id), cut, "house_cut"
                ):
                    return market, "Finalization is frozen; the treasury credit needs review."
            plan["state"] = "complete"
            market.resolved_outcome = None if cancelled else winning_choice
            market.state = "cancelled" if cancelled else "resolved"
            for user_id, entry in market.entries.items():
                if entry.get("state") == "funded":
                    entry["state"] = "refunded" if plan.get("refunded") or cancelled else (
                        "paid" if user_id in plan.get("payouts", {}) else "lost"
                    )
            self._audit(market, "settlement_complete", state=market.state)
            await self._save_market(guild, markets, market)
            return market, None

    @commands.group(name="predict", aliases=["prediction", "predictions"], invoke_without_command=True)
    @commands.guild_only()
    async def predict(self, ctx):
        """Create and vote on server-local prediction games."""
        await ctx.send_help()

    @predict.command(name="setup")
    async def predict_setup(self, ctx):
        """Open interactive free or play-credit prediction creation."""
        enabled = await self.config.guild(ctx.guild).bank_enabled()
        await ctx.send(
            "Choose a prediction type. Play-credit choices are available only when enabled "
            "by a server administrator.",
            view=PredictionSetupView(self, ctx.author.id, enabled),
        )

    @predict.command(name="create")
    async def predict_create(self, ctx, duration: str, *, definition: str):
        """Create one: duration, question, then outcomes separated by |."""
        parsed_duration = self.parse_duration(duration)
        parsed_definition = self.parse_definition(definition)
        if parsed_duration is None or parsed_definition is None:
            await ctx.send("Use `predict create 1d Question? | Outcome one | Outcome two` (2–5 outcomes; 1 minute to 30 days).")
            return
        question, outcomes = parsed_definition
        now = datetime.now(timezone.utc)
        async with self.config.guild(ctx.guild).all() as settings:
            market_id = int(settings["next_market_id"])
            settings["next_market_id"] = market_id + 1
            market = PredictionMarket(market_id, ctx.guild.id, ctx.author.id, question, outcomes, now + parsed_duration, now)
            settings["markets"][str(market_id)] = market.to_raw()
            channel_id = settings.get("channel_id")
        destination = ctx.guild.get_channel(channel_id) if channel_id else ctx.channel
        if destination is None or not isinstance(destination, discord.TextChannel):
            destination = ctx.channel
        view = PredictionEntryView(self, ctx.guild.id, market)
        self.bot.add_view(view)
        await destination.send(embed=self.market_embed(market), view=view)
        if destination.id != ctx.channel.id:
            await ctx.send(f"Prediction #{market_id} was posted in {destination.mention}.")

    @predict.command(name="createbank", aliases=["createstaked"])
    async def predict_create_bank(self, ctx, duration: str, stake: str, *, definition: str):
        """Create a play-credit prediction using a fixed stake or min-max range."""
        settings = await self.config.guild(ctx.guild).all()
        if not settings.get("bank_enabled", False):
            await ctx.send("Bank-backed predictions are disabled in this server.")
            return
        parsed_duration = self.parse_duration(duration)
        parsed_definition = self.parse_definition(definition)
        parsed_stake = self.parse_stake(stake)
        if parsed_duration is None or parsed_definition is None or parsed_stake is None:
            await ctx.send(
                "Use `predict createbank 1d 100 Question? | Yes | No` for a fixed stake, "
                "or replace `100` with a range such as `10-500`."
            )
            return
        mode, minimum, maximum = parsed_stake
        if minimum < int(settings["stake_min"]) or maximum > int(settings["stake_max"]):
            await ctx.send(
                f"Stake limits for this server are {settings['stake_min']}–{settings['stake_max']}."
            )
            return
        question, outcomes = parsed_definition
        now = datetime.now(timezone.utc)
        async with self._guild_lock(ctx.guild.id):
            async with self.config.guild(ctx.guild).all() as current:
                market_id = int(current["next_market_id"])
                current["next_market_id"] = market_id + 1
                market = PredictionMarket(
                    market_id, ctx.guild.id, ctx.author.id, question, outcomes,
                    now + parsed_duration, now, stake_mode=mode,
                    stake_min=minimum, stake_max=maximum,
                    house_cut_bps=int(current.get("house_cut_bps", 0)),
                    treasury_user_id=current.get("treasury_user_id"),
                )
                current["markets"][str(market_id)] = market.to_raw()
                channel_id = current.get("channel_id")
        destination = ctx.guild.get_channel(channel_id) if channel_id else ctx.channel
        if not isinstance(destination, discord.TextChannel):
            destination = ctx.channel
        view = PredictionEntryView(self, ctx.guild.id, market)
        self.bot.add_view(view)
        await destination.send(
            embed=await self.market_embed_with_currency(market, ctx.guild), view=view
        )
        if destination.id != ctx.channel.id:
            await ctx.send(f"Prediction #{market_id} was posted in {destination.mention}.")

    @predict.command(name="stake")
    async def predict_stake(self, ctx, market_id: int, amount: int, *, outcome: str):
        """Confirm a play-credit stake and pick an outcome."""
        async with self._guild_lock(ctx.guild.id):
            markets = await self.config.guild(ctx.guild).markets()
            market = self._market_from_raw(markets.get(str(market_id)))
            if market is None or not market.uses_bank:
                await ctx.send("That bank-backed prediction does not exist.")
                return
            if not market.is_open():
                await ctx.send("Voting is closed for that prediction.")
                return
            choice = self.outcome_index(market, outcome)
            if choice is None or not market.stake_min <= amount <= market.stake_max:
                await ctx.send(
                    f"Choose a valid outcome and a stake from {market.stake_min} to {market.stake_max}."
                )
                return
            user_id = str(ctx.author.id)
            existing = market.entries.get(user_id)
            if existing and existing.get("state") == "funded":
                if int(existing.get("stake", 0)) != amount:
                    await ctx.send("Your accepted stake is locked; you may only change the outcome.")
                    return
                existing["choice"] = choice
                market.votes[user_id] = choice
                self._audit(market, "entry_choice_changed", user_id=user_id, choice=choice)
                markets[str(market_id)] = market.to_raw()
                await self.config.guild(ctx.guild).markets.set(markets)
                await ctx.send(f"Your funded pick is now **{market.outcomes[choice]}**.")
                return
            if existing and existing.get("state") == "prepared":
                prepared_amount = int(existing.get("stake", 0))
                before = int(existing.get("balance_before", -1))
                current_balance = await bank.get_balance(ctx.author)
                if prepared_amount != amount:
                    await ctx.send("A different stake is already awaiting reconciliation for this prediction.")
                    return
                if current_balance == before - amount:
                    existing["state"] = "funded"
                    existing["choice"] = choice
                    market.votes[user_id] = choice
                    self._audit(market, "withdrawal_reconciled", user_id=user_id, amount=amount)
                    markets[str(market_id)] = market.to_raw()
                    await self.config.guild(ctx.guild).markets.set(markets)
                    await ctx.send(f"Recovered your funded pick: **{market.outcomes[choice]}**.")
                    return
                if current_balance != before:
                    existing["state"] = "ambiguous"
                    existing["observed_balance"] = current_balance
                    market.state = "frozen"
                    self._audit(market, "withdrawal_ambiguous", user_id=user_id, amount=amount)
                    markets[str(market_id)] = market.to_raw()
                    await self.config.guild(ctx.guild).markets.set(markets)
                    await ctx.send("This entry is frozen for moderator reconciliation; no new charge was made.")
                    return
            exposure = sum(
                int(entry.get("stake", 0)) for raw in markets.values()
                for entry_user, entry in (raw.get("entries", {}) if isinstance(raw, dict) else {}).items()
                if entry_user == user_id and entry.get("state") == "funded"
            )
            limit = int(await self.config.guild(ctx.guild).exposure_limit())
            if exposure + amount > limit:
                await ctx.send(f"That would exceed the per-user exposure limit of {limit} credits.")
                return
            balance_before = await bank.get_balance(ctx.author)
            operation_id = f"{ctx.guild.id}:{market_id}:entry:{user_id}"
            market.entries[user_id] = {
                "choice": choice, "stake": amount, "state": "prepared",
                "withdrawal_id": operation_id, "balance_before": balance_before,
            }
            self._audit(market, "withdrawal_prepared", user_id=user_id, amount=amount, operation_id=operation_id)
            markets[str(market_id)] = market.to_raw()
            await self.config.guild(ctx.guild).markets.set(markets)
            try:
                await bank.withdraw_credits(ctx.author, amount)
            except ValueError:
                market.entries[user_id]["state"] = "failed"
                self._audit(market, "withdrawal_failed", user_id=user_id, amount=amount)
                markets[str(market_id)] = market.to_raw()
                await self.config.guild(ctx.guild).markets.set(markets)
                await ctx.send("You do not have enough credits for that stake.")
                return
            market.entries[user_id]["state"] = "funded"
            market.votes[user_id] = choice
            self._audit(market, "withdrawal_applied", user_id=user_id, amount=amount, operation_id=operation_id)
            markets[str(market_id)] = market.to_raw()
            try:
                await self.config.guild(ctx.guild).markets.set(markets)
            except Exception:
                await bank.deposit_credits(ctx.author, amount)
                market.entries[user_id]["state"] = "recovered"
                self._audit(market, "withdrawal_recovered", user_id=user_id, amount=amount)
                markets[str(market_id)] = market.to_raw()
                try:
                    await self.config.guild(ctx.guild).markets.set(markets)
                finally:
                    raise
        currency = await bank.get_currency_name(ctx.guild)
        await ctx.send(
            f"Confirmed: **{amount} {currency}** on **{market.outcomes[choice]}**. "
            "The stake is refunded if the prediction is cancelled or has no winner."
        )

    @predict.command(name="vote", aliases=["pick"])
    async def predict_vote(self, ctx, market_id: int, *, outcome: str):
        """Vote for one outcome. A later vote replaces your earlier choice."""
        async with self.config.guild(ctx.guild).markets() as markets:
            market = self._market_from_raw(markets.get(str(market_id)))
            if market is None:
                await ctx.send("That prediction does not exist.")
                return
            if not market.is_open():
                await ctx.send("Voting is closed for that prediction.")
                return
            if market.uses_bank:
                await ctx.send(f"Use `predict stake {market_id} <amount> <outcome>` for this prediction.")
                return
            index = self.outcome_index(market, outcome)
            if index is None:
                await ctx.send("Choose an outcome number or its exact name. Use `predict show <market-id>` to view choices.")
                return
            market.votes[str(ctx.author.id)] = index
            markets[str(market_id)] = market.to_raw()
        await ctx.send(f"Your pick for prediction #{market_id}: **{market.outcomes[index]}**.")

    @predict.command(name="show", aliases=["market"])
    async def predict_show(self, ctx, market_id: int):
        """Show a prediction and its current vote totals."""
        market = self._market_from_raw((await self.config.guild(ctx.guild).markets()).get(str(market_id)))
        if market is None:
            await ctx.send("That prediction does not exist.")
            return
        embed = (
            await self.market_embed_with_currency(market, ctx.guild)
            if market.uses_bank else self.market_embed(market)
        )
        view = PredictionEntryView(self, ctx.guild.id, market) if market.is_open() else None
        await ctx.send(embed=embed, view=view)

    @predict.command(name="list")
    async def predict_list(self, ctx):
        """List open predictions in this server."""
        markets = [self._market_from_raw(raw) for raw in (await self.config.guild(ctx.guild).markets()).values()]
        open_markets = sorted((market for market in markets if market and market.is_open()), key=lambda market: market.closes_at)
        if not open_markets:
            await ctx.send("There are no open predictions in this server.")
            return
        lines = [f"**#{market.market_id}** {market.question} — closes <t:{int(market.closes_at.timestamp())}:R>" for market in open_markets[:20]]
        await ctx.send(embed=discord.Embed(title="Open predictions", description="\n".join(lines), color=discord.Color.blurple()))

    @predict.command(name="settle", aliases=["resolve"])
    async def predict_settle(self, ctx, market_id: int, *, outcome: str):
        """Settle a closed prediction; its creator or a server manager may do so."""
        preview = self._market_from_raw(
            (await self.config.guild(ctx.guild).markets()).get(str(market_id))
        )
        if preview and preview.uses_bank:
            is_manager = ctx.author.guild_permissions.manage_guild
            if preview.creator_id != ctx.author.id and not is_manager:
                await ctx.send("Only the prediction creator or a server manager can settle it.")
                return
            if preview.is_open() and not is_manager:
                await ctx.send("The prediction creator can settle it after voting closes.")
                return
            index = self.outcome_index(preview, outcome)
            if index is None:
                await ctx.send("Choose an outcome number or its exact name.")
                return
            market, error = await self._finalize_bank_market(ctx.guild, market_id, index)
            if error:
                await ctx.send(error)
                return
            currency = await bank.get_currency_name(ctx.guild)
            await ctx.send(
                f"Prediction #{market_id} resolved: **{market.outcomes[index]}**. "
                f"The {currency} pool has been finalized."
            )
            return
        async with self.config.guild(ctx.guild).all() as settings:
            market = self._market_from_raw(settings["markets"].get(str(market_id)))
            if market is None:
                await ctx.send("That prediction does not exist.")
                return
            is_manager = ctx.author.guild_permissions.manage_guild
            if market.creator_id != ctx.author.id and not is_manager:
                await ctx.send("Only the prediction creator or a server manager can settle it.")
                return
            if market.is_resolved and not is_manager:
                await ctx.send("That prediction is already resolved. A server manager may correct it if needed.")
                return
            if market.is_open() and not is_manager:
                await ctx.send("The prediction creator can settle it after voting closes. A server manager may resolve it early if needed.")
                return
            index = self.outcome_index(market, outcome)
            if index is None:
                await ctx.send("Choose an outcome number or its exact name.")
                return
            previous = market.resolved_outcome
            if previous is not None:
                for user_id, choice in market.votes.items():
                    if choice == previous:
                        settings["scores"][user_id] = max(0, int(settings["scores"].get(user_id, 0)) - 1)
            market.resolved_outcome = index
            for user_id, choice in market.votes.items():
                if choice == index:
                    settings["scores"][user_id] = int(settings["scores"].get(user_id, 0)) + 1
            settings["markets"][str(market_id)] = market.to_raw()
        await ctx.send(f"Prediction #{market_id} resolved: **{market.outcomes[index]}**. Correct picks earned one server point.")

    @predict.command(name="cancel", aliases=["invalidate"])
    async def predict_cancel(self, ctx, market_id: int):
        """Cancel a prediction and refund every accepted play-credit stake."""
        market = self._market_from_raw(
            (await self.config.guild(ctx.guild).markets()).get(str(market_id))
        )
        if market is None:
            await ctx.send("That prediction does not exist.")
            return
        is_manager = ctx.author.guild_permissions.manage_guild
        if market.creator_id != ctx.author.id and not is_manager:
            await ctx.send("Only the prediction creator or a server manager can cancel it.")
            return
        if not market.uses_bank:
            await ctx.send("Free predictions cannot currently be cancelled; settle or leave it closed.")
            return
        _, error = await self._finalize_bank_market(ctx.guild, market_id, cancelled=True)
        await ctx.send(error or f"Prediction #{market_id} cancelled; accepted stakes were refunded.")

    @predict.command(name="audit")
    @commands.admin_or_permissions(manage_guild=True)
    async def predict_audit(self, ctx, market_id: int):
        """Reconcile a prediction pool and its bounded Bank operation journal."""
        market = self._market_from_raw(
            (await self.config.guild(ctx.guild).markets()).get(str(market_id))
        )
        if market is None:
            await ctx.send("That prediction does not exist.")
            return
        funded_pool = sum(
            int(entry.get("stake", 0)) for entry in market.entries.values()
            if entry.get("state") in {"funded", "paid", "lost", "refunded"}
        )
        settlement = market.settlement or {}
        payouts = sum(int(value) for value in settlement.get("payouts", {}).values())
        cut = int(settlement.get("house_cut", 0))
        operations = settlement.get("operations", {})
        states = {}
        for operation in operations.values():
            state = operation.get("state", "unknown")
            states[state] = states.get(state, 0) + 1
        state_text = ", ".join(f"{key}: {value}" for key, value in sorted(states.items())) or "none"
        recent = market.audit[-10:]
        events = "\n".join(
            f"• {item.get('event', 'unknown')} · {item.get('at', 'unknown')}" for item in recent
        ) or "No journal events."
        await ctx.send(
            f"**Prediction #{market_id} Bank audit**\n"
            f"State: **{market.state}** · Pool: **{funded_pool}** · "
            f"Planned payouts: **{payouts}** · Treasury cut: **{cut}**\n"
            f"Reconciliation: **{payouts + cut}/{funded_pool}** · Operations: {state_text}\n"
            f"{events}",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @predict.command(name="leaderboard", aliases=["scores"])
    async def predict_leaderboard(self, ctx):
        """Show this server's prediction leaderboard."""
        scores = await self.config.guild(ctx.guild).scores()
        ranked = sorted(((int(user_id), int(score)) for user_id, score in scores.items() if int(score) > 0), key=lambda item: (-item[1], item[0]))
        if not ranked:
            await ctx.send("No prediction points have been earned yet.")
            return
        lines = []
        for position, (user_id, score) in enumerate(ranked[:20], start=1):
            member = ctx.guild.get_member(user_id)
            name = member.mention if member else f"Former member ({user_id})"
            lines.append(f"{position}. {name} — **{score}** point(s)")
        await ctx.send(embed=discord.Embed(title="Prediction leaderboard", description="\n".join(lines), color=discord.Color.gold()))

    @commands.group(name="predictset", aliases=["predictionset"], invoke_without_command=True)
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def predictset(self, ctx):
        """Configure this server's prediction game."""
        await ctx.send_help()

    @predictset.group(name="bank", invoke_without_command=True)
    async def predictset_bank(self, ctx):
        """Configure optional Red Bank-backed prediction stakes."""
        settings = await self.config.guild(ctx.guild).all()
        currency = await bank.get_currency_name(ctx.guild)
        scope = "global" if await bank.is_global() else "server-local"
        treasury = settings.get("treasury_user_id")
        await ctx.send(
            f"Bank predictions: **{'ON' if settings.get('bank_enabled') else 'OFF'}** · "
            f"{scope} {currency}\nStake limits: **{settings['stake_min']}–{settings['stake_max']}** · "
            f"Exposure: **{settings['exposure_limit']}** · House cut: "
            f"**{int(settings.get('house_cut_bps', 0)) / 100:g}%** · "
            f"Treasury: {f'<@{treasury}>' if treasury else 'not configured'}",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @predictset_bank.command(name="enable")
    async def predictset_bank_enable(self, ctx):
        """Enable play-credit predictions; global Bank requires the bot owner."""
        if await bank.is_global() and not await ctx.bot.is_owner(ctx.author):
            await ctx.send("Only a bot owner may enable predictions against the global Bank.")
            return
        await self.config.guild(ctx.guild).bank_enabled.set(True)
        await ctx.send("Bank-backed predictions are enabled. Existing free predictions are unchanged.")

    @predictset_bank.command(name="disable")
    async def predictset_bank_disable(self, ctx):
        """Disable creation of new Bank-backed predictions."""
        await self.config.guild(ctx.guild).bank_enabled.set(False)
        await ctx.send("New Bank-backed predictions are disabled. Existing funded markets must still be finalized.")

    @predictset_bank.command(name="limits")
    async def predictset_bank_limits(self, ctx, minimum: int, maximum: int, exposure: int = None):
        """Set minimum, maximum, and optional per-user total exposure."""
        exposure = maximum * 5 if exposure is None else exposure
        if minimum < 1 or maximum < minimum or exposure < maximum:
            await ctx.send("Use positive limits with minimum ≤ maximum ≤ exposure.")
            return
        await self.config.guild(ctx.guild).stake_min.set(minimum)
        await self.config.guild(ctx.guild).stake_max.set(maximum)
        await self.config.guild(ctx.guild).exposure_limit.set(exposure)
        await ctx.send(f"Stake limits are {minimum}–{maximum}; per-user exposure is {exposure} credits.")

    @predictset_bank.command(name="housecut")
    async def predictset_bank_housecut(
        self, ctx, percent: str, treasury: discord.Member = None
    ):
        """Set a zero-to-25% losing-pool cut and its treasury member; zero disables it."""
        try:
            basis_points = int(Decimal(percent) * 100)
        except (InvalidOperation, ValueError, OverflowError):
            basis_points = -1
        if not 0 <= basis_points <= 2500:
            await ctx.send("House cut must be between 0 and 25 percent.")
            return
        if basis_points and treasury is None:
            await ctx.send("Choose a treasury member whenever the house cut is above zero.")
            return
        await self.config.guild(ctx.guild).house_cut_bps.set(basis_points)
        await self.config.guild(ctx.guild).treasury_user_id.set(
            treasury.id if basis_points else None
        )
        await ctx.send(
            "House cut disabled." if not basis_points else
            f"House cut set to {basis_points / 100:g}% of the losing pool for {treasury.mention}."
        )

    @predictset.command(name="channel")
    async def predictset_channel(self, ctx, channel: discord.TextChannel):
        """Send new predictions to a dedicated channel."""
        await self.config.guild(ctx.guild).channel_id.set(channel.id)
        await ctx.send(f"New predictions will post in {channel.mention}.")

    @predictset.command(name="channelclear", aliases=["channeloff"])
    async def predictset_channel_clear(self, ctx):
        """Post new predictions in the command channel instead."""
        await self.config.guild(ctx.guild).channel_id.set(None)
        await ctx.send("New predictions will post in the channel where they are created.")
