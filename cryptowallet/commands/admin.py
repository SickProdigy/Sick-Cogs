import io
import json
import logging
import re
import time
from urllib.parse import urlparse

import discord
from redbot.core import commands

from ..core.models import IntentStatus
from ..core.networks import BASE_MAINNET, BASE_SEPOLIA, NETWORKS, NetworkCapability
from ..core.validation import (
    format_atomic_amount,
    normalize_evm_address,
    parse_native_amount,
)
from ..providers import WalletProviderError
from ..backend.config import MAINNET_ENABLE_ACKNOWLEDGEMENT
from ..backend.usage import (
    NODE_FREE_BILLING_UNITS,
    NODE_SAFETY_TARGET,
    WALLET_FREE_OPERATIONS,
    WALLET_SAFETY_TARGET,
)


log = logging.getLogger("red.Sick-Cogs.CryptoWallet")


TOTP_RESET_ACKNOWLEDGEMENT = "I CONFIRM IDENTITY REVIEW AND RESET 2FA"


NETWORK_EMOJI_NAMES = {
    "base-sepolia": "base",
    "ethereum-sepolia": "ethereum",
    "arbitrum-sepolia": "arbitrum",
    "polygon-amoy": "polygon",
    "avalanche-fuji": "avalanche",
    "solana-devnet": "solana",
    "optimism": "optimism",
    "bnb": "bnb",
    "zora": "zora",
    "tron": "tron",
    "linea": "linea",
}
NETWORK_EMOJI_ALIASES = {
    alias: key
    for key, name in NETWORK_EMOJI_NAMES.items()
    for alias in (key, name, key.replace("-", "_"))
}


class WalletAdminCommands:
    """Owner-only wallet integration commands."""

    @commands.group(name="walletset", invoke_without_command=True)
    @commands.is_owner()
    async def walletset(self, ctx: commands.Context):
        """Configure CryptoWallet.

        Inspects and configures wallet security, provider, token, and companion settings.
        """

        await ctx.send_help()


    def _wallet_user_id(self, reference: str) -> int | None:
        """Parse a raw Discord snowflake or an actual user mention."""
        match = re.fullmatch(r"<@!?(\d+)>|(\d+)", reference.strip())
        if match is None:
            return None
        user_id = int(match.group(1) or match.group(2))
        return user_id if 0 < user_id < 2**64 else None

    @walletset.command(name="lock", aliases=("freeze",))
    @commands.is_owner()
    async def walletset_lock(self, ctx: commands.Context, target: str):
        """Lock a user wallet.

        Emergency-locks a user wallet and revokes bot signing authorization.
        """
        user_id = self._wallet_user_id(target)
        if user_id is None:
            await ctx.send("Provide a Discord user mention or numeric Discord user ID.")
            return
        user_config = self.config.user_from_id(user_id)
        mention = f"<{chr(64)}{user_id}>"
        already_locked = await user_config.security_locked()
        await user_config.security_locked.set(True)
        await user_config.security_locked_at.set(int(time.time()))
        await user_config.security_lock_source.set("owner")
        async with user_config.intents() as intents:
            for intent in intents.values():
                if intent.get("status") == "pending":
                    intent["status"] = "rejected"
        profile = await user_config.profile()
        revocation = "No stored wallet profile needed provider revocation."
        if profile is not None:
            try:
                await self.wallet_provider.revoke_authorization(profile, BASE_SEPOLIA.key)
                revocation = "The current bot signing authorization was revoked."
            except WalletProviderError:
                log.exception("Emergency wallet-lock delegation revocation failed")
                revocation = (
                    "CDP revocation could not be confirmed; the local lock remains active. "
                    "Retry this command when CDP is available."
                )
        state = "remains" if already_locked else "is now"
        await ctx.send(
            f"{mention}’s wallet {state} emergency-locked. {revocation} "
            "Only a bot owner can unlock it."
        )

    @walletset.command(name="2fareset", aliases=("totpreset",))
    @commands.is_owner()
    async def walletset_2fa_reset(
        self, ctx: commands.Context, target: str, *, acknowledgement: str = ""
    ):
        """Reset lost authenticator state after locked-wallet identity review."""

        user_id = self._wallet_user_id(target)
        if user_id is None:
            await ctx.send("Provide a Discord user mention or numeric Discord user ID.")
            return
        user_config = self.config.user_from_id(user_id)
        mention = f"<{chr(64)}{user_id}>"
        if not await user_config.security_locked():
            await ctx.send(
                f"{mention} must remain emergency-locked before authenticator recovery."
            )
            return
        if acknowledgement != TOTP_RESET_ACKNOWLEDGEMENT:
            await ctx.send(
                "After independent identity review, repeat the command with this exact "
                f"acknowledgement: `{TOTP_RESET_ACKNOWLEDGEMENT}`"
            )
            return
        if await user_config.totp_security() is None:
            await ctx.send(f"{mention} has no stored authenticator enrollment.")
            return
        await self.disable_user_totp(user_id)
        await ctx.send(
            f"{mention} authenticator state was reset after owner identity review. "
            "The wallet remains emergency-locked; separately review authorization before unlocking."
        )

    @walletset.command(name="unlock", aliases=("unfreeze",))
    @commands.is_owner()
    async def walletset_unlock(self, ctx: commands.Context, target: str):
        """Unlock a user wallet.

        Removes an emergency wallet lock after an independent identity review.
        """
        user_id = self._wallet_user_id(target)
        if user_id is None:
            await ctx.send("Provide a Discord user mention or numeric Discord user ID.")
            return
        user_config = self.config.user_from_id(user_id)
        mention = f"<{chr(64)}{user_id}>"
        if not await user_config.security_locked():
            await ctx.send(f"{mention}’s wallet is not emergency-locked.")
            return
        await user_config.security_locked.set(False)
        await user_config.security_locked_at.set(0)
        await user_config.security_lock_source.set(None)
        await ctx.send(
            f"{mention}’s wallet is unlocked. No signing authorization was "
            "created; their next send may require protected authorization."
        )

    @walletset.group(name="token", aliases=("tokens",), invoke_without_command=True)
    @commands.is_owner()
    async def walletset_token(self, ctx: commands.Context):
        """Moderate shared tokens.

        Reviews recognized, hidden, and banned shared wallet tokens.
        """
        await ctx.invoke(self.walletset_token_list)

    async def _walletset_token_status(
        self, ctx: commands.Context, network_key: str, contract_address: str, status: str
    ):
        network = NETWORKS.get(network_key.strip().lower())
        try:
            contract = normalize_evm_address(contract_address).lower()
        except ValueError:
            await ctx.send("Enter a valid EVM token contract address.")
            return
        if network is None:
            await ctx.send("That enabled network is unknown.")
            return
        async with self.config.token_registry() as registry:
            entry = (registry.get(network.key) or {}).get(contract)
            if entry is None:
                await ctx.send("That token is not registered.")
                return
            entry["status"] = status
            entry["moderated_at"] = int(time.time())
            entry["moderated_by"] = ctx.author.id
            symbol = str(entry.get("symbol") or "TOKEN")
        await ctx.send(f"**{symbol}** on {network.name} is now **{status}**.")

    @walletset_token.command(name="recognize")
    @commands.is_owner()
    async def walletset_token_recognize(
        self, ctx: commands.Context, network_key: str, contract_address: str
    ):
        """Mark a community token as recognized by the bot owner."""
        await self._walletset_token_status(ctx, network_key, contract_address, "recognized")

    @walletset_token.command(name="hide")
    @commands.is_owner()
    async def walletset_token_hide(
        self, ctx: commands.Context, network_key: str, contract_address: str
    ):
        """Hide a token while retaining its moderation record."""
        await self._walletset_token_status(ctx, network_key, contract_address, "hidden")

    @walletset_token.command(name="ban")
    @commands.is_owner()
    async def walletset_token_ban(
        self, ctx: commands.Context, network_key: str, contract_address: str
    ):
        """Ban a token and prevent its resubmission."""
        await self._walletset_token_status(ctx, network_key, contract_address, "banned")

    @walletset_token.command(name="unban")
    @commands.is_owner()
    async def walletset_token_unban(
        self, ctx: commands.Context, network_key: str, contract_address: str
    ):
        """Return a banned token to hidden state for later review."""
        await self._walletset_token_status(ctx, network_key, contract_address, "hidden")

    @walletset_token.command(name="list", aliases=("tokens",))
    @commands.is_owner()
    async def walletset_token_list(self, ctx: commands.Context):
        """List visible, hidden, and banned token records."""
        registry = await self.config.token_registry()
        lines = [
            f"- **{entry.get('symbol', 'TOKEN')}** · `{network_key}` · "
            f"**{entry.get('status', 'community')}**\n  `{contract}`"
            for network_key, entries in registry.items()
            for contract, entry in entries.items()
        ]
        await ctx.send(
            "**Token moderation registry**\n" + "\n".join(lines)
            if lines else "The token moderation registry is empty."
        )

    @walletset.group(name="emoji", aliases=("emojis",), invoke_without_command=True)
    @commands.is_owner()
    async def walletset_emoji(self, ctx: commands.Context):
        """Manage network emojis.

        Shows application-emoji IDs configured for wallet network labels.
        """

        configured = await self.config.network_emojis()
        lines = [
            f"{name}: `{configured.get(key, 'not configured')}`"
            for key, name in NETWORK_EMOJI_NAMES.items()
        ]
        await ctx.send(
            "**CryptoWallet network emojis**\n"
            + "\n".join(lines)
            + "\nUpload the packaged PNG files as application emojis, then use "
            "`walletset emoji sync` to discover them by name."
        )

    @walletset_emoji.command(name="sync")
    @commands.is_owner()
    async def walletset_emoji_sync(self, ctx: commands.Context):
        """Discover uploaded application emojis by their packaged names."""

        try:
            application_emojis = await self.bot.fetch_application_emojis()
        except discord.HTTPException:
            await ctx.send(
                "Discord could not retrieve this application's uploaded emojis."
            )
            return
        by_name = {emoji.name.lower(): str(emoji.id) for emoji in application_emojis}
        found = {
            key: by_name[name]
            for key, name in NETWORK_EMOJI_NAMES.items()
            if name in by_name
        }
        async with self.config.network_emojis() as configured:
            configured.update(found)
        missing = [
            name for name in NETWORK_EMOJI_NAMES.values() if name not in by_name
        ]
        message = f"Saved `{len(found)}` application emoji mapping(s)."
        if missing:
            message += " Missing: " + ", ".join(missing) + "."
        await ctx.send(message)

    @walletset_emoji.command(name="set")
    @commands.is_owner()
    async def walletset_emoji_set(
        self, ctx: commands.Context, network: str, emoji: str
    ):
        """Assign an uploaded application emoji to a wallet network."""

        network_key = NETWORK_EMOJI_ALIASES.get(network.strip().lower())
        if network_key is None:
            await ctx.send(
                "Unknown network. Use base, ethereum, arbitrum, polygon, avalanche, "
                "solana, optimism, bnb, zora, tron, or linea."
            )
            return
        raw = emoji.strip()
        if raw.isdigit():
            emoji_id = raw
        else:
            match = re.fullmatch(r"<a?:[A-Za-z0-9_]{2,32}:(\d{15,22})>", raw)
            emoji_id = match.group(1) if match else ""
        if not re.fullmatch(r"\d{15,22}", emoji_id):
            await ctx.send(
                "Provide the numeric Discord emoji ID or a custom emoji mention."
            )
            return
        async with self.config.network_emojis() as configured:
            configured[network_key] = emoji_id
        name = NETWORK_EMOJI_NAMES[network_key]
        await ctx.send(f"{name.title()} wallet labels now use <:{name}:{emoji_id}>.")

    @walletset_emoji.command(name="clear")
    @commands.is_owner()
    async def walletset_emoji_clear(self, ctx: commands.Context, network: str):
        """Restore the built-in fallback symbol for a wallet network."""

        network_key = NETWORK_EMOJI_ALIASES.get(network.strip().lower())
        if network_key is None:
            await ctx.send(
                "Unknown network. Use base, ethereum, arbitrum, polygon, avalanche, "
                "solana, optimism, bnb, zora, tron, or linea."
            )
            return
        async with self.config.network_emojis() as configured:
            configured.pop(network_key, None)
        await ctx.send(
            f"{NETWORK_EMOJI_NAMES[network_key].title()} now uses its fallback symbol."
        )

    @walletset.group(name="mainnet", invoke_without_command=True)
    @commands.is_owner()
    async def walletset_mainnet(self, ctx: commands.Context):
        """Show the fail-closed Base mainnet release gate."""
        await ctx.invoke(self.walletset_mainnet_status)

    @walletset_mainnet.command(name="status")
    @commands.is_owner()
    async def walletset_mainnet_status(self, ctx: commands.Context):
        """Show non-secret Base mainnet policy state."""
        policy = await self.config.base_mainnet_policy()
        capabilities = policy.get("capabilities") or {}
        enabled_capabilities = sorted(
            name for name, enabled in capabilities.items() if enabled
        )
        gate = "enabled" if policy.get("enabled") else "disabled"
        pause = "active" if policy.get("paused", True) else "inactive"
        reviewed = ", ".join(enabled_capabilities) or "none"
        limits = policy.get("limits_atomic") or {}
        limit_values = []
        for key in ("per_transaction", "per_user_day", "installation_day"):
            try:
                value = int(limits.get(key, 0))
            except (TypeError, ValueError):
                value = 0
            limit_values.append(
                format_atomic_amount(value, BASE_MAINNET) if value > 0 else "blocked"
            )
        await ctx.send(
            "**Base mainnet experimental gate**\n"
            f"Gate: `{gate}`\n"
            f"Emergency pause: `{pause}`\n"
            "Access: `bot owner only`\n"
            "Release: `experimental — real funds may be permanently lost`\n"
            f"Reviewed capabilities: `{reviewed}`\n"
            f"Limits (transaction / user-day / installation-day): "
            f"`{limit_values[0]} / {limit_values[1]} / {limit_values[2]} ETH`"
        )

    @walletset_mainnet.command(name="pause")
    @commands.is_owner()
    async def walletset_mainnet_pause(self, ctx: commands.Context):
        """Disable and pause the Base mainnet experimental gate."""
        async with self.config.base_mainnet_policy() as policy:
            policy["enabled"] = False
            policy["paused"] = True
        await ctx.send(
            "Base mainnet is disabled and emergency-paused. Testnet operation is unchanged."
        )

    @walletset_mainnet.command(name="capability", aliases=("capabilities",))
    @commands.is_owner()
    async def walletset_mainnet_capability(
        self, ctx: commands.Context, capability: str, state: str = None, *,
        acknowledgement: str = "",
    ):
        """Inspect, disable, or guarded-enable one Base mainnet capability."""
        try:
            requested = NetworkCapability(capability.strip().lower())
        except ValueError:
            choices = ", ".join(item.value for item in NetworkCapability)
            await ctx.send(f"Unknown capability. Choose one of: `{choices}`.")
            return
        policy = await self.config.base_mainnet_policy()
        configured = bool((policy.get("capabilities") or {}).get(requested.value))
        if state is None or state.strip().lower() in {"status", "show"}:
            code_ready = BASE_MAINNET.supports(requested)
            await ctx.send(
                f"Base mainnet `{requested.value}`: policy "
                f"`{'enabled' if configured else 'disabled'}`, code "
                f"`{'reviewed' if code_ready else 'unavailable'}`."
            )
            return
        requested_state = state.strip().lower()
        if requested_state in {"disable", "disabled", "off", "false"}:
            async with self.config.base_mainnet_policy() as stored:
                stored.setdefault("capabilities", {})[requested.value] = False
            await ctx.send(
                f"Base mainnet `{requested.value}` is disabled. No other capability changed."
            )
            return
        if requested_state not in {"enable", "enabled", "on", "true"}:
            await ctx.send("State must be `status`, `enable`, or `disable`.")
            return
        if acknowledgement.strip() != MAINNET_ENABLE_ACKNOWLEDGEMENT:
            await ctx.send(
                "No setting changed. Enabling a reviewed mainnet capability requires: "
                f"`{MAINNET_ENABLE_ACKNOWLEDGEMENT}`"
            )
            return
        if not BASE_MAINNET.supports(requested):
            await ctx.send(
                f"No setting changed. Base mainnet `{requested.value}` is not enabled "
                "at the reviewed code boundary."
            )
            return
        async with self.config.base_mainnet_policy() as stored:
            stored.setdefault("capabilities", {})[requested.value] = True
        await ctx.send(
            f"Base mainnet `{requested.value}` is policy-enabled for the bot-owner-only "
            "experimental gate. Real funds may be permanently lost."
        )

    @walletset_mainnet.command(name="limits")
    @commands.is_owner()
    async def walletset_mainnet_limits(
        self, ctx: commands.Context, per_transaction: str = None,
        per_user_day: str = None, installation_day: str = None,
    ):
        """Show or atomically set all experimental Base mainnet limits."""
        values = (per_transaction, per_user_day, installation_day)
        if all(value is None for value in values):
            await WalletAdminCommands.walletset_mainnet_status.callback(self, ctx)
            return
        if any(value is None for value in values):
            await ctx.send(
                "Provide all three limits: per-transaction, per-user daily, and "
                "installation-wide daily amounts in ETH."
            )
            return
        try:
            parsed = tuple(parse_native_amount(value, BASE_MAINNET) for value in values)
        except ValueError as exc:
            await ctx.send(str(exc))
            return
        if not parsed[0] <= parsed[1] <= parsed[2]:
            await ctx.send(
                "Limits must satisfy per-transaction ≤ per-user daily ≤ "
                "installation-wide daily."
            )
            return
        async with self.config.base_mainnet_policy() as policy:
            policy["limits_atomic"] = {
                "per_transaction": str(parsed[0]),
                "per_user_day": str(parsed[1]),
                "installation_day": str(parsed[2]),
            }
        await ctx.send(
            "Saved fail-closed Base mainnet limits. This does not enable any "
            "mainnet capability or remove the emergency pause."
        )

    @walletset_mainnet.command(name="enable")
    @commands.is_owner()
    async def walletset_mainnet_enable(
        self, ctx: commands.Context, *, acknowledgement: str = ""
    ):
        """Arm the owner-only experimental gate after every code-level review."""
        if acknowledgement.strip() != MAINNET_ENABLE_ACKNOWLEDGEMENT:
            await ctx.send(
                "No setting changed. To acknowledge the experimental permanent-loss risk, "
                f"repeat this command with: `{MAINNET_ENABLE_ACKNOWLEDGEMENT}`"
            )
            return
        if not BASE_MAINNET.enabled or not BASE_MAINNET.capabilities.enabled():
            await ctx.send(
                "No setting changed. Base mainnet has no reviewed code-level capabilities "
                "and remains unavailable until the 2.0 security gates are complete."
            )
            return
        async with self.config.base_mainnet_policy() as policy:
            policy["enabled"] = True
            policy["paused"] = False
            policy["owner_only"] = True
            policy["experimental"] = True
            policy["enabled_by"] = ctx.author.id
            policy["enabled_at"] = int(time.time())
        await ctx.send(
            "Base mainnet experimental access is armed for bot-owner use only. "
            "Real funds may be permanently lost."
        )

    @walletset.command(name="pause")
    @commands.is_owner()
    async def walletset_pause(self, ctx: commands.Context):
        """Pause wallet operations.

        Pauses provider-backed wallet operations and confirmation checks.
        """
        if await self.config.provider_paused():
            await ctx.send("CryptoWallet provider processing is already paused.")
            return
        await self.config.provider_paused.set(True)
        await ctx.send(
            "CryptoWallet provider processing is paused. No submitted transaction will "
            "be resubmitted; confirmation checks will resume from persisted state."
        )

    @walletset.command(name="resume")
    @commands.is_owner()
    async def walletset_resume(self, ctx: commands.Context):
        """Resume wallet operations.

        Resumes provider-backed wallet operations and confirmation checks.
        """
        if not await self.config.provider_paused():
            await ctx.send("CryptoWallet provider processing is already active.")
            return
        await self.config.provider_paused.set(False)
        self.confirmation_wakeup.set()
        await ctx.send("CryptoWallet provider processing resumed.")

    @walletset.command(name="reconcile")
    @commands.is_owner()
    async def walletset_reconcile(
        self, ctx: commands.Context, target: str, intent_id: str
    ):
        """Reconcile one intent.

        Rechecks one uncertain intent using its stored provider identifier.
        """
        user_id = self._wallet_user_id(target)
        if user_id is None:
            await ctx.send("Provide a Discord user mention or numeric Discord user ID.")
            return
        intent = await self._stored_intent(user_id, intent_id.strip())
        if intent is None:
            await ctx.send("No stored transaction intent matches that user and reference.")
            return
        if intent.status is not IntentStatus.UNCERTAIN:
            await ctx.send(
                f"That intent is `{intent.status.value}`, not uncertain; no reconciliation "
                "was performed."
            )
            return
        if not (intent.transaction_hash or intent.user_operation_hash):
            await ctx.send(
                "That uncertain intent has no TXID or provider operation hash. It cannot be "
                "resolved automatically; retain it and reconcile the provider and chain records."
            )
            return
        if await self.config.provider_paused():
            await ctx.send("Provider processing is paused; reconciliation was not performed.")
            return
        try:
            intent = await self._refresh_submitted_intent(user_id, intent.intent_id)
        except (RuntimeError, WalletProviderError) as exc:
            await ctx.send(f"Transaction reconciliation is unavailable: {exc}")
            return
        network = NETWORKS.get(intent.network)
        if network is None:
            await ctx.send("That intent references an unsupported network.")
            return
        if intent.status is IntentStatus.CONFIRMED:
            outcome = "The transaction is confirmed."
        elif intent.status is IntentStatus.FAILED:
            outcome = "The provider or chain reports that the transaction failed."
        else:
            outcome = (
                "The transaction is still unresolved and remains uncertain. Do not submit "
                "a replacement."
            )
        await ctx.send(outcome, embed=self._intent_embed(intent, network, await ctx.embed_color()))

    @walletset.command(name="sendlimit")
    @commands.is_owner()
    async def walletset_send_limit(
        self, ctx: commands.Context, network_key: str = None, amount: str = None
    ):
        """Manage send limits.

        Shows or sets the maximum native amount for one transaction.
        """
        limits = await self.config.send_limits_atomic()
        if network_key is None:
            lines = []
            for network in NETWORKS.values():
                if not network.supports(NetworkCapability.SEND):
                    continue
                raw = limits.get(network.key)
                try:
                    limit = int(raw) if raw is not None else 0
                except (TypeError, ValueError):
                    limit = 0
                if raw is None:
                    value = "not set (testnet unrestricted)"
                elif limit > 0:
                    value = (
                        f"{format_atomic_amount(limit, network)} "
                        f"{network.native_symbol}"
                    )
                else:
                    value = "invalid (sends blocked)"
                lines.append(f"{network.name}: `{value}`")
            await ctx.send("**Per-transaction send limits**\n" + "\n".join(lines))
            return
        network = self._send_network(network_key)
        if network is None or not network.supports(NetworkCapability.SEND):
            await ctx.send("Choose a send-enabled testnet from `wallet networks`.")
            return
        if amount is None:
            raw = limits.get(network.key)
            if raw is None:
                await ctx.send(f"{network.name} has no additional testnet send limit.")
            else:
                try:
                    limit = int(raw)
                except (TypeError, ValueError):
                    limit = 0
                if limit <= 0:
                    await ctx.send(
                        f"{network.name} has an invalid limit; sends are blocked."
                    )
                else:
                    await ctx.send(
                        f"{network.name} send limit: "
                        f"`{format_atomic_amount(limit, network)} "
                        f"{network.native_symbol}`."
                    )
            return
        if amount.strip().lower() in {"clear", "none", "off"}:
            if not network.testnet:
                await ctx.send("Production-network send limits cannot be cleared.")
                return
            async with self.config.send_limits_atomic() as stored:
                stored.pop(network.key, None)
            await ctx.send(f"{network.name} testnet send limit cleared.")
            return
        try:
            value_atomic = parse_native_amount(amount, network)
        except ValueError as exc:
            await ctx.send(str(exc))
            return
        async with self.config.send_limits_atomic() as stored:
            stored[network.key] = str(value_atomic)
        await ctx.send(
            f"{network.name} per-transaction send limit set to "
            f"`{format_atomic_amount(value_atomic, network)} {network.native_symbol}`."
        )

    @walletset.command(name="delegationdays")
    @commands.is_owner()
    async def walletset_delegation_days(
        self, ctx: commands.Context, days: int = None
    ):
        """Set authorization lifetime.

        Shows or sets the default signed authorization lifetime policy.
        """
        current = int(await self.config.delegation_duration_days() or 0)
        if days is None:
            await ctx.send(f"Wallet delegation lifetime: `{current} day(s)`.")
            return
        maximum = int(await self.config.delegation_max_duration_days() or 0)
        if not 1 <= days <= maximum:
            await ctx.send(
                f"Choose a recommended lifetime from 1 through `{maximum}` days."
            )
            return
        await self.config.delegation_duration_days.set(days)
        await ctx.send(
            f"New wallet authorizations will expire after `{days} day(s)`. "
            "Existing authorizations are unchanged."
        )

    @walletset.command(name="delegationmaxdays")
    @commands.is_owner()
    async def walletset_delegation_max_days(
        self, ctx: commands.Context, days: int = None
    ):
        """Set maximum authorization.

        Shows or sets the maximum member-selectable authorization lifetime.
        """
        current = int(await self.config.delegation_max_duration_days() or 0)
        if days is None:
            await ctx.send(f"Maximum wallet delegation lifetime: `{current} day(s)`.")
            return
        recommended = int(await self.config.delegation_duration_days() or 0)
        if not recommended <= days <= 365:
            await ctx.send(
                f"Choose a maximum from `{recommended}` through `365` days, or lower "
                "the recommended duration first."
            )
            return
        await self.config.delegation_max_duration_days.set(days)
        await ctx.send(
            f"Members may select wallet authorization lifetimes up to `{days} day(s)`."
        )

    @walletset.command(name="usage")
    @commands.is_owner()
    async def walletset_usage(self, ctx: commands.Context):
        """Show provider workload.

        Shows confirmation workload and provider safety state.
        """
        pending = 0
        due = 0
        now = int(time.time())
        for user_data in (await self.config.all_users()).values():
            for data in (user_data.get("intents") or {}).values():
                if data.get("status") != "submitted":
                    continue
                pending += 1
                if int(data.get("confirmation_next_check_at", 0) or 0) <= now:
                    due += 1
        paused = await self.config.provider_paused()
        usage = await self.flush_provider_usage()
        wallet_operations = int(usage.get("wallet_operations_estimated", 0) or 0)
        node_units = int(usage.get("node_billing_units_estimated", 0) or 0)
        await ctx.send(
            "**CryptoWallet usage and processing**\n"
            f"Accounting period: `{usage.get('period', 'unknown')} UTC`\n"
            f"Provider processing: `{'paused' if paused else 'active'}`\n"
            f"Pending confirmations: `{pending}` (`{due}` currently due)\n"
            "Confirmation limit: `60 checks/minute`\n"
            "First check: `20–30 seconds after submission`\n"
            f"CDP requests: `{usage.get('cdp_reads', 0)} reads`, "
            f"`{usage.get('cdp_writes', 0)} writes` "
            f"(`{self.recent_cdp_request_count()}` in the last minute)\n"
            f"Onchain Data reads: `{usage.get('onchain_data_reads', 0)}`\n"
            f"Estimated wallet operations: `{wallet_operations} / {WALLET_SAFETY_TARGET}` "
            f"safety target (`{WALLET_FREE_OPERATIONS}` published free allowance)\n"
            f"Estimated CDP Node usage: `{node_units:,} / {NODE_SAFETY_TARGET:,} BU` "
            f"safety target (`{NODE_FREE_BILLING_UNITS:,} BU` published free allowance)\n"
            "Current Base Sepolia RPC fallbacks are public endpoints and add no estimated "
            "CDP Node BU. Local figures are conservative estimates; the CDP billing "
            "portal remains authoritative. No operation is stopped automatically."
        )

    @walletset.command(name="view")
    @commands.is_owner()
    async def walletset_view(self, ctx: commands.Context):
        """Show wallet settings.

        Shows non-secret wallet integration and companion settings.
        """

        approval_base_url = await self.config.approval_base_url()
        provider = str(self.wallet_provider.name or "unconfigured").upper()
        network = NETWORKS.get(await self.config.default_network(), BASE_SEPOLIA)
        deployment_id = await self.config.deployment_id()
        application_id = self.discord_application_id()
        cdp = await self.wallet_provider.readiness()
        jwt_auth = await self.jwt_public_status()
        recovery_relay = await self.recovery_relay_status()
        authorization_ready = bool(cdp["configured"] and jwt_auth["configured"])
        await ctx.send(
            "**Wallet integration**\n"
            f"Provider: `{provider}`\n"
            f"Network: `{network.name}` ({network.reference_label} `{network.reference}`)\n"
            f"Approval website: `{approval_base_url or 'not configured'}`\n"
            f"Deployment: `{deployment_id or 'not initialized'}`\n"
            f"Discord application: `{application_id or 'unavailable'}`\n"
            f"CDP credentials: `{'configured' if cdp['configured'] else 'not configured'}`\n"
            f"Custom authentication: `{'configured' if jwt_auth['configured'] else 'not configured'}`\n"
            f"Current authorization flow: `{'ready' if authorization_ready else 'not ready'}`\n"
            f"One-time recovery relay: `{'configured' if recovery_relay['configured'] else 'not configured'}`\n"
            "Mainnet: `disabled`\n"
            "Architecture: `static companion + authenticated outbound relay`"
        )

    @walletset.command(name="cdpstatus")
    @commands.is_owner()
    async def walletset_cdp_status(self, ctx: commands.Context):
        """Show CDP readiness.

        Shows CDP readiness without displaying credential values.
        """
        readiness = await self.wallet_provider.readiness()
        if readiness["configured"]:
            await ctx.send(
                "CDP credentials are configured in server-side shared API tokens. "
                "Automatic Base Sepolia provisioning and balance lookup are enabled."
            )
            return
        missing = ", ".join(readiness["missing"])
        await ctx.send(f"CDP is not configured. Missing secret-store fields: `{missing}`.")

    @walletset.command(name="cdpcheck")
    @commands.is_owner()
    async def walletset_cdp_check(self, ctx: commands.Context):
        """Check CDP credentials.

        Validates CDP credentials with one read-only API request.
        """
        async with ctx.typing():
            result = await self.wallet_provider.diagnostics()
        if result["ready"]:
            await ctx.send(
                "**CDP diagnostic passed**\n"
                "Secret-store fields: `present`\n"
                "API key material: `valid format`\n"
                "Wallet Secret: `valid format`\n"
                "Read-only project authentication: `successful`\n"
                "No wallet, transaction, policy, or delegation was created."
            )
            return
        if result["stage"] == "configuration":
            missing = ", ".join(result.get("missing") or []) or "unknown"
            await ctx.send(
                "CDP diagnostic stopped before making a request. "
                f"Missing secret-store fields: `{missing}`."
            )
            return
        error = result.get("error") or "unknown authentication failure"
        guidance = (
            "Check the Secret API Key ID/secret, Wallet Secret, server clock, "
            "public-IP allowlist, and key permissions."
        )
        await ctx.send(
            "**CDP diagnostic failed safely**\n"
            f"Result: `{error}`\n"
            f"{guidance}\n"
            "No secret values were displayed and no CDP state was changed."
        )

    @walletset.command(name="jwtstatus")
    @commands.is_owner()
    async def walletset_jwt_status(self, ctx: commands.Context):
        """Show custom-auth status.

        Shows the public CDP custom-auth configuration.
        """
        status = await self.jwt_public_status()
        if not status["configured"]:
            await ctx.send(
                "Custom authentication is incomplete. Configure the companion URL and CDP "
                "project ID, then reload the cog to initialize its signing key."
            )
            return
        await ctx.send(
            "**CDP custom authentication**\n"
            f"Issuer: `{status['issuer']}`\n"
            f"Audience: `{status['audience']}`\n"
            f"JWKS URL: `{status['jwks_url']}`\n"
            f"Key ID: `{status['kid']}`\n"
            "Algorithm: `ES256`\n"
            "Authorization uses a signed static handoff; no inbound cog listener is used."
        )

    @walletset.command(name="jwksfile")
    @commands.is_owner()
    async def walletset_jwks_file(self, ctx: commands.Context):
        """Export the public JWKS.

        Exports the public JWKS file required by CDP custom authentication.
        """
        jwks = await self.jwt_jwks()
        if jwks is None:
            await ctx.send("Custom authentication is not completely configured.")
            return
        payload = json.dumps(jwks, indent=2).encode("utf-8")
        await ctx.send(
            "Upload this public-key file as `jwks.json` beside the wallet web files. "
            "It contains no private key or provider credential. This is normally a one-time "
            "custom-auth setup step; upload it again only if the bot's JWT signing identity "
            "changes.",
            file=discord.File(io.BytesIO(payload), filename="jwks.json"),
        )

    @walletset.command(name="approvalurl")
    @commands.is_owner()
    async def walletset_approval_url(self, ctx: commands.Context, url: str):
        """Set the approval website.

        Sets the HTTPS origin used for protected wallet approvals.
        """

        normalized = url.strip().rstrip("/")
        parsed = urlparse(normalized)
        if (
            parsed.scheme != "https"
            or not parsed.netloc
            or parsed.username
            or parsed.password
            or parsed.params
            or parsed.query
            or parsed.fragment
            or "//" in parsed.path
            or "%2e" in parsed.path.casefold()
            or any(part in {".", ".."} for part in parsed.path.split("/"))
        ):
            await ctx.send(
                "Provide an HTTPS URL without a query, fragment, credentials, or unsafe path, "
                "for example `https://sickgaming.net/cryptowallet`."
            )
            return
        await self.config.approval_base_url.set(normalized)
        await ctx.send("Wallet companion URL updated. No credentials were stored.")

    @walletset.command(name="clearapprovalurl")
    @commands.is_owner()
    async def walletset_clear_approval_url(self, ctx: commands.Context):
        """Clear the approval website.

        Disables the configured wallet companion origin.
        """

        await self.config.approval_base_url.set(None)
        await ctx.send("Wallet companion URL cleared; account-control links are disabled.")
