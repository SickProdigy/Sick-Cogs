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
HISTORY_PAGE_SIZE = 10
WALLET_SUMMARY_COOLDOWN_SECONDS = 10
WALLET_HISTORY_COOLDOWN_SECONDS = 15
WALLET_PROVIDER_COOLDOWN_SECONDS = 10
WALLET_RPC_COOLDOWN_SECONDS = 5
HISTORY_NEXT_COOLDOWN_SECONDS = 3


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


class WalletCommands:
    """User-facing wallet commands."""

    async def _wallet_read_allowed(
        self, ctx: commands.Context, key: str, cooldown_seconds: int
    ) -> bool:
        """Rate-limit provider reads for ordinary users while exempting administrators."""
        if await self.bot.is_owner(ctx.author):
            return True
        permissions = getattr(ctx.author, "guild_permissions", None)
        if permissions is not None and permissions.administrator:
            return True

        now = time.monotonic()
        cooldown_key = (ctx.author.id, key)
        allowed_at = self.wallet_read_cooldowns.get(cooldown_key, 0.0)
        if now < allowed_at:
            remaining = max(1, int(allowed_at - now + 0.999))
            await ctx.send(
                f"Please wait {remaining} second(s) before requesting that wallet data again."
            )
            return False
        self.wallet_read_cooldowns[cooldown_key] = now + cooldown_seconds
        return True

    async def _wallet_profile_or_error(self, ctx: commands.Context) -> dict | None:
        if await self.config.provider_paused():
            await ctx.send(
                "CryptoWallet provider processing is paused by the bot owner. "
                "Local wallet settings remain available."
            )
            return None
        try:
            return await self.get_or_create_wallet_profile(ctx.author)
        except WalletProviderError as exc:
            await ctx.send(f"Wallet provisioning is unavailable: {exc}")
        except RuntimeError as exc:
            await ctx.send(str(exc))
        return None

    async def _wallet_embed(
        self, ctx: commands.Context, profile: dict, user=None
    ) -> discord.Embed:
        embed = discord.Embed(title="Crypto Wallet", color=discord.Color.green())
        embed.add_field(name="Network", value=f"{BASE_SEPOLIA.name} (testnet)", inline=False)
        display_name = discord.utils.escape_markdown((user or ctx.author).display_name)
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

    @commands.group(
        name="wallet", aliases=("wallets", "cryptowallet"), invoke_without_command=True
    )
    async def wallet(self, ctx: commands.Context, member: discord.Member = None):
        """Show your wallet or another member's existing public wallet profile."""
        if not await self._wallet_read_allowed(
            ctx, "summary", WALLET_SUMMARY_COOLDOWN_SECONDS
        ):
            return
        if await self.config.provider_paused():
            await ctx.send(
                "CryptoWallet provider processing is paused by the bot owner. "
                "Local wallet settings remain available."
            )
            return
        target = member or ctx.author
        if target.id == ctx.author.id:
            profile = await self._wallet_profile_or_error(ctx)
            if profile is None:
                return
        else:
            profile = await self.config.user(target).profile()
            if profile is None:
                display_name = discord.utils.escape_markdown(target.display_name)
                await ctx.send(f"{display_name} does not have a public wallet profile yet.")
                return
        await ctx.send(embed=await self._wallet_embed(ctx, profile, target))

    @wallet.command(name="balance", aliases=("funds",))
    async def wallet_balance(self, ctx: commands.Context):
        """Show your Base Sepolia address and native ETH balance."""
        if not await self._wallet_read_allowed(
            ctx, "summary", WALLET_SUMMARY_COOLDOWN_SECONDS
        ):
            return
        profile = await self._wallet_profile_or_error(ctx)
        if profile is None:
            return
        await ctx.send(embed=await self._wallet_embed(ctx, profile))

    @wallet.command(name="notifications", aliases=("notify",))
    async def wallet_notifications(
        self, ctx: commands.Context, enabled: bool = None
    ):
        """Show or change optional wallet transaction confirmation DMs."""
        if enabled is None:
            enabled = await self.config.user(ctx.author).notifications_enabled()
            state = "enabled" if enabled else "disabled"
            await ctx.send(
                f"Wallet transaction DMs are currently **{state}**. "
                "Automatic incoming-deposit alerts are not implemented yet."
            )
            return
        await self.config.user(ctx.author).notifications_enabled.set(enabled)
        state = "enabled" if enabled else "disabled"
        await ctx.send(
            f"Wallet transaction DMs are now **{state}**. "
            "Transaction cards will continue updating either way. Automatic "
            "incoming-deposit alerts are not implemented yet."
        )

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
        if not await self._wallet_read_allowed(
            ctx, "authorization", WALLET_PROVIDER_COOLDOWN_SECONDS
        ):
            return
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
        if not await self._wallet_read_allowed(
            ctx, "authorization", WALLET_PROVIDER_COOLDOWN_SECONDS
        ):
            return
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
        if not await self._wallet_read_allowed(
            ctx, "authorization", WALLET_PROVIDER_COOLDOWN_SECONDS
        ):
            return
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
    def _activity_embed(address: str, page: dict, page_index: int, color) -> discord.Embed:
        """Render one CDP-indexed page of public address activity."""
        embed = discord.Embed(title="Your Wallet Activity", color=color)
        normalized_address = address.lower()
        for item in page["transactions"][:HISTORY_PAGE_SIZE]:
            if not isinstance(item, dict):
                continue
            content = item.get("content") or {}
            tx_hash = str(item.get("transaction_hash") or content.get("hash") or "").lower()
            if len(tx_hash) != 66 or not tx_hash.startswith("0x"):
                continue
            try:
                int(tx_hash[2:], 16)
            except ValueError:
                continue

            transfers = []
            traces = content.get("flattened_traces") or []
            if isinstance(traces, list):
                for trace in traces:
                    if not isinstance(trace, dict) or trace.get("error"):
                        continue
                    from_address = str(trace.get("from") or "")
                    to_address = str(trace.get("to") or "")
                    try:
                        raw_value = str(trace.get("value") or "0")
                        value_wei = int(
                            raw_value,
                            16 if raw_value.lower().startswith("0x") else 10,
                        )
                    except ValueError:
                        continue
                    if (
                        value_wei > 0
                        and (
                            from_address.lower() == normalized_address
                            or to_address.lower() == normalized_address
                        )
                    ):
                        transfers.append((from_address, to_address, value_wei))

            from_address = str(content.get("from") or item.get("from_address_id") or "")
            to_address = str(content.get("to") or "")
            try:
                raw_value = str(content.get("value") or "0")
                value_wei = int(
                    raw_value, 16 if raw_value.lower().startswith("0x") else 10
                )
            except ValueError:
                value_wei = 0
            if transfers:
                from_address, to_address, value_wei = transfers[0]

            from_wallet = from_address.lower() == normalized_address
            to_wallet = to_address.lower() == normalized_address
            if from_wallet and to_wallet:
                direction = "🔵 Self transfer"
            elif from_wallet:
                direction = "🔴 Sent"
            elif to_wallet:
                direction = "🟢 Received"
            else:
                direction = "⚪ Contract activity"
            amount = (
                f" · {format_wei_as_eth(value_wei)} {BASE_SEPOLIA.native_symbol}"
                if value_wei > 0
                else ""
            )
            details = [
                f"From: `{from_address or 'Unavailable'}`",
                f"To: `{to_address or 'Contract creation'}`",
                f"TXID: [{tx_hash}]({BASE_SEPOLIA.explorer_url}/tx/{tx_hash})",
            ]
            timestamp = str(content.get("block_timestamp") or "")
            try:
                created_at = int(
                    datetime.fromisoformat(timestamp.replace("Z", "+00:00")).timestamp()
                )
            except (TypeError, ValueError):
                created_at = 0
            if created_at:
                details.append(f"Confirmed <t:{created_at}:R>")
            embed.add_field(
                name=f"{direction}{amount}",
                value="\n".join(details),
                inline=False,
            )
        if not embed.fields:
            embed.description = "No indexed Base Sepolia activity was found for this wallet."
        embed.set_footer(text=f"Page {page_index + 1} · 10 transactions per page")
        return embed

    @staticmethod
    def _intent_quote(intent: TransactionIntent) -> tuple:
        """Return the exact security-sensitive quote represented by an approval view."""
        return (
            intent.profile_id,
            intent.network,
            intent.from_address.lower(),
            intent.to_address.lower(),
            intent.value_wei,
            intent.estimated_gas_fee_wei,
            intent.gas_sponsored,
            intent.created_at,
            intent.expires_at,
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
        if intent.status in {IntentStatus.PENDING, IntentStatus.PROCESSING}:
            embed.add_field(
                name="Expires", value=f"<t:{intent.expires_at}:R>", inline=True
            )
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
        if await self.config.provider_paused():
            await interaction.followup.send(
                "CryptoWallet provider processing is paused by the bot owner. "
                "This intent remains pending and nothing was submitted.",
                ephemeral=True,
            )
            return
        intent = await self._stored_intent(view.user_id, view.intent_id)
        if intent is None or intent.status is not IntentStatus.PENDING:
            await interaction.followup.send("This transaction is no longer pending.", ephemeral=True)
            return
        if self._intent_quote(intent) != view.quote:
            network = NETWORKS.get(intent.network)
            if network is None:
                await interaction.followup.send(
                    "The transaction changed to an unsupported network.", ephemeral=True
                )
                return
            view.disable_controls()
            new_view = WalletIntentView(self, view.user_id, intent)
            await interaction.message.edit(
                embed=self._intent_embed(intent, network, None),
                view=new_view,
            )
            await interaction.followup.send(
                "The transaction changed after it was displayed. Review the updated "
                "preview and approve it again.",
                ephemeral=True,
            )
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
            refreshed_intent = await self.wallet_provider.prepare_transaction(intent)
        except WalletProviderError as exc:
            await interaction.followup.send(
                f"The final transaction quote could not be refreshed: {exc}",
                ephemeral=True,
            )
            return
        if self._intent_quote(refreshed_intent) != self._intent_quote(intent):
            if (
                refreshed_intent.intent_id != intent.intent_id
                or refreshed_intent.profile_id != intent.profile_id
                or refreshed_intent.status is not IntentStatus.PENDING
            ):
                await interaction.followup.send(
                    "The refreshed transaction quote did not match this pending intent.",
                    ephemeral=True,
                )
                return
            network = NETWORKS.get(refreshed_intent.network)
            if network is None:
                await interaction.followup.send(
                    "The refreshed transaction uses an unsupported network.", ephemeral=True
                )
                return
            await self.config.user_from_id(view.user_id).intents.set_raw(
                intent.intent_id,
                value=refreshed_intent.to_dict(),
            )
            view.disable_controls()
            new_view = WalletIntentView(self, view.user_id, refreshed_intent)
            await interaction.message.edit(
                embed=self._intent_embed(refreshed_intent, network, None),
                view=new_view,
            )
            await interaction.followup.send(
                "The transaction quote changed. Review the updated preview and approve "
                "it again before anything is signed.",
                ephemeral=True,
            )
            return
        intent = refreshed_intent
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
            await self.schedule_confirmation(
                view.user_id,
                intent.intent_id,
                interaction.message,
            )

    @wallet.command(name="send")
    async def wallet_send(self, ctx: commands.Context, to_address: str, amount: str):
        """Prepare an unsigned Base Sepolia ETH transfer intent."""
        if not await self._wallet_read_allowed(
            ctx, "send", WALLET_PROVIDER_COOLDOWN_SECONDS
        ):
            return
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
            view=WalletIntentView(self, ctx.author.id, intent),
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
        if (
            intent.status is IntentStatus.SUBMITTED
            and not await self.config.provider_paused()
        ):
            if not await self._wallet_read_allowed(
                ctx, "intent", WALLET_PROVIDER_COOLDOWN_SECONDS
            ):
                return
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

    @wallet.command(name="txid")
    async def wallet_txid(self, ctx: commands.Context, txid: str):
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
        if not await self._wallet_read_allowed(
            ctx, "rpc", WALLET_RPC_COOLDOWN_SECONDS
        ):
            return
        try:
            transaction = await get_transaction(lookup)
        except BaseRpcError as exc:
            await ctx.send(f"Base Sepolia transaction lookup is unavailable: {exc}")
            return
        if transaction is None:
            await ctx.send("No Base Sepolia transaction was found with that TXID.")
            return
        stored_intent = None
        for data in (await self.expire_and_trim_intents(ctx.author)).values():
            if str(data.get("transaction_hash") or "").lower() != lookup:
                continue
            try:
                stored_intent = TransactionIntent.from_dict(data)
            except (KeyError, TypeError, ValueError):
                pass
            break
        wallet_transfers = transaction["wallet_transfers"]
        if stored_intent is not None:
            wallet_transfers = [
                {
                    "from_address": stored_intent.from_address,
                    "to_address": stored_intent.to_address,
                    "value_wei": stored_intent.value_wei,
                }
            ]
        success = transaction["success"]
        status = "Pending" if success is None else "Confirmed" if success else "Failed"
        color = (
            discord.Color.gold() if success is None
            else discord.Color.green() if success
            else discord.Color.red()
        )
        embed = discord.Embed(title=f"{status} Base Sepolia transaction", color=color)
        if (
            str(transaction["to_address"] or "").lower()
            == "0x5ff137d4b0fdcd49dca30c7cf57e578a026d2789"
        ):
            embed.description = (
                "This smart-account transfer appears under internal transactions "
                "in the explorer."
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
        if wallet_transfers:
            for index, transfer in enumerate(wallet_transfers[:5], start=1):
                suffix = "" if len(wallet_transfers) == 1 else f" {index}"
                embed.add_field(
                    name=f"From{suffix}",
                    value=f"`{transfer['from_address']}`",
                    inline=False,
                )
                embed.add_field(
                    name=f"To{suffix}",
                    value=f"`{transfer['to_address']}`",
                    inline=False,
                )
                embed.add_field(
                    name=f"Value{suffix}",
                    value=(
                        f"{format_wei_as_eth(transfer['value_wei'])} "
                        f"{BASE_SEPOLIA.native_symbol}"
                    ),
                    inline=True,
                )
        elif wallet_transfers is None:
            embed.add_field(
                name="Transfer details",
                value="Temporarily unavailable; use the explorer link above.",
                inline=False,
            )
        else:
            embed.add_field(
                name="From",
                value=f"`{transaction['from_address']}`",
                inline=False,
            )
            embed.add_field(
                name="To",
                value=f"`{transaction['to_address'] or 'Contract creation'}`",
                inline=False,
            )
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

    @wallet.command(name="transactions", aliases=("tx", "trans", "history"))
    async def wallet_transactions(self, ctx: commands.Context):
        """Browse your wallet's indexed incoming and outgoing blockchain activity."""
        if not await self._wallet_read_allowed(
            ctx, "history", WALLET_HISTORY_COOLDOWN_SECONDS
        ):
            return
        profile = await self._wallet_profile_or_error(ctx)
        if profile is None:
            return
        account = self._account_for_network(profile, BASE_SEPOLIA.key)
        if account is None:
            await ctx.send("Your wallet profile has no Base Sepolia account.")
            return
        try:
            address = normalize_evm_address(str(account.get("address") or ""))
            page = await self.wallet_provider.get_transaction_history(
                address,
                BASE_SEPOLIA.key,
                limit=HISTORY_PAGE_SIZE,
            )
        except (ValueError, WalletProviderError) as exc:
            await ctx.send(f"Wallet activity is unavailable: {exc}")
            return
        view = WalletHistoryView(
            self,
            ctx.author.id,
            address,
            page,
            discord.Color.blurple(),
        )
        await ctx.send(embed=view.embed(), view=view)
