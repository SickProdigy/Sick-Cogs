import asyncio
import logging
import discord
from discord.ui import Button, View

from .objects import AlreadyEnteredError, GiveawayEnterError

log = logging.getLogger("red.sick-cogs.Giveaways")


class GiveawayView(View):
    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog


BUTTON_STYLE = {
    "blurple": discord.ButtonStyle.primary,
    "grey": discord.ButtonStyle.secondary,
    "green": discord.ButtonStyle.success,
    "red": discord.ButtonStyle.danger,
    "gray": discord.ButtonStyle.secondary,
}


class GiveawayButton(Button):
    def __init__(
        self,
        label: str,
        style: str,
        emoji,
        cog,
        id,
        update=False,
    ):
        super().__init__(
            label=label, style=BUTTON_STYLE[style], emoji=emoji, custom_id=f"giveaway_button:{id}"
        )
        self.default_label = label
        self.update = update
        self.cog = cog

    async def callback(self, interaction: discord.Interaction):
        giveaway = self.cog.giveaways.get(interaction.message.id)
        if giveaway is None:
            await interaction.response.send_message(
                "This giveaway is no longer active.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)
        lock = self.cog.entry_locks.setdefault(interaction.message.id, asyncio.Lock())
        async with lock:
            giveaway = self.cog.giveaways.get(interaction.message.id)
            if giveaway is None:
                await interaction.followup.send(
                    "This giveaway is no longer active.", ephemeral=True
                )
                return
            try:
                await giveaway.add_entrant(interaction.user, cog=self.cog)
            except GiveawayEnterError as exc:
                await interaction.followup.send(exc.message, ephemeral=True)
                return
            except AlreadyEnteredError:
                await interaction.followup.send(
                    "You are already in the giveaway.", ephemeral=True
                )
                return
            except Exception:
                log.exception("Unexpected error while entering giveaway %s", giveaway.messageid)
                await interaction.followup.send(
                    "I could not add your entry. Please try again later.", ephemeral=True
                )
                return
            await self.update_label(giveaway, interaction)

        await interaction.followup.send(
            f"You have been entered into the giveaway for {giveaway.prize}.",
            ephemeral=True,
        )

    async def update_label(self, giveaway, interaction):
        if self.update:
            if len(set(giveaway.entrants)) >= 1:
                self.label = f"{self.default_label} ({len(set(giveaway.entrants))})"
            if len(set(giveaway.entrants)) == 0:
                self.label = self.default_label
            try:
                await interaction.message.edit(view=self.view)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                log.warning("Could not update entrant count for giveaway %s", giveaway.messageid)
