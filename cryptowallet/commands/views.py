import time

import discord

from ..models import TransactionIntent
from ..networks import BASE_SEPOLIA
from ..providers import WalletProviderError

from .constants import (
    HISTORY_NEXT_COOLDOWN_SECONDS,
    HISTORY_PAGE_SIZE,
    INTENT_LIFETIME_SECONDS,
)


class WalletHistoryView(discord.ui.View):
    """Owner-bound cursor pagination for public wallet activity."""

    def __init__(self, cog, user_id: int, address: str, page: dict, color):
        super().__init__(timeout=180)
        self.cog = cog
        self.user_id = user_id
        self.address = address
        self.pages = [page]
        self.page_index = 0
        self.color = color
        self.next_allowed_at = 0.0
        self.add_item(
            discord.ui.Button(
                label="View full history",
                style=discord.ButtonStyle.link,
                url=f"{BASE_SEPOLIA.explorer_url}/address/{address}",
            )
        )
        self._sync_buttons()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.user_id:
            return True
        await interaction.response.send_message(
            "Only the wallet owner can browse this transaction history.", ephemeral=True
        )
        return False

    def _sync_buttons(self) -> None:
        self.previous.disabled = self.page_index == 0
        page = self.pages[self.page_index]
        self.next.disabled = (
            self.page_index >= len(self.pages) - 1 and not page["has_more"]
        )

    def embed(self) -> discord.Embed:
        return self.cog._activity_embed(
            self.address,
            self.pages[self.page_index],
            self.page_index,
            self.color,
        )

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary)
    async def previous(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page_index = max(0, self.page_index - 1)
        self._sync_buttons()
        await interaction.response.edit_message(embed=self.embed(), view=self)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
        if await self.cog.config.provider_paused():
            await interaction.response.send_message(
                "CryptoWallet provider processing is paused by the bot owner.",
                ephemeral=True,
            )
            return
        if self.page_index >= len(self.pages) - 1:
            now = time.monotonic()
            if now < self.next_allowed_at:
                remaining = max(1, int(self.next_allowed_at - now + 0.999))
                await interaction.response.send_message(
                    f"Please wait {remaining} second(s) before loading another page.",
                    ephemeral=True,
                )
                return
            self.next_allowed_at = now + HISTORY_NEXT_COOLDOWN_SECONDS
            page_token = self.pages[self.page_index]["next_page"]
            try:
                page = await self.cog.wallet_provider.get_transaction_history(
                    self.address,
                    BASE_SEPOLIA.key,
                    page_token=page_token,
                    limit=HISTORY_PAGE_SIZE,
                )
            except WalletProviderError as exc:
                await interaction.response.send_message(str(exc), ephemeral=True)
                return
            self.pages.append(page)
        self.page_index += 1
        self._sync_buttons()
        await interaction.response.edit_message(embed=self.embed(), view=self)


class WalletIntentView(discord.ui.View):
    """Owner-bound approval controls for one pending transaction intent."""

    def __init__(self, cog, user_id: int, intent: TransactionIntent):
        super().__init__(timeout=INTENT_LIFETIME_SECONDS)
        self.cog = cog
        self.user_id = user_id
        self.intent_id = intent.intent_id
        self.quote = cog._intent_quote(intent)
        self.processing = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.user_id:
            return True
        await interaction.response.send_message(
            "Only the wallet owner can approve or reject this transaction.", ephemeral=True
        )
        return False

    def disable_controls(self) -> None:
        for item in self.children:
            item.disabled = True

    @discord.ui.button(label="Approve", emoji="✅", style=discord.ButtonStyle.success)
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.processing:
            await interaction.response.send_message(
                "This transaction is already being checked.", ephemeral=True
            )
            return
        self.processing = True
        try:
            await self.cog.approve_intent_interaction(interaction, self)
        finally:
            self.processing = False

    @discord.ui.button(label="Reject", emoji="❌", style=discord.ButtonStyle.danger)
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.processing:
            await interaction.response.send_message(
                "This transaction is already being checked.", ephemeral=True
            )
            return
        self.processing = True
        try:
            await self.cog.reject_intent_interaction(interaction, self)
        finally:
            self.processing = False


class WalletRevocationView(discord.ui.View):
    """Owner-bound confirmation for account-scoped delegation revocation."""

    def __init__(self, cog, user_id: int, profile: dict):
        super().__init__(timeout=60)
        self.cog = cog
        self.user_id = user_id
        self.profile = profile
        self.processing = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.user_id:
            return True
        await interaction.response.send_message(
            "Only the wallet owner can revoke this authorization.", ephemeral=True
        )
        return False

    def disable_controls(self) -> None:
        for item in self.children:
            item.disabled = True

    @discord.ui.button(label="Revoke authorization", emoji="🔒", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.processing:
            await interaction.response.send_message(
                "Authorization revocation is already being checked.", ephemeral=True
            )
            return
        self.processing = True
        try:
            await self.cog.revoke_authorization_interaction(interaction, self)
        finally:
            self.processing = False

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.disable_controls()
        await interaction.response.edit_message(view=self)
        await interaction.followup.send("Wallet authorization was not changed.", ephemeral=True)
