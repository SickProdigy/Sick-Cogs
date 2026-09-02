import hashlib
import logging
import secrets
import time
from urllib.parse import urlparse

import discord
from redbot.core import Config, commands

from .companion import CompanionServer
from .models import (
    ApprovalPurpose,
    ApprovalSession,
    ApprovalStatus,
    IntentStatus,
    TransactionIntent,
)
from .networks import BASE_SEPOLIA, DEFAULT_NETWORK, NETWORKS
from .validation import (
    format_wei_as_eth,
    normalize_evm_address,
    parse_eth_to_wei,
)


INTENT_LIFETIME_SECONDS = 15 * 60
APPROVAL_LIFETIME_SECONDS = 10 * 60
MAX_STORED_INTENTS = 25
MAX_STORED_APPROVALS = 10

log = logging.getLogger("red.Sick-Cogs.CryptoWallet")


class CryptoWallet(commands.Cog):
    """Manage public smart-wallet information through a secure companion service."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=9365048217, force_registration=True)
        self.config.register_global(
            approval_base_url=None,
            provider="unconfigured",
            default_network=DEFAULT_NETWORK,
            companion_enabled=False,
            companion_host="127.0.0.1",
            companion_port=8787,
        )
        self.config.register_user(profile=None, intents={}, approval_sessions={})
        self.companion = CompanionServer(self)

    async def initialize(self):
        """Restore the loopback companion only when explicitly enabled."""

        if await self.config.companion_enabled():
            try:
                await self.companion.start(
                    await self.config.companion_host(),
                    await self.config.companion_port(),
                )
            except Exception:
                log.exception("The configured wallet companion listener could not start")

    def cog_unload(self):
        self.bot.loop.create_task(self.companion.stop())

    async def red_delete_data_for_user(self, *, requester, user_id: int):
        """Delete the Discord-side wallet profile metadata for a user."""

        await self.config.user_from_id(user_id).clear()

    @commands.group(name="wallet", aliases=("cryptowallet",), invoke_without_command=True)
    async def wallet(self, ctx: commands.Context):
        """Show your wallet profile and prototype status."""

        profile = await self.config.user(ctx.author).profile()
        embed = discord.Embed(
            title="Crypto Wallet",
            color=await ctx.embed_color(),
        )
        embed.add_field(name="Network", value=f"{BASE_SEPOLIA.name} (testnet)", inline=False)
        if profile is None:
            embed.description = (
                "No wallet is linked yet. Enrollment will be enabled after the secure "
                "companion service and CDP user-owned wallet flow are configured."
            )
        else:
            accounts = profile.get("accounts") or []
            embed.description = "Your public wallet profile is linked."
            for account in accounts[:5]:
                address = str(account.get("address") or "Unavailable")
                account_type = str(account.get("account_type") or "unknown")
                embed.add_field(
                    name=account_type.replace("_", " ").title(),
                    value=f"`{address}`",
                    inline=False,
                )
        embed.set_footer(text="Prototype only — do not use with real funds")
        await ctx.send(embed=embed)

    @wallet.command(name="networks")
    async def wallet_networks(self, ctx: commands.Context):
        """List networks enabled for this prototype."""

        lines = [
            f"- **{network.name}** — chain ID `{network.chain_id}` "
            f"({network.native_symbol}, testnet)"
            for network in NETWORKS.values()
        ]
        await ctx.send("**Enabled wallet networks**\n" + "\n".join(lines))

    @wallet.command(name="claim")
    async def wallet_claim(self, ctx: commands.Context):
        """Claim and configure control of an automatically provisioned wallet."""

        profile = await self.config.user(ctx.author).profile()
        if profile is None:
            await ctx.send(
                "No wallet has been provisioned yet. Automatic CDP provisioning is the "
                "next implementation milestone."
            )
            return
        approval_base_url = await self.config.approval_base_url()
        if not approval_base_url or not self.companion.running:
            await ctx.send(
                "Wallet claiming is unavailable until the account-control companion is running."
            )
            return
        if await self.discord_oauth_config() is None:
            await ctx.send("Discord OAuth credentials are not configured for the companion.")
            return

        token = await self.create_approval_session(ctx.author.id, ApprovalPurpose.CLAIM)
        await ctx.send(
            f"Open this single-use wallet claim link within 10 minutes:\n"
            f"<{approval_base_url}/session/{token}>\n"
            "It currently verifies Discord ownership only; it does not expose or export keys."
        )

    @staticmethod
    def _token_digest(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    async def create_approval_session(
        self, discord_user_id: int, purpose: ApprovalPurpose, intent_id: str | None = None
    ) -> str:
        """Persist the digest of a new one-time state token and return the token once."""

        token = secrets.token_urlsafe(32)
        now = int(time.time())
        session = ApprovalSession(
            token_digest=self._token_digest(token),
            discord_user_id=discord_user_id,
            purpose=purpose,
            created_at=now,
            expires_at=now + APPROVAL_LIFETIME_SECONDS,
            intent_id=intent_id,
        )
        async with self.config.user_from_id(discord_user_id).approval_sessions() as sessions:
            sessions[session.token_digest] = session.to_dict()
            ordered = sorted(
                sessions.items(),
                key=lambda item: int(item[1].get("created_at", 0) or 0),
                reverse=True,
            )
            sessions.clear()
            sessions.update(ordered[:MAX_STORED_APPROVALS])
        return token

    async def resolve_approval_session(self, token: str) -> ApprovalSession | None:
        """Resolve valid one-time state without storing or logging its bearer value."""

        if len(token) < 32 or len(token) > 128:
            return None
        digest = self._token_digest(token)
        all_users = await self.config.all_users()
        for user_id, user_data in all_users.items():
            data = (user_data.get("approval_sessions") or {}).get(digest)
            if data is None:
                continue
            try:
                session = ApprovalSession.from_dict(data)
            except (KeyError, TypeError, ValueError):
                return None
            if (
                session.discord_user_id != int(user_id)
                or session.status is not ApprovalStatus.PENDING
                or session.expires_at <= int(time.time())
            ):
                return None
            return session
        return None

    async def consume_approval_session(self, token: str, discord_user_id: int) -> bool:
        """Atomically consume state after the matching Discord OAuth identity returns."""

        digest = self._token_digest(token)
        now = int(time.time())
        async with self.config.user_from_id(discord_user_id).approval_sessions() as sessions:
            data = sessions.get(digest)
            if data is None:
                return False
            try:
                session = ApprovalSession.from_dict(data)
            except (KeyError, TypeError, ValueError):
                return False
            if session.status is not ApprovalStatus.PENDING or session.expires_at <= now:
                return False
            session.status = ApprovalStatus.IDENTITY_VERIFIED
            session.consumed_at = now
            sessions[digest] = session.to_dict()
            return True

    async def discord_oauth_config(self) -> dict | None:
        """Return complete OAuth configuration without persisting its secret in cog config."""

        tokens = await self.bot.get_shared_api_tokens("cryptowallet")
        client_id = tokens.get("client_id")
        client_secret = tokens.get("client_secret")
        approval_base_url = await self.config.approval_base_url()
        if not client_id or not client_secret or not approval_base_url:
            return None
        return {
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": f"{approval_base_url}/oauth/callback",
        }

    async def _expire_and_trim_intents(self, user) -> dict:
        """Expire pending intents and retain only the newest bounded history."""

        now = int(time.time())
        async with self.config.user(user).intents() as intents:
            for data in intents.values():
                if (
                    data.get("status") == IntentStatus.PENDING.value
                    and int(data.get("expires_at", 0) or 0) <= now
                ):
                    data["status"] = IntentStatus.EXPIRED.value

            ordered = sorted(
                intents.items(),
                key=lambda item: int(item[1].get("created_at", 0) or 0),
                reverse=True,
            )
            intents.clear()
            intents.update(ordered[:MAX_STORED_INTENTS])
            return dict(intents)

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
        embed.add_field(
            name="Network", value=f"{network.name} (`{network.chain_id}`)", inline=True
        )
        embed.add_field(
            name="Amount",
            value=f"{format_wei_as_eth(intent.value_wei)} {network.native_symbol}",
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

    @wallet.command(name="send")
    async def wallet_send(self, ctx: commands.Context, to_address: str, amount: str):
        """Prepare an unsigned Base Sepolia ETH transfer intent.

        Nothing is signed or broadcast by this command.
        """

        profile = await self.config.user(ctx.author).profile()
        if profile is None:
            await ctx.send("Link or enroll a wallet before creating a transaction intent.")
            return

        network_key = await self.config.default_network()
        network = NETWORKS.get(network_key)
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
        )
        if not intent.profile_id:
            await ctx.send("Your wallet profile is incomplete and must be linked again.")
            return

        async with self.config.user(ctx.author).intents() as intents:
            intents[intent.intent_id] = intent.to_dict()
        await self._expire_and_trim_intents(ctx.author)

        await ctx.send(embed=self._intent_embed(intent, network, await ctx.embed_color()))

    @wallet.command(name="transaction", aliases=("intent", "tx"))
    async def wallet_transaction(self, ctx: commands.Context, intent_id: str):
        """Show one of your transaction intents by ID."""

        intents = await self._expire_and_trim_intents(ctx.author)
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

    @commands.group(name="walletset", invoke_without_command=True)
    @commands.is_owner()
    async def walletset(self, ctx: commands.Context):
        """Configure the global wallet companion integration."""

        await ctx.send_help()

    @walletset.command(name="view")
    @commands.is_owner()
    async def walletset_view(self, ctx: commands.Context):
        """Show non-secret wallet integration settings."""

        approval_base_url = await self.config.approval_base_url()
        provider = await self.config.provider()
        network_key = await self.config.default_network()
        network = NETWORKS.get(network_key, BASE_SEPOLIA)
        oauth_ready = await self.discord_oauth_config() is not None
        await ctx.send(
            "**Wallet integration**\n"
            f"Provider: `{provider}`\n"
            f"Network: `{network.name}` (`{network.chain_id}`)\n"
            f"Companion URL: `{approval_base_url or 'not configured'}`\n"
            f"Companion listener: `{'running' if self.companion.running else 'stopped'}`\n"
            f"Discord OAuth: `{'configured' if oauth_ready else 'not configured'}`\n"
            "Mainnet: `disabled`"
        )

    @walletset.group(name="companion", invoke_without_command=True)
    @commands.is_owner()
    async def walletset_companion(self, ctx: commands.Context):
        """Start or stop the loopback companion listener."""

        await ctx.send_help()

    @walletset_companion.command(name="start")
    @commands.is_owner()
    async def walletset_companion_start(self, ctx: commands.Context, port: int = 8787):
        """Start the loopback listener behind your HTTPS reverse proxy."""

        if self.companion.running:
            await ctx.send(
                "The wallet companion is already running; stop it before changing ports."
            )
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
        await ctx.send(f"Companion listening on `127.0.0.1:{port}` for the HTTPS proxy.")

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
        await ctx.send("Wallet companion URL cleared; enrollment is disabled.")
