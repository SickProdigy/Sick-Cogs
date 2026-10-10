import asyncio
import datetime
import io
import logging
from typing import Optional
from urllib.parse import urlparse

import aiohttp
import discord
from discord.ext import tasks
from redbot.core import Config, commands
from redbot.core.bot import Red
from redbot.core.utils import can_user_send_messages_in
from redbot.core.utils.chat_formatting import humanize_list

from .client import (
    ANNOUNCEMENTS_URL,
    CATEGORIES,
    CATEGORY_LABELS,
    USER_AGENT,
    SteamNewsClient,
    SteamNewsError,
    classify_news,
    extract_image,
    extract_images,
    image_dimensions,
    optimize_gallery_image,
    item_id,
    new_items,
    plain_text,
    recent_items,
)


log = logging.getLogger("red.sick-cogs.Ark")
CONFIG_IDENTIFIER = 620260924023998301
LEGACY_CONFIG_COG_NAME = "ArkAnnouncements"
DEFAULT_CATEGORIES = ["all"]
MIN_INTERVAL_MINUTES = 5
MAX_INTERVAL_MINUTES = 1440
MAX_POSTED_IDS = 500
DELIVERY_MODES = ("card", "article")
ARTICLE_TEXT_LIMIT = 1400
MAX_GALLERY_IMAGES = 4
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_GALLERY_BYTES = 5 * 1024 * 1024
STEAM_IMAGE_HOSTS = {"clan.steamstatic.com", "clan.akamai.steamstatic.com"}


def utc_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def category_display_names(categories):
    return [
        "All Steam Posts" if category == "all" else CATEGORY_LABELS.get(category, category)
        for category in categories
    ]


def due(last_poll_at: Optional[str], interval_minutes: int) -> bool:
    if not last_poll_at:
        return True
    try:
        parsed = datetime.datetime.fromisoformat(last_poll_at)
    except (TypeError, ValueError):
        return True
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return utc_now() >= parsed + datetime.timedelta(minutes=interval_minutes)


class Ark(commands.Cog):
    """Publish official ARK: Survival Ascended Steam announcements."""

    __author__ = ["SickProdigy"]
    __version__ = "1.1.4"

    default_guild = {
        "enabled": False,
        "channel_id": None,
        "role_id": None,
        "categories": DEFAULT_CATEGORIES,
        "delivery_mode": "card",
        "interval_minutes": 10,
        "posted_ids": [],
        "last_poll_at": None,
        "last_success_at": None,
    }

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(
            self, identifier=CONFIG_IDENTIFIER, force_registration=True
        )
        self.legacy_config = Config.get_conf(
            None,
            identifier=CONFIG_IDENTIFIER,
            force_registration=True,
            cog_name=LEGACY_CONFIG_COG_NAME,
        )
        self.config.register_global(legacy_namespace_migrated=False)
        self.config.register_guild(**self.default_guild)
        self.session: Optional[aiohttp.ClientSession] = None
        self._poll_locks = {}

    async def cog_load(self):
        await self._migrate_legacy_config()
        self.poll_loop.start()

    async def _migrate_legacy_config(self):
        if await self.config.legacy_namespace_migrated():
            return
        for guild_id, settings in (await self.legacy_config.all_guilds()).items():
            await self.config.guild_from_id(int(guild_id)).set(settings)
        await self.config.legacy_namespace_migrated.set(True)

    async def red_delete_data_for_user(self, **kwargs):
        """This cog stores no user data."""
        return

    def cog_unload(self):
        self.poll_loop.cancel()
        if self.session and not self.session.closed:
            self.bot.loop.create_task(self.session.close())

    async def get_session(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30), headers={"User-Agent": USER_AGENT}
            )
        return self.session

    async def get_client(self) -> SteamNewsClient:
        return SteamNewsClient(await self.get_session())

    async def get_channel(self, guild: discord.Guild, channel_id: int):
        channel = guild.get_channel_or_thread(int(channel_id))
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(int(channel_id))
            except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                return None
        if getattr(channel, "guild", None) != guild:
            return None
        if not can_user_send_messages_in(guild.me, channel):
            return None
        return channel

    def make_embed(self, item: dict, *, image_url: Optional[str] = None) -> discord.Embed:
        category = classify_news(item)
        timestamp = datetime.datetime.fromtimestamp(
            int(item.get("date") or 0), tz=datetime.timezone.utc
        )
        description = plain_text(item.get("contents") or "", youtube_links=True) or "Open the official Steam announcement for details."
        url = str(item.get("url") or ANNOUNCEMENTS_URL)
        embed = discord.Embed(
            title=str(item.get("title") or "ARK: Survival Ascended announcement")[:256],
            url=url,
            description=description,
            color=discord.Color.from_rgb(45, 117, 92),
            timestamp=timestamp,
        )
        embed.set_author(name="ARK: Survival Ascended — Official Steam News", url=ANNOUNCEMENTS_URL)
        embed.set_footer(text=CATEGORY_LABELS.get(category, CATEGORY_LABELS["official"]))
        image = image_url or extract_image(item.get("contents") or "")
        if image:
            embed.set_image(url=image)
        return embed

    async def select_card_image(self, contents: str) -> Optional[str]:
        """Choose the first real artwork image while skipping Steam title strips."""
        candidates = extract_images(contents, limit=12)
        session = await self.get_session()
        for url in candidates:
            if (urlparse(url).hostname or "").casefold() not in STEAM_IMAGE_HOSTS:
                continue
            try:
                async with session.get(url, headers={"Range": "bytes=0-65535"}) as response:
                    final_host = (response.url.host or "").casefold()
                    if response.status >= 400 or final_host not in STEAM_IMAGE_HOSTS:
                        continue
                    header = bytearray()
                    async for chunk in response.content.iter_chunked(16 * 1024):
                        header.extend(chunk)
                        if len(header) >= 64 * 1024:
                            break
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
                continue
            dimensions = image_dimensions(bytes(header))
            if dimensions and dimensions[0] >= 400 and dimensions[1] >= 200:
                return url
        return extract_image(contents)

    def make_article_text(self, item: dict) -> str:
        title = discord.utils.escape_markdown(
            str(item.get("title") or "ARK: Survival Ascended announcement")[:240]
        )
        url = str(item.get("url") or ANNOUNCEMENTS_URL)
        full_text = plain_text(
            item.get("contents") or "", limit=100_000, youtube_links=True
        )
        truncated = len(full_text) > ARTICLE_TEXT_LIMIT
        description = plain_text(
            item.get("contents") or "", limit=ARTICLE_TEXT_LIMIT, youtube_links=True
        )
        lines = [
            "**ARK: Survival Ascended — Official Steam News**",
            f"## [{title}]({url})",
        ]
        if description:
            lines.extend(("", description))
        if truncated:
            lines.extend(("", f"[Continue reading on Steam…]({url})"))
        return "\n".join(lines)

    async def download_gallery_files(self, contents: str):
        files = []
        gallery_bytes = 0
        session = await self.get_session()
        # Inspect extra candidates so small title strips do not consume gallery slots.
        for url in extract_images(contents, limit=12):
            if len(files) >= MAX_GALLERY_IMAGES:
                break
            if (urlparse(url).hostname or "").casefold() not in STEAM_IMAGE_HOSTS:
                continue
            try:
                async with session.get(url) as response:
                    final_host = (response.url.host or "").casefold()
                    content_type = response.headers.get("Content-Type", "").casefold()
                    content_length = int(response.headers.get("Content-Length") or 0)
                    if (
                        response.status >= 400
                        or final_host not in STEAM_IMAGE_HOSTS
                        or not content_type.startswith("image/")
                        or content_length > MAX_IMAGE_BYTES
                    ):
                        continue
                    chunks = bytearray()
                    async for chunk in response.content.iter_chunked(64 * 1024):
                        chunks.extend(chunk)
                        if len(chunks) > MAX_IMAGE_BYTES:
                            break
                    payload = bytes(chunks)
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
                log.warning("Could not download ARK article image from %s", url)
                continue
            dimensions = image_dimensions(payload)
            if (
                len(payload) > MAX_IMAGE_BYTES
                or dimensions is None
                or dimensions[0] < 400
                or dimensions[1] < 200
                or dimensions[0] > 8192
                or dimensions[1] > 8192
            ):
                continue
            try:
                payload = await asyncio.to_thread(optimize_gallery_image, payload)
            except ValueError:
                log.warning("Could not resize ARK article image from %s", url)
                continue
            if gallery_bytes + len(payload) > MAX_GALLERY_BYTES:
                continue
            gallery_bytes += len(payload)
            files.append(
                discord.File(
                    io.BytesIO(payload),
                    filename=f"ark-announcement-{len(files) + 1}.jpg",
                )
            )
        return files

    async def send_article(self, channel, item: dict, role_id: Optional[int]):
        role = channel.guild.get_role(int(role_id)) if role_id else None
        article = self.make_article_text(item)
        content = f"{role.mention}\n{article}" if role else article
        files = await self.download_gallery_files(item.get("contents") or "")
        await channel.send(
            content=content,
            files=files,
            allowed_mentions=discord.AllowedMentions(everyone=False, users=False, roles=bool(role)),
            suppress_embeds=True,
        )

    async def send_item(
        self, channel, item: dict, role_id: Optional[int], delivery_mode: str = "card"
    ):
        if delivery_mode == "article":
            await self.send_article(channel, item, role_id)
            return
        role = channel.guild.get_role(int(role_id)) if role_id else None
        image_url = await self.select_card_image(item.get("contents") or "")
        await channel.send(
            content=role.mention if role else None,
            embed=self.make_embed(item, image_url=image_url),
            allowed_mentions=discord.AllowedMentions(
                everyone=False, users=False, roles=bool(role)
            ),
        )

    async def seed_current(self, guild: discord.Guild) -> int:
        items = await (await self.get_client()).fetch()
        ids = [item_id(item) for item in items if item_id(item)]
        await self.config.guild(guild).posted_ids.set(ids[-MAX_POSTED_IDS:])
        await self.config.guild(guild).last_poll_at.set(utc_now().isoformat())
        await self.config.guild(guild).last_success_at.set(utc_now().isoformat())
        return len(ids)

    async def poll_guild(self, guild: discord.Guild, *, force: bool = False) -> int:
        lock = self._poll_locks.setdefault(guild.id, asyncio.Lock())
        async with lock:
            return await self._poll_guild_unlocked(guild, force=force)

    async def _poll_guild_unlocked(self, guild: discord.Guild, *, force: bool = False) -> int:
        group = self.config.guild(guild)
        settings = await group.all()
        if not settings["enabled"] and not force:
            return 0
        if not settings.get("channel_id"):
            return 0
        interval = max(MIN_INTERVAL_MINUTES, int(settings.get("interval_minutes", 10)))
        if not force and not due(settings.get("last_poll_at"), interval):
            return 0

        channel = await self.get_channel(guild, settings["channel_id"])
        if channel is None:
            log.warning("ARK announcement channel %s is unavailable in guild %s", settings["channel_id"], guild.id)
            await group.last_poll_at.set(utc_now().isoformat())
            return 0

        items = await (await self.get_client()).fetch()
        posted_ids = [str(value) for value in settings.get("posted_ids", [])]
        if not posted_ids:
            await group.posted_ids.set([item_id(item) for item in items][-MAX_POSTED_IDS:])
            await group.last_poll_at.set(utc_now().isoformat())
            await group.last_success_at.set(utc_now().isoformat())
            return 0

        enabled_categories = set(settings.get("categories") or DEFAULT_CATEGORIES)
        sent = 0
        for item in new_items(items, posted_ids):
            identifier = item_id(item)
            category = classify_news(item)
            try:
                if "all" in enabled_categories or category in enabled_categories:
                    await self.send_item(
                        channel,
                        item,
                        settings.get("role_id"),
                        settings.get("delivery_mode", "card"),
                    )
                    sent += 1
            except (discord.Forbidden, discord.NotFound):
                log.warning("Could not publish ARK announcement in channel %s", channel.id)
                break
            except discord.HTTPException:
                log.exception("Could not publish ARK announcement %s", identifier)
                break
            else:
                posted_ids.append(identifier)

        await group.posted_ids.set(posted_ids[-MAX_POSTED_IDS:])
        await group.last_poll_at.set(utc_now().isoformat())
        await group.last_success_at.set(utc_now().isoformat())
        return sent

    @tasks.loop(minutes=1)
    async def poll_loop(self):
        for guild in list(self.bot.guilds):
            if await self.bot.cog_disabled_in_guild(self, guild):
                continue
            try:
                await self.poll_guild(guild)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("ARK announcement poll failed for guild %s", guild.id)

    @poll_loop.before_loop
    async def before_poll_loop(self):
        await self.bot.wait_until_red_ready()

    @commands.group(name="ark", invoke_without_command=True)
    @commands.guild_only()
    async def ark(self, ctx: commands.Context):
        """Browse official ARK: Survival Ascended Steam announcements.

        Quick setup for server administrators and owners:
        `[p]arkset channel #updates`
        `[p]arkset mode card` (or `article`)
        `[p]arkset autopost start`
        """
        await ctx.invoke(self.ark_latest)

    @ark.command(name="updates")
    async def ark_updates(self, ctx: commands.Context, count: int = 5):
        """Show 2 to 10 recent announcements as a newest-to-oldest clickable list."""
        if not 2 <= count <= 10:
            await ctx.send("Choose between 2 and 10 recent announcements.")
            return
        try:
            items = list(
                reversed(recent_items(await (await self.get_client()).fetch(count=50), count))
            )
        except SteamNewsError as error:
            await ctx.send(str(error))
            return
        lines = []
        for item in items:
            title = discord.utils.escape_markdown(
                str(item.get("title") or "ARK announcement")[:180]
            )
            url = str(item.get("url") or ANNOUNCEMENTS_URL)
            timestamp = int(item.get("date") or 0)
            label = CATEGORY_LABELS.get(classify_news(item), CATEGORY_LABELS["official"])
            lines.append(f"• <t:{timestamp}:D> — [{title}]({url}) — {label}")
        embed = discord.Embed(
            title="Recent ARK announcements",
            description="\n".join(lines) or "No official ARK announcements were found.",
            color=discord.Color.from_rgb(45, 117, 92),
        )
        await ctx.send(embed=embed)

    @ark.command(name="latest")
    async def ark_latest(self, ctx: commands.Context, category: Optional[str] = None):
        """Show the latest official Steam announcement, optionally by category."""
        requested = category.casefold().strip() if category else None
        if requested and requested not in CATEGORIES and requested != "official":
            await ctx.send(f"Choose one of: {humanize_list(list(CATEGORIES))}.")
            return
        try:
            items = await (await self.get_client()).fetch(count=50)
        except SteamNewsError as error:
            await ctx.send(str(error))
            return
        if requested:
            items = [item for item in items if classify_news(item) == requested]
        if not items:
            await ctx.send("No matching official ARK announcement was found.")
            return
        latest = max(items, key=lambda item: int(item.get("date") or 0))
        mode = await self.config.guild(ctx.guild).delivery_mode()
        await self.send_item(ctx.channel, latest, None, mode)

    @commands.group(name="arkset", invoke_without_command=True)
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def arkset(self, ctx: commands.Context):
        """Configure official ARK: Survival Ascended announcements."""
        await ctx.invoke(self.arkset_status)

    @arkset.command(name="status")
    async def arkset_status(self, ctx: commands.Context):
        settings = await self.config.guild(ctx.guild).all()
        channel = ctx.guild.get_channel_or_thread(settings["channel_id"]) if settings["channel_id"] else None
        role = ctx.guild.get_role(settings["role_id"]) if settings["role_id"] else None
        categories = settings.get("categories") or DEFAULT_CATEGORIES
        embed = discord.Embed(title="ARK: Survival Ascended announcements", color=discord.Color.green())
        embed.add_field(name="Automatic posting", value="Running" if settings["enabled"] else "Stopped")
        embed.add_field(name="Channel", value=channel.mention if channel else "Not configured")
        embed.add_field(name="Notification role", value=role.mention if role else "None")
        embed.add_field(name="Delivery style", value=settings.get("delivery_mode", "card").title())
        embed.add_field(name="Categories", value=humanize_list(category_display_names(categories)), inline=False)
        embed.add_field(name="Polling interval", value=f"{settings['interval_minutes']} minutes")
        embed.add_field(name="Last successful check", value=settings.get("last_success_at") or "Never", inline=False)
        await ctx.send(embed=embed)

    @arkset.command(name="channel")
    async def arkset_channel(self, ctx: commands.Context, channel: Optional[discord.TextChannel] = None):
        """Set the announcement channel; omit it to disable and clear the channel."""
        group = self.config.guild(ctx.guild)
        if channel is None:
            await group.enabled.set(False)
            await group.channel_id.set(None)
            await ctx.send("Automatic ARK posting is stopped and the channel has been cleared.")
            return
        if not can_user_send_messages_in(ctx.guild.me, channel):
            await ctx.send("I cannot send messages in that channel.")
            return
        try:
            seeded = await self.seed_current(ctx.guild)
        except SteamNewsError as error:
            await ctx.send(f"That channel was not saved because Steam could not be reached: {error}")
            return
        await group.channel_id.set(channel.id)
        await ctx.send(
            f"ARK announcements will use {channel.mention}. Seeded {seeded} current Steam item(s), so old posts will not flood the channel. Use `{ctx.clean_prefix}arkset autopost start` when ready."
        )

    @arkset.group(name="autopost", invoke_without_command=True)
    async def arkset_autopost(self, ctx: commands.Context):
        """Control automatic ARK announcement posting."""
        running = await self.config.guild(ctx.guild).enabled()
        await ctx.send(
            f"Automatic ARK posting is {'running' if running else 'stopped'}. "
            f"Use `{ctx.clean_prefix}arkset autopost {'stop' if running else 'start'}`."
        )

    @arkset_autopost.command(name="start")
    async def arkset_autopost_start(self, ctx: commands.Context, recent_posts: int = 0):
        """Start automatic posting, optionally backfilling 2 to 10 matching posts."""
        group = self.config.guild(ctx.guild)
        if recent_posts and not 2 <= recent_posts <= 10:
            await ctx.send("Choose between 2 and 10 recent posts, or omit the number for no backfill.")
            return
        if await group.enabled():
            await ctx.send(
                "Automatic ARK posting is already running. Stop it before starting with a backfill."
                if recent_posts
                else "Automatic ARK posting is already running."
            )
            return
        channel_id = await group.channel_id()
        if not channel_id:
            await ctx.send(f"Set a channel first with `{ctx.clean_prefix}arkset channel #channel`.")
            return
        if not await group.posted_ids():
            try:
                await self.seed_current(ctx.guild)
            except SteamNewsError as error:
                await ctx.send(f"Automatic ARK posting was not started because Steam could not be reached: {error}")
                return
        published = 0
        if recent_posts:
            channel = await self.get_channel(ctx.guild, channel_id)
            if channel is None:
                await ctx.send("Automatic ARK posting was not started because the configured channel is unavailable.")
                return
            categories = set(await group.categories() or DEFAULT_CATEGORIES)
            try:
                items = await (await self.get_client()).fetch(count=100)
                matching = [
                    item
                    for item in items
                    if "all" in categories or classify_news(item) in categories
                ]
                selected = recent_items(matching, recent_posts)
                for index, item in enumerate(selected):
                    await self.send_item(
                        channel, item, None, await group.delivery_mode()
                    )
                    published += 1
                    if index < len(selected) - 1:
                        await asyncio.sleep(1)
            except SteamNewsError as error:
                await ctx.send(f"Automatic ARK posting was not started because Steam could not be reached: {error}")
                return
            except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                await ctx.send("Automatic ARK posting was not started because the recent posts could not be published.")
                return
        await group.enabled.set(True)
        message = "Automatic ARK posting is now running."
        if published:
            message += f" Published {published} recent post(s), oldest to newest, without role mentions."
            if published < recent_posts:
                message += f" Only {published} of {recent_posts} requested posts matched the configured categories in Steam's 100 most recent posts."
        await ctx.send(message)

    @arkset_autopost.command(name="stop")
    async def arkset_autopost_stop(self, ctx: commands.Context):
        """Stop automatic ARK announcement posting."""
        await self.config.guild(ctx.guild).enabled.set(False)
        await ctx.send("Automatic ARK posting is now stopped.")

    @arkset.command(name="role")
    async def arkset_role(self, ctx: commands.Context, role: Optional[discord.Role] = None):
        """Set the optional role mentioned for matching announcements; omit to clear."""
        await self.config.guild(ctx.guild).role_id.set(role.id if role else None)
        await ctx.send(f"ARK announcements will mention {role.mention}." if role else "The ARK notification role was cleared.")

    @arkset.command(name="mode")
    async def arkset_mode(self, ctx: commands.Context, mode: str = ""):
        """Choose card embeds or article-style posts with trailer and image gallery."""
        mode = mode.casefold().strip()
        group = self.config.guild(ctx.guild)
        if not mode:
            current = await group.delivery_mode()
            await ctx.send(f"ARK announcements currently use `{current}` mode.")
            return
        if mode not in DELIVERY_MODES:
            await ctx.send("Choose `card` or `article`.")
            return
        await group.delivery_mode.set(mode)
        await ctx.send(f"ARK announcements will use `{mode}` mode.")

    @arkset.command(name="categories")
    async def arkset_categories(self, ctx: commands.Context, *, categories: str = ""):
        """Set content filters; hotfixes means posts explicitly named as hotfixes, not Steam Regular Updates."""
        values = {value.casefold() for value in categories.replace(",", " ").split()}
        if not values:
            current = await self.config.guild(ctx.guild).categories()
            await ctx.send(f"Enabled categories: {humanize_list(category_display_names(current or DEFAULT_CATEGORIES))}")
            return
        invalid = values - set(CATEGORIES) - {"all"}
        if invalid or ("all" in values and len(values) > 1):
            await ctx.send(f"Choose `all` or any of: {humanize_list(list(CATEGORIES))}.")
            return
        ordered = ["all"] if "all" in values else [name for name in CATEGORIES if name in values]
        await self.config.guild(ctx.guild).categories.set(ordered)
        await ctx.send(f"ARK announcement categories set to {humanize_list(category_display_names(ordered))}.")

    @arkset.command(name="interval")
    async def arkset_interval(self, ctx: commands.Context, minutes: int):
        """Set how often Steam is checked, from 5 to 1440 minutes."""
        if not MIN_INTERVAL_MINUTES <= minutes <= MAX_INTERVAL_MINUTES:
            await ctx.send(f"Choose an interval from {MIN_INTERVAL_MINUTES} to {MAX_INTERVAL_MINUTES} minutes.")
            return
        await self.config.guild(ctx.guild).interval_minutes.set(minutes)
        await ctx.send(f"Steam will be checked every {minutes} minutes.")

    @arkset.command(name="preview")
    async def arkset_preview(self, ctx: commands.Context, category: Optional[str] = None):
        """Preview the latest official announcement without changing delivery state."""
        await ctx.invoke(self.ark_latest, category=category)

    @arkset.command(name="check")
    async def arkset_check(self, ctx: commands.Context):
        """Check Steam now and publish any new matching announcements."""
        try:
            sent = await self.poll_guild(ctx.guild, force=True)
        except SteamNewsError as error:
            await ctx.send(str(error))
            return
        await ctx.send(f"ARK Steam check complete; published {sent} new announcement(s).")
