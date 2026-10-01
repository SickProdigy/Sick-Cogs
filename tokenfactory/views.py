from typing import TYPE_CHECKING

import discord

from .constants import DEFAULT_DECIMALS
from .mainnet_factory import MainnetFactoryReview
from .mainnet_review import MainnetTokenReview
from .terms import TOKENFACTORY_DEPLOYMENT_ACKNOWLEDGEMENT
from .models import TokenDraft
from .validation import normalize_decimals, normalize_name, normalize_symbol, parse_supply

if TYPE_CHECKING:
    from .tokenfactory import TokenFactory


class TokenFactoryTermsView(discord.ui.View):
    """Discord-native, product-specific one-time terms acceptance."""

    def __init__(self, cog: "TokenFactory", user_id: int, terms_url: str | None, *, current: bool):
        super().__init__(timeout=180)
        accept_button = self.children[0]
        self.remove_item(accept_button)
        if terms_url:
            self.add_item(discord.ui.Button(
                label="View terms", style=discord.ButtonStyle.link, url=terms_url
            ))
        accept_button.disabled = current
        if current:
            accept_button.label = "Terms accepted"
        self.add_item(accept_button)
        self.cog = cog
        self.user_id = int(user_id)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.user_id:
            return True
        await interaction.response.send_message(
            "Only the account owner can accept these TokenFactory terms.", ephemeral=True
        )
        return False

    @discord.ui.button(label="Accept terms", style=discord.ButtonStyle.success)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        if await self.cog.has_current_mainnet_terms(self.user_id):
            button.disabled = True
            button.label = "Terms accepted"
            await interaction.message.edit(view=self)
            await interaction.followup.send("Your TokenFactory terms acceptance is already current.", ephemeral=True)
            return
        await self.cog.accept_mainnet_terms(self.user_id)
        button.disabled = True
        button.label = "Terms accepted"
        await interaction.message.edit(view=self)
        await interaction.followup.send(
            "TokenFactory mainnet terms accepted. This does not deploy a token or authorize a transaction.",
            ephemeral=True,
        )


def mainnet_review_embed(review: MainnetTokenReview) -> discord.Embed:
    """Render one immutable requester-owned deployment disclosure."""

    scale = 10**review.decimals
    whole, remainder = divmod(review.supply_atomic, scale)
    supply = str(whole)
    if remainder:
        supply += f".{remainder:0{review.decimals}d}".rstrip("0")
    max_fee = review.max_gas_fee_wei / 10**18
    embed = discord.Embed(
        title="Review Base mainnet TokenFactory deployment",
        description=(
            "This immutable review is non-executable. It prepares the exact disclosure "
            "that a separately gated protected approval must match."
        ),
        color=discord.Color.red(),
    )
    embed.add_field(name="Network", value="Base mainnet (\u00608453\u0060)", inline=True)
    embed.add_field(
        name="Token", value=f"{review.name} ({review.symbol})", inline=False
    )
    embed.add_field(name="Fixed supply", value=supply, inline=True)
    embed.add_field(
        name="Signer", value=f"\u0060{review.signer_address}\u0060", inline=False
    )
    embed.add_field(name="Recipient", value=f"\u0060{review.recipient}\u0060", inline=False)
    embed.add_field(name="Request ID", value=f"\u0060{review.request_id}\u0060", inline=False)
    embed.add_field(
        name="Target factory", value=f"\u0060{review.target_factory}\u0060", inline=False
    )
    embed.add_field(
        name="Calldata SHA-256",
        value=f"\u0060{review.calldata_sha256}\u0060",
        inline=False,
    )
    embed.add_field(name="Gas limit", value=f"\u0060{review.gas_limit:,}\u0060", inline=True)
    embed.add_field(
        name="Gas ceiling", value=f"\u0060{max_fee:.8f} ETH\u0060", inline=True
    )
    embed.add_field(name="Gas payer", value=review.gas_payer, inline=False)
    embed.add_field(name="Gas sponsorship", value="None · creator pays network gas", inline=False)
    embed.add_field(name="Native value", value="\u00600.00000000 ETH\u0060", inline=True)
    embed.add_field(
        name="Review fingerprint", value=f"\u0060{review.fingerprint}\u0060", inline=False
    )
    embed.add_field(
        name="Creator acknowledgment",
        value=(
            "I reviewed these exact details. I am creating this token, accept "
            "responsibility for its legality and use, and understand deployment is irreversible."
        ),
        inline=False,
    )
    embed.add_field(
        name="Irreversible",
        value=(
            "A confirmed mainnet deployment cannot be undone. The token supply, "
            "recipient, and contract code become permanent."
        ),
        inline=False,
    )
    embed.set_footer(text="Requester-bound review - no transaction submitted")
    return embed


def mainnet_factory_review_embed(review: MainnetFactoryReview) -> discord.Embed:
    max_fee = review.max_gas_fee_wei / 10**18
    embed = discord.Embed(
        title="Review Base mainnet TokenFactory deployment",
        description=(
            "This immutable review is non-executable. The deterministic factory "
            "must pass every external gate before this approval can be used."
        ),
        color=discord.Color.red(),
    )
    embed.add_field(name="Network", value="Base mainnet (`8453`)", inline=True)
    embed.add_field(
        name="Signer", value=f"`{review.signer_address}`", inline=False
    )
    embed.add_field(
        name="Singleton target",
        value=f"`{review.singleton_address}`",
        inline=False,
    )
    embed.add_field(
        name="Predicted factory",
        value=f"`{review.predicted_factory_address}`",
        inline=False,
    )
    embed.add_field(
        name="Creation code SHA-256",
        value=f"`{review.creation_code_sha256}`",
        inline=False,
    )
    embed.add_field(
        name="Calldata SHA-256",
        value=f"`{review.calldata_sha256}`",
        inline=False,
    )
    embed.add_field(name="Gas limit", value=f"`{review.gas_limit:,}`", inline=True)
    embed.add_field(
        name="Gas ceiling", value=f"`{max_fee:.8f} ETH`", inline=True
    )
    embed.add_field(name="Gas payer", value=review.gas_payer, inline=False)
    embed.add_field(name="Native value", value="`0.00000000 ETH`", inline=True)
    embed.add_field(
        name="Review fingerprint",
        value=f"`{review.fingerprint}`",
        inline=False,
    )
    embed.add_field(
        name="Irreversible",
        value=(
            "A confirmed Base mainnet factory deployment cannot be undone or "
            "replaced. Incorrect code or configuration may permanently affect "
            "every later token deployment."
        ),
        inline=False,
    )
    embed.set_footer(text="Owner-only factory staging - non-executable")
    return embed


class MainnetFactoryApprovalModal(
    discord.ui.Modal, title="Approve mainnet factory"
):
    acknowledgement = discord.ui.TextInput(
        label="Type DEPLOY BASE MAINNET FACTORY",
        placeholder="DEPLOY BASE MAINNET FACTORY",
        min_length=27,
        max_length=27,
    )
    def __init__(self, view: "MainnetFactoryApprovalView"):
        super().__init__(timeout=120)
        self.view = view

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            approval = await self.view.cog.approve_mainnet_factory_review(
                interaction.user.id,
                self.view.review_fingerprint,
                acknowledgement=str(self.acknowledgement.value),
            )
        except (RuntimeError, ValueError) as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        self.view.disable_controls()
        if self.view.message is not None:
            await self.view.message.edit(view=self.view)
        await interaction.followup.send(
            "Protected factory approval recorded for this exact review until "
            f"<t:{approval.expires_at}:R>. No transaction was submitted.",
            ephemeral=True,
        )


class MainnetFactoryApprovalView(discord.ui.View):
    def __init__(self, cog: "TokenFactory", owner_id: int, review: MainnetFactoryReview):
        super().__init__(timeout=10 * 60)
        self.cog = cog
        self.owner_id = int(owner_id)
        self.review_fingerprint = review.fingerprint
        self.message = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message(
            "Only the bot owner who created this factory review can approve it.",
            ephemeral=True,
        )
        return False

    def disable_controls(self) -> None:
        for item in self.children:
            item.disabled = True

    async def on_timeout(self) -> None:
        self.disable_controls()
        if self.message is not None:
            await self.message.edit(view=self)

    @discord.ui.button(
        label="Protected factory approval",
        emoji="🔐",
        style=discord.ButtonStyle.danger,
    )
    async def approve(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await interaction.response.send_modal(MainnetFactoryApprovalModal(self))


class MainnetCanaryApprovalModal(
    discord.ui.Modal, title="Approve Base mainnet deployment"
):
    acknowledgement = discord.ui.TextInput(
        label="Type the creator responsibility phrase",
        placeholder=TOKENFACTORY_DEPLOYMENT_ACKNOWLEDGEMENT,
        min_length=len(TOKENFACTORY_DEPLOYMENT_ACKNOWLEDGEMENT),
        max_length=len(TOKENFACTORY_DEPLOYMENT_ACKNOWLEDGEMENT),
    )
    def __init__(self, view: "MainnetCanaryApprovalView"):
        super().__init__(timeout=120)
        self.view = view

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            approval = await self.view.cog.approve_mainnet_canary_review(
                interaction.user.id,
                self.view.review_fingerprint,
                acknowledgement=str(self.acknowledgement.value),
            )
        except (RuntimeError, ValueError) as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        self.view.disable_controls()
        if self.view.message is not None:
            await self.view.message.edit(view=self.view)
        await interaction.followup.send(
            "Protected requester approval recorded for this exact review until "
            f"<t:{approval.expires_at}:R>. No transaction was submitted.",
            ephemeral=True,
        )


class MainnetCanaryApprovalView(discord.ui.View):
    """Requester-bound Discord approval for one immutable deployment review."""

    def __init__(self, cog: "TokenFactory", owner_id: int, review: MainnetTokenReview):
        super().__init__(timeout=10 * 60)
        self.cog = cog
        self.owner_id = int(owner_id)
        self.review_fingerprint = review.fingerprint
        self.message = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message(
            "Only the member who created this deployment review can approve it.",
            ephemeral=True,
        )
        return False

    def disable_controls(self) -> None:
        for item in self.children:
            item.disabled = True

    async def on_timeout(self) -> None:
        self.disable_controls()
        if self.message is not None:
            await self.message.edit(view=self)

    @discord.ui.button(
        label="Protected approval",
        emoji="🔐",
        style=discord.ButtonStyle.danger,
    )
    async def approve(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await interaction.response.send_modal(MainnetCanaryApprovalModal(self))


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
                name=normalize_name(self.name_input.value),
                symbol=normalize_symbol(self.symbol_input.value),
                decimals=decimals,
                supply_atomic=parse_supply(self.supply_input.value, decimals),
                network=self.view_ref.network,
                chain_id=8453 if self.view_ref.network == "base-mainnet" else 84532,
            )
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        self.view_ref.draft = draft
        enabled = self.view_ref.deployment_available
        self.view_ref.discord_deploy.disabled = not enabled
        self.view_ref.external_deploy.disabled = not (
            enabled
        )
        await self.view_ref.cog.save_draft(self.view_ref.user, draft)
        await interaction.response.edit_message(embed=self.view_ref.embed(), view=self.view_ref)
        await interaction.followup.send("Token deployment draft saved.", ephemeral=True)


class TokenFactoryDraftView(discord.ui.View):
    def __init__(
        self, cog: "TokenFactory", user, draft=None,
        *, network: str = "base-sepolia", deployment_available: bool = False,
    ):
        super().__init__(timeout=900)
        self.cog = cog
        self.user = user
        self.user_id = user.id
        self.draft = draft
        self.network = network
        self.deployment_available = deployment_available
        self.discord_deploy.disabled = not (deployment_available and draft is not None)
        self.external_deploy.disabled = not (
            deployment_available and draft is not None
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
            title=("Base mainnet token deployment draft"
                   if self.network == "base-mainnet"
                   else "Base Sepolia token deployment draft"),
            description=(
                ("Create a fixed-supply ERC-20 token. "
                 if self.network == "base-mainnet" else
                 "Create a fixed-supply ERC-20 test token. ")
                + "Completing this card does not "
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
        embed.add_field(
            name="Network",
            value=("Base mainnet (`8453`)" if self.network == "base-mainnet"
                   else "Base Sepolia (`84532`)"),
            inline=True,
        )
        embed.add_field(
            name="Token recipient", value="Chosen with the deployment wallet", inline=False
        )
        embed.add_field(
            name="Deployment",
            value=(
                "Ready for protected review and confirmation."
                if self.deployment_available
                else "Disabled or emergency-paused by the bot owner."
            ),
            inline=False,
        )
        embed.set_footer(text=(
            "Mainnet disabled until every reviewed release gate is complete"
            if self.network == "base-mainnet" else
            "Testnet only · fixed supply · no bot mint or ownership authority"
        ))
        return embed

    @discord.ui.button(label="Enter token details", style=discord.ButtonStyle.primary)
    async def edit_details(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(TokenDetailsModal(self))

    @discord.ui.button(label="Deploy to Discord Wallet", style=discord.ButtonStyle.success)
    async def discord_deploy(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if self.draft is None or not self.deployment_available:
            await interaction.response.send_message(
                "Token deployment is unavailable.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        try:
            draft = await self.cog.resolve_discord_wallet_draft(self.user, self.draft)
        except Exception as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        confirmation = TokenDeploymentConfirmView(
            self.cog, self.user, draft, self.cog.execution_terms(route="discord", network=draft.network)
        )
        await interaction.followup.send(
            embed=confirmation.embed(), view=confirmation, ephemeral=True
        )

    @discord.ui.button(label="Deploy with External Wallet", style=discord.ButtonStyle.primary)
    async def external_deploy(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if self.draft is None or not self.deployment_available:
            await interaction.response.send_message(
                "Token deployment is unavailable.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        try:
            link = await self.cog.create_external_deployment_link(self.user, self.draft)
        except Exception as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        view = discord.ui.View(timeout=180)
        view.add_item(discord.ui.Button(
            label="Open external wallet deployment", url=link, emoji="🔗"
        ))
        await interaction.followup.send(
            ("Connect a Base mainnet wallet on the protected page. "
             if self.draft.network == "base-mainnet" else
             "Connect a Base Sepolia wallet on the protected page. ")
            + "That wallet pays gas. Leave recipient blank to send the full supply "
              "to the signer.",
            view=view, ephemeral=True,
        )


class TokenDeploymentConfirmView(discord.ui.View):
    """Requester-bound final confirmation for one immutable token draft."""

    def __init__(self, cog: "TokenFactory", user, draft: TokenDraft, execution_terms: dict):
        super().__init__(timeout=180)
        self.cog = cog
        self.user = user
        self.user_id = user.id
        self.draft = draft
        self.execution_terms = execution_terms
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
                ("Review every immutable field. Confirmation submits a Base mainnet "
                 "operation and cannot be undone after confirmation."
                 if self.draft.network == "base-mainnet" else
                 "Review every immutable field. Confirmation submits a sponsored Base "
                 "Sepolia operation and cannot be undone after confirmation.")
            ),
            color=discord.Color.orange(),
        )
        embed.add_field(name="Token", value=f"{self.draft.name} ({self.draft.symbol})", inline=False)
        embed.add_field(name="Fixed supply", value=supply, inline=True)
        embed.add_field(name="Decimals", value=str(self.draft.decimals), inline=True)
        embed.add_field(
            name="Network",
            value=("Base mainnet (`8453`)" if self.draft.network == "base-mainnet"
                   else "Base Sepolia (`84532`)"),
            inline=True,
        )
        embed.add_field(name="Recipient", value=f"`{self.draft.owner_address}`", inline=False)
        embed.add_field(name="Gas limit", value=f"`{self.execution_terms['gas_limit']:,}`", inline=True)
        embed.add_field(name="Native value", value="`0.00000000 ETH`", inline=True)
        embed.add_field(
            name="Network gas",
            value=("Creator wallet pays · submission blocked until a bounded fee quote is enforceable"
                   if self.draft.network == "base-mainnet" else
                   "Sponsorship active · paid by CDP paymaster"),
            inline=False,
        )
        embed.add_field(
            name="Authority",
            value="No later minting, administrator, upgrade, or bot ownership.",
            inline=False,
        )
        embed.set_footer(text=(
            "Mainnet fee enforcement gate not complete · no submission available"
            if self.draft.network == "base-mainnet" else
            "Testnet only · explicit confirmation · active wallet authorization required"
        ))
        return embed

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.user_id:
            return True
        await interaction.response.send_message(
            "Only the member who reviewed this draft can deploy it.", ephemeral=True
        )
        return False

    @discord.ui.button(label="Deploy token", style=discord.ButtonStyle.danger, emoji="⚠️")
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
            result = await self.cog.submit_token_deployment(
                self.user,
                self.draft,
                self.execution_terms,
                guild_id=getattr(interaction, "guild_id", None),
            )
        except Exception as exc:
            await interaction.followup.send(f"Token deployment failed: {exc}", ephemeral=True)
            return
        deployment = await self.cog.command_hint(
            "tokenfactory deployment", guild=getattr(interaction, "guild", None)
        )
        if result.get("already_deployed"):
            embed = discord.Embed(
                title="Token already deployed",
                description=(
                    "The reviewed token already exists on Base Sepolia. "
                    "Automatic verification will save it in your TokenFactory history."
                ),
                color=discord.Color.blurple(),
            )
            embed.add_field(
                name="Token contract",
                value=f"`{result['token_address']}`",
                inline=False,
            )
            embed.add_field(
                name="Next step",
                value=(
                    "Automatic verification is running. If it cannot finish, "
                    f"you can recover with `{deployment}`."
                ),
                inline=False,
            )
        else:
            embed = discord.Embed(
                title="Token deployment submitted",
                description=(
                    "Your sponsored Base Sepolia deployment was accepted and is now "
                    "waiting for network confirmation."
                ),
                color=discord.Color.gold(),
            )
            embed.add_field(
                name="Token",
                value=f"{self.draft.name} ({self.draft.symbol})",
                inline=False,
            )
            embed.add_field(
                name="User operation",
                value=f"`{result['user_operation_hash']}`",
                inline=False,
            )
            embed.add_field(
                name="Next step",
                value=(
                    "Automatic verification is running. You will receive one private "
                    "success card after confirmation. If it cannot finish, use "
                    f"`{deployment}`."
                ),
                inline=False,
            )
            embed.set_footer(text="Sponsored by CDP paymaster · no native ETH charged")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content="Token deployment cancelled.", view=self)


class FactoryDeploymentView(discord.ui.View):
    """One-use owner confirmation for the pinned infrastructure deployment."""

    def __init__(self, cog: "TokenFactory", user, creation_code: str, execution_terms: dict):
        super().__init__(timeout=180)
        self.cog = cog
        self.user = user
        self.user_id = user.id
        self.creation_code = creation_code
        self.execution_terms = execution_terms
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
                self.user, self.creation_code, self.execution_terms
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
            verify = await self.cog.command_hint(
                "tokenfactoryset verifyfactory",
                guild=getattr(interaction, "guild", None),
            )
            message += f"\nRun `{verify}` after confirmation."
        await interaction.followup.send(message, ephemeral=True)
