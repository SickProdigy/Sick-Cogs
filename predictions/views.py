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

    @discord.ui.button(label="Confirm stake", style=discord.ButtonStyle.green)
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
        await interaction.response.edit_message(content="Stake cancelled; no credits were withdrawn.", view=None)
        self.stop()


class StakeAmountModal(discord.ui.Modal, title="Choose your play-credit stake"):
    amount = discord.ui.TextInput(label="Stake amount", placeholder="Enter a whole number", max_length=18)

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
                f"Stake must be from {self.minimum} to {self.maximum}.", ephemeral=True
            )
            return
        await self.cog.send_stake_confirmation(interaction, self.market_id, self.choice, amount)


class PredictionEntryView(discord.ui.View):
    def __init__(self, cog, guild_id: int, market):
        super().__init__(timeout=None)
        self.cog = cog
        self.guild_id = guild_id
        self.market_id = market.market_id
        for index, outcome in enumerate(market.outcomes):
            button = discord.ui.Button(
                label=outcome[:80], style=discord.ButtonStyle.primary,
                custom_id=f"predictions:pick:{guild_id}:{market.market_id}:{index}",
            )
            button.callback = self._callback(index)
            self.add_item(button)

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


class CreatePredictionModal(discord.ui.Modal):
    duration = discord.ui.TextInput(label="Duration", placeholder="1d", max_length=8)
    question = discord.ui.TextInput(label="Question", max_length=240)
    outcomes = discord.ui.TextInput(
        label="Outcomes separated by |", placeholder="Yes | No",
        style=discord.TextStyle.paragraph, max_length=410,
    )
    stake = discord.ui.TextInput(
        label="Stake or range", placeholder="100 or 10-500", required=False, max_length=40,
    )

    def __init__(self, cog, user_id: int, mode: str):
        super().__init__(title=f"Create {mode} prediction")
        self.cog = cog
        self.user_id = user_id
        self.mode = mode
        if mode == "free":
            self.remove_item(self.stake)

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This setup belongs to another member.", ephemeral=True)
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
                self.cog, ctx, self.duration.value, self.stake.value, definition=definition
            )


class PredictionSetupView(discord.ui.View):
    def __init__(self, cog, user_id: int, bank_enabled: bool):
        super().__init__(timeout=300)
        self.cog = cog
        self.user_id = user_id
        if not bank_enabled:
            self.fixed.disabled = True
            self.ranged.disabled = True

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.user_id:
            return True
        await interaction.response.send_message("This setup belongs to another member.", ephemeral=True)
        return False

    @discord.ui.button(label="Free prediction", style=discord.ButtonStyle.secondary)
    async def free(self, interaction, button):
        await interaction.response.send_modal(CreatePredictionModal(self.cog, self.user_id, "free"))

    @discord.ui.button(label="Fixed stake", style=discord.ButtonStyle.primary)
    async def fixed(self, interaction, button):
        await interaction.response.send_modal(CreatePredictionModal(self.cog, self.user_id, "fixed stake"))

    @discord.ui.button(label="Stake range", style=discord.ButtonStyle.primary)
    async def ranged(self, interaction, button):
        await interaction.response.send_modal(CreatePredictionModal(self.cog, self.user_id, "stake range"))
