from typing import TYPE_CHECKING

import discord

from .constants import DEFAULT_DECIMALS
from .models import TokenDraft
from .validation import normalize_decimals, normalize_name, normalize_symbol, parse_supply

if TYPE_CHECKING:
    from .tokenfactory import TokenFactory


class TokenDetailsModal(discord.ui.Modal):
    def __init__(self, view: "TokenFactoryDraftView"):
        super().__init__(title="Fixed-supply test token")
        self.view_ref = view
        current = view.draft
        self.name_input = discord.ui.TextInput(
            label="Token name", default=current.name if current else "", max_length=64
        )
        self.symbol_input = discord.ui.TextInput(
            label="Symbol", default=current.symbol if current else "", max_length=10
        )
        self.supply_input = discord.ui.TextInput(
            label="Fixed supply", placeholder="1000000", max_length=40
        )
        self.decimals_input = discord.ui.TextInput(
            label="Decimals", default=str(current.decimals if current else DEFAULT_DECIMALS),
            max_length=2,
        )
        for item in (
            self.name_input,
            self.symbol_input,
            self.supply_input,
            self.decimals_input,
        ):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            decimals = normalize_decimals(self.decimals_input.value)
            draft = TokenDraft(
                creator_discord_id=self.view_ref.user_id,
                wallet_profile_id=self.view_ref.wallet_profile_id,
                owner_address=self.view_ref.owner_address,
                name=normalize_name(self.name_input.value),
                symbol=normalize_symbol(self.symbol_input.value),
                decimals=decimals,
                supply_atomic=parse_supply(self.supply_input.value, decimals),
            )
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        self.view_ref.draft = draft
        self.view_ref.deploy.disabled = not self.view_ref.deployment_available
        self.view_ref.deploy.label = (
            "Review deployment" if self.view_ref.deployment_available
            else "Deployment unavailable"
        )
        await self.view_ref.cog.save_draft(self.view_ref.user, draft)
        await interaction.response.edit_message(embed=self.view_ref.embed(), view=self.view_ref)
        await interaction.followup.send("Token deployment draft saved.", ephemeral=True)


class TokenFactoryDraftView(discord.ui.View):
    def __init__(
        self, cog: "TokenFactory", user, wallet_context: dict, draft=None,
        *, deployment_available: bool = False,
    ):
        super().__init__(timeout=900)
        self.cog = cog
        self.user = user
        self.user_id = user.id
        self.wallet_profile_id = wallet_context["profile_id"]
        self.owner_address = wallet_context["owner_address"]
        self.draft = draft
        self.deployment_available = deployment_available
        self.deploy.disabled = not (deployment_available and draft is not None)
        self.deploy.label = (
            "Review deployment" if deployment_available else "Deployment unavailable"
        )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.user_id:
            return True
        await interaction.response.send_message(
            "Only the member who opened this card can edit it.", ephemeral=True
        )
        return False

    def embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="Base Sepolia token deployment draft",
            description=(
                "Create a fixed-supply ERC-20 test token. Completing this card does not "
                "deploy anything or move funds."
            ),
            color=discord.Color.blurple(),
        )
        if self.draft is None:
            embed.add_field(name="Token", value="Not configured", inline=False)
            embed.add_field(name="Supply", value="Not configured", inline=True)
        else:
            scale = 10**self.draft.decimals
            whole, remainder = divmod(self.draft.supply_atomic, scale)
            display = str(whole)
            if remainder:
                display += f".{remainder:0{self.draft.decimals}d}".rstrip("0")
            embed.add_field(
                name="Token", value=f"{self.draft.name} ({self.draft.symbol})", inline=False
            )
            embed.add_field(name="Fixed supply", value=display, inline=True)
            embed.add_field(name="Decimals", value=str(self.draft.decimals), inline=True)
        embed.add_field(name="Network", value="Base Sepolia (`84532`)", inline=True)
        embed.add_field(name="Token owner", value=f"`{self.owner_address}`", inline=False)
        embed.add_field(
            name="Deployment",
            value=(
                "Ready for protected review and confirmation."
                if self.deployment_available
                else "Disabled or emergency-paused by the bot owner."
            ),
            inline=False,
        )
        embed.set_footer(text="Testnet only · fixed supply · no bot mint or ownership authority")
        return embed

    @discord.ui.button(label="Enter token details", style=discord.ButtonStyle.primary)
    async def edit_details(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(TokenDetailsModal(self))

    @discord.ui.button(label="Review deployment", style=discord.ButtonStyle.success)
    async def deploy(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.draft is None or not self.deployment_available:
            await interaction.response.send_message(
                "Token deployment is unavailable.", ephemeral=True
            )
            return
        confirmation = TokenDeploymentConfirmView(self.cog, self.user, self.draft)
        await interaction.response.send_message(
            embed=confirmation.embed(), view=confirmation, ephemeral=True
        )


class TokenDeploymentConfirmView(discord.ui.View):
    """Requester-bound final confirmation for one immutable token draft."""

    def __init__(self, cog: "TokenFactory", user, draft: TokenDraft):
        super().__init__(timeout=180)
        self.cog = cog
        self.user = user
        self.user_id = user.id
        self.draft = draft
        self.processing = False

    def embed(self) -> discord.Embed:
        scale = 10**self.draft.decimals
        whole, remainder = divmod(self.draft.supply_atomic, scale)
        supply = str(whole)
        if remainder:
            supply += f".{remainder:0{self.draft.decimals}d}".rstrip("0")
        embed = discord.Embed(
            title="Confirm fixed-supply token deployment",
            description=(
                "Review every immutable field. Confirmation submits a sponsored Base "
                "Sepolia operation and cannot be undone after confirmation."
            ),
            color=discord.Color.orange(),
        )
        embed.add_field(name="Token", value=f"{self.draft.name} ({self.draft.symbol})", inline=False)
        embed.add_field(name="Fixed supply", value=supply, inline=True)
        embed.add_field(name="Decimals", value=str(self.draft.decimals), inline=True)
        embed.add_field(name="Network", value="Base Sepolia (`84532`)", inline=True)
        embed.add_field(name="Recipient", value=f"`{self.draft.owner_address}`", inline=False)
        embed.add_field(
            name="Authority",
            value="No later minting, administrator, upgrade, or bot ownership.",
            inline=False,
        )
        embed.set_footer(text="Testnet only · explicit confirmation · active wallet authorization required")
        return embed

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.user_id:
            return True
        await interaction.response.send_message(
            "Only the member who reviewed this draft can deploy it.", ephemeral=True
        )
        return False

    @discord.ui.button(label="Deploy test token", style=discord.ButtonStyle.danger, emoji="⚠️")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.processing:
            await interaction.response.send_message(
                "Token deployment is already being processed.", ephemeral=True
            )
            return
        self.processing = True
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(view=self)
        try:
            result = await self.cog.submit_token_deployment(self.user, self.draft)
        except Exception as exc:
            await interaction.followup.send(f"Token deployment failed: {exc}", ephemeral=True)
            return
        if result.get("already_deployed"):
            message = (
                f"This token already exists at `{result['token_address']}`. "
                "Run `tokenfactory deployment` to verify and record it."
            )
        else:
            message = (
                "Fixed-supply token deployment submitted. "
                f"User operation: `{result['user_operation_hash']}`\n"
                "Run `tokenfactory deployment` after confirmation."
            )
        await interaction.followup.send(message, ephemeral=True)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content="Token deployment cancelled.", view=self)


class FactoryDeploymentView(discord.ui.View):
    """One-use owner confirmation for the pinned infrastructure deployment."""

    def __init__(self, cog: "TokenFactory", user, creation_code: str):
        super().__init__(timeout=180)
        self.cog = cog
        self.user = user
        self.user_id = user.id
        self.creation_code = creation_code
        self.processing = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.user_id and await self.cog.bot.is_owner(
            interaction.user
        ):
            return True
        await interaction.response.send_message(
            "Only the bot owner who requested this deployment can approve it.",
            ephemeral=True,
        )
        return False

    @discord.ui.button(
        label="Deploy pinned factory", style=discord.ButtonStyle.danger, emoji="⚠️"
    )
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.processing:
            await interaction.response.send_message(
                "Factory deployment is already being processed.", ephemeral=True
            )
            return
        self.processing = True
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(view=self)
        try:
            result = await self.cog.deploy_pinned_factory(
                self.user, self.creation_code
            )
        except RuntimeError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        if result.get("already_deployed"):
            message = f"The pinned factory already exists at `{result['address']}`."
        else:
            message = (
                "Pinned factory deployment submitted. "
                f"User operation: `{result['user_operation_hash']}`"
            )
            if result.get("transaction_hash"):
                message += f"\nTransaction: `{result['transaction_hash']}`"
            message += "\nRun `tokenfactoryset verifyfactory` after confirmation."
        await interaction.followup.send(message, ephemeral=True)
