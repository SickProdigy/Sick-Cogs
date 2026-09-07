import asyncio
import datetime
import logging
from typing import Optional, Union

import aiohttp
import discord
from discord.ext import tasks
from redbot.core import Config, checks, commands
from redbot.core.bot import Red
from redbot.core.utils import can_user_send_messages_in


log = logging.getLogger("red.Sick-Cogs.DadJokes")

CONFIG_IDENTIFIER = 5829948157
DAD_JOKE_URL = "https://icanhazdadjoke.com/"
USER_AGENT = "Sick-Cogs-DadJokes/1.1.0 (+https://gitea.rcs1.top/sickprodigy/Sick-Cogs)"
GuildMessageable = Union[discord.TextChannel, discord.VoiceChannel, discord.StageChannel, discord.Thread]


def utc_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def parse_datetime(value: Optional[str]) -> Optional[datetime.datetime]:
    if not value:
        return None
    try:
        parsed = datetime.datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed.astimezone(datetime.timezone.utc)


class DadJokes(commands.Cog):
    """Random dad jokes from icanhazdadjoke.com"""

    __author__ = ["SickProdigy", "UltimatePancake"]
    __version__ = "1.1.0"

    default_guild = {
        "enabled": False,
        "channel_id": None,
        "interval_minutes": 360,
        "next_joke_at": None,
    }

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=CONFIG_IDENTIFIER, force_registration=True)
        self.config.register_guild(**self.default_guild)
        self.session: Optional[aiohttp.ClientSession] = None
        self.random_joke_loop.start()

    async def red_delete_data_for_user(self, **kwargs):
        """Nothing to delete."""
        return

    def cog_unload(self):
        self.random_joke_loop.cancel()
        if self.session and not self.session.closed:
            self.bot.loop.create_task(self.session.close())

    async def get_session(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            timeout = aiohttp.ClientTimeout(total=30)
            headers = {"Accept": "text/plain", "User-Agent": USER_AGENT}
            self.session = aiohttp.ClientSession(timeout=timeout, headers=headers)
        return self.session

    async def fetch_joke(self) -> str:
        session = await self.get_session()
        async with session.get(DAD_JOKE_URL) as response:
            if response.status != 200:
                raise RuntimeError("Oops! Cannot get a dad joke...")
            joke = (await response.text(encoding="UTF-8")).strip()
        if not joke:
            raise RuntimeError("Oops! Cannot get a dad joke...")
        return joke

    async def get_channel(self, guild: discord.Guild, channel_id: int) -> Optional[GuildMessageable]:
        channel = guild.get_channel_or_thread(channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                return None
        if getattr(channel, "guild", None) != guild:
            return None
        if not isinstance(channel, (discord.TextChannel, discord.VoiceChannel, discord.StageChannel, discord.Thread)):
            return None
        if can_user_send_messages_in(guild.me, channel):
            return channel
        return None

    @staticmethod
    def next_run_after(interval_minutes: int) -> str:
        return (utc_now() + datetime.timedelta(minutes=max(1, int(interval_minutes)))).isoformat()

    async def send_random_joke(self, channel: GuildMessageable) -> str:
        joke = await self.fetch_joke()
        await channel.send(f"`{joke}`")
        return joke

    async def maybe_send_scheduled_joke(self, guild: discord.Guild):
        settings = await self.config.guild(guild).all()
        if not settings["enabled"] or not settings["channel_id"]:
            return

        next_joke_at = parse_datetime(settings.get("next_joke_at"))
        if next_joke_at and next_joke_at > utc_now():
            return

        channel = await self.get_channel(guild, int(settings["channel_id"]))
        if not channel:
            await self.config.guild(guild).enabled.set(False)
            log.warning("Disabled dad joke posting for guild %s because channel %s is unavailable.", guild.id, settings["channel_id"])
            return

        try:
            await self.send_random_joke(channel)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Failed to send scheduled dad joke for guild %s", guild.id)
            return

        await self.config.guild(guild).next_joke_at.set(self.next_run_after(settings["interval_minutes"]))

    @tasks.loop(minutes=1)
    async def random_joke_loop(self):
        await self.bot.wait_until_red_ready()
        for guild in list(self.bot.guilds):
            if await self.bot.cog_disabled_in_guild(self, guild):
                continue
            try:
                await self.maybe_send_scheduled_joke(guild)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Dad joke scheduler failed for guild %s", guild.id)

    @random_joke_loop.before_loop
    async def before_random_joke_loop(self):
        await self.bot.wait_until_red_ready()

    @commands.command()
    async def dadjoke(self, ctx: commands.Context):
        """Gets a random dad joke."""
        try:
            joke = await self.fetch_joke()
        except RuntimeError as exc:
            return await ctx.send(str(exc))
        except aiohttp.ClientConnectionError:
            return await ctx.send("Oops! Cannot get a dad joke...")

        await ctx.send(f"`{joke}`")

    @commands.guild_only()
    @commands.group(name="dadjokeset", aliases=("dadjokes",), invoke_without_command=True)
    @checks.mod_or_permissions(manage_guild=True)
    async def dadjokeset(self, ctx: commands.Context):
        """Configure random dad joke posting."""
        pass

    @dadjokeset.command(name="channel")
    async def dadjokeset_channel(self, ctx: commands.Context, channel: Optional[GuildMessageable] = None):
        """Set the channel for scheduled random dad jokes. Defaults to this channel."""
        channel = channel or ctx.channel
        if not await self.get_channel(ctx.guild, channel.id):
            await ctx.send("I cannot send messages in that channel.")
            return
        await self.config.guild(ctx.guild).channel_id.set(channel.id)
        await ctx.send(f"Scheduled dad jokes will be posted in {channel.mention}.")

    @dadjokeset.command(name="interval")
    async def dadjokeset_interval(self, ctx: commands.Context, minutes: int):
        """Set minutes to wait before sending the next scheduled dad joke (5-43200)."""
        if minutes < 5 or minutes > 43200:
            await ctx.send("Interval must be between 5 and 43200 minutes.")
            return
        await self.config.guild(ctx.guild).interval_minutes.set(minutes)
        await self.config.guild(ctx.guild).next_joke_at.set(self.next_run_after(minutes))
        await ctx.send(f"Scheduled dad jokes will wait {minutes} minute(s) between posts.")

    @dadjokeset.command(name="enable", aliases=("enabled",))
    async def dadjokeset_enable(self, ctx: commands.Context):
        """Enable scheduled random dad jokes."""
        settings = await self.config.guild(ctx.guild).all()
        if not settings["channel_id"]:
            await ctx.send(f"Set a dad joke channel first with `{ctx.clean_prefix}dadjokeset channel`.")
            return
        await self.config.guild(ctx.guild).enabled.set(True)
        if not settings.get("next_joke_at"):
            await self.config.guild(ctx.guild).next_joke_at.set(self.next_run_after(settings["interval_minutes"]))
        await ctx.send("Scheduled dad jokes are now enabled.")

    @dadjokeset.command(name="disable", aliases=("disabled",))
    async def dadjokeset_disable(self, ctx: commands.Context):
        """Disable scheduled random dad jokes."""
        await self.config.guild(ctx.guild).enabled.set(False)
        await ctx.send("Scheduled dad jokes are now disabled.")

    @dadjokeset.command(name="force")
    async def dadjokeset_force(self, ctx: commands.Context):
        """Send a dad joke to the configured channel now and reset the wait timer."""
        settings = await self.config.guild(ctx.guild).all()
        if not settings["channel_id"]:
            await ctx.send("Set a dad joke channel first.")
            return
        channel = await self.get_channel(ctx.guild, int(settings["channel_id"]))
        if not channel:
            await ctx.send("I cannot send messages in the configured channel.")
            return
        try:
            await self.send_random_joke(channel)
        except RuntimeError as exc:
            await ctx.send(str(exc))
            return
        await self.config.guild(ctx.guild).next_joke_at.set(self.next_run_after(settings["interval_minutes"]))
        await ctx.send(f"Sent a dad joke to {channel.mention} and reset the wait timer.")

    @dadjokeset.command(name="settings")
    async def dadjokeset_settings(self, ctx: commands.Context):
        """Show current random dad joke settings."""
        settings = await self.config.guild(ctx.guild).all()
        channel = ctx.guild.get_channel_or_thread(settings["channel_id"]) if settings["channel_id"] else None
        embed = discord.Embed(title="Dad joke settings", colour=discord.Colour.blurple())
        embed.add_field(name="Enabled", value=str(settings["enabled"]), inline=True)
        embed.add_field(name="Channel", value=channel.mention if channel else "Not set", inline=True)
        embed.add_field(name="Interval", value=f"{settings['interval_minutes']} minute(s)", inline=True)
        embed.add_field(name="Next joke", value=settings.get("next_joke_at") or "After enabling", inline=False)
        await ctx.send(embed=embed)
