import asyncio
import logging
import time
import discord
from discord.ext import tasks
from redbot.core import Config, commands

from .models import due_notifications, normalize_offsets, roll_cycle, validate_percent
from .provider import ManualUsageProvider

log = logging.getLogger("red.sickcogs.codex")

CONFIG_IDENTIFIER = 620351947235
DEFAULT_USER = {
    "enabled": False,
    "reset_at": 0,
    "cycle_seconds": 7 * 86400,
    "reminder_offsets": [48 * 3600, 24 * 3600],
    "sent_keys": [],
    "remaining_percent": None,
    "updated_at": 0,
    "low_threshold": 10,
    "low_notified_reset": 0,
}
USAGE_URL = "https://chatgpt.com/#settings/Usage"


class Codex(commands.Cog):
    """Private Codex allowance-cycle reminders without account scraping."""

    __author__ = ["SickProdigy"]
    __version__ = "0.1.0"

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(
            self, identifier=CONFIG_IDENTIFIER, force_registration=True
        )
        self.config.register_user(**DEFAULT_USER)
        self.provider = ManualUsageProvider(self.config)

    async def cog_load(self):
        self.notification_loop.start()

    def cog_unload(self):
        self.notification_loop.cancel()

    async def red_delete_data_for_user(self, *, requester, user_id: int):
        await self.config.user_from_id(user_id).clear()

    async def _user(self, user_id):
        user = self.bot.get_user(int(user_id))
        if user is None:
            try:
                user = await self.bot.fetch_user(int(user_id))
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                return None
        return user

    async def _send_user(self, user_id, message):
        user = await self._user(user_id)
        if user is None:
            return False
        try:
            await user.send(message)
        except (discord.Forbidden, discord.HTTPException):
            return False
        return True

    async def process_user(self, user_id, data, now=None):
        now = int(time.time() if now is None else now)
        if not data.get("enabled") or not data.get("reset_at"):
            return 0
        group = self.config.user_from_id(user_id)
        reset_at = int(data["reset_at"])
        cycle_seconds = int(data.get("cycle_seconds", 7 * 86400))
        sent = list(data.get("sent_keys", []))
        delivered = 0

        if reset_at <= now:
            next_reset, _ = roll_cycle(now, reset_at, cycle_seconds)
            remaining = data.get("remaining_percent")
            summary = (
                f" Your last manual estimate was {remaining}% remaining."
                if remaining is not None
                else ""
            )
            if await self._send_user(
                user_id,
                f"Your tracked Codex usage cycle reset.{summary} "
                f"The next reset is <t:{next_reset}:F>.",
            ):
                delivered += 1
            await group.reset_at.set(next_reset)
            await group.remaining_percent.set(None)
            await group.updated_at.set(0)
            await group.sent_keys.set([])
            await group.low_notified_reset.set(0)
            reset_at, sent = next_reset, []

        for key, offset in due_notifications(
            now, reset_at,
            (
                offset
                for offset in data.get("reminder_offsets", [])
                if 0 < int(offset) < cycle_seconds
            ),
            sent
        ):
            hours = offset // 3600
            remaining = data.get("remaining_percent")
            estimate = (
                f" Your manual estimate is {remaining}% remaining."
                if remaining is not None
                else ""
            )
            if await self._send_user(
                user_id,
                f"Your tracked Codex cycle resets <t:{reset_at}:R> "
                f"({hours}h reminder).{estimate} Review official usage: {USAGE_URL}",
            ):
                sent.append(key)
                delivered += 1
        if sent != data.get("sent_keys", []):
            await group.sent_keys.set(sent[-20:])
        return delivered

    @tasks.loop(minutes=5)
    async def notification_loop(self):
        now = int(time.time())
        for user_id, data in (await self.config.all_users()).items():
            try:
                await self.process_user(user_id, data, now)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Codex reminder processing failed for user %s", user_id)

    @notification_loop.before_loop
    async def before_notification_loop(self):
        await self.bot.wait_until_red_ready()

    async def _private(self, ctx, content=None, embed=None):
        try:
            await ctx.author.send(content=content, embed=embed)
        except (discord.Forbidden, discord.HTTPException):
            await ctx.send(
                "I could not DM you. Enable direct messages and try again."
            )
            return False
        if ctx.guild:
            await ctx.tick()
        return True

    async def status_embed(self, user):
        data = await self.config.user(user).all()
        embed = discord.Embed(
            title="Codex usage reminders",
            description=(
                "Manual private tracking. OpenAI does not expose remaining allowance "
                "or reset timestamps to this cog."
            ),
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="Notifications",
            value="Enabled" if data["enabled"] else "Paused",
        )
        reset_at = int(data.get("reset_at", 0) or 0)
        embed.add_field(
            name="Tracked reset",
            value=f"<t:{reset_at}:F> (<t:{reset_at}:R>)" if reset_at else "Not configured",
            inline=False,
        )
        remaining = data.get("remaining_percent")
        embed.add_field(
            name="Manual remaining estimate",
            value=f"{remaining}%" if remaining is not None else "Not recorded",
        )
        embed.add_field(
            name="Reminder offsets",
            value=", ".join(
                f"{int(value) // 3600}h" for value in data["reminder_offsets"]
            ),
        )
        embed.add_field(
            name="Official usage",
            value=f"[Open ChatGPT Settings → Usage]({USAGE_URL})",
            inline=False,
        )
        embed.set_footer(
            text="Estimates are entered by you and are never visible to other members."
        )
        return embed

    @commands.group(name="codex", invoke_without_command=True)
    async def codex(self, ctx):
        """Manage your private Codex usage-cycle reminders."""
        await self._private(ctx, embed=await self.status_embed(ctx.author))

    @codex.command(name="setup")
    async def setup_cycle(
        self, ctx, hours_until_reset: float, cycle_hours: int = 168
    ):
        """Track a reset relative to now and its recurring cycle length."""
        if not 0 < hours_until_reset <= 720:
            await ctx.send("Hours until reset must be above 0 and at most 720.")
            return
        if not 1 <= cycle_hours <= 720:
            await ctx.send("Cycle hours must be from 1 through 720.")
            return
        now = int(time.time())
        group = self.config.user(ctx.author)
        await group.reset_at.set(now + int(hours_until_reset * 3600))
        await group.cycle_seconds.set(cycle_hours * 3600)
        await group.sent_keys.set([])
        await group.low_notified_reset.set(0)
        await group.enabled.set(True)
        await self._private(
            ctx,
            "Codex usage-cycle reminders are enabled. "
            "Use the status command to review the private schedule.",
        )

    @codex.command(name="remaining")
    async def remaining(self, ctx, percent: int):
        """Record your own estimate of remaining allowance."""
        try:
            percent = validate_percent(percent)
        except ValueError as error:
            await ctx.send(str(error))
            return
        group = self.config.user(ctx.author)
        data = await group.all()
        await group.remaining_percent.set(percent)
        await group.updated_at.set(int(time.time()))
        reset_at = int(data.get("reset_at", 0) or 0)
        threshold = int(data.get("low_threshold", 10))
        note = ""
        if percent <= threshold and reset_at:
            note = f" This is at or below your {threshold}% low-allowance threshold."
            await group.low_notified_reset.set(reset_at)
        await self._private(
            ctx,
            f"Your manual remaining estimate is now {percent}%.{note} "
            f"Official usage remains available at {USAGE_URL}",
        )

    @codex.command(name="reminders")
    async def reminders(self, ctx, *hours: int):
        """Set one or more reminder offsets, such as 48 24 1."""
        try:
            offsets = normalize_offsets(hours)
        except ValueError as error:
            await ctx.send(str(error))
            return
        await self.config.user(ctx.author).reminder_offsets.set(offsets)
        await self.config.user(ctx.author).sent_keys.set([])
        await self._private(
            ctx,
            "Reminder offsets saved: "
            + ", ".join(f"{value // 3600}h" for value in offsets),
        )

    @codex.command(name="threshold")
    async def threshold(self, ctx, percent: int):
        """Set the low-allowance threshold for your manual estimate."""
        try:
            percent = validate_percent(percent)
        except ValueError as error:
            await ctx.send(str(error))
            return
        await self.config.user(ctx.author).low_threshold.set(percent)
        await self._private(ctx, f"Low-allowance threshold set to {percent}%.")

    @codex.command(name="pause")
    async def pause(self, ctx):
        """Pause notifications without deleting your schedule."""
        await self.config.user(ctx.author).enabled.set(False)
        await self._private(ctx, "Codex usage reminders are paused.")

    @codex.command(name="resume")
    async def resume(self, ctx):
        """Resume a configured notification schedule."""
        reset_at = await self.config.user(ctx.author).reset_at()
        if not reset_at:
            await ctx.send("Configure a cycle with the setup command first.")
            return
        await self.config.user(ctx.author).enabled.set(True)
        await self._private(ctx, "Codex usage reminders are enabled.")

    @codex.command(name="disconnect")
    async def disconnect(self, ctx, confirm: bool = False):
        """Delete all of your Codex reminder data."""
        if not confirm:
            await ctx.send(
                "Run this command again with true to delete your private Codex data."
            )
            return
        await self.config.user(ctx.author).clear()
        await self._private(ctx, "Your Codex reminder data was deleted.")

    @codex.command(name="about")
    async def about(self, ctx):
        """Explain live usage limitations and the manual fallback."""
        await self._private(
            ctx,
            "OpenAI currently directs third-party apps to ChatGPT Settings → Usage. "
            "It does not provide this cog a supported remaining-balance or reset-time "
            f"feed, so reminders use only dates and estimates you enter. {USAGE_URL}",
        )
