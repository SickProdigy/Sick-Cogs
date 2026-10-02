"""Private owner-bound controls for one immutable Polymarket trade approval."""

import time

import discord

from .trade_confirmation import TradeConfirmationState
from .trade_request import TradeApprovalRequest


class TradeApprovalView(discord.ui.View):
    def __init__(self, cog, request: TradeApprovalRequest):
        super().__init__(timeout=max(1.0, request.approval.expires_at - time.time()))
        self.cog = cog
        self.user_id = request.approval.requester_id
        self.request_id = request.request_id
        self.fingerprint = request.approval.fingerprint
        self.message = None
        self.processing = False
        controls = list(self.children)
        self.clear_items()
        state = request.confirmation.state
        if state is TradeConfirmationState.AWAITING_APPROVAL:
            self.add_item(controls[0])
            self.add_item(controls[3])
        elif state is TradeConfirmationState.AWAITING_FINAL_CONFIRMATION:
            self.add_item(controls[1])
            self.add_item(controls[2])

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.user_id:
            return True
        await interaction.response.send_message(
            "Only the account owner can use this Polymarket trade card.",
            ephemeral=True,
        )
        return False

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True
        if self.message is not None:
            await self.message.edit(view=self)

    async def _run(self, interaction: discord.Interaction, method: str) -> None:
        if self.processing:
            await interaction.response.send_message(
                "This trade request is already being checked.", ephemeral=True
            )
            return
        self.processing = True
        try:
            await getattr(self.cog, method)(interaction, self)
        finally:
            self.processing = False

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.success)
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._run(interaction, "approve_trade_interaction")

    @discord.ui.button(label="Yes, place trade", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._run(interaction, "confirm_trade_interaction")

    @discord.ui.button(label="No", style=discord.ButtonStyle.secondary)
    async def decline_final(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._run(interaction, "decline_trade_interaction")

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._run(interaction, "decline_trade_interaction")
