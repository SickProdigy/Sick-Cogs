"""Owner-bound Discord controls for exact active-order cancellation."""

import time

import discord

from .order_action import CancelApprovalRequest
from .trade_confirmation import TradeConfirmationState


class CancelApprovalView(discord.ui.View):
    def __init__(self, cog, request: CancelApprovalRequest):
        super().__init__(timeout=max(1.0, request.expires_at - time.time()))
        self.cog = cog
        self.user_id = request.requester_id
        self.request_id = request.request_id
        self.fingerprint = request.fingerprint
        self.message = None
        self.processing = False
        controls = list(self.children)
        self.clear_items()
        if request.confirmation.state is TradeConfirmationState.AWAITING_APPROVAL:
            self.add_item(controls[0])
            self.add_item(controls[3])
        elif (
            request.confirmation.state
            is TradeConfirmationState.AWAITING_FINAL_CONFIRMATION
        ):
            self.add_item(controls[1])
            self.add_item(controls[2])

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.user_id:
            return True
        await interaction.response.send_message(
            "Only the account owner can use this cancellation card.",
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
                "This cancellation is already being checked.", ephemeral=True
            )
            return
        self.processing = True
        try:
            await getattr(self.cog, method)(interaction, self)
        finally:
            self.processing = False

    @discord.ui.button(label="Cancel order", style=discord.ButtonStyle.danger)
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._run(interaction, "approve_cancel_interaction")

    @discord.ui.button(label="Yes, cancel order", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._run(interaction, "confirm_cancel_interaction")

    @discord.ui.button(label="No", style=discord.ButtonStyle.secondary)
    async def decline_final(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._run(interaction, "decline_cancel_interaction")

    @discord.ui.button(label="Keep order", style=discord.ButtonStyle.secondary)
    async def keep(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._run(interaction, "decline_cancel_interaction")
