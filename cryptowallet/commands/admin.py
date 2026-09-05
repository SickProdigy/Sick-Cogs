import io
import json
import logging
import re
import time
from urllib.parse import urlparse

import discord
from redbot.core import commands

from ..core.networks import BASE_SEPOLIA, NETWORKS, NetworkCapability
from ..core.validation import (
    format_atomic_amount,
    normalize_evm_address,
    parse_native_amount,
)
from ..providers import WalletProviderError
from ..backend.usage import (
    NODE_FREE_BILLING_UNITS,
    NODE_SAFETY_TARGET,
    WALLET_FREE_OPERATIONS,
    WALLET_SAFETY_TARGET,
)


log = logging.getLogger("red.Sick-Cogs.CryptoWallet")


class WalletAdminCommands:
    """Owner-only wallet integration commands."""

    @commands.group(name="walletset", invoke_without_command=True)
    @commands.is_owner()
    async def walletset(self, ctx: commands.Context):
        """Configure CryptoWallet and inspect optional companion infrastructure."""

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
        """Emergency-lock a user wallet and revoke its bot signing authorization."""
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

    @walletset.command(name="unlock", aliases=("unfreeze",))
    @commands.is_owner()
    async def walletset_unlock(self, ctx: commands.Context, target: str):
        """Remove an emergency wallet lock after identity review."""
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
        """Moderate shared wallet tokens."""
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

    @walletset.command(name="pause")
    @commands.is_owner()
    async def walletset_pause(self, ctx: commands.Context):
        """Pause provider-backed wallet operations and confirmation checks."""
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
        """Resume provider-backed wallet operations and confirmation checks."""
        if not await self.config.provider_paused():
            await ctx.send("CryptoWallet provider processing is already active.")
            return
        await self.config.provider_paused.set(False)
        self.confirmation_wakeup.set()
        await ctx.send("CryptoWallet provider processing resumed.")

    @walletset.command(name="sendlimit")
    @commands.is_owner()
    async def walletset_send_limit(
        self, ctx: commands.Context, network_key: str = None, amount: str = None
    ):
        """Show or set the maximum native amount for one transaction."""
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
        """Show or set the signed authorization lifetime policy."""
        current = int(await self.config.delegation_duration_days() or 0)
        if days is None:
            await ctx.send(f"Wallet delegation lifetime: `{current} day(s)`.")
            return
        if not 1 <= days <= 365:
            await ctx.send("Choose a delegation lifetime from 1 through 365 days.")
            return
        await self.config.delegation_duration_days.set(days)
        await ctx.send(
            f"New wallet authorizations will expire after `{days} day(s)`. "
            "Existing authorizations are unchanged."
        )

    @walletset.command(name="usage")
    @commands.is_owner()
    async def walletset_usage(self, ctx: commands.Context):
        """Show confirmation workload and provider safety state."""
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
        """Show non-secret wallet integration settings."""

        approval_base_url = await self.config.approval_base_url()
        provider = await self.config.provider()
        network = NETWORKS.get(await self.config.default_network(), BASE_SEPOLIA)
        oauth_ready = await self.discord_oauth_config() is not None
        deployment_id = await self.config.deployment_id()
        application_id = self.discord_application_id()
        pairing = await self.companion_pairing_status()
        cdp = await self.wallet_provider.readiness()
        jwt_auth = await self.jwt_public_status()
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
            "Mainnet: `disabled`\n\n"
            "**Optional future recovery relay**\n"
            f"Website pairing: `{'paired' if pairing['paired'] else 'not paired'}`\n"
            f"Discord OAuth: `{'configured' if oauth_ready else 'not configured'}`\n"
            f"Companion listener: `{'running' if self.companion.running else 'stopped'}`\n"
            "Pairing and the listener are not required for current wallet authorization or sends."
        )

    @walletset.command(name="cdpstatus")
    @commands.is_owner()
    async def walletset_cdp_status(self, ctx: commands.Context):
        """Show CDP readiness without displaying credential values."""
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
        """Validate CDP credentials with one read-only API request."""
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
        """Show the public CDP custom-auth configuration."""
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
            "Website pairing and the companion listener are not required for this flow."
        )

    @walletset.command(name="jwksfile")
    @commands.is_owner()
    async def walletset_jwks_file(self, ctx: commands.Context):
        """Export the public JWKS file required by CDP custom authentication."""
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

    @walletset.command(name="pair")
    @commands.is_owner()
    async def walletset_pair(self, ctx: commands.Context):
        """Pair the optional future recovery relay; not needed for current sends."""
        code, expires_at = await self.begin_companion_pairing()
        message = (
            "Optional recovery-relay pairing code (single use):\n"
            f"`{code}`\nExpires <t:{expires_at}:R>. Enter it only in the private website setup."
        )
        try:
            await ctx.author.send(message)
        except Exception:
            await self.cancel_companion_pairing()
            await ctx.send("I could not DM you, so no pairing code was left active.")
            return
        await ctx.send(
            "I sent the optional recovery-relay pairing code to your DMs. Pairing is not "
            "required for current wallet authorization, balances, activity, or sends."
        )

    @walletset.command(name="paircancel")
    @commands.is_owner()
    async def walletset_pair_cancel(self, ctx: commands.Context):
        """Cancel an outstanding website pairing code."""
        await self.cancel_companion_pairing()
        await ctx.send("Outstanding companion pairing code cancelled.")

    @walletset.command(name="pairstatus")
    @commands.is_owner()
    async def walletset_pair_status(self, ctx: commands.Context):
        """Show non-secret companion website pairing status."""
        status = await self.companion_pairing_status()
        message = (
            f"Website pairing: `{'paired' if status['paired'] else 'not paired'}`\n"
            f"Installation: `{status['installation_id'] or 'none'}`\n"
            f"Paired at: `{status['paired_at'] or 'never'}`"
        )
        if not status["paired"]:
            message += (
                "\nThis optional recovery relay is not configured. Current wallet "
                "authorization, balances, activity, and sends are unaffected."
            )
        await ctx.send(message)

    @walletset.command(name="unpair")
    @commands.is_owner()
    async def walletset_unpair(self, ctx: commands.Context):
        """Revoke the companion website installation credential."""
        await self.unpair_companion()
        await ctx.send("Companion website unpaired; its previous credential is revoked.")

    @walletset.group(name="companion", invoke_without_command=True)
    @commands.is_owner()
    async def walletset_companion(self, ctx: commands.Context):
        """Manage the optional loopback listener for future protected account tools."""

        await ctx.send_help()

    @walletset_companion.command(name="start")
    @commands.is_owner()
    async def walletset_companion_start(self, ctx: commands.Context, port: int = 8787):
        """Enable the optional loopback listener behind an HTTPS reverse proxy."""

        if self.companion.running:
            await ctx.send("The wallet companion is already running; stop it before changing ports.")
            return
        if not 1024 <= port <= 65535:
            await ctx.send("Choose an unprivileged TCP port from 1024 through 65535.")
            return
        if not await self.config.approval_base_url():
            await ctx.send("Configure the public HTTPS approval URL first.")
            return
        try:
            await self.companion.start("127.0.0.1", port)
        except Exception:
            log.exception("The wallet companion could not be started by command")
            await ctx.send("The loopback companion could not start; check the Red logs.")
            return
        await self.config.companion_host.set("127.0.0.1")
        await self.config.companion_port.set(port)
        await self.config.companion_enabled.set(True)
        await ctx.send(
            f"Optional companion listening on `127.0.0.1:{port}` for the HTTPS proxy. "
            "It will start automatically on future cog loads until disabled."
        )

    @walletset_companion.command(name="stop")
    @commands.is_owner()
    async def walletset_companion_stop(self, ctx: commands.Context):
        """Stop and disable the companion listener."""

        await self.companion.stop()
        await self.config.companion_enabled.set(False)
        await ctx.send("Wallet companion stopped and disabled.")

    @walletset.command(name="approvalurl")
    @commands.is_owner()
    async def walletset_approval_url(self, ctx: commands.Context, url: str):
        """Set the HTTPS origin used for secure wallet approvals."""

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
        """Disable the configured wallet companion origin."""

        await self.config.approval_base_url.set(None)
        await ctx.send("Wallet companion URL cleared; account-control links are disabled.")
