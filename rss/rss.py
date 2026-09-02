import asyncio
import logging
import warnings

import discord
from bs4 import MarkupResemblesLocatorWarning
from redbot.core import commands
from redbot.core.utils import can_user_send_messages_in

from .commands import RSSCommands
from .config import RSS_VERSION, create_config, migrate_stored_feeds
from .delivery import RSSDeliveryMixin
from .fetcher import RSSFetcherMixin
from .models import migrate_feed_data
from .renderer import FeedRenderer
from .scheduler import FeedJob, FeedScheduler

# Originally based on aikaterna-cogs RSS; maintained here by SickProdigy.
log = logging.getLogger("red.Sick-Cogs.RSS")

RSS_USER_AGENT = (
    f"Sick-Cogs-RSS/{RSS_VERSION} "
    "(+https://gitea.rcs1.top/sickprodigy/Sick-Cogs/src/branch/develop/rss)"
)

warnings.filterwarnings(
    "ignore",
    category=DeprecationWarning,
    # Ignore the warning in feedparser module *and* our module to account for the unreleased fix of this warning:
    # https://github.com/kurtmckee/feedparser/pull/278
    module=r"^(feedparser|rss)(\..+)?$",
    message=(
        "To avoid breaking existing software while fixing issue 310, a temporary mapping has been created from"
        " `updated_parsed` to `published_parsed` if `updated_parsed` doesn't exist"
    ),
)
warnings.filterwarnings("ignore", module="rss", category=MarkupResemblesLocatorWarning)

class RSS(RSSCommands, RSSFetcherMixin, RSSDeliveryMixin, commands.Cog):
    """RSS feeds for your server."""

    def __init__(self, bot):
        self.bot = bot

        self.config = create_config(self)

        self._read_feeds_loop = None
        self._scheduler = None

        self._headers = {"User-Agent": RSS_USER_AGENT}
        self._session = None
        self._renderer = FeedRenderer()

    async def red_delete_data_for_user(self, **kwargs):
        """Nothing to delete"""
        return

    def initialize(self):
        self._read_feeds_loop = self.bot.loop.create_task(self._initialize())

    async def _initialize(self):
        await self._get_http_session()
        await migrate_stored_feeds(self.config, log)
        await self.read_feeds()

    def cog_unload(self):
        if self._read_feeds_loop:
            self._read_feeds_loop.cancel()
        if self._session and not self._session.closed:
            self.bot.loop.create_task(self._session.close())

    async def _get_channel_object(self, channel_id: int):
        """Helper for rss feed loop."""
        channel = self.bot.get_channel(channel_id)
        if not channel:
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except (discord.errors.Forbidden, discord.errors.NotFound):
                return None
        if channel and can_user_send_messages_in(channel.guild.me, channel):
            return channel
        return None

    async def read_feeds(self):
        """Run the bounded feed scheduler until the cog unloads."""
        await self.bot.wait_until_red_ready()
        self._scheduler = FeedScheduler(
            self._collect_feed_jobs,
            self._check_scheduled_feed,
            concurrency=5,
            interval=300,
        )
        await self._scheduler.run()

    async def _collect_feed_jobs(self):
        config_data = await self.config.all_channels()
        jobs = []
        for channel_id, channel_data in config_data.items():
            channel = await self._get_channel_object(int(channel_id))
            if not channel:
                continue
            if await self.bot.cog_disabled_in_guild(self, channel.guild):
                continue

            for feed_name, feed_data in channel_data.get("feeds", {}).items():
                migrated, _ = migrate_feed_data(feed_data)
                if migrated["paused"]:
                    continue
                jobs.append(
                    FeedJob(
                        key=(int(channel_id), feed_name),
                        channel=channel,
                        feed_name=feed_name,
                        feed_data=migrated,
                    )
                )
        return jobs

    async def _check_scheduled_feed(self, job: FeedJob) -> int:
        try:
            await self.get_current_feed(job.channel, job.feed_name, job.feed_data)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            await self._record_feed_check(job.channel, job.feed_name, error)
            log.exception(
                "RSS check failed for %s in channel %s",
                job.feed_name,
                job.channel.id,
            )

        current = await self.config.channel(job.channel).feeds.get_raw(
            job.feed_name, default=None
        )
        if not current:
            return 0
        try:
            return max(0, int(current.get("consecutive_failures", 0) or 0))
        except (TypeError, ValueError):
            return 0
