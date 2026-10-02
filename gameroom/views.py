import asyncio
from typing import Optional

import discord

from .games import BlackjackGame, HigherLowerGame, format_hand, hand_value


class OwnerView(discord.ui.View):
    def __init__(self, owner_id: int, timeout: float = 120):
        super().__init__(timeout=timeout)
        self.owner_id = owner_id
        self.message: Optional[discord.Message] = None
        self.lock = asyncio.Lock()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message(
            "Start your own game so nobody else can control this one.",
            ephemeral=True,
        )
        return False

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass

    def finish(self) -> None:
        for item in self.children:
            item.disabled = True
        self.stop()


class GameMenuView(OwnerView):
    def __init__(self, owner_id: int, prefix: str):
        super().__init__(owner_id, timeout=90)
        self.prefix = prefix

    async def _instructions(self, interaction: discord.Interaction, text: str) -> None:
        await interaction.response.send_message(text, ephemeral=True)

    @discord.ui.button(label="Dice", emoji="🎲", style=discord.ButtonStyle.primary)
    async def dice(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await self._instructions(
            interaction,
            f"Try `{self.prefix}dice d20` or `{self.prefix}dice 2d6+3`.",
        )

    @discord.ui.button(label="Coin flip", emoji="🪙", style=discord.ButtonStyle.secondary)
    async def coin(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await self._instructions(interaction, f"Run `{self.prefix}coinflip`.")

    @discord.ui.button(label="Blackjack", emoji="🃏", style=discord.ButtonStyle.success)
    async def blackjack(
        self, interaction: discord.Interaction, _button: discord.ui.Button
    ) -> None:
        await self._instructions(interaction, f"Run `{self.prefix}blackjack`.")

    @discord.ui.button(label="Higher / Lower", emoji="↕️", style=discord.ButtonStyle.secondary)
    async def higher_lower(
        self, interaction: discord.Interaction, _button: discord.ui.Button
    ) -> None:
        await self._instructions(interaction, f"Run `{self.prefix}higherlower`.")


class BlackjackView(OwnerView):
    def __init__(self, owner_id: int, game: BlackjackGame):
        super().__init__(owner_id)
        self.game = game
        self._sync_buttons()

    def _sync_buttons(self) -> None:
        if self.game.finished:
            self.finish()
            return
        self.double_down.disabled = len(self.game.player) != 2

    def embed(self) -> discord.Embed:
        dealer_hidden = not self.game.finished
        dealer_text = format_hand(self.game.dealer, hide_second=dealer_hidden)
        if dealer_hidden:
            dealer_value = self.game.dealer[0].high_value
            if dealer_value > 10:
                dealer_value = 11 if self.game.dealer[0].rank == "A" else 10
            dealer_text += f"  (showing {dealer_value})"
        else:
            dealer_text += f"  ({hand_value(self.game.dealer)})"

        embed = discord.Embed(title="🃏 GameRoom Blackjack", color=discord.Color.blurple())
        embed.add_field(
            name="Dealer",
            value=dealer_text,
            inline=False,
        )
        embed.add_field(
            name="Your hand",
            value=f"{format_hand(self.game.player)}  ({hand_value(self.game.player)})",
            inline=False,
        )
        if self.game.result:
            embed.description = f"**{self.game.result}**"
        else:
            embed.description = "Hit, stand, or take one card and stand with Double."
        embed.set_footer(text="Free play • Dealer stands on all 17s")
        return embed

    async def _update(self, interaction: discord.Interaction) -> None:
        self._sync_buttons()
        await interaction.response.edit_message(embed=self.embed(), view=self)

    @discord.ui.button(label="Hit", emoji="➕", style=discord.ButtonStyle.primary)
    async def hit(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        async with self.lock:
            self.game.hit()
            await self._update(interaction)

    @discord.ui.button(label="Stand", emoji="✋", style=discord.ButtonStyle.secondary)
    async def stand(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        async with self.lock:
            self.game.stand()
            await self._update(interaction)

    @discord.ui.button(label="Double", emoji="⏬", style=discord.ButtonStyle.success)
    async def double_down(
        self, interaction: discord.Interaction, _button: discord.ui.Button
    ) -> None:
        async with self.lock:
            self.game.double()
            await self._update(interaction)


class HigherLowerView(OwnerView):
    def __init__(self, owner_id: int, game: HigherLowerGame):
        super().__init__(owner_id)
        self.game = game

    def embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="↕️ Higher or Lower",
            description=self.game.last_result,
            color=discord.Color.gold(),
        )
        embed.add_field(name="Current card", value=self.game.current.label, inline=True)
        embed.add_field(name="Score", value=str(self.game.score), inline=True)
        embed.set_footer(text="Aces are high • Ties continue the game • First to 10 wins")
        return embed

    async def _guess(self, interaction: discord.Interaction, higher: bool) -> None:
        async with self.lock:
            self.game.guess(higher)
            if self.game.finished:
                self.finish()
            await interaction.response.edit_message(embed=self.embed(), view=self)

    @discord.ui.button(label="Higher", emoji="⬆️", style=discord.ButtonStyle.success)
    async def higher(
        self, interaction: discord.Interaction, _button: discord.ui.Button
    ) -> None:
        await self._guess(interaction, True)

    @discord.ui.button(label="Lower", emoji="⬇️", style=discord.ButtonStyle.danger)
    async def lower(
        self, interaction: discord.Interaction, _button: discord.ui.Button
    ) -> None:
        await self._guess(interaction, False)
