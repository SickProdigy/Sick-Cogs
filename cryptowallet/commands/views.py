import time

import discord

from ..core.models import IntentStatus, TransactionIntent
from ..core.networks import BASE_SEPOLIA, NETWORKS


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


class WalletTotpEnrollmentModal(discord.ui.Modal, title="Confirm authenticator setup"):
    code = discord.ui.TextInput(
        label="6-digit authenticator code",
        placeholder="123456",
        min_length=6,
        max_length=6,
    )

    def __init__(self, view):
        super().__init__(timeout=120)
        self.view = view

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        if int(time.time()) >= self.view.expires_at:
            await interaction.followup.send(
                "This enrollment expired. Start setup again from Discord.",
                ephemeral=True,
            )
            return
        try:
            if self.view.ciphertext is None:
                self.view.ciphertext = await self.view.cog.poll_totp_enrollment_result(
                    self.view.result_handle
                )
            if self.view.ciphertext is None:
                await interaction.followup.send(
                    "Open the protected setup page first, then submit this modal again.",
                    ephemeral=True,
                )
                return
            activated = await self.view.cog.activate_encrypted_totp_enrollment(
                self.view.user_id, self.view.ciphertext, str(self.code.value)
            )
        except (RuntimeError, ValueError):
            await interaction.followup.send(
                "Authenticator enrollment could not be verified. Nothing was enabled.",
                ephemeral=True,
            )
            return
        if not activated:
            await interaction.followup.send(
                "That code is invalid, expired, or the wallet already has authenticator protection. Nothing was changed.",
                ephemeral=True,
            )
            return
        self.view.ciphertext = None
        self.view.disable_controls()
        self.view.stop()
        if self.view.message is not None:
            await self.view.message.edit(view=self.view)
        await interaction.followup.send(
            "Authenticator protection is enabled for future wallet sends.",
            ephemeral=True,
        )


class WalletTotpEnrollmentView(discord.ui.View):
    """Owner-bound confirmation for one browser-generated encrypted seed."""

    def __init__(self, cog, user_id: int, result_handle: str, expires_at: int):
        super().__init__(timeout=max(1.0, expires_at - time.time()))
        self.cog = cog
        self.user_id = user_id
        self.result_handle = result_handle
        self.expires_at = expires_at
        self.ciphertext = None
        self.message = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.user_id:
            return True
        await interaction.response.send_message(
            "Only the wallet owner can confirm this enrollment.", ephemeral=True
        )
        return False

    def disable_controls(self) -> None:
        for item in self.children:
            item.disabled = True

    async def on_timeout(self) -> None:
        self.ciphertext = None
        self.disable_controls()
        if self.message is not None:
            await self.message.edit(view=self)

    @discord.ui.button(
        label="Confirm enrollment", emoji="🔐", style=discord.ButtonStyle.primary
    )
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(WalletTotpEnrollmentModal(self))

class WalletTotpModal(discord.ui.Modal, title="Verify wallet send"):
    """Private authenticator challenge bound to one displayed transaction."""

    code = discord.ui.TextInput(
        label="6-digit authenticator code",
        placeholder="123456",
        min_length=6,
        max_length=6,
    )

    def __init__(self, view, fingerprint: str):
        super().__init__(timeout=120)
        self.view = view
        self.fingerprint = fingerprint

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if self.view.processing:
            await interaction.response.send_message(
                "This transaction is already being checked.", ephemeral=True
            )
            return
        self.view.processing = True
        try:
            await self.view.cog.approve_intent_interaction(
                interaction,
                self.view,
                totp_code=str(self.code.value),
                totp_fingerprint=self.fingerprint,
            )
        finally:
            self.view.processing = False


class WalletIntentView(discord.ui.View):
    """Owner-bound approval controls for one pending transaction intent."""

    def __init__(self, cog, user_id: int, intent: TransactionIntent):
        super().__init__(timeout=max(1.0, intent.expires_at - time.time()))
        self.cog = cog
        self.user_id = user_id
        self.intent_id = intent.intent_id
        self.quote = cog._intent_quote(intent)
        self.processing = False
        self.message = None

    async def on_timeout(self) -> None:
        intent = await self.cog._stored_intent(self.user_id, self.intent_id)
        if intent is None or intent.status is not IntentStatus.PENDING:
            return
        intent.status = IntentStatus.EXPIRED
        await self.cog.config.user_from_id(self.user_id).intents.set_raw(
            intent.intent_id, value=intent.to_dict()
        )
        self.clear_items()
        if self.message is not None:
            await self.message.edit(
                embed=self.cog._intent_embed(intent, NETWORKS[intent.network], None),
                view=None,
            )

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
            await self.cog.begin_approve_intent_interaction(interaction, self)
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
            "Only the wallet owner can manage this authorization.", ephemeral=True
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


class WalletAuthorizationView(WalletRevocationView):
    """Owner-bound controls for an active signing authorization."""

    def __init__(self, cog, user_id: int, profile: dict):
        super().__init__(cog, user_id, profile)
