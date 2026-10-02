"""Private owner-bound controls for Polymarket session authorization."""

import time

import discord

from .session_approval import SessionApprovalRequest, SessionApprovalState


class SessionApprovalView(discord.ui.View):
    def __init__(self, cog, request: SessionApprovalRequest, eligibility_url: str | None = None):
        super().__init__(timeout=max(1.0, request.expires_at - time.time()))
        self.cog = cog
        self.user_id = request.requester_id
        self.request_id = request.request_id
        self.fingerprint = request.fingerprint
        self.message = None
        self.processing = False
        controls = list(self.children)
        self.clear_items()
        if request.state is SessionApprovalState.AWAITING_ELIGIBILITY:
            if eligibility_url is not None:
                self.add_item(discord.ui.Button(
                    label="Check eligibility", style=discord.ButtonStyle.link,
                    url=eligibility_url,
                ))
            self.add_item(controls[0])
            self.add_item(controls[4])
        elif request.state is SessionApprovalState.AWAITING_APPROVAL:
            self.add_item(controls[1])
            self.add_item(controls[4])
        elif request.state is SessionApprovalState.AWAITING_FINAL_CONFIRMATION:
            self.add_item(controls[2])
            self.add_item(controls[3])
        elif request.state is SessionApprovalState.APPROVED:
            controls[1].label = "Continue"
            self.add_item(controls[1])

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.user_id:
            return True
        await interaction.response.send_message(
            "Only the account owner can use this Polymarket approval card.",
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
                "This session request is already being checked.", ephemeral=True
            )
            return
        self.processing = True
        try:
            await getattr(self.cog, method)(interaction, self)
        finally:
            self.processing = False

    @discord.ui.button(label="Eligibility checked", style=discord.ButtonStyle.primary)
    async def check_eligibility(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._run(interaction, "check_session_eligibility_interaction")

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.success)
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._run(interaction, "approve_session_interaction")

    @discord.ui.button(label="Yes, authorize", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._run(interaction, "confirm_session_interaction")

    @discord.ui.button(label="No", style=discord.ButtonStyle.secondary)
    async def decline_final(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._run(interaction, "decline_session_interaction")

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._run(interaction, "decline_session_interaction")
