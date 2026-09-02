import asyncio
import logging
import secrets

from redbot.core import commands

from .admin import WalletAdminCommands
from .commands import WalletCommands
from .companion import CompanionServer
from .config import WalletConfigMixin, create_config
from .pairing import CompanionPairingMixin
from .providers import CdpWalletProvider
from .provisioning import WalletProvisioningMixin
from .sessions import ApprovalSessionMixin

log = logging.getLogger("red.Sick-Cogs.CryptoWallet")


class CryptoWallet(
    WalletCommands,
    WalletAdminCommands,
    WalletConfigMixin,
    ApprovalSessionMixin,
    CompanionPairingMixin,
    WalletProvisioningMixin,
    commands.Cog,
):
    """Manage public smart-wallet information through a secure companion service."""

    def __init__(self, bot):
        self.bot = bot
        self.config = create_config(self)
        self.pairing_lock = asyncio.Lock()
        self.initialize_provisioning()
        self.wallet_provider = CdpWalletProvider(bot)
        self.companion = CompanionServer(self)

    async def initialize(self):
        """Restore the loopback companion only when explicitly enabled."""
        if not await self.config.deployment_id():
            await self.config.deployment_id.set(secrets.token_urlsafe(24))
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

    async def discord_oauth_config(self) -> dict | None:
        """Return complete OAuth configuration without storing its secret in cog config."""
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

    def discord_application_id(self) -> int | None:
        """Return the immutable Discord application ID for this bot process."""
        application_id = getattr(self.bot, "application_id", None)
        if application_id:
            return int(application_id)
        user = getattr(self.bot, "user", None)
        if user is not None and getattr(user, "id", None):
            return int(user.id)
        return None
