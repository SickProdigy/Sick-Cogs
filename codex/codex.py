import asyncio
import logging
import time

import aiohttp
import discord
from discord.ext import tasks
from redbot.core import Config, checks, commands
from redbot.core.data_manager import cog_data_path

from .manager import CodexManager, CodexManagerError
from .models import (
    alert_key,
    due_alerts,
    iter_limit_windows,
    validate_alert_levels,
)

log = logging.getLogger("red.sickcogs.codex")
CONFIG_IDENTIFIER = 620351947235
DEFAULT_GLOBAL = {"schema_version": 3}
DEFAULT_USER = {
    "connected": False,
    "enabled": True,
    "alert_levels": [50, 75, 95],
    "sent_alerts": [],
    "last_checked_at": 0,
}


class CodexLinkView(discord.ui.View):
    def __init__(self, url):
        super().__init__(timeout=600)
        self.add_item(discord.ui.Button(
            label="Open ChatGPT authorization",
            style=discord.ButtonStyle.link,
            url=url,
        ))


class Codex(commands.Cog):
    """Private live Codex allowance status and notifications."""

    __author__ = ["SickProdigy"]
    __version__ = "0.3.1"

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(
            self, identifier=CONFIG_IDENTIFIER, force_registration=True
        )
        self.config.register_global(**DEFAULT_GLOBAL)
        self.config.register_user(**DEFAULT_USER)
        self.session = None
        self.manager = CodexManager(
            cog_data_path(self),
            lambda: self.session,
            install_path=cog_data_path(raw_name="SickCogsShared") / "codex",
        )
        self._linking = set()
        self._install_task = None

    async def cog_load(self):
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=30)
        )
        if not self.manager.executable():
            self._install_task = asyncio.create_task(self._install_shared_cli())
        self.notification_loop.start()

    async def _install_shared_cli(self):
        try:
            version = await self.manager.install()
        except asyncio.CancelledError:
            raise
        except CodexManagerError as exc:
            log.warning("Automatic Codex CLI installation failed: %s", exc)
        else:
            log.info("Shared Codex CLI installed: %s", version or "version unavailable")

    def cog_unload(self):
        self.notification_loop.cancel()
        if self._install_task and not self._install_task.done():
            self._install_task.cancel()
        if self.session and not self.session.closed:
            asyncio.create_task(self.session.close())

    async def red_delete_data_for_user(self, *, requester, user_id: int):
        try:
            await self.manager.logout(user_id)
        except CodexManagerError:
            pass
        self.manager.remove_account(user_id)
        await self.config.user_from_id(user_id).clear()

    async def _user(self, user_id):
        user = self.bot.get_user(int(user_id))
        if user is None:
            try:
                user = await self.bot.fetch_user(int(user_id))
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                return None
        return user

    async def _send_user(self, user_id, content=None, embed=None, view=None):
        user = await self._user(user_id)
        if user is None:
            return None
        try:
            return await user.send(content=content, embed=embed, view=view)
        except (discord.Forbidden, discord.HTTPException):
            return None

    async def _private(self, ctx, content=None, embed=None, view=None):
        try:
            message = await ctx.author.send(
                content=content, embed=embed, view=view
            )
        except (discord.Forbidden, discord.HTTPException):
            await ctx.send(
                "I could not DM you. Enable direct messages and try again."
            )
            return None
        if ctx.guild:
            await ctx.tick()
        return message

    @staticmethod
    def _window_label(window):
        if window.duration_minutes:
            hours = window.duration_minutes / 60
            if hours >= 24 and hours % 24 == 0:
                return f"{hours / 24:g}-day {window.window_name}"
            return f"{hours:g}-hour {window.window_name}"
        return window.window_name.title()

    async def usage_embed(self, user_id, payload=None):
        if payload is None:
            payload = await self.manager.rate_limits(user_id)
        windows = list(iter_limit_windows(payload))
        if not windows:
            raise CodexManagerError(
                "The linked account did not return Codex rate-limit windows."
            )
        embed = discord.Embed(
            title="Codex account usage",
            description="Live allowance reported by Codex app-server.",
            color=discord.Color.blurple(),
        )
        for window in windows[:10]:
            reset = (
                f"<t:{window.resets_at}:F> (<t:{window.resets_at}:R>)"
                if window.resets_at
                else "Not reported"
            )
            embed.add_field(
                name=f"{window.limit_name} - {self._window_label(window)}",
                value=(
                    f"**{window.remaining_percent}% remaining** "
                    f"({window.used_percent}% used)\nReset: {reset}"
                ),
                inline=False,
            )
        data = await self.config.user_from_id(user_id).all()
        state = "on" if data["enabled"] else "paused"
        levels = ", ".join(f"{level}%" for level in data["alert_levels"])
        embed.set_footer(
            text=f"Usage alerts: {state} at {levels} used"
        )
        return embed

    @staticmethod
    def _alert_advice(level):
        if level >= 95:
            return " Critical: consider pausing new work until the allowance resets."
        if level >= 75:
            return " Heads up: consider wrapping up long-running work."
        return ""

    async def process_user(self, user_id, data):
        if not data.get("connected") or not data.get("enabled"):
            return 0
        payload = await self.manager.rate_limits(user_id)
        levels = data.get("alert_levels", [50, 75, 95])
        sent = list(data.get("sent_alerts", []))
        delivered = 0
        for window, level in due_alerts(payload, levels, sent):
            reset = (
                f" It resets <t:{window.resets_at}:R>."
                if window.resets_at
                else ""
            )
            message = (
                f"Your **{window.limit_name}** "
                f"{self._window_label(window)} allowance has "
                f"crossed your **{level}% used** alert "
                f"({window.used_percent}% used, "
                f"{window.remaining_percent}% remaining).{reset}"
                f"{self._alert_advice(level)}"
            )
            if await self._send_user(user_id, message):
                sent.append(alert_key(window, level))
                delivered += 1
        group = self.config.user_from_id(user_id)
        await group.sent_alerts.set(sent[-30:])
        await group.last_checked_at.set(int(time.time()))
        return delivered

    @tasks.loop(minutes=15)
    async def notification_loop(self):
        for user_id, data in (await self.config.all_users()).items():
            try:
                await self.process_user(user_id, data)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Codex usage polling failed for user %s", user_id)

    @notification_loop.before_loop
    async def before_notification_loop(self):
        await self.bot.wait_until_red_ready()

    @commands.group(name="codex", invoke_without_command=True)
    async def codex(self, ctx):
        """Show your private live Codex allowance."""
        if not await self.config.user(ctx.author).connected():
            await self._private(
                ctx,
                "Your Codex account is not connected. "
                f"Use {ctx.clean_prefix}codex connect.",
            )
            return
        try:
            embed = await self.usage_embed(ctx.author.id)
        except CodexManagerError as exc:
            await self._private(ctx, f"Codex usage is unavailable: {exc}")
            return
        await self._private(ctx, embed=embed)

    @codex.command(name="connect")
    async def connect(self, ctx):
        """Privately link your ChatGPT account with a device code."""
        user_id = ctx.author.id
        if user_id in self._linking:
            await ctx.send("A Codex connection is already waiting for you.")
            return
        self._linking.add(user_id)
        server = None
        message = None
        try:
            if self._install_task and not self._install_task.done():
                await self._private(
                    ctx, "Codex is finishing its automatic setup. Try again shortly."
                )
                return
            server, request = await self.manager.begin_device_login(user_id)
            message = await self._private(
                ctx,
                content=(
                    "**Connect your Codex account**\n"
                    "1. Open the authorization page.\n"
                    f"2. Enter this one-time code: {request['userCode']}\n"
                    "3. Approve access.\n\nWaiting for ChatGPT..."
                ),
                view=CodexLinkView(request["verificationUrl"]),
            )
            if message is None:
                return
            result = await server.wait_for_login(request["loginId"])
            if not result.get("success"):
                raise CodexManagerError(
                    result.get("error") or "ChatGPT did not approve the link."
                )
            await self.config.user(ctx.author).connected.set(True)
            await self.config.user(ctx.author).sent_alerts.set([])
            await message.edit(
                content=(
                    "**Codex connected**\n"
                    "Run codex here or in a shared server to see live usage."
                ),
                view=None,
            )
        except CodexManagerError as exc:
            target = message.edit if message else None
            if target:
                await target(content=f"Codex linking failed: {exc}", view=None)
            else:
                await self._private(ctx, f"Codex linking failed: {exc}")
        finally:
            self._linking.discard(user_id)
            if server:
                await server.close()

    @codex.command(name="alerts")
    async def alerts(self, ctx, *percentages: int):
        """Set used-percentage alerts, such as 50 75 95."""
        if not percentages:
            levels = await self.config.user(ctx.author).alert_levels()
            await self._private(
                ctx,
                "Current Codex usage alerts: "
                + ", ".join(f"{level}% used" for level in levels),
            )
            return
        try:
            levels = validate_alert_levels(percentages)
        except ValueError as exc:
            await ctx.send(str(exc))
            return
        group = self.config.user(ctx.author)
        await group.alert_levels.set(list(levels))
        await group.sent_alerts.set([])
        await self._private(
            ctx,
            "Codex usage alerts set for "
            + ", ".join(f"{level}% used" for level in levels)
            + ".",
        )

    @codex.command(name="threshold")
    async def threshold(self, ctx, percent_remaining: int):
        """Set one legacy remaining-percentage warning threshold."""
        try:
            remaining = validate_alert_levels([percent_remaining])[0]
        except ValueError as exc:
            await ctx.send(str(exc))
            return
        used = max(1, 100 - remaining)
        group = self.config.user(ctx.author)
        await group.alert_levels.set([used])
        await group.sent_alerts.set([])
        await self._private(
            ctx,
            f"Alert set for {used}% used ({remaining}% remaining). "
            f"Use {ctx.clean_prefix}codex alerts 50 75 95 for milestones.",
        )

    @codex.command(name="pause")
    async def pause(self, ctx):
        """Pause automatic allowance notifications."""
        await self.config.user(ctx.author).enabled.set(False)
        await self._private(ctx, "Codex allowance notifications are paused.")

    @codex.command(name="resume")
    async def resume(self, ctx):
        """Resume automatic allowance notifications."""
        if not await self.config.user(ctx.author).connected():
            await ctx.send("Connect your Codex account first.")
            return
        await self.config.user(ctx.author).enabled.set(True)
        await self._private(ctx, "Codex allowance notifications are enabled.")

    @codex.command(name="disconnect")
    async def disconnect(self, ctx, confirm: bool = False):
        """Revoke and delete your Codex connection."""
        if not confirm:
            await ctx.send(
                "Run this command again with true to disconnect Codex "
                "and delete its local credentials."
            )
            return
        warning = ""
        try:
            await self.manager.logout(ctx.author.id)
        except CodexManagerError:
            warning = (
                " Remote revocation could not be confirmed; disconnect the "
                "app in ChatGPT Settings if needed."
            )
        self.manager.remove_account(ctx.author.id)
        await self.config.user(ctx.author).clear()
        await self._private(
            ctx,
            "Your Codex connection and local data were deleted." + warning,
        )

    @codex.command(name="about")
    async def about(self, ctx):
        """Explain the live Codex usage connection."""
        await self._private(
            ctx,
            "This cog uses the official Codex device-code login and "
            "account/rateLimits/read to show live allowance windows. "
            "It does not read conversations, prompts, or API keys.",
        )

    @commands.group(name="codexset", invoke_without_command=True)
    @checks.is_owner()
    async def codexset(self, ctx):
        """Manage the bot's private Codex CLI installation."""
        version = await self.manager.version()
        await ctx.send(
            f"Managed Codex CLI: {version}"
            if version
            else "Managed Codex CLI is not installed."
        )

    @codexset.command(name="install")
    async def codexset_install(self, ctx):
        """Install or update the official Codex CLI."""
        message = await ctx.send("Installing the official Codex CLI...")
        try:
            version = await self.manager.install()
        except CodexManagerError as exc:
            await message.edit(content=f"Codex installation failed: {exc}")
            return
        await message.edit(content=f"Codex is installed: {version}")
