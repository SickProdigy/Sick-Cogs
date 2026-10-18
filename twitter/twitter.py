import asyncio
import datetime
import logging
from typing import Optional

import aiohttp
import discord
from discord.ext import tasks
from redbot.core import Config, commands
from redbot.core.bot import Red
from redbot.core.utils import can_user_send_messages_in

from .client import USER_AGENT, XAPIError, XClient, new_posts, normalize_username


log = logging.getLogger("red.sick-cogs.Twitter")
CONFIG_IDENTIFIER = 6202609230144001
TOKEN_NAMESPACE = "twitter"
MIN_INTERVAL_MINUTES = 5
MAX_INTERVAL_MINUTES = 1440
MAX_CONCURRENT_TIMELINE_REQUESTS = 4


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc)


def due(last_poll_at, interval_minutes):
    if not last_poll_at:
        return True
    try:
        parsed = datetime.datetime.fromisoformat(last_poll_at)
    except (TypeError, ValueError):
        return True
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return utc_now() >= parsed + datetime.timedelta(minutes=interval_minutes)


class Twitter(commands.Cog):
    """Publish new X posts from selected accounts into Discord channels."""

    __author__ = ["SickProdigy"]
    __version__ = "0.1.2"

    default_guild = {
        "enabled": False,
        "interval_minutes": 5,
        "last_poll_at": None,
        "manager_role_id": None,
        "feeds": {},
    }

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=CONFIG_IDENTIFIER, force_registration=True)
        self.config.register_guild(**self.default_guild)
        self.session: Optional[aiohttp.ClientSession] = None
        self._poll_locks = {}
        self.poll_loop.start()

    async def red_delete_data_for_user(self, **kwargs):
        """This cog stores no member data."""
        return

    def cog_unload(self):
        self.poll_loop.cancel()
        if self.session and not self.session.closed:
            self.bot.loop.create_task(self.session.close())

    async def get_session(self):
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30), headers={"User-Agent": USER_AGENT}
            )
        return self.session

    async def get_client(self):
        tokens = await self.bot.get_shared_api_tokens(TOKEN_NAMESPACE)
        token = str(tokens.get("bearer_token") or "").strip()
        if not token:
            raise XAPIError(
                "The bot owner must configure `[p]set api twitter bearer_token,YOUR_TOKEN`."
            )
        return XClient(await self.get_session(), token)

    async def can_manage(self, member):
        if member.guild_permissions.manage_guild:
            return True
        role_id = await self.config.guild(member.guild).manager_role_id()
        return role_id is not None and any(role.id == role_id for role in member.roles)

    async def require_manager(self, ctx):
        if await self.can_manage(ctx.author):
            return True
        await ctx.send("Manage Server or the configured Twitter manager role is required.")
        return False

    async def get_channel(self, guild, channel_id):
        """Return (channel, permanently_missing) without deleting on transient failures."""
        channel = guild.get_channel_or_thread(int(channel_id))
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(int(channel_id))
            except discord.NotFound:
                return None, True
            except (discord.Forbidden, discord.HTTPException):
                return None, False
        if getattr(channel, "guild", None) != guild:
            return None, True
        if not can_user_send_messages_in(guild.me, channel):
            return None, False
        return channel, False

    async def send_post(self, channel, username, post):
        url = f"https://x.com/{username}/status/{post['id']}"
        await channel.send(
            f"New post from **@{username}**\n{url}",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def poll_guild(self, guild, *, force=False, timeline_results=None):
        lock = self._poll_locks.setdefault(guild.id, asyncio.Lock())
        async with lock:
            return await self._poll_guild_unlocked(
                guild,
                force=force,
                timeline_results=timeline_results,
            )

    async def _poll_guild_unlocked(self, guild, *, force=False, timeline_results=None):
        group = self.config.guild(guild)
        settings = await group.all()
        if not settings["enabled"] and not force:
            return 0
        interval = max(MIN_INTERVAL_MINUTES, int(settings["interval_minutes"]))
        if not force and not due(settings.get("last_poll_at"), interval):
            return 0
        feeds = settings.get("feeds", {})
        if not feeds:
            await group.last_poll_at.set(utc_now().isoformat())
            return 0
        client = None
        local_results = {}
        local_cursors = {}
        if timeline_results is None:
            for feed in feeds.values():
                user_id = str(feed.get("user_id") or "")
                cursors = [
                    str(value)
                    for value in feed.get("channels", {}).values()
                    if str(value).isdigit()
                ]
                if not user_id.isdigit() or not cursors:
                    continue
                oldest = min(cursors, key=int)
                current = local_cursors.get(user_id)
                if current is None or int(oldest) < int(current):
                    local_cursors[user_id] = oldest
        sent = 0
        changed = False
        for key, feed in list(feeds.items()):
            channels = feed.get("channels", {})
            if not channels:
                feeds.pop(key, None)
                changed = True
                continue
            cursors = [str(value) for value in channels.values() if str(value).isdigit()]
            if not cursors:
                continue
            user_id = str(feed.get("user_id") or "")
            if timeline_results is not None:
                posts = timeline_results.get(user_id)
                if posts is None:
                    continue
            else:
                try:
                    if user_id not in local_results:
                        client = client or await self.get_client()
                        local_results[user_id] = tuple(
                            await client.user_posts(user_id, local_cursors[user_id])
                        )
                    posts = local_results[user_id]
                except XAPIError as error:
                    log.warning("X poll failed for @%s in guild %s: %s", key, guild.id, error)
                    continue
            for channel_id, cursor in list(channels.items()):
                channel, permanently_missing = await self.get_channel(guild, int(channel_id))
                if channel is None:
                    if permanently_missing:
                        channels.pop(channel_id, None)
                        changed = True
                        log.warning("Removed deleted Twitter channel %s in guild %s", channel_id, guild.id)
                    else:
                        log.warning(
                            "Twitter channel %s in guild %s is temporarily inaccessible; subscription retained",
                            channel_id,
                            guild.id,
                        )
                    continue
                pending = new_posts(posts, cursor)
                for post in pending:
                    try:
                        await self.send_post(channel, feed["username"], post)
                    except (discord.Forbidden, discord.NotFound):
                        channels.pop(channel_id, None)
                        changed = True
                        break
                    except discord.HTTPException:
                        log.exception("Could not publish X post %s to channel %s", post["id"], channel_id)
                        break
                    else:
                        channels[channel_id] = str(post["id"])
                        changed = True
                        sent += 1
            if not channels:
                feeds.pop(key, None)
        if changed:
            await group.feeds.set(feeds)
        await group.last_poll_at.set(utc_now().isoformat())
        return sent

    async def poll_once(self):
        due_guilds = []
        account_cursors = {}
        for guild in list(self.bot.guilds):
            if await self.bot.cog_disabled_in_guild(self, guild):
                continue
            try:
                settings = await self.config.guild(guild).all()
                interval = max(
                    MIN_INTERVAL_MINUTES,
                    int(settings.get("interval_minutes", MIN_INTERVAL_MINUTES)),
                )
                if not settings.get("enabled") or not due(
                    settings.get("last_poll_at"), interval
                ):
                    continue
                due_guilds.append(guild)
                for feed in settings.get("feeds", {}).values():
                    user_id = str(feed.get("user_id") or "")
                    cursors = [
                        str(value)
                        for value in feed.get("channels", {}).values()
                        if str(value).isdigit()
                    ]
                    if not user_id.isdigit() or not cursors:
                        continue
                    oldest = min(cursors, key=int)
                    current = account_cursors.get(user_id)
                    if current is None or int(oldest) < int(current):
                        account_cursors[user_id] = oldest
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Could not prepare Twitter poll for guild %s", guild.id)

        timeline_results = {}
        if account_cursors:
            try:
                client = await self.get_client()
            except asyncio.CancelledError:
                raise
            except XAPIError as error:
                log.warning("Could not prepare X timeline polling: %s", error)
            else:
                semaphore = asyncio.Semaphore(MAX_CONCURRENT_TIMELINE_REQUESTS)

                async def fetch_timeline(user_id, cursor):
                    try:
                        async with semaphore:
                            posts = tuple(await client.user_posts(user_id, cursor))
                    except asyncio.CancelledError:
                        raise
                    except XAPIError as error:
                        log.warning("X timeline poll failed for user %s: %s", user_id, error)
                        posts = None
                    except Exception:
                        log.exception("Unexpected X timeline failure for user %s", user_id)
                        posts = None
                    return user_id, posts

                timeline_results = dict(
                    await asyncio.gather(
                        *(
                            fetch_timeline(user_id, cursor)
                            for user_id, cursor in account_cursors.items()
                        )
                    )
                )

        for guild in due_guilds:
            try:
                await self.poll_guild(guild, timeline_results=timeline_results)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Twitter delivery failed for guild %s", guild.id)

    @tasks.loop(minutes=1)
    async def poll_loop(self):
        try:
            await self.poll_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Twitter polling cycle failed")

    @poll_loop.before_loop
    async def before_poll_loop(self):
        await self.bot.wait_until_red_ready()

    @commands.group(name="twitterset", aliases=["xset"], invoke_without_command=True)
    @commands.guild_only()
    async def twitterset(self, ctx):
        """Configure X post announcements for this server."""
        await ctx.invoke(self.twitterset_status)

    @twitterset.command(name="status")
    async def twitterset_status(self, ctx):
        settings = await self.config.guild(ctx.guild).all()
        feeds = settings.get("feeds", {})
        subscriptions = sum(len(feed.get("channels", {})) for feed in feeds.values())
        role = ctx.guild.get_role(settings["manager_role_id"]) if settings["manager_role_id"] else None
        try:
            await self.get_client()
            api = "Configured"
        except XAPIError:
            api = "Missing"
        embed = discord.Embed(title="Twitter / X announcements", color=discord.Color.blue())
        embed.add_field(name="Enabled", value="Yes" if settings["enabled"] else "No")
        embed.add_field(name="Native X API", value=api)
        embed.add_field(name="Polling interval", value=f"{settings['interval_minutes']} minutes")
        embed.add_field(name="Subscriptions", value=str(subscriptions))
        embed.add_field(name="Manager role", value=role.mention if role else "Manage Server only")
        embed.add_field(name="Last poll", value=settings.get("last_poll_at") or "Never", inline=False)
        await ctx.send(embed=embed)

    @twitterset.command(name="add")
    async def twitterset_add(self, ctx, channel: discord.TextChannel, username: str):
        """Post future messages from an X account into a channel."""
        if not await self.require_manager(ctx):
            return
        try:
            normalized = normalize_username(username)
            user = await (await self.get_client()).user_by_username(normalized)
        except (ValueError, XAPIError) as error:
            await ctx.send(str(error), allowed_mentions=discord.AllowedMentions.none())
            return
        if not can_user_send_messages_in(ctx.guild.me, channel):
            await ctx.send(
                "I cannot send messages in that channel.",
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return
        cursor = str(user.get("most_recent_tweet_id") or "0")
        lock = self._poll_locks.setdefault(ctx.guild.id, asyncio.Lock())
        async with lock:
            async with self.config.guild(ctx.guild).feeds() as feeds:
                feed = feeds.setdefault(normalized, {
                    "user_id": str(user["id"]),
                    "username": user.get("username") or normalized,
                    "name": user.get("name") or user.get("username") or normalized,
                    "channels": {},
                })
                feed["user_id"] = str(user["id"])
                feed["username"] = user.get("username") or normalized
                feed["name"] = user.get("name") or feed["username"]
                already = str(channel.id) in feed["channels"]
                feed["channels"].setdefault(str(channel.id), cursor)
        await ctx.send(
            f"@{user.get('username') or normalized} is already assigned to {channel.mention}."
            if already
            else f"Future posts from **@{user.get('username') or normalized}** will publish in {channel.mention}. Existing posts were skipped.",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @twitterset.command(name="remove", aliases=["delete"])
    async def twitterset_remove(self, ctx, channel: discord.TextChannel, username: str):
        """Remove an account-to-channel subscription."""
        if not await self.require_manager(ctx):
            return
        try:
            normalized = normalize_username(username)
        except ValueError as error:
            await ctx.send(str(error))
            return
        removed = False
        lock = self._poll_locks.setdefault(ctx.guild.id, asyncio.Lock())
        async with lock:
            async with self.config.guild(ctx.guild).feeds() as feeds:
                feed = feeds.get(normalized)
                if feed:
                    removed = feed.get("channels", {}).pop(str(channel.id), None) is not None
                    if not feed.get("channels"):
                        feeds.pop(normalized, None)
        await ctx.send(
            f"Removed **@{normalized}** from {channel.mention}."
            if removed
            else f"**@{normalized}** was not assigned to {channel.mention}."
        )

    @twitterset.command(name="list")
    async def twitterset_list(self, ctx):
        feeds = await self.config.guild(ctx.guild).feeds()
        lines = []
        for feed in sorted(feeds.values(), key=lambda item: item.get("username", "").casefold()):
            mentions = [f"<#{channel_id}>" for channel_id in feed.get("channels", {})]
            lines.append(f"**@{feed.get('username', 'unknown')}** → {', '.join(mentions)}")
        await ctx.send(
            embed=discord.Embed(
                title="Twitter / X subscriptions",
                description="\n".join(lines) or "No subscriptions are configured.",
                color=discord.Color.blue(),
            )
        )

    @twitterset.command(name="enabled")
    async def twitterset_enabled(self, ctx, enabled: bool):
        if not await self.require_manager(ctx):
            return
        await self.config.guild(ctx.guild).enabled.set(enabled)
        await ctx.send(f"Twitter announcements are now {'enabled' if enabled else 'disabled'}.")

    @twitterset.command(name="interval")
    async def twitterset_interval(self, ctx, minutes: int):
        if not await self.require_manager(ctx):
            return
        if not MIN_INTERVAL_MINUTES <= minutes <= MAX_INTERVAL_MINUTES:
            await ctx.send(f"Choose an interval from {MIN_INTERVAL_MINUTES} to {MAX_INTERVAL_MINUTES} minutes.")
            return
        await self.config.guild(ctx.guild).interval_minutes.set(minutes)
        await ctx.send(f"Twitter accounts will be checked every {minutes} minutes.")

    @twitterset.command(name="managerrole")
    async def twitterset_managerrole(self, ctx, role: Optional[discord.Role] = None):
        if not ctx.author.guild_permissions.manage_guild:
            await ctx.send("Manage Server is required to change the Twitter manager role.")
            return
        await self.config.guild(ctx.guild).manager_role_id.set(role.id if role else None)
        await ctx.send(
            f"{role.mention} can now manage Twitter subscriptions."
            if role else "Twitter subscription management is limited to Manage Server."
        )

    @twitterset.command(name="test")
    async def twitterset_test(self, ctx, username: str):
        """Verify API access and resolve an X username without posting."""
        if not await self.require_manager(ctx):
            return
        try:
            user = await (await self.get_client()).user_by_username(username)
        except (ValueError, XAPIError) as error:
            await ctx.send(str(error), allowed_mentions=discord.AllowedMentions.none())
            return
        await ctx.send(
            f"Native X API connected: **{user.get('name', user['username'])}** (@{user['username']}, ID `{user['id']}`).",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @twitterset.command(name="check")
    async def twitterset_check(self, ctx):
        """Run one poll now."""
        if not await self.require_manager(ctx):
            return
        try:
            sent = await self.poll_guild(ctx.guild, force=True)
        except XAPIError as error:
            await ctx.send(str(error), allowed_mentions=discord.AllowedMentions.none())
            return
        await ctx.send(f"Twitter check complete; published {sent} new post(s).")
