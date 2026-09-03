import secrets
import time
from datetime import datetime
from urllib.parse import quote

import discord
from redbot.core import commands

from .models import IntentStatus, TransactionIntent
from .networks import BASE_SEPOLIA, NETWORKS
from .providers import WalletProviderError
from .validation import format_wei_as_eth, normalize_evm_address, parse_eth_to_wei

INTENT_LIFETIME_SECONDS = 15 * 60


class WalletIntentView(discord.ui.View):
    """Owner-bound approval controls for one pending transaction intent."""

    def __init__(self, cog, user_id: int, intent_id: str):
        super().__init__(timeout=INTENT_LIFETIME_SECONDS)
        self.cog = cog
        self.user_id = user_id
        self.intent_id = intent_id
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


class WalletCommands:
    """User-facing wallet commands."""

    async def _wallet_profile_or_error(self, ctx: commands.Context) -> dict | None:
        try:
            return await self.get_or_create_wallet_profile(ctx.author)
        except WalletProviderError as exc:
            await ctx.send(f"Wallet provisioning is unavailable: {exc}")
        except RuntimeError as exc:
            await ctx.send(str(exc))
        return None

    async def _wallet_embed(self, ctx: commands.Context, profile: dict) -> discord.Embed:
        embed = discord.Embed(title="Crypto Wallet", color=await ctx.embed_color())
        embed.add_field(name="Network", value=f"{BASE_SEPOLIA.name} (testnet)", inline=False)
        embed.description = "Your public wallet profile is linked."
        accounts = profile.get("accounts") or []
        for account in accounts[:5]:
            address = str(account.get("address") or "Unavailable")
            account_type = str(account.get("account_type") or "unknown")
            embed.add_field(
                name=account_type.replace("_", " ").title(),
                value=f"[{address}]({BASE_SEPOLIA.explorer_url}/address/{address})",
                inline=False,
            )
        account = self._account_for_network(profile, BASE_SEPOLIA.key)
        if account is not None:
            try:
                balance_wei = await self.wallet_provider.get_native_balance(
                    str(account.get("address") or ""), BASE_SEPOLIA.key
                )
                balance = f"{format_wei_as_eth(balance_wei)} {BASE_SEPOLIA.native_symbol}"
            except (ValueError, WalletProviderError):
                balance = "Temporarily unavailable"
            embed.add_field(name="Balance", value=balance, inline=False)
        embed.set_footer(text="Prototype only — do not use with real funds")
        return embed

    @commands.group(name="wallet", aliases=("cryptowallet",), invoke_without_command=True)
    async def wallet(self, ctx: commands.Context):
        """Show your wallet profile and prototype status."""
        profile = await self._wallet_profile_or_error(ctx)
        if profile is None:
            return
        await ctx.send(embed=await self._wallet_embed(ctx, profile))

    @wallet.command(name="balance", aliases=("funds",))
    async def wallet_balance(self, ctx: commands.Context):
        """Show your Base Sepolia address and native ETH balance."""
        profile = await self._wallet_profile_or_error(ctx)
        if profile is None:
            return
        await ctx.send(embed=await self._wallet_embed(ctx, profile))

    @wallet.command(name="networks")
    async def wallet_networks(self, ctx: commands.Context):
        """List networks enabled for this prototype."""
        lines = [
            f"- **{network.name}** — chain ID `{network.chain_id}` "
            f"({network.native_symbol}, testnet)"
            for network in NETWORKS.values()
        ]
        await ctx.send("**Enabled wallet networks**\n" + "\n".join(lines))

    @wallet.command(name="authorize", aliases=("auth",))
    async def wallet_authorize(self, ctx: commands.Context):
        """Authorize limited bot actions for your provisioned wallet."""
        profile = await self._wallet_profile_or_error(ctx)
        if profile is None:
            return
        try:
            expires_at = await self.send_authorization_link(ctx.author, profile)
        except RuntimeError as exc:
            await ctx.send(f"Wallet authorization is unavailable: {exc}")
            return
        await ctx.send(f"I sent your wallet authorization link by DM; it expires <t:{expires_at}:R>.")

    async def send_authorization_link(self, user, profile: dict) -> int:
        """DM a short-lived authorization link and return its expiry."""
        approval_base_url = str(await self.config.approval_base_url() or "").rstrip("/")
        token, expires_at = await self.create_authorization_handoff(user.id, profile)
        link = f"{approval_base_url}/session.html#handoff={quote(token, safe='')}"
        try:
            await user.send(
                "Open this protected wallet authorization link before "
                f"<t:{expires_at}:R>:\n<{link}>\n"
                "Confirming creates a 24-hour delegation for this Base Sepolia smart account. "
                "Do not share this link."
            )
        except discord.Forbidden as exc:
            raise RuntimeError(
                "I could not DM you. Enable direct messages and try again."
            ) from exc
        return expires_at

    @wallet.command(name="authorization", aliases=("authstatus",))
    async def wallet_authorization(self, ctx: commands.Context):
        """Show whether the bot currently has limited signing authorization."""
        profile = await self._wallet_profile_or_error(ctx)
        if profile is None:
            return
        try:
            status = await self.wallet_provider.get_delegation_status(
                profile, BASE_SEPOLIA.key
            )
        except WalletProviderError as exc:
            await ctx.send(f"Wallet authorization status is unavailable: {exc}")
            return
        if status["active"]:
            expiry = datetime.fromisoformat(
                status["expires_at"].replace("Z", "+00:00")
            )
            await ctx.send(
                "Limited signing authorization is active for your Base Sepolia wallet "
                f"until <t:{int(expiry.timestamp())}:F>."
            )
            return
        await ctx.send(
            "No active signing authorization exists. You can still receive funds and view "
            "your wallet; authorization will be requested when you first approve a send."
        )

    @staticmethod
    def _account_for_network(profile: dict, network_key: str) -> dict | None:
        for account in profile.get("accounts") or []:
            if account.get("network") == network_key:
                return account
        return None

    @staticmethod
    def _intent_embed(intent: TransactionIntent, network, color) -> discord.Embed:
        embed = discord.Embed(title="Wallet transaction intent", color=color)
        embed.add_field(name="Status", value=intent.status.value.title(), inline=True)
        embed.add_field(name="Network", value=f"{network.name} (`{network.chain_id}`)", inline=True)
        embed.add_field(
            name="Amount",
            value=f"{format_wei_as_eth(intent.value_wei)} {network.native_symbol}",
            inline=True,
        )
        gas_value = f"{format_wei_as_eth(intent.estimated_gas_fee_wei)} {network.native_symbol}"
        if intent.gas_sponsored:
            gas_value += " (sponsored by CDP)"
        embed.add_field(name="Estimated gas fee", value=gas_value, inline=True)
        embed.add_field(
            name="Estimated total",
            value=(
                f"{format_wei_as_eth(intent.value_wei + intent.estimated_gas_fee_wei)} "
                f"{network.native_symbol}"
            ),
            inline=True,
        )
        embed.add_field(name="From", value=f"`{intent.from_address}`", inline=False)
        embed.add_field(name="To", value=f"`{intent.to_address}`", inline=False)
        embed.add_field(name="Intent ID", value=f"`{intent.intent_id}`", inline=False)
        embed.add_field(name="Expires", value=f"<t:{intent.expires_at}:R>", inline=True)
        if intent.transaction_hash:
            embed.add_field(
                name="Transaction",
                value=(
                    f"[{intent.transaction_hash}]"
                    f"({network.explorer_url}/tx/{intent.transaction_hash})"
                ),
                inline=False,
            )
        embed.set_footer(text="Unsigned testnet intent — no transaction has been sent")
        return embed

    async def _stored_intent(self, user_id: int, intent_id: str) -> TransactionIntent | None:
        data = await self.config.user_from_id(user_id).intents.get_raw(intent_id, default=None)
        if data is None:
            return None
        try:
            return TransactionIntent.from_dict(data)
        except (KeyError, TypeError, ValueError):
            return None

    async def reject_intent_interaction(
        self, interaction: discord.Interaction, view: WalletIntentView
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        intent = await self._stored_intent(view.user_id, view.intent_id)
        if intent is None or intent.status is not IntentStatus.PENDING:
            await interaction.followup.send("This transaction is no longer pending.", ephemeral=True)
            return
        intent.status = IntentStatus.REJECTED
        await self.config.user_from_id(view.user_id).intents.set_raw(
            intent.intent_id, value=intent.to_dict()
        )
        view.disable_controls()
        network = NETWORKS[intent.network]
        color = interaction.message.embeds[0].color if interaction.message.embeds else None
        await interaction.message.edit(
            embed=self._intent_embed(intent, network, color),
            view=view,
        )
        await interaction.followup.send("Transaction rejected. No funds were moved.", ephemeral=True)

    async def approve_intent_interaction(
        self, interaction: discord.Interaction, view: WalletIntentView
    ) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        intent = await self._stored_intent(view.user_id, view.intent_id)
        if intent is None or intent.status is not IntentStatus.PENDING:
            await interaction.followup.send("This transaction is no longer pending.", ephemeral=True)
            return
        if intent.expires_at <= int(time.time()):
            intent.status = IntentStatus.EXPIRED
            await self.config.user_from_id(view.user_id).intents.set_raw(
                intent.intent_id, value=intent.to_dict()
            )
            view.disable_controls()
            await interaction.message.edit(view=view)
            await interaction.followup.send("This transaction quote has expired.", ephemeral=True)
            return
        profile = await self.config.user_from_id(view.user_id).profile()
        if profile is None or intent.profile_id != str(profile.get("profile_id") or ""):
            await interaction.followup.send("The wallet profile no longer matches this intent.", ephemeral=True)
            return
        try:
            authorization = await self.wallet_provider.get_delegation_status(
                profile, intent.network
            )
        except WalletProviderError as exc:
            await interaction.followup.send(
                f"Authorization could not be checked: {exc}", ephemeral=True
            )
            return
        if not authorization["active"]:
            try:
                expires_at = await self.send_authorization_link(interaction.user, profile)
            except RuntimeError as exc:
                await interaction.followup.send(str(exc), ephemeral=True)
                return
            await interaction.followup.send(
                "I sent the required authorization link by DM. Complete it, then press "
                f"Approve again before the transaction expires <t:{intent.expires_at}:R>. "
                f"The authorization link expires <t:{expires_at}:R>.",
                ephemeral=True,
            )
            return
        view.disable_controls()
        await interaction.message.edit(view=view)
        await interaction.followup.send(
            "Authorization is active and this preview passed its approval checks. Signing and "
            "broadcast are not enabled yet, so approval was not persisted and no funds moved.",
            ephemeral=True,
        )

    @wallet.command(name="send")
    async def wallet_send(self, ctx: commands.Context, to_address: str, amount: str):
        """Prepare an unsigned Base Sepolia ETH transfer intent."""
        profile = await self._wallet_profile_or_error(ctx)
        if profile is None:
            return
        network = NETWORKS.get(await self.config.default_network())
        if network is None or not network.testnet:
            await ctx.send("Transaction intents are restricted to an enabled test network.")
            return
        account = self._account_for_network(profile, network.key)
        if account is None:
            await ctx.send(f"Your wallet profile has no account for {network.name}.")
            return
        try:
            from_address = normalize_evm_address(str(account.get("address") or ""))
            recipient = normalize_evm_address(to_address)
            value_wei = parse_eth_to_wei(amount)
        except ValueError as exc:
            await ctx.send(str(exc))
            return
        try:
            balance_wei = await self.wallet_provider.get_native_balance(
                from_address, network.key
            )
        except WalletProviderError as exc:
            await ctx.send(f"The transaction preview is unavailable: {exc}")
            return
        if value_wei > balance_wei:
            await ctx.send(
                "Insufficient Base Sepolia balance. Available: "
                f"`{format_wei_as_eth(balance_wei)} {network.native_symbol}`."
            )
            return
        now = int(time.time())
        intent = TransactionIntent(
            intent_id=secrets.token_urlsafe(12),
            profile_id=str(profile.get("profile_id") or ""),
            network=network.key,
            from_address=from_address,
            to_address=recipient,
            value_wei=value_wei,
            created_at=now,
            expires_at=now + INTENT_LIFETIME_SECONDS,
            estimated_gas_fee_wei=0,
            gas_sponsored=True,
        )
        if not intent.profile_id:
            await ctx.send("Your wallet profile is incomplete and must be linked again.")
            return
        async with self.config.user(ctx.author).intents() as intents:
            intents[intent.intent_id] = intent.to_dict()
        await self.expire_and_trim_intents(ctx.author)
        await ctx.send(
            embed=self._intent_embed(intent, network, await ctx.embed_color()),
            view=WalletIntentView(self, ctx.author.id, intent.intent_id),
        )

    @wallet.command(name="transaction", aliases=("intent", "tx"))
    async def wallet_transaction(self, ctx: commands.Context, intent_id: str):
        """Show one of your transaction intents by ID."""
        intents = await self.expire_and_trim_intents(ctx.author)
        data = intents.get(intent_id.strip())
        if data is None:
            await ctx.send("No transaction intent with that ID belongs to your wallet profile.")
            return
        try:
            intent = TransactionIntent.from_dict(data)
        except (KeyError, TypeError, ValueError):
            await ctx.send("That stored transaction intent is invalid and cannot be displayed.")
            return
        network = NETWORKS.get(intent.network)
        if network is None:
            await ctx.send("That transaction intent references an unsupported network.")
            return
        await ctx.send(embed=self._intent_embed(intent, network, await ctx.embed_color()))
