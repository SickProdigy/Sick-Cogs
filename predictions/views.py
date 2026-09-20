"""Interactive setup and persistent entry controls for Predictions."""

import discord


class InteractionContext:
    """Small command-callback adapter so buttons and commands share one service path."""

    def __init__(self, interaction: discord.Interaction, *, ephemeral=True):
        self.interaction = interaction
        self.guild = interaction.guild
        self.author = interaction.user
        self.actual_channel = interaction.channel
        self.channel = self
        self.id = interaction.channel_id
        self.bot = interaction.client
        self.ephemeral = ephemeral

    async def send(self, content=None, **kwargs):
        kwargs.setdefault("ephemeral", self.ephemeral)
        if self.interaction.response.is_done():
            return await self.interaction.followup.send(content, **kwargs)
        return await self.interaction.response.send_message(content, **kwargs)


class StakeConfirmView(discord.ui.View):
    def __init__(self, cog, user_id: int, market_id: int, choice: int, amount: int):
        super().__init__(timeout=60)
        self.cog = cog
        self.user_id = user_id
        self.market_id = market_id
        self.choice = choice
        self.amount = amount

    @discord.ui.button(label="Confirm entry", style=discord.ButtonStyle.green)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This confirmation belongs to another member.", ephemeral=True)
            return
        for child in self.children:
            child.disabled = True
        await self.cog.predict_stake.callback(
            self.cog, InteractionContext(interaction), self.market_id, self.amount,
            outcome=str(self.choice + 1),
        )
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This confirmation belongs to another member.", ephemeral=True)
            return
        await interaction.response.edit_message(content="Entry cancelled; no credits were withdrawn.", view=None)
        self.stop()


class StakeAmountModal(discord.ui.Modal, title="Choose your play-credit entry"):
    amount = discord.ui.TextInput(label="Entry amount", placeholder="Enter a whole number", max_length=18)

    def __init__(self, cog, market_id: int, choice: int, minimum: int, maximum: int):
        super().__init__()
        self.cog = cog
        self.market_id = market_id
        self.choice = choice
        self.minimum = minimum
        self.maximum = maximum
        self.amount.placeholder = f"Whole number from {minimum} to {maximum}"[:100]

    async def on_submit(self, interaction: discord.Interaction):
        try:
            amount = int(str(self.amount))
        except ValueError:
            await interaction.response.send_message("Enter a whole-number stake.", ephemeral=True)
            return
        if not self.minimum <= amount <= self.maximum:
            await interaction.response.send_message(
                f"Entry must be from {self.minimum} to {self.maximum}.", ephemeral=True
            )
            return
        await self.cog.send_stake_confirmation(interaction, self.market_id, self.choice, amount)


class PredictionEntryView(discord.ui.View):
    def __init__(self, cog, guild_id: int, market, viewer_id=None, viewer_can_manage=False):
        super().__init__(timeout=None)
        self.cog = cog
        self.guild_id = guild_id
        self.market_id = market.market_id
        viewer_has_entry = viewer_id is not None and (
            str(viewer_id) in market.votes or str(viewer_id) in market.entries
        )
        if not viewer_has_entry:
            for index, outcome in enumerate(market.outcomes):
                button = discord.ui.Button(
                    label=outcome[:80], style=discord.ButtonStyle.primary,
                    custom_id=f"predictions:pick:{guild_id}:{market.market_id}:{index}",
                    disabled=not market.is_open(),
                )
                button.callback = self._callback(index)
                self.add_item(button)
        show_manage = (
            viewer_id is None or viewer_id == market.creator_id or viewer_can_manage
        )
        if show_manage:
            manage = discord.ui.Button(
                label="Manage", style=discord.ButtonStyle.secondary, row=1,
                custom_id=f"predictions:manage:{guild_id}:{market.market_id}",
            )
            manage.callback = self._manage
            self.add_item(manage)

    async def _manage(self, interaction: discord.Interaction):
        markets = await self.cog.config.guild(interaction.guild).markets()
        market = self.cog._market_from_raw(markets.get(str(self.market_id)))
        if market is None:
            await interaction.response.send_message("That prediction no longer exists.", ephemeral=True)
            return
        can_manage = (
            interaction.user.id == market.creator_id
            or interaction.user.guild_permissions.manage_guild
        )
        if not can_manage:
            await interaction.response.send_message(
                "Only the prediction creator or a server manager can manage it.", ephemeral=True
            )
            return
        await interaction.response.send_message(
            f"Manage prediction **#{self.market_id}**.",
            view=PredictionManageView(self.cog, interaction.user.id, market), ephemeral=True,
        )

    def _callback(self, choice: int):
        async def callback(interaction: discord.Interaction):
            if interaction.guild_id != self.guild_id:
                await interaction.response.send_message("This prediction belongs to another server.", ephemeral=True)
                return
            markets = await self.cog.config.guild(interaction.guild).markets()
            market = self.cog._market_from_raw(markets.get(str(self.market_id)))
            if market is None or not market.is_open():
                await interaction.response.send_message("Voting is closed for this prediction.", ephemeral=True)
                return
            if not market.uses_bank:
                await self.cog.predict_vote.callback(
                    self.cog, InteractionContext(interaction), self.market_id,
                    outcome=str(choice + 1),
                )
                return
            if market.stake_mode == "fixed":
                await self.cog.send_stake_confirmation(
                    interaction, self.market_id, choice, market.stake_min
                )
            else:
                await interaction.response.send_modal(
                    StakeAmountModal(
                        self.cog, self.market_id, choice, market.stake_min, market.stake_max
                    )
                )
        return callback


class PredictionManageView(discord.ui.View):
    def __init__(self, cog, user_id: int, market):
        super().__init__(timeout=120)
        self.cog = cog
        self.user_id = user_id
        self.market_id = market.market_id
        for index, outcome in enumerate(market.outcomes):
            button = discord.ui.Button(
                label=f"Resolve: {outcome}"[:80], style=discord.ButtonStyle.success,
                row=0, custom_id=f"prediction-manage-resolve-{index}",
            )
            button.callback = self._resolve(index)
            self.add_item(button)
        cancel_label = "Cancel and refund" if market.uses_bank else "Cancel prediction"
        cancel = discord.ui.Button(label=cancel_label, style=discord.ButtonStyle.danger, row=1)
        cancel.callback = self._cancel
        self.add_item(cancel)
        audit = discord.ui.Button(label="View audit", style=discord.ButtonStyle.secondary, row=1)
        audit.callback = self._audit
        self.add_item(audit)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.user_id:
            return True
        await interaction.response.send_message("This management panel belongs to another member.", ephemeral=True)
        return False

    def _resolve(self, choice: int):
        async def callback(interaction: discord.Interaction):
            await self.cog.predict_resolve.callback(
                self.cog, InteractionContext(interaction), self.market_id,
                outcome=str(choice + 1),
            )
            self.stop()
        return callback

    async def _cancel(self, interaction: discord.Interaction):
        await self.cog.predict_cancel.callback(
            self.cog, InteractionContext(interaction), self.market_id
        )
        self.stop()

    async def _audit(self, interaction: discord.Interaction):
        await self.cog.predict_audit.callback(
            self.cog, InteractionContext(interaction), self.market_id
        )



class CreatePredictionModal(discord.ui.Modal):
    duration = discord.ui.TextInput(label="Duration", placeholder="1d", max_length=8)
    question = discord.ui.TextInput(label="Question", max_length=240)
    outcomes = discord.ui.TextInput(
        label="Outcomes separated by |", placeholder="Yes | No",
        style=discord.TextStyle.paragraph, max_length=410,
    )
    stake = discord.ui.TextInput(
        label="Entry amount or range", placeholder="100 or 10-500", required=False, max_length=40,
    )

    def __init__(self, cog, user_id: int, mode: str, stake_min=0, stake_max=0):
        super().__init__(title=f"Start {mode} prediction")
        self.cog = cog
        self.user_id = user_id
        self.mode = mode
        self.stake_min = stake_min
        self.stake_max = stake_max
        if mode in {"free", "credit pool"}:
            self.remove_item(self.stake)

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This creator belongs to another member.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=False)
        definition = f"{self.question.value} | {self.outcomes.value}"
        ctx = InteractionContext(interaction, ephemeral=False)
        if self.mode == "free":
            await self.cog.predict_create.callback(
                self.cog, ctx, self.duration.value, definition=definition
            )
        else:
            await self.cog.predict_create_bank.callback(
                self.cog, ctx, self.duration.value,
                f"{self.stake_min}-{self.stake_max}", definition=definition
            )


class PredictionHomeView(discord.ui.View):
    def __init__(self, cog, user_id: int, bank_enabled: bool, stake_min=10, stake_max=10000):
        super().__init__(timeout=300)
        self.cog = cog
        self.user_id = user_id
        self.bank_enabled = bank_enabled
        self.stake_min = stake_min
        self.stake_max = stake_max

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.user_id:
            return True
        await interaction.response.send_message(
            "Run `predict` to open your own prediction menu.", ephemeral=True
        )
        return False

    @discord.ui.button(label="Start", emoji="➕", style=discord.ButtonStyle.success)
    async def start(self, interaction, button):
        await interaction.response.edit_message(
            content="What kind of prediction do you want to start?",
            embed=None,
            view=PredictionStartView(
                self.cog, self.user_id, self.bank_enabled, self.stake_min, self.stake_max
            ),
        )

    @discord.ui.button(label="Open", emoji="📊", style=discord.ButtonStyle.primary)
    async def open_markets(self, interaction, button):
        await self.cog.predict_list.callback(self.cog, InteractionContext(interaction))

    @discord.ui.button(label="Recent", emoji="🕘", style=discord.ButtonStyle.secondary)
    async def recent(self, interaction, button):
        await self.cog.predict_recent.callback(self.cog, InteractionContext(interaction))

    @discord.ui.button(label="Mine", emoji="👤", style=discord.ButtonStyle.secondary)
    async def mine(self, interaction, button):
        await self.cog.predict_mine.callback(self.cog, InteractionContext(interaction))

    @discord.ui.button(label="Leaderboard", emoji="🏆", style=discord.ButtonStyle.secondary, row=1)
    async def leaderboard(self, interaction, button):
        await self.cog.predict_leaderboard.callback(self.cog, InteractionContext(interaction))



class PredictionStartView(discord.ui.View):
    def __init__(self, cog, user_id: int, bank_enabled: bool, stake_min=10, stake_max=10000):
        super().__init__(timeout=300)
        self.cog = cog
        self.user_id = user_id
        self.stake_min = stake_min
        self.stake_max = stake_max
        if not bank_enabled:
            self.credit_pool.disabled = True

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.user_id:
            return True
        await interaction.response.send_message("This creator belongs to another member.", ephemeral=True)
        return False

    @discord.ui.button(label="Free prediction", style=discord.ButtonStyle.secondary)
    async def free(self, interaction, button):
        await interaction.response.send_modal(CreatePredictionModal(self.cog, self.user_id, "free"))

    @discord.ui.button(label="Credit pool", style=discord.ButtonStyle.primary)
    async def credit_pool(self, interaction, button):
        await interaction.response.send_modal(CreatePredictionModal(
            self.cog, self.user_id, "credit pool", self.stake_min, self.stake_max
        ))


class MarketBrowserSelect(discord.ui.Select):
    def __init__(self, browser):
        self.browser = browser
        start = browser.page * browser.page_size
        page_markets = browser.markets[start:start + browser.page_size]
        options = [
            discord.SelectOption(
                label=f"#{market.market_id} {market.question}"[:100],
                value=str(market.market_id),
                description=browser.option_description(market)[:100],
            )
            for market in page_markets
        ]
        super().__init__(placeholder="Choose a prediction to view or enter", options=options)

    async def callback(self, interaction: discord.Interaction):
        await self.browser.cog.predict_status.callback(
            self.browser.cog, InteractionContext(interaction), int(self.values[0])
        )


class MarketBrowserView(discord.ui.View):
    page_size = 20

    def __init__(self, cog, user_id: int, markets, mode: str, currency: str):
        super().__init__(timeout=300)
        self.cog = cog
        self.user_id = user_id
        self.all_markets = markets
        self.mode = mode
        self.currency = currency
        self.page = 0
        self.mine_tab = "created"
        self.markets = self._markets_for_tab()
        if mode == "mine" and not self.markets:
            self.mine_tab = "entered"
            self.markets = self._markets_for_tab()
        self._refresh_items()

    def _markets_for_tab(self):
        if self.mode != "mine":
            return self.all_markets
        if self.mine_tab == "created":
            return [market for market in self.all_markets if market.creator_id == self.user_id]
        user_id = str(self.user_id)
        return [
            market for market in self.all_markets
            if user_id in market.votes or user_id in market.entries
        ]

    @property
    def page_count(self):
        return max(1, (len(self.markets) + self.page_size - 1) // self.page_size)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.user_id:
            return True
        await interaction.response.send_message("Run `predict` to open your own browser.", ephemeral=True)
        return False

    @staticmethod
    def state_label(market):
        if market.is_resolved:
            return "resolved"
        if market.state in {"cancelled", "frozen"}:
            return market.state
        return "open" if market.is_open() else "voting closed"

    def option_description(self, market):
        state = self.state_label(market)
        if market.uses_bank:
            return f"{state} · {len(market.funded_entries())} people · {sum(market.pool_totals())} {self.currency}"
        return f"{state} · {len(market.votes)} votes · free"

    def embed(self):
        title = {"open": "Open predictions", "recent": "Recent predictions"}.get(
            self.mode, f"My predictions · {self.mine_tab.title()}"
        )
        start = self.page * self.page_size
        page_markets = self.markets[start:start + self.page_size]
        lines = [
            f"**#{market.market_id}** {market.question}\n{self.option_description(market)}"
            for market in page_markets
        ]
        embed = discord.Embed(
            title=title,
            description="\n\n".join(lines) or "No predictions in this view.",
            color=discord.Color.blurple(),
        )
        embed.set_footer(text=f"Page {self.page + 1}/{self.page_count} · Select a market to view or enter")
        return embed

    def _refresh_items(self):
        self.clear_items()
        if self.markets:
            self.add_item(MarketBrowserSelect(self))
        if self.mode == "mine":
            created = discord.ui.Button(
                label="Created", style=(
                    discord.ButtonStyle.primary if self.mine_tab == "created"
                    else discord.ButtonStyle.secondary
                )
            )
            created.callback = self._show_created
            self.add_item(created)
            entered = discord.ui.Button(
                label="Entered", style=(
                    discord.ButtonStyle.primary if self.mine_tab == "entered"
                    else discord.ButtonStyle.secondary
                )
            )
            entered.callback = self._show_entered
            self.add_item(entered)
        if self.page_count > 1:
            previous = discord.ui.Button(
                label="Previous", style=discord.ButtonStyle.secondary, disabled=self.page == 0
            )
            previous.callback = self._previous
            self.add_item(previous)
            next_page = discord.ui.Button(
                label="Next", style=discord.ButtonStyle.secondary,
                disabled=self.page + 1 >= self.page_count,
            )
            next_page.callback = self._next
            self.add_item(next_page)

    async def _set_mine_tab(self, interaction, tab):
        self.mine_tab = tab
        self.markets = self._markets_for_tab()
        self.page = 0
        self._refresh_items()
        await interaction.response.edit_message(embed=self.embed(), view=self)

    async def _show_created(self, interaction):
        await self._set_mine_tab(interaction, "created")

    async def _show_entered(self, interaction):
        await self._set_mine_tab(interaction, "entered")

    async def _previous(self, interaction):
        self.page = max(0, self.page - 1)
        self._refresh_items()
        await interaction.response.edit_message(embed=self.embed(), view=self)

    async def _next(self, interaction):
        self.page = min(self.page_count - 1, self.page + 1)
        self._refresh_items()
        await interaction.response.edit_message(embed=self.embed(), view=self)
