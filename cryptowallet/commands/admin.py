import io
import json
import logging
import time
from urllib.parse import urlparse

import discord
from redbot.core import commands

from ..core.networks import BASE_SEPOLIA, NETWORKS
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
            f"Network: `{network.name}` (`{network.chain_id}`)\n"
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
