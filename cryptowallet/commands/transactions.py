import re
import secrets
import time

import discord
from redbot.core import commands

from ..core.models import IntentStatus, TransactionIntent
from ..core.networks import (
    BASE_SEPOLIA, NETWORKS, SOLANA_DEVNET, ChainFamily, NetworkCapability
)
from ..providers import WalletProviderError
from ..core.validation import (
    format_atomic_amount,
    normalize_address_for_network,
    parse_native_amount,
)
from .constants import INTENT_LIFETIME_SECONDS, WALLET_PROVIDER_COOLDOWN_SECONDS
from .core import WalletCoreCommands
from .views import WalletIntentView


class WalletTransactionCommands:
    """Transaction intent creation, approval, and status commands."""

    @staticmethod
    def _send_network(value: str):
        aliases = {
            "base": BASE_SEPOLIA.key,
            "base-sepolia": BASE_SEPOLIA.key,
            "eth": "ethereum-sepolia",
            "ethereum": "ethereum-sepolia",
            "ethereum-sepolia": "ethereum-sepolia",
            "arb": "arbitrum-sepolia",
            "arbitrum": "arbitrum-sepolia",
            "arbitrum-sepolia": "arbitrum-sepolia",
            "pol": "polygon-amoy",
            "polygon": "polygon-amoy",
            "polygon-amoy": "polygon-amoy",
            "avax": "avalanche-fuji",
            "avalanche": "avalanche-fuji",
            "avalanche-fuji": "avalanche-fuji",
            "sol": SOLANA_DEVNET.key,
            "solana": SOLANA_DEVNET.key,
            "solana-devnet": SOLANA_DEVNET.key,
        }
        return NETWORKS.get(aliases.get(value.strip().lower(), ""))

    @staticmethod
    def _intent_quote(intent: TransactionIntent) -> tuple:
        """Return the exact security-sensitive quote represented by an approval view."""
        return (
            intent.profile_id,
            intent.network,
            intent.from_address,
            intent.to_address,
            intent.value_wei,
            intent.estimated_gas_fee_wei,
            intent.gas_sponsored,
            intent.created_at,
            intent.expires_at,
        )

    async def _send_recipient_address(self, ctx, value: str, network) -> str | None:
        """Resolve a direct address or lazily provision a mentioned server member."""
        mention = re.fullmatch(r"<@!?(\d+)>", value.strip())
        if mention is None:
            try:
                return normalize_address_for_network(value, network)
            except ValueError as exc:
                await ctx.send(str(exc))
                return None
        if ctx.guild is None:
            await ctx.send("Member wallet recipients must be mentioned from a server.")
            return None
        target_id = int(mention.group(1))
        target = next(
            (
                member
                for member in getattr(getattr(ctx, "message", None), "mentions", ())
                if member.id == target_id
            ),
            None,
        )
        if target is None:
            target = ctx.guild.get_member(target_id)
        if target is None:
            await ctx.send("Mention a current member of this server as the wallet recipient.")
            return None
        if target.bot:
            await ctx.send("Wallet transfers cannot provision bot accounts.")
            return None
        target_profile = await self._wallet_profile_for_user_or_error(ctx, target)
        if target_profile is None:
            return None
        target_account = self._account_for_network(target_profile, network.key)
        if target_account is None:
            await ctx.send(f"{target.display_name}'s wallet has no account for {network.name}.")
            return None
        try:
            return normalize_address_for_network(
                str(target_account.get("address") or ""), network
            )
        except ValueError:
            await ctx.send("The mentioned member's stored wallet address is invalid.")
            return None

    async def _send_value_allowed(self, ctx, network, value_atomic: int) -> bool:
        """Enforce configured ceilings and require explicit limits for production."""
        limits = await self.config.send_limits_atomic()
        raw_limit = limits.get(network.key)
        if raw_limit is None:
            if network.testnet:
                return True
            await ctx.send(
                f"Sending on {network.name} is blocked because no owner-approved "
                "transaction limit is configured."
            )
            return False
        try:
            limit = int(raw_limit)
        except (TypeError, ValueError):
            limit = 0
        if limit <= 0:
            await ctx.send(
                f"Sending on {network.name} is blocked because its transaction "
                "limit configuration is invalid."
            )
            return False
        if value_atomic > limit:
            await ctx.send(
                f"This transfer exceeds the {network.name} per-transaction limit of "
                f"`{format_atomic_amount(limit, network)} {network.native_symbol}`."
            )
            return False
        return True

    @staticmethod
    def _intent_result_view(intent: TransactionIntent, network):
        if not intent.transaction_hash:
            return None
        view = discord.ui.View(timeout=None)
        view.add_item(discord.ui.Button(
            label="View transaction",
            style=discord.ButtonStyle.link,
            url=network.explorer_transaction_url(intent.transaction_hash),
        ))
        return view

    @staticmethod
    def _intent_embed(intent: TransactionIntent, network, color) -> discord.Embed:
        titles = {
            IntentStatus.UNCERTAIN: "Transaction outcome uncertain",
            IntentStatus.SUBMITTED: "Submitted wallet transaction",
            IntentStatus.CONFIRMED: "Confirmed wallet transaction",
            IntentStatus.FAILED: "Failed wallet transaction",
        }
        colors = {
            IntentStatus.PENDING: discord.Color.blurple(),
            IntentStatus.PROCESSING: discord.Color.blurple(),
            IntentStatus.UNCERTAIN: discord.Color.orange(),
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
        embed.add_field(name="Network", value=f"{network.name} ({network.reference_label} `{network.reference}`)", inline=True)
        embed.add_field(
            name="Amount",
            value=f"{format_atomic_amount(intent.value_wei, network)} {network.native_symbol}",
            inline=True,
        )
        gas_value = f"{format_atomic_amount(intent.estimated_gas_fee_wei, network)} {network.native_symbol}"
        if intent.gas_sponsored:
            gas_value += " (sponsored by CDP)"
        embed.add_field(name="Estimated gas fee", value=gas_value, inline=True)
        embed.add_field(
            name="Estimated total",
            value=(
                f"{format_atomic_amount(intent.value_wei + intent.estimated_gas_fee_wei, network)} "
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
                name="TXID",
                value=f"```text\n{intent.transaction_hash}\n```",
                inline=False,
            )
        if intent.user_operation_hash:
            embed.add_field(
                name="User operation",
                value=f"`{intent.user_operation_hash}`",
                inline=False,
            )
        if intent.block_number is not None:
            embed.add_field(
                name="Slot" if network.family is ChainFamily.SOLANA else "Block",
                value=f"`{intent.block_number}`",
                inline=True,
            )
        if intent.status in {IntentStatus.PENDING, IntentStatus.PROCESSING}:
            footer = "Unsigned testnet intent — no transaction has been sent"
        elif intent.status is IntentStatus.UNCERTAIN:
            footer = "Submission outcome unknown — do not send a replacement"
        elif intent.status is IntentStatus.CONFIRMED:
            footer = f"Confirmed {network.name} testnet transaction"
        elif intent.status is IntentStatus.SUBMITTED:
            footer = f"Submitted to {network.name} — awaiting confirmation"
        else:
            footer = f"{network.name} testnet transaction intent"
        embed.set_footer(text=footer)
        return embed

    async def _refresh_submitted_intent(self, user_id: int, intent_id: str) -> TransactionIntent:
        intent = await self._stored_intent(user_id, intent_id)
        if intent is None:
            raise RuntimeError("The stored transaction intent is unavailable.")
        if intent.status not in {IntentStatus.SUBMITTED, IntentStatus.UNCERTAIN}:
            return intent
        was_uncertain = intent.status is IntentStatus.UNCERTAIN
        if was_uncertain and not (intent.transaction_hash or intent.user_operation_hash):
            raise RuntimeError(
                "This uncertain intent has no provider transaction identifier and cannot "
                "be reconciled automatically."
            )
        profile = await self.config.user_from_id(user_id).profile()
        if profile is None or intent.profile_id != str(profile.get("profile_id") or ""):
            raise RuntimeError("The wallet profile no longer matches this intent.")
        result = await self.wallet_provider.get_transaction_status(profile, intent)
        provider_status = result["provider_status"]
        final_status = (
            IntentStatus.CONFIRMED if provider_status == "complete"
            else IntentStatus.FAILED if provider_status in {"dropped", "failed"}
            else IntentStatus.UNCERTAIN if was_uncertain
            else IntentStatus.SUBMITTED
        )
        async with self.config.user_from_id(user_id).intents() as intents:
            stored = intents.get(intent_id)
            if (
                not stored
                or stored.get("status") != intent.status.value
                or stored.get("user_operation_hash") != intent.user_operation_hash
                or stored.get("transaction_hash") != intent.transaction_hash
            ):
                raise RuntimeError("The stored operation changed while its status was checked.")
            stored["status"] = final_status.value
            stored["provider_status"] = provider_status
            stored["transaction_hash"] = result["transaction_hash"]
            stored["block_number"] = result["block_number"]
            if final_status in {IntentStatus.CONFIRMED, IntentStatus.FAILED}:
                stored["confirmation_delivered"] = False
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
        if await self.config.user_from_id(view.user_id).security_locked():
            await interaction.followup.send(
                "This wallet is emergency-locked. Nothing was submitted; only the bot "
                "owner can unlock it after identity review.",
                ephemeral=True,
            )
            return
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
                    stored["status"] = IntentStatus.UNCERTAIN.value
                    stored["provider_status"] = "unknown"
                    intent = TransactionIntent.from_dict(stored)
            await interaction.message.edit(
                embed=self._intent_embed(intent, network, color), view=view
            )
            await interaction.followup.send(
                f"Submission outcome is uncertain: {exc} Do not create a replacement transfer; "
                "keep this intent ID and contact the bot owner before taking further action.",
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
            embed=self._intent_embed(intent, network, color),
            view=(
                self._intent_result_view(intent, network)
                if intent.transaction_hash else view
            ),
        )
        if final_status is IntentStatus.CONFIRMED:
            message = f"Transaction confirmed on {network.name}."
        elif final_status is IntentStatus.FAILED:
            message = "CDP reported that the transaction failed or was dropped."
        else:
            message = f"Transaction submitted to {network.name} and awaiting confirmation."
        await interaction.followup.send(message, ephemeral=True)
        if final_status is IntentStatus.SUBMITTED:
            await self.schedule_confirmation(
                view.user_id,
                intent.intent_id,
                interaction.message,
            )

    @WalletCoreCommands.wallet.command(name="send")
    async def wallet_send(
        self, ctx: commands.Context, network_or_address: str,
        address_or_amount: str, amount: str = None
    ):
        """Prepare an unsigned native-token transfer on the enabled test network."""
        if not await self._wallet_sensitive_allowed(ctx):
            return
        if not await self._wallet_read_allowed(
            ctx, "send", WALLET_PROVIDER_COOLDOWN_SECONDS
        ):
            return
        profile = await self._wallet_profile_or_error(ctx)
        if profile is None:
            return
        if amount is None:
            network = NETWORKS.get(await self.config.default_network())
            to_address, amount = network_or_address, address_or_amount
        else:
            network = self._send_network(network_or_address)
            to_address = address_or_amount
        if (
            network is None
            or not network.testnet
            or not network.supports(NetworkCapability.SEND)
            or not self.wallet_provider.supports(network.key, NetworkCapability.SEND)
        ):
            if network is None:
                await ctx.send(
                    "That wallet network is unknown. Use `wallet networks` to list testnets."
                )
            else:
                await ctx.send(
                    f"Sending is not enabled for {network.name}. "
                    "Only capability-reviewed testnet send paths are available."
                )
            return
        account = self._account_for_network(profile, network.key)
        if account is None:
            await ctx.send(f"Your wallet profile has no account for {network.name}.")
            return
        try:
            from_address = normalize_address_for_network(str(account.get("address") or ""), network)
            value_wei = parse_native_amount(amount, network)
        except ValueError as exc:
            await ctx.send(str(exc))
            return
        if not await self._send_value_allowed(ctx, network, value_wei):
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
                f"Insufficient {network.name} balance. Available: "
                f"`{format_atomic_amount(balance_wei, network)} {network.native_symbol}`."
            )
            return
        recipient = await self._send_recipient_address(ctx, to_address, network)
        if recipient is None:
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
            gas_sponsored=network.supports(NetworkCapability.SPONSORSHIP),
        )
        if not intent.profile_id:
            await ctx.send("Your wallet profile is incomplete and must be linked again.")
            return
        try:
            intent = await self.wallet_provider.prepare_transaction(intent)
        except WalletProviderError as exc:
            await ctx.send(f"The transaction preview is unavailable: {exc}")
            return
        if value_wei + intent.estimated_gas_fee_wei > balance_wei:
            await ctx.send(f"Insufficient {network.name} balance for the amount and network fee.")
            return
        async with self.config.user(ctx.author).intents() as intents:
            intents[intent.intent_id] = intent.to_dict()
        await self.expire_and_trim_intents(ctx.author)
        await ctx.send(
            embed=self._intent_embed(intent, network, await ctx.embed_color()),
            view=WalletIntentView(self, ctx.author.id, intent),
        )

    @WalletCoreCommands.wallet.command(name="intent", aliases=("transaction",))
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
            (intent.status is IntentStatus.SUBMITTED or (
                intent.status is IntentStatus.UNCERTAIN
                and bool(intent.transaction_hash or intent.user_operation_hash)
            ))
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
