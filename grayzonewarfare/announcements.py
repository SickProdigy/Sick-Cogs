"""Optional per-server Steam announcements alongside quest lookup."""
import asyncio
import datetime
import logging
from typing import Optional

import discord
from discord.ext import tasks
from redbot.core import Config, commands
from redbot.core.utils import can_user_send_messages_in

from .news_client import SteamNewsClient, SteamNewsError, extract_image, item_id, new_items, plain_text

log = logging.getLogger("red.sick-cogs.GrayZoneWarfare.news")
CONFIG_IDENTIFIER = 6202611242479810


def now():
    return datetime.datetime.now(datetime.timezone.utc)


class SteamAnnouncements(commands.Cog):
    default_guild = {
        "enabled": False, "channel_id": None, "interval_minutes": 10,
        "posted_ids": [], "last_poll_at": None, "last_success_at": None,
    }

    def init_announcements(self):
        self.config = Config.get_conf(self, identifier=CONFIG_IDENTIFIER, force_registration=True)
        self.config.register_guild(**self.default_guild)
        self._news_locks = {}

    async def cog_load(self):
        self.news_loop.start()

    def news_lock(self, guild):
        return self._news_locks.setdefault(guild.id, asyncio.Lock())

    async def fetch_news(self):
        return await SteamNewsClient(self.session).fetch()

    @staticmethod
    def news_embed(item):
        embed = discord.Embed(
            title=str(item.get("title") or "Gray Zone Warfare announcement")[:256],
            url=item.get("url"),
            description=plain_text(item.get("contents") or "", limit=3500, youtube_links=True) or "Open the announcement on Steam for details.",
            colour=discord.Colour.from_rgb(191, 124, 46),
        )
        image = extract_image(item.get("contents") or "")
        if image:
            embed.set_image(url=image)
        embed.set_footer(text="Gray Zone Warfare • Official Steam News")
        return embed

    async def show_news(self, ctx):
        try:
            items = await self.fetch_news()
        except SteamNewsError as error:
            await ctx.send(str(error)); return
        if not items:
            await ctx.send("No official Gray Zone Warfare announcements were found."); return
        latest = max(items, key=lambda item: int(item.get("date") or 0))
        await ctx.send(embed=self.news_embed(latest), allowed_mentions=discord.AllowedMentions.none())

    async def seed_news(self, group):
        items = await self.fetch_news()
        await group.posted_ids.set([item_id(item) for item in items][-500:])
        await group.last_poll_at.set(now().isoformat())
        return len(items)

    async def poll_news_guild(self, guild, items):
        async with self.news_lock(guild):
            group = self.config.guild(guild)
            settings = await group.all()
            if not settings["enabled"] or not settings["channel_id"]:
                return 0
            channel = guild.get_channel_or_thread(settings["channel_id"])
            if channel is None or not can_user_send_messages_in(guild.me, channel) or not channel.permissions_for(guild.me).embed_links:
                return 0
            await group.last_poll_at.set(now().isoformat())
            posted = list(settings["posted_ids"])
            sent = 0
            for item in new_items(items, posted):
                # Persist only successful sends; leave failed and later items for retry.
                await channel.send(embed=self.news_embed(item), allowed_mentions=discord.AllowedMentions.none())
                posted.append(item_id(item))
                await group.posted_ids.set(posted[-500:])
                sent += 1
            await group.last_success_at.set(now().isoformat())
            return sent

    async def poll_news_once(self):
        due = []
        for guild in list(self.bot.guilds):
            if await self.bot.cog_disabled_in_guild(self, guild):
                continue
            settings = await self.config.guild(guild).all()
            if not settings["enabled"] or not settings["channel_id"]:
                continue
            try:
                last = datetime.datetime.fromisoformat(settings["last_poll_at"]) if settings["last_poll_at"] else None
                if last and last.tzinfo is None:
                    last = last.replace(tzinfo=datetime.timezone.utc)
            except (ValueError, TypeError):
                last = None
            if last is None or now() >= last + datetime.timedelta(minutes=max(5, settings["interval_minutes"])):
                due.append(guild)
        if not due:
            return
        items = await self.fetch_news()
        for guild in due:
            try:
                await self.poll_news_guild(guild, items)
            except Exception:
                log.exception("Steam announcement delivery failed in guild %s", guild.id)

    @tasks.loop(minutes=1)
    async def news_loop(self):
        try:
            await self.poll_news_once()
        except Exception:
            log.exception("Steam announcement poll failed")

    @news_loop.before_loop
    async def before_news_loop(self):
        await self.bot.wait_until_red_ready()

    @commands.group(name="gzwset", aliases=["grayzonewarfareset"], invoke_without_command=True)
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def gzwset(self, ctx):
        """Configure official Steam announcements. Set a channel, then autopost start."""
        settings = await self.config.guild(ctx.guild).all()
        channel = ctx.guild.get_channel_or_thread(settings["channel_id"]) if settings["channel_id"] else None
        await ctx.send(
            f"Gray Zone Warfare announcements: {'running' if settings['enabled'] else 'stopped'}.\n"
            f"Channel: {channel.mention if channel else 'Not configured'}\n"
            f"Interval: {settings['interval_minutes']} minutes\n"
            f"Last successful check: {settings['last_success_at'] or 'Never'}",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @gzwset.command(name="channel")
    async def gzwset_channel(self, ctx, channel: Optional[discord.TextChannel] = None):
        """Set the update channel; omit it to stop posting and clear the channel."""
        async with self.news_lock(ctx.guild):
            group = self.config.guild(ctx.guild)
            if channel is None:
                await group.enabled.set(False)
                await group.channel_id.set(None)
                await ctx.send("Automatic posting stopped and the channel cleared."); return
            if channel.guild.id != ctx.guild.id or not can_user_send_messages_in(ctx.guild.me, channel) or not channel.permissions_for(ctx.guild.me).embed_links:
                await ctx.send("I need permission to send messages and embed links in that server channel."); return
            try:
                seeded = await self.seed_news(group)
            except SteamNewsError as error:
                await ctx.send(f"Channel not saved: {error}"); return
            await group.enabled.set(False)
            await group.channel_id.set(channel.id)
            await ctx.send(f"Channel set to {channel.mention}; {seeded} existing announcements marked as seen. Use `{ctx.clean_prefix}gzwset autopost start` to enable new posts.")

    @gzwset.group(name="autopost", invoke_without_command=True)
    async def gzwset_autopost(self, ctx):
        """Start or stop scheduled Steam announcements."""
        await ctx.send_help()

    @gzwset_autopost.command(name="start")
    async def gzwset_start(self, ctx):
        """Start posting future announcements without backfilling old posts."""
        async with self.news_lock(ctx.guild):
            group = self.config.guild(ctx.guild)
            if await group.enabled():
                await ctx.send("Automatic posting is already running."); return
            channel_id = await group.channel_id()
            channel = ctx.guild.get_channel_or_thread(channel_id) if channel_id else None
            if channel is None or not can_user_send_messages_in(ctx.guild.me, channel) or not channel.permissions_for(ctx.guild.me).embed_links:
                await ctx.send("Set an accessible announcement channel with send/embed permissions first."); return
            try:
                await self.seed_news(group)
            except SteamNewsError as error:
                await ctx.send(f"Automatic posting was not started: {error}"); return
            await group.enabled.set(True)
            await ctx.send("Automatic Gray Zone Warfare announcements started.")

    @gzwset_autopost.command(name="stop")
    async def gzwset_stop(self, ctx):
        """Stop scheduled announcements."""
        async with self.news_lock(ctx.guild):
            await self.config.guild(ctx.guild).enabled.set(False)
        await ctx.send("Automatic Gray Zone Warfare announcements stopped.")

    @gzwset.command(name="interval")
    async def gzwset_interval(self, ctx, minutes: int):
        """Set the check interval from 5 to 1440 minutes (default 10)."""
        if not 5 <= minutes <= 1440:
            await ctx.send("Choose an interval between 5 and 1440 minutes."); return
        await self.config.guild(ctx.guild).interval_minutes.set(minutes)
        await ctx.send(f"Steam announcements will be checked every {minutes} minutes.")
