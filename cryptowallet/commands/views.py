import discord

from ..core.models import TransactionIntent
from ..core.networks import BASE_SEPOLIA
from .constants import (
    INTENT_LIFETIME_SECONDS,
)


class WalletHistoryView(discord.ui.View):
    """A bounded 10-transaction result with a permanent full-history link."""

    def __init__(self, cog, user_id: int, address: str, page: dict, color, network=BASE_SEPOLIA):
        super().__init__(timeout=180)
        self.cog = cog
        self.user_id = user_id
        self.address = address
        self.page = page
        self.color = color
        self.network = network
        self.add_item(
            discord.ui.Button(
                label="View complete history",
                style=discord.ButtonStyle.link,
                url=network.explorer_address_url(address),
            )
        )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.user_id:
            return True
        await interaction.response.send_message(
            "Only the wallet owner can use this transaction-history card.", ephemeral=True
        )
        return False

    def embed(self) -> discord.Embed:
        return self.cog._activity_embed(
            self.address, self.page, 0, self.color, self.network
        )


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
    """Owner-bound confirmation for wallet-profile delegation revocation."""

    def __init__(self, cog, user_id: int, profile: dict, *, allow_renew: bool = False):
        super().__init__(timeout=180 if allow_renew else 60)
        self.cog = cog
        self.user_id = user_id
        self.profile = profile
        self.processing = False
        if not allow_renew:
            renewal_button = next(
                item for item in self.children if item.label == "Renew authorization"
            )
            self.remove_item(renewal_button)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.user_id:
            return True
        await interaction.response.send_message(
            "Only the wallet owner can manage this authorization.", ephemeral=True
        )
        return False

    def disable_controls(self) -> None:
        for item in self.children:
            item.disabled = True

    def disable_renewal(self) -> None:
        for item in self.children:
            if item.label == "Renew authorization":
                item.disabled = True

    @discord.ui.button(label="Renew authorization", emoji="🔐", style=discord.ButtonStyle.primary)
    async def renew(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.processing:
            await interaction.response.send_message(
                "This authorization is already being checked.", ephemeral=True
            )
            return
        self.processing = True
        try:
            await self.cog.renew_authorization_interaction(interaction, self)
        finally:
            self.processing = False

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


class WalletAuthorizationView(WalletRevocationView):
    """Owner-bound controls for an active signing authorization."""

    def __init__(self, cog, user_id: int, profile: dict):
        super().__init__(cog, user_id, profile, allow_renew=True)
