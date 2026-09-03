import asyncio
import secrets
import time
from datetime import datetime
from urllib.parse import quote

import discord
from redbot.core import commands

from .models import IntentStatus, TransactionIntent
from .networks import BASE_SEPOLIA, NETWORKS
from .providers import WalletProviderError
from .providers.base_rpc import BaseRpcError, get_transaction
from .validation import format_wei_as_eth, normalize_evm_address, parse_eth_to_wei

INTENT_LIFETIME_SECONDS = 15 * 60
CONFIRMATION_POLL_SECONDS = 5
CONFIRMATION_POLL_ATTEMPTS = 24
HISTORY_PAGE_SIZE = 5


class WalletHistoryView(discord.ui.View):
    """Owner-bound pagination for stored wallet transaction intents."""

    def __init__(self, user_id: int, intents: list[TransactionIntent], color):
        super().__init__(timeout=180)
        self.user_id = user_id
        self.intents = intents
        self.color = color
        self.page = 0
        self.page_count = max(
            1, (len(intents) + HISTORY_PAGE_SIZE - 1) // HISTORY_PAGE_SIZE
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
        self.previous.disabled = self.page == 0
        self.next.disabled = self.page >= self.page_count - 1

    def embed(self) -> discord.Embed:
        start = self.page * HISTORY_PAGE_SIZE
        page_intents = self.intents[start : start + HISTORY_PAGE_SIZE]
        embed = discord.Embed(title="Your Wallet Transactions", color=self.color)
        for intent in page_intents:
            network = NETWORKS.get(intent.network)
            symbol = network.native_symbol if network else "ETH"
            heading = (
                f"{intent.status.value.title()} · "
                f"{format_wei_as_eth(intent.value_wei)} {symbol}"
            )
            lines = [f"To: `{intent.to_address}`", f"Intent: `{intent.intent_id}`"]
            if intent.transaction_hash and network:
                lines.append(
                    f"TXID: [{intent.transaction_hash}]"
                    f"({network.explorer_url}/tx/{intent.transaction_hash})"
                )
            lines.append(f"Created <t:{intent.created_at}:R>")
            embed.add_field(name=heading, value="\n".join(lines), inline=False)
        embed.set_footer(
            text=(
                f"Page {self.page + 1} of {self.page_count} · "
                f"Last {len(self.intents)} stored"
            )
        )
        return embed

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary)
    async def previous(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = max(0, self.page - 1)
        self._sync_buttons()
        await interaction.response.edit_message(embed=self.embed(), view=self)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = min(self.page_count - 1, self.page + 1)
        self._sync_buttons()
        await interaction.response.edit_message(embed=self.embed(), view=self)


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
        embed = discord.Embed(title="Crypto Wallet", color=discord.Color.green())
        embed.add_field(name="Network", value=f"{BASE_SEPOLIA.name} (testnet)", inline=False)
        display_name = discord.utils.escape_markdown(ctx.author.display_name)
        embed.description = f"{display_name}’s public wallet and balance."
        accounts = profile.get("accounts") or []
        for account in accounts[:5]:
            address = str(account.get("address") or "Unavailable")
            embed.add_field(
                name="Wallet Address",
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
            status = await self.wallet_provider.get_delegation_status(
                profile, BASE_SEPOLIA.key
            )
            if status["active"]:
                expiry = datetime.fromisoformat(
                    status["expires_at"].replace("Z", "+00:00")
                )
                await ctx.send(
                    "Your wallet is already authorized for limited signing until "
                    f"<t:{int(expiry.timestamp())}:F>. No new authorization was created."
                )
                return
            expires_at = await self.send_authorization_link(ctx.author, profile)
        except (RuntimeError, WalletProviderError) as exc:
            await ctx.send(f"Wallet authorization is unavailable: {exc}")
            return
        await ctx.send(f"I sent your wallet authorization link by DM; it expires <t:{expires_at}:R>.")

    async def send_authorization_link(self, user, profile: dict) -> int:
        """DM a short-lived authorization link and return its expiry."""
        approval_base_url = str(await self.config.approval_base_url() or "").rstrip("/")
        token, expires_at = await self.create_authorization_handoff(user.id, profile)
        link = f"{approval_base_url}/session.html#handoff={quote(token, safe='')}"
        embed = discord.Embed(
            title="Authorize Crypto Wallet",
            description=(
                "Grant the bot limited signing access to this Base Sepolia test wallet."
            ),
            color=discord.Color.blurple(),
        )
        embed.add_field(name="Link expires", value=f"<t:{expires_at}:R>", inline=True)
        embed.add_field(name="Authorization duration", value="24 hours", inline=True)
        embed.add_field(name="Scope", value="This wallet only", inline=False)
        embed.set_footer(text="Do not share or forward this authorization.")
        view = discord.ui.View(timeout=3 * 60)
        view.add_item(
            discord.ui.Button(
                label="Authorize wallet",
                emoji="🔐",
                style=discord.ButtonStyle.link,
                url=link,
            )
        )
        try:
            await user.send(embed=embed, view=view)
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

    @wallet.command(name="revoke", aliases=("deauthorize",))
    async def wallet_revoke(self, ctx: commands.Context):
        """Revoke limited signing authorization for your Base Sepolia wallet."""
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
        if not status["active"]:
            await ctx.send("No active signing authorization exists for this wallet.")
            return
        expiry = datetime.fromisoformat(status["expires_at"].replace("Z", "+00:00"))
        await ctx.send(
            "Revoke limited signing authorization for "
            f"{status['address']}?\n"
            "This does not delete the wallet or move funds. Future sends will require "
            f"authorization again. The current authorization expires <t:{int(expiry.timestamp())}:R>.",
            view=WalletRevocationView(self, ctx.author.id, profile),
        )

    async def revoke_authorization_interaction(
        self, interaction: discord.Interaction, view: WalletRevocationView
    ) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            await self.wallet_provider.revoke_authorization(view.profile, BASE_SEPOLIA.key)
            status = await self.wallet_provider.get_delegation_status(
                view.profile, BASE_SEPOLIA.key
            )
        except WalletProviderError as exc:
            await interaction.followup.send(
                f"Wallet authorization could not be revoked: {exc}", ephemeral=True
            )
            return
        if status["active"]:
            await interaction.followup.send(
                "CDP still reports this wallet authorization as active; no success was recorded.",
                ephemeral=True,
            )
            return
        view.disable_controls()
        await interaction.message.edit(view=view)
        await interaction.followup.send(
            "Limited signing authorization was revoked. Your wallet and funds were not changed. "
            "The next send will require authorization again.",
            ephemeral=True,
        )

    @staticmethod
    def _account_for_network(profile: dict, network_key: str) -> dict | None:
        for account in profile.get("accounts") or []:
            if account.get("network") == network_key:
                return account
        return None

    @staticmethod
    def _intent_embed(intent: TransactionIntent, network, color) -> discord.Embed:
        titles = {
            IntentStatus.SUBMITTED: "Submitted wallet transaction",
            IntentStatus.CONFIRMED: "Confirmed wallet transaction",
            IntentStatus.FAILED: "Failed wallet transaction",
        }
        colors = {
            IntentStatus.PENDING: discord.Color.blurple(),
            IntentStatus.PROCESSING: discord.Color.blurple(),
            IntentStatus.APPROVED: discord.Color.blurple(),
            IntentStatus.SUBMITTED: discord.Color.gold(),
            IntentStatus.CONFIRMED: discord.Color.green(),
            IntentStatus.FAILED: discord.Color.red(),
            IntentStatus.REJECTED: discord.Color.red(),
            IntentStatus.EXPIRED: discord.Color.dark_grey(),
        }
        embed = discord.Embed(
            title=titles.get(intent.status, "Wallet transaction intent"),
            color=colors.get(intent.status, color),
        )
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
        if intent.user_operation_hash:
            embed.add_field(
                name="User operation",
                value=f"`{intent.user_operation_hash}`",
                inline=False,
            )
        if intent.block_number is not None:
            embed.add_field(name="Block", value=f"`{intent.block_number}`", inline=True)
        if intent.status in {IntentStatus.PENDING, IntentStatus.PROCESSING}:
            footer = "Unsigned testnet intent — no transaction has been sent"
        elif intent.status is IntentStatus.CONFIRMED:
            footer = "Confirmed Base Sepolia testnet transaction"
        elif intent.status is IntentStatus.SUBMITTED:
            footer = "Submitted to Base Sepolia — awaiting confirmation"
        else:
            footer = "Base Sepolia testnet transaction intent"
        embed.set_footer(text=footer)
        return embed

    async def _refresh_submitted_intent(self, user_id: int, intent_id: str) -> TransactionIntent:
        intent = await self._stored_intent(user_id, intent_id)
        if intent is None:
            raise RuntimeError("The stored transaction intent is unavailable.")
        if intent.status is not IntentStatus.SUBMITTED:
            return intent
        profile = await self.config.user_from_id(user_id).profile()
        if profile is None or intent.profile_id != str(profile.get("profile_id") or ""):
            raise RuntimeError("The wallet profile no longer matches this intent.")
        result = await self.wallet_provider.get_transaction_status(profile, intent)
        provider_status = result["provider_status"]
        final_status = (
            IntentStatus.CONFIRMED if provider_status == "complete"
            else IntentStatus.FAILED if provider_status in {"dropped", "failed"}
            else IntentStatus.SUBMITTED
        )
        async with self.config.user_from_id(user_id).intents() as intents:
            stored = intents.get(intent_id)
            if not stored or stored.get("user_operation_hash") != intent.user_operation_hash:
                raise RuntimeError("The stored operation changed while its status was checked.")
            stored["status"] = final_status.value
            stored["provider_status"] = provider_status
            stored["transaction_hash"] = result["transaction_hash"]
            stored["block_number"] = result["block_number"]
            return TransactionIntent.from_dict(stored)

    async def _poll_submitted_intent(self, user_id: int, intent_id: str, user) -> None:
        try:
            for _ in range(CONFIRMATION_POLL_ATTEMPTS):
                await asyncio.sleep(CONFIRMATION_POLL_SECONDS)
                intent = await self._refresh_submitted_intent(user_id, intent_id)
                if intent.status is IntentStatus.SUBMITTED:
                    continue
                network = NETWORKS[intent.network]
                if intent.status is IntentStatus.CONFIRMED and intent.transaction_hash:
                    explorer_url = (
                        f"{network.explorer_url}/tx/{intent.transaction_hash}"
                    )
                    await user.send(
                        "Transaction confirmed on Base Sepolia.\n"
                        f"**TXID:** [{intent.transaction_hash}]({explorer_url})\n"
                        "**Copy TXID:**\n"
                        f"```text\n{intent.transaction_hash}\n```"
                    )
                elif intent.status is IntentStatus.FAILED:
                    await user.send(
                        f"Transaction `{intent.intent_id}` failed or was dropped by CDP.",
                    )
                return
        except (WalletProviderError, RuntimeError, discord.HTTPException):
            return

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
        try:
            current_balance = await self.wallet_provider.get_native_balance(
                intent.from_address, intent.network
            )
        except WalletProviderError as exc:
            await interaction.followup.send(
                f"The final balance check failed: {exc}", ephemeral=True
            )
            return
        if current_balance < intent.value_wei + intent.estimated_gas_fee_wei:
            await interaction.followup.send(
                "The wallet balance no longer covers the displayed total. Create a new "
                "transaction preview.",
                ephemeral=True,
            )
            return
        async with self.config.user_from_id(view.user_id).intents() as intents:
            current_data = intents.get(intent.intent_id)
            try:
                current = TransactionIntent.from_dict(current_data)
            except (KeyError, TypeError, ValueError):
                current = None
            if current is None or current.status is not IntentStatus.PENDING:
                await interaction.followup.send(
                    "This transaction is no longer pending.", ephemeral=True
                )
                return
            if current.to_dict() != intent.to_dict():
                await interaction.followup.send(
                    "The transaction changed after it was displayed. Create a new preview.",
                    ephemeral=True,
                )
                return
            current.status = IntentStatus.PROCESSING
            intents[intent.intent_id] = current.to_dict()
            intent = current
        view.disable_controls()
        network = NETWORKS[intent.network]
        color = interaction.message.embeds[0].color if interaction.message.embeds else None
        await interaction.message.edit(
            embed=self._intent_embed(intent, network, color), view=view
        )
        try:
            result = await self.wallet_provider.submit_transaction(profile, intent)
        except WalletProviderError as exc:
            async with self.config.user_from_id(view.user_id).intents() as intents:
                stored = intents.get(intent.intent_id)
                if stored and stored.get("status") == IntentStatus.PROCESSING.value:
                    stored["provider_status"] = "unknown"
            await interaction.followup.send(
                f"Submission outcome is uncertain: {exc} Do not create a replacement transfer; "
                "check this intent before taking further action.",
                ephemeral=True,
            )
            return
        provider_status = result["provider_status"]
        if provider_status == "complete":
            final_status = IntentStatus.CONFIRMED
        elif provider_status in {"dropped", "failed"}:
            final_status = IntentStatus.FAILED
        else:
            final_status = IntentStatus.SUBMITTED
        async with self.config.user_from_id(view.user_id).intents() as intents:
            stored = intents.get(intent.intent_id)
            if not stored or stored.get("status") != IntentStatus.PROCESSING.value:
                await interaction.followup.send(
                    "CDP accepted the operation, but its local intent state could not be "
                    "reconciled automatically. Do not submit another transfer.",
                    ephemeral=True,
                )
                return
            stored["status"] = final_status.value
            stored["provider_status"] = provider_status
            stored["user_operation_hash"] = result["user_operation_hash"]
            stored["transaction_hash"] = result["transaction_hash"]
            stored["block_number"] = result["block_number"]
            intent = TransactionIntent.from_dict(stored)
        await interaction.message.edit(
            embed=self._intent_embed(intent, network, color), view=view
        )
        if final_status is IntentStatus.CONFIRMED:
            message = "Transaction confirmed on Base Sepolia."
        elif final_status is IntentStatus.FAILED:
            message = "CDP reported that the transaction failed or was dropped."
        else:
            message = "Transaction submitted to Base Sepolia and awaiting confirmation."
        await interaction.followup.send(message, ephemeral=True)
        if final_status is IntentStatus.SUBMITTED:
            task = self.bot.loop.create_task(
                self._poll_submitted_intent(view.user_id, intent.intent_id, interaction.user)
            )
            self.confirmation_tasks.add(task)
            task.add_done_callback(self.confirmation_tasks.discard)

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

    @wallet.command(name="intent", aliases=("transaction",))
    async def wallet_intent(self, ctx: commands.Context, reference: str):
        """Show one of your private stored intents by bot reference."""
        intents = await self.expire_and_trim_intents(ctx.author)
        lookup = reference.strip()
        data = intents.get(lookup)
        if data is None:
            await ctx.send(
                "No stored intent with that bot reference belongs to your wallet profile."
            )
            return
        try:
            intent = TransactionIntent.from_dict(data)
        except (KeyError, TypeError, ValueError):
            await ctx.send("That stored transaction intent is invalid and cannot be displayed.")
            return
        if intent.status is IntentStatus.SUBMITTED:
            try:
                intent = await self._refresh_submitted_intent(ctx.author.id, intent.intent_id)
            except (RuntimeError, WalletProviderError) as exc:
                await ctx.send(f"The latest transaction status is unavailable: {exc}")
                return
        network = NETWORKS.get(intent.network)
        if network is None:
            await ctx.send("That transaction intent references an unsupported network.")
            return
        await ctx.send(embed=self._intent_embed(intent, network, await ctx.embed_color()))

    @wallet.command(name="tx")
    async def wallet_tx(self, ctx: commands.Context, txid: str):
        """Look up a public Base Sepolia transaction by transaction hash."""
        lookup = txid.strip().lower()
        if len(lookup) != 66 or not lookup.startswith("0x"):
            await ctx.send("Enter a complete transaction hash beginning with `0x`.")
            return
        try:
            int(lookup[2:], 16)
        except ValueError:
            await ctx.send("Enter a valid hexadecimal transaction hash.")
            return
        try:
            transaction = await get_transaction(lookup)
        except BaseRpcError as exc:
            await ctx.send(f"Base Sepolia transaction lookup is unavailable: {exc}")
            return
        if transaction is None:
            await ctx.send("No Base Sepolia transaction was found with that TXID.")
            return
        success = transaction["success"]
        status = "Pending" if success is None else "Confirmed" if success else "Failed"
        color = (
            discord.Color.gold() if success is None
            else discord.Color.green() if success
            else discord.Color.red()
        )
        embed = discord.Embed(title=f"{status} Base Sepolia transaction", color=color)
        if str(transaction["to_address"] or "").lower() == "0x5ff137d4b0fdcd49dca30c7cf57e578a026d2789":
            embed.description = (
                "This is an account-abstraction bundle transaction. The wallet transfer below "
                "appears under internal transactions in the explorer."
            )
        embed.add_field(name="Status", value=status, inline=True)
        embed.add_field(
            name="Network",
            value=f"{BASE_SEPOLIA.name} (`{BASE_SEPOLIA.chain_id}`)",
            inline=True,
        )
        if transaction["block_number"] is not None:
            embed.add_field(
                name="Block", value=f"`{transaction['block_number']}`", inline=True
            )
        embed.add_field(
            name="TXID",
            value=f"[{lookup}]({BASE_SEPOLIA.explorer_url}/tx/{lookup})",
            inline=False,
        )
        embed.add_field(
            name="Bundle sender",
            value=f"`{transaction['from_address']}`",
            inline=False,
        )
        embed.add_field(
            name="Called contract",
            value=f"`{transaction['to_address'] or 'Contract creation'}`",
            inline=False,
        )
        wallet_transfers = transaction["wallet_transfers"]
        if wallet_transfers:
            for index, transfer in enumerate(wallet_transfers[:5], start=1):
                label = (
                    "Wallet transfer"
                    if len(wallet_transfers) == 1
                    else f"Wallet transfer {index}"
                )
                embed.add_field(
                    name=label,
                    value=(
                        f"**{format_wei_as_eth(transfer['value_wei'])} "
                        f"{BASE_SEPOLIA.native_symbol}**\n"
                        f"From: `{transfer['from_address']}`\n"
                        f"To: `{transfer['to_address']}`"
                    ),
                    inline=False,
                )
        else:
            embed.add_field(
                name="Value",
                value=(
                    f"{format_wei_as_eth(transaction['value_wei'])} "
                    f"{BASE_SEPOLIA.native_symbol}"
                ),
                inline=True,
            )
        embed.set_footer(text="Public on-chain transaction data")
        await ctx.send(embed=embed)

    @wallet.command(name="transactions", aliases=("history",))
    async def wallet_transactions(self, ctx: commands.Context):
        """Browse your private stored wallet transaction history."""
        stored = await self.expire_and_trim_intents(ctx.author)
        intents = []
        for data in stored.values():
            try:
                intents.append(TransactionIntent.from_dict(data))
            except (KeyError, TypeError, ValueError):
                continue
        intents.sort(key=lambda intent: intent.created_at, reverse=True)
        if not intents:
            await ctx.send("You have no stored wallet transactions yet.")
            return
        view = WalletHistoryView(ctx.author.id, intents, await ctx.embed_color())
        await ctx.send(embed=view.embed(), view=view)
