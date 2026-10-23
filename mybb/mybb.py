"""Per-guild MyBB bridge with verified External Sign-In membership roles."""

import asyncio
import logging
import time
from collections import defaultdict
from typing import Optional

import discord
from discord.ext import tasks
from redbot.core import Config, commands

from .client import MyBBAPIError, MyBBClient
from .membership import ExternalSignInClient, MembershipError
from .roles import RoleSyncError, entitlement_name, reconcile_roles, validate_role


GUILD_DEFAULTS = {
    "enabled": False,
    "board_url": None,
    "default_forum_id": None,
    "forum_aliases": {},
    "manager_role_id": None,
    "membership_roles": {},
    "membership_url": None,
    "membership_enabled": False,
    "membership_failures": 0,
    "membership_next_check": 0,
    "membership_cursor": 0,
    "publisher_role_ids": [],
    "publishing_forum_ids": [],
    "announcement_feeds": {},
    "announcement_next_check": 0,
    "announcement_failures": 0,
}
MEMBER_DEFAULTS = {
    "connected": False,
    "membership_link": {},
    "membership_owned_role_ids": [],
    "membership_last_success": 0,
    "membership_last_error": None,
}
log = logging.getLogger("red.sick-cogs.MyBB")


class MyBB(commands.Cog):
    """Link MyBB membership with optional owner-controlled forum tools."""

    __author__ = ["SickProdigy"]
    __version__ = "0.3.0"
    CONFIG_IDENTIFIER = 620260922060001

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=self.CONFIG_IDENTIFIER, force_registration=True)
        self.config.register_guild(**GUILD_DEFAULTS)
        self.config.register_member(**MEMBER_DEFAULTS)
        self._membership_locks = defaultdict(asyncio.Lock)
        self._membership_guild_locks = defaultdict(asyncio.Lock)

    async def cog_load(self):
        self.membership_loop.start()
        self.announcement_loop.start()

    def cog_unload(self):
        self.membership_loop.cancel()
        self.announcement_loop.cancel()

    @staticmethod
    def guild_namespace(guild_id):
        return f"mybb_guild_{int(guild_id)}"

    @staticmethod
    def member_namespace(guild_id, user_id):
        return f"mybb_user_{int(guild_id)}_{int(user_id)}"

    @staticmethod
    def membership_guild_namespace(guild_id):
        return f"mybb_membership_guild_{int(guild_id)}"

    @staticmethod
    def membership_member_namespace(guild_id, user_id):
        return f"mybb_membership_link_{int(guild_id)}_{int(user_id)}"

    async def red_delete_data_for_user(self, *, user_id: int, **kwargs):
        """Remove personal API access and verified membership-link data."""
        for guild_id in await self.config.all_guilds():
            guild = self.bot.get_guild(guild_id)
            if guild is not None:
                await self.clear_membership(guild, user_id, notify_provider=True)
            await self.config.member_from_ids(guild_id, user_id).clear()
            await self.bot.remove_shared_api_tokens(
                self.member_namespace(guild_id, user_id), "token"
            )
            await self.bot.remove_shared_api_tokens(
                self.membership_member_namespace(guild_id, user_id), "link_token"
            )

    async def is_publisher(self, member):
        role_ids = set(await self.config.guild(member.guild).publisher_role_ids())
        return member.id == member.guild.owner_id or any(role.id in role_ids for role in member.roles)

    async def has_personal_connection(self, guild_id, user_id):
        tokens = await self.bot.get_shared_api_tokens(self.member_namespace(guild_id, user_id))
        return bool(tokens.get("token"))

    async def clear_guild_member_connections(self, guild):
        members = await self.config.all_members(guild)
        for user_id in members:
            await self.bot.remove_shared_api_tokens(
                self.member_namespace(guild.id, user_id), "token"
            )
            await self.config.member_from_ids(guild.id, user_id).connected.clear()

    async def clear_guild_memberships(self, guild, *, notify_provider=True):
        members = await self.config.all_members(guild)
        for user_id, data in members.items():
            if data.get("membership_link") or data.get("membership_owned_role_ids"):
                await self.clear_membership(guild, user_id, notify_provider=notify_provider)

    async def membership_client(self, guild):
        board_url = await self.config.guild(guild).membership_url()
        tokens = await self.bot.get_shared_api_tokens(self.membership_guild_namespace(guild.id))
        if not board_url or not tokens.get("client_id") or not tokens.get("client_secret"):
            raise MembershipError("Membership linking is not configured for this server.", code="not_configured")
        return ExternalSignInClient(board_url, tokens["client_id"], tokens["client_secret"])

    async def membership_token(self, guild_id, user_id):
        tokens = await self.bot.get_shared_api_tokens(self.membership_member_namespace(guild_id, user_id))
        return tokens.get("link_token")

    async def reconcile_membership(self, member):
        """Fetch one authoritative snapshot and apply only owned mapped roles."""
        async with self._membership_guild_locks[member.guild.id], self._membership_locks[(member.guild.id, member.id)]:
            group = self.config.member(member)
            link = await group.membership_link()
            token = await self.membership_token(member.guild.id, member.id)
            if not link or not token:
                raise MembershipError("Your MyBB membership is not linked.", code="not_linked")
            client = await self.membership_client(member.guild)
            try:
                result = await client.membership(token, member.name)
                expected = {
                    "issuer": client.issuer,
                    "discord_user_id": str(member.id),
                    "context": f"guild:{member.guild.id}",
                    "sub": str(link.get("sub", "")),
                }
                if any(str(result.get(key, "")) != value for key, value in expected.items()):
                    raise MembershipError("External Sign-In returned mismatched link identity.", code="invalid_response")
                entitlements = result["entitlements"] if result["status"] == "active" else set()
                mappings = await self.config.guild(member.guild).membership_roles()
                owned = await group.membership_owned_role_ids()
                await reconcile_roles(
                    member, mappings, entitlements, owned,
                    group.membership_owned_role_ids.set,
                )
                now = int(time.time())
                await group.membership_last_success.set(now)
                await group.membership_last_error.clear()
                link["status"] = result["status"]
                link["checked_at"] = int(result.get("checked_at") or now)
                await group.membership_link.set(link)
                return result
            except (MembershipError, discord.HTTPException, RoleSyncError) as error:
                await group.membership_last_error.set(str(error)[:300])
                raise

    async def finish_membership_link(self, member, client, result):
        expected = {
            "issuer": client.issuer,
            "discord_user_id": str(member.id),
            "context": f"guild:{member.guild.id}",
        }
        if any(str(result.get(key, "")) != value for key, value in expected.items()):
            raise MembershipError("External Sign-In returned mismatched link identity.", code="invalid_response")
        if not all(result.get(key) for key in ("sub", "link_id", "link_token")):
            raise MembershipError("External Sign-In returned incomplete link credentials.", code="invalid_response")
        async with self._membership_locks[(member.guild.id, member.id)]:
            await self.bot.set_shared_api_tokens(
                self.membership_member_namespace(member.guild.id, member.id),
                link_token=str(result["link_token"]),
            )
            await self.config.member(member).membership_link.set({
                "issuer": client.issuer, "sub": str(result["sub"]),
                "link_id": str(result["link_id"]), "status": "linked", "checked_at": 0,
            })
        return await self.reconcile_membership(member)

    async def clear_membership(self, guild, user_id, *, notify_provider):
        """Deliberately unlink, then remove roles owned by this cog."""
        async with self._membership_guild_locks[guild.id], self._membership_locks[(guild.id, user_id)]:
            group = self.config.member_from_ids(guild.id, user_id)
            link = await group.membership_link()
            token = await self.membership_token(guild.id, user_id)
            if not link or not token:
                raise MembershipError("No verified MyBB membership link is active.", code="not_linked")
            if notify_provider and token:
                client = await self.membership_client(guild)
                await client.unlink(token)  # ambiguous failure preserves local access for retry
            member = guild.get_member(user_id)
            owned = await group.membership_owned_role_ids()
            if member is not None:
                await reconcile_roles(member, {}, set(), owned, group.membership_owned_role_ids.set)
            elif owned:
                await group.membership_owned_role_ids.set([])
            await self.bot.remove_shared_api_tokens(
                self.membership_member_namespace(guild.id, user_id), "link_token"
            )
            await group.membership_link.clear()
            await group.membership_last_success.clear()
            await group.membership_last_error.clear()

    @tasks.loop(minutes=1)
    async def membership_loop(self):
        """Run one bounded reconciliation pass only when its due cycle arrives."""
        now = int(time.time())
        for guild_id, data in (await self.config.all_guilds()).items():
            if not data.get("membership_enabled") or now < int(data.get("membership_next_check", 0)):
                continue
            guild = self.bot.get_guild(guild_id)
            if guild is None:
                continue
            failures = 0
            linked = [
                (user_id, member_data)
                for user_id, member_data in (await self.config.all_members(guild)).items()
                if member_data.get("membership_link")
            ]
            cursor = int(data.get("membership_cursor", 0)) % max(len(linked), 1)
            ordered = linked[cursor:] + linked[:cursor]
            checked = 0
            for user_id, member_data in ordered[:100]:
                member = guild.get_member(user_id)
                if member is None:
                    continue
                checked += 1
                try:
                    await self.reconcile_membership(member)
                except (MembershipError, discord.HTTPException, RoleSyncError):
                    failures += 1
            await self.config.guild(guild).membership_cursor.set(
                (cursor + min(len(ordered), 100)) % max(len(linked), 1)
            )
            previous = int(data.get("membership_failures", 0))
            failure_count = min(previous + 1, 5) if failures else 0
            await self.config.guild(guild).membership_failures.set(failure_count)
            delay = min(900 * (2 ** failure_count), 21600)
            await self.config.guild(guild).membership_next_check.set(now + delay)

    @membership_loop.before_loop
    async def before_membership_loop(self):
        await self.bot.wait_until_red_ready()

    @membership_loop.error
    async def membership_loop_error(self, error):
        log.exception("MyBB membership loop failed; Discord roles were not inferred from the error.", exc_info=error)

    @staticmethod
    def _thread_id(thread):
        try:
            return int(thread.get("thread_id") or thread.get("tid") or 0)
        except (TypeError, ValueError):
            return 0

    async def deliver_forum_thread(self, channel, thread, mode):
        url = str(thread.get("thread_url") or thread.get("url") or thread.get("link") or "")
        if not url.startswith("https://"):
            raise MyBBAPIError("MyBB returned a thread without a safe HTTPS link.", code="invalid_response")
        allowed = discord.AllowedMentions.none()
        if mode == "link":
            await channel.send(url, allowed_mentions=allowed)
            return
        description = str(thread.get("message") or thread.get("description") or "Open the forum thread to read more.")
        embed = discord.Embed(
            title=str(thread.get("subject") or thread.get("title") or "New forum thread")[:256],
            url=url, description=description[:500], color=discord.Color.blurple(),
        )
        embed.add_field(name="Author", value=str(thread.get("username") or thread.get("author") or "Unknown")[:256])
        embed.add_field(name="Forum", value=str(thread.get("forum_name") or thread.get("forum_id") or "MyBB")[:256])
        embed.set_footer(text="Read the complete thread on the forum")
        await channel.send(embed=embed, allowed_mentions=allowed)

    async def check_forum_announcements(self, guild):
        group = self.config.guild(guild)
        feeds = await group.announcement_feeds()
        client = await self.client_for(guild)
        failures = 0
        for forum_key, feed in feeds.items():
            if not feed.get("enabled"):
                continue
            channel = guild.get_channel(int(feed.get("channel_id", 0)))
            if channel is None:
                failures += 1
                continue
            try:
                response = await client.threads(int(forum_key), page=1, per_page=20)
                threads = sorted(self.data_list(response, "threads"), key=self._thread_id)
                marker = int(feed.get("last_thread_id", 0))
                unseen = [item for item in threads if self._thread_id(item) > marker]
                if marker == 0:
                    if threads:
                        feed["last_thread_id"] = self._thread_id(threads[-1])
                    continue
                for thread in unseen:
                    await self.deliver_forum_thread(channel, thread, feed.get("mode", "link"))
                    feed["last_thread_id"] = self._thread_id(thread)
            except (MyBBAPIError, discord.HTTPException):
                failures += 1
        await group.announcement_feeds.set(feeds)
        return failures

    @tasks.loop(minutes=1)
    async def announcement_loop(self):
        now = int(time.time())
        for guild_id, data in (await self.config.all_guilds()).items():
            if now < int(data.get("announcement_next_check", 0)) or not any(
                feed.get("enabled") for feed in data.get("announcement_feeds", {}).values()
            ):
                continue
            guild = self.bot.get_guild(guild_id)
            if guild is None:
                continue
            try:
                failures = await self.check_forum_announcements(guild)
            except MyBBAPIError:
                failures = 1
            previous = int(data.get("announcement_failures", 0))
            count = min(previous + 1, 5) if failures else 0
            await self.config.guild(guild).announcement_failures.set(count)
            await self.config.guild(guild).announcement_next_check.set(now + min(900 * (2 ** count), 21600))

    @announcement_loop.before_loop
    async def before_announcement_loop(self):
        await self.bot.wait_until_red_ready()

    @announcement_loop.error
    async def announcement_loop_error(self, error):
        log.exception("MyBB announcement loop failed.", exc_info=error)

    async def client_for(self, guild, member=None, *, authenticated=True, personal_only=False):
        board_url = await self.config.guild(guild).board_url()
        if not board_url:
            raise MyBBAPIError(
                "This Discord server has not connected a MyBB board yet.", code="not_configured"
            )
        token = None
        if authenticated and not personal_only:
            shared = await self.bot.get_shared_api_tokens(self.guild_namespace(guild.id))
            token = shared.get("token")
        if authenticated and not token:
            message = (
                "Connect your MyBB account first."
                if personal_only
                else "This server's MyBB connector is missing."
            )
            raise MyBBAPIError(message, code="not_configured")
        try:
            return MyBBClient(board_url, token if authenticated else None)
        except ValueError as exc:
            raise MyBBAPIError(str(exc), code="invalid_configuration") from exc

    async def request(self, guild, member, method, path, **kwargs):
        client = await self.client_for(
            guild, member, authenticated=path != "/health"
        )
        return await client.request(method, path, **kwargs)

    async def ensure_enabled(self, destination, guild):
        if await self.config.guild(guild).enabled():
            return True
        await destination.send("The MyBB bridge is disabled for this Discord server.")
        return False

    @staticmethod
    def data_list(response, key):
        data = response.get("data", [])
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            value = data.get(key, [])
            return value if isinstance(value, list) else []
        return []

    @staticmethod
    def forum_line(forum):
        return (
            f"**{forum.get('name', 'Unnamed forum')}** — ID `{forum.get('forum_id', '?')}` • "
            f"{forum.get('threads', 0)} threads • {forum.get('posts', 0)} posts"
        )

    @staticmethod
    def thread_line(thread):
        subject = thread.get("subject", "Untitled thread")
        thread_id = thread.get("thread_id", "?")
        author = thread.get("username", "Unknown")
        return (
            f"**{subject}** — ID `{thread_id}` • {author} • "
            f"{thread.get('replies', 0)} replies • {thread.get('views', 0)} views"
        )

    async def send_error(self, destination, error):
        suffix = f" Try again in {error.retry_after} seconds." if error.retry_after else ""
        await destination.send(f"MyBB could not complete that request: {error}{suffix}")

    async def search_recent(self, guild, member, forum_id, query):
        """Search a bounded recent-thread window until the API adds full-text search."""
        lowered = query.casefold()
        matches = []
        client = await self.client_for(guild, member)
        for page in range(1, 4):
            response = await client.threads(forum_id, page=page, per_page=100)
            threads = self.data_list(response, "threads")
            for thread in threads:
                haystack = " ".join(
                    str(thread.get(key) or "") for key in ("subject", "username", "message")
                ).casefold()
                if lowered in haystack:
                    matches.append(thread)
                    if len(matches) >= 20:
                        return matches
            if len(threads) < 100:
                break
        return matches

    @staticmethod
    def normalize_forum_alias(alias):
        alias = str(alias).strip().casefold()
        if not alias or len(alias) > 32 or not all(
            character.isalnum() or character in "-_" for character in alias
        ):
            raise ValueError(
                "Forum aliases must be 1–32 characters using only letters, numbers, hyphens, or underscores."
            )
        if alias == "default" or alias.isdecimal():
            raise ValueError("`default` and numeric names are reserved forum destinations.")
        return alias

    async def resolve_forum_destination(self, guild, destination):
        destination = str(destination).strip().casefold()
        if destination == "default":
            forum_id = await self.config.guild(guild).default_forum_id()
            if not forum_id:
                raise ValueError("This server has no default publishing forum configured.")
            return int(forum_id), "default"
        if destination.isdecimal() and int(destination) > 0:
            return int(destination), None
        alias = self.normalize_forum_alias(destination)
        aliases = await self.config.guild(guild).forum_aliases()
        forum_id = aliases.get(alias)
        if not forum_id:
            raise ValueError(
                f"Unknown forum alias `{alias}`. Use `default`, a forum ID, or a configured alias."
            )
        return int(forum_id), alias

    @commands.group(name="mybb", aliases=["forum"], invoke_without_command=True)
    @commands.guild_only()
    async def mybb(self, ctx):
        """Open this server's MyBB bridge."""
        from .views import MyBBHome

        data = await self.config.guild(ctx.guild).all()
        membership_link = await self.config.member(ctx.author).membership_link()
        embed = discord.Embed(
            title="MyBB Bridge",
            description=(
                "Browse the optional forum connector or link your website account for membership roles."
            ),
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="Website account linking",
            value=(
                f"Provider: {data.get('membership_url') or 'Not configured'}\n"
                f"Your account: {'Linked' if membership_link else 'Not linked'}"
            ),
            inline=False,
        )
        embed.add_field(
            name="Optional forum tools",
            value=(
                f"Board: {data['board_url'] or 'Not connected'}\n"
                f"Server access: {'Enabled' if data['enabled'] else 'Disabled'}"
            ),
            inline=False,
        )
        embed.add_field(
            name="Default publishing forum",
            value=f"ID {data['default_forum_id']}" if data["default_forum_id"] else "Not set",
        )
        embed.set_footer(text=f"Link your account with {ctx.clean_prefix}mybb membership • Membership setup: {ctx.clean_prefix}mybbset membership setup")
        await ctx.send(embed=embed, view=MyBBHome(self, ctx.author))

    @mybb.command(name="connect")
    async def connect(self, ctx):
        """Open verified website-account linking controls."""
        await ctx.invoke(self.mybb_membership)

    @mybb.command(name="disconnect")
    async def disconnect(self, ctx):
        """Open membership controls to intentionally unlink your website account."""
        await ctx.invoke(self.mybb_membership)

    @mybb.group(name="membership", invoke_without_command=True)
    async def mybb_membership(self, ctx):
        """Link MyBB membership and manage synchronized Discord access."""
        from .views import MembershipView

        link = await self.config.member(ctx.author).membership_link()
        enabled = await self.config.guild(ctx.guild).membership_enabled()
        await ctx.send(
            "Open your private membership controls below. Forum API access and verified membership linking are separate connections.",
            view=MembershipView(self, ctx.author, bool(link), enabled),
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @mybb_membership.command(name="status")
    async def mybb_membership_status(self, ctx):
        link = await self.config.member(ctx.author).membership_link()
        last = await self.config.member(ctx.author).membership_last_success()
        error = await self.config.member(ctx.author).membership_last_error()
        message = (
            f"Linked · status `{link.get('status', 'unknown')}`"
            + (f" · last successful check <t:{last}:R>" if last else " · first successful check pending")
            if link else "No verified MyBB membership link is active."
        )
        await ctx.send(
            message + (f" · last error: {error}" if error else ""),
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @mybb_membership.command(name="sync")
    @commands.cooldown(1, 60, commands.BucketType.member)
    async def mybb_membership_sync(self, ctx):
        try:
            result = await self.reconcile_membership(ctx.author)
        except (MembershipError, discord.HTTPException, RoleSyncError) as error:
            await ctx.send(
                f"Membership sync could not complete; existing roles were preserved. {error}",
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return
        await ctx.send(f"Membership synchronized: `{result['status']}`.")

    @mybb.command(name="forums")
    async def forums(self, ctx):
        """List forums visible through your or the server connector."""
        if not await self.is_publisher(ctx.author):
            await ctx.send("Forum tools are limited to the server owner and configured publisher roles.")
            return
        if not await self.ensure_enabled(ctx, ctx.guild):
            return
        try:
            response = await self.request(ctx.guild, ctx.author, "GET", "/forums")
        except MyBBAPIError as error:
            await self.send_error(ctx, error)
            return
        forums = self.data_list(response, "forums")
        if not forums:
            await ctx.send("The active connector exposes no readable forums.")
            return
        await ctx.send(
            embed=discord.Embed(
                title="MyBB forums",
                description="\n".join(self.forum_line(item) for item in forums[:25]),
                color=discord.Color.blurple(),
            )
        )

    @mybb.command(name="threads")
    async def threads(self, ctx, forum_id: Optional[int] = None, page: int = 1):
        """List threads in a readable forum."""
        if not await self.is_publisher(ctx.author):
            await ctx.send("Forum tools are limited to the server owner and configured publisher roles.")
            return
        if not await self.ensure_enabled(ctx, ctx.guild):
            return
        forum_id = forum_id or await self.config.guild(ctx.guild).default_forum_id()
        if not forum_id:
            await ctx.send(f"Provide a forum ID or configure one with `{ctx.clean_prefix}mybbset defaultforum <id>`.")
            return
        try:
            response = await self.request(
                ctx.guild,
                ctx.author,
                "GET",
                f"/forums/{forum_id}/threads",
                params={"page": max(1, page), "per_page": 20},
            )
        except MyBBAPIError as error:
            await self.send_error(ctx, error)
            return
        threads = self.data_list(response, "threads")
        if not threads:
            await ctx.send("No readable threads were returned for that forum and page.")
            return
        await ctx.send(
            embed=discord.Embed(
                title=f"MyBB forum {forum_id} • page {max(1, page)}",
                description="\n".join(self.thread_line(item) for item in threads[:20]),
                color=discord.Color.blurple(),
            )
        )

    @mybb.command(name="thread")
    async def thread(self, ctx, thread_id: int):
        """Show one readable MyBB thread."""
        if not await self.is_publisher(ctx.author):
            await ctx.send("Forum tools are limited to the server owner and configured publisher roles.")
            return
        if not await self.ensure_enabled(ctx, ctx.guild):
            return
        try:
            response = await self.request(ctx.guild, ctx.author, "GET", f"/threads/{thread_id}")
        except MyBBAPIError as error:
            await self.send_error(ctx, error)
            return
        data = response.get("data", {})
        if not isinstance(data, dict):
            data = {}
        first_post = data.get("first_post") if isinstance(data.get("first_post"), dict) else {}
        embed = discord.Embed(
            title=data.get("subject", f"Thread {thread_id}"),
            url=data.get("thread_url"),
            description=(data.get("message") or first_post.get("message") or "No preview returned.")[:4000],
            color=discord.Color.blurple(),
        )
        embed.add_field(name="Author", value=data.get("username", "Unknown"))
        embed.add_field(name="Replies", value=str(data.get("replies", 0)))
        embed.add_field(name="Views", value=str(data.get("views", 0)))
        await ctx.send(embed=embed)

    @mybb.command(name="search")
    async def search(self, ctx, *, query: str):
        """Search up to 300 recent threads in the configured default forum."""
        if not await self.is_publisher(ctx.author):
            await ctx.send("Forum tools are limited to the server owner and configured publisher roles.")
            return
        if not await self.ensure_enabled(ctx, ctx.guild):
            return
        forum_id = await self.config.guild(ctx.guild).default_forum_id()
        if not forum_id:
            await ctx.send("Set a default forum before searching the recent thread index.")
            return
        query = query.strip()
        if len(query) < 2:
            await ctx.send("Enter at least two characters to search for.")
            return
        try:
            matches = await self.search_recent(ctx.guild, ctx.author, forum_id, query)
        except MyBBAPIError as error:
            await self.send_error(ctx, error)
            return
        description = "\n".join(self.thread_line(item) for item in matches) or "No matching recent threads were found."
        embed = discord.Embed(
            title=f"MyBB recent search • {query[:100]}",
            description=description,
            color=discord.Color.blurple(),
        )
        embed.set_footer(text="Search currently checks up to 300 recent threads in the default forum; full-text API search is planned.")
        await ctx.send(embed=embed)

    @mybb.command(name="draft", aliases=["publish"])
    async def draft(self, ctx, destination: str, *, subject: str):
        """Review a replied-to Discord message before publishing it as a MyBB thread."""
        if not await self.ensure_enabled(ctx, ctx.guild):
            return
        if not await self.is_publisher(ctx.author):
            await ctx.send(
                "Only the server owner or a configured forum-publisher role may publish."
            )
            return
        try:
            forum_id, forum_alias = await self.resolve_forum_destination(
                ctx.guild, destination
            )
        except ValueError as error:
            await ctx.send(str(error))
            return
        allowed_forums = set(await self.config.guild(ctx.guild).publishing_forum_ids())
        if forum_id not in allowed_forums:
            await ctx.send("That forum is not in this server's publishing allowlist.")
            return
        reference = ctx.message.reference
        message = reference.resolved if reference and isinstance(reference.resolved, discord.Message) else None
        if message is None and reference and reference.message_id:
            try:
                message = await ctx.channel.fetch_message(reference.message_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                message = None
        if message is None:
            await ctx.send("Reply to the Discord message you want to review and publish.")
            return
        body = message.content.strip()
        if message.attachments:
            body += "\n\nAttachments:\n" + "\n".join(attachment.url for attachment in message.attachments)
        if not body:
            await ctx.send("That message has no text or attachments to publish.")
            return
        from .views import PublishReview

        view = PublishReview(
            self,
            ctx.author,
            forum_id,
            subject.strip(),
            body,
            message.jump_url,
            message.id,
            message.channel.id,
            message.guild.id,
            forum_alias=forum_alias,
        )
        await ctx.send(embed=view.embed(), view=view)

    @commands.group(name="mybbset", invoke_without_command=True)
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def mybbset(self, ctx):
        """Configure this Discord server's MyBB board."""
        await ctx.invoke(self.mybbset_setup)

    @mybbset.command(name="setup")
    @commands.guildowner()
    async def mybbset_setup(self, ctx):
        """Open the interactive board connector."""
        from .views import MyBBSetupView

        await ctx.send(embed=await self.settings_embed(ctx.guild), view=MyBBSetupView(self, ctx.author))

    async def settings_embed(self, guild):
        data = await self.config.guild(guild).all()
        shared = await self.bot.get_shared_api_tokens(self.guild_namespace(guild.id))
        membership_tokens = await self.bot.get_shared_api_tokens(self.membership_guild_namespace(guild.id))
        embed = discord.Embed(
            title="MyBB server setup",
            description="Configure website membership independently from optional API-v1 forum tools.",
            color=discord.Color.blurple(),
        )
        embed.add_field(name="Board", value=data["board_url"] or "Not connected", inline=False)
        embed.add_field(name="Server connector", value="Stored securely" if shared.get("token") else "Not configured")
        embed.add_field(name="Server access", value="Enabled" if data["enabled"] else "Disabled")
        embed.add_field(
            name="Default publishing forum", value=str(data["default_forum_id"] or "Not set")
        )
        aliases = data.get("forum_aliases", {})
        embed.add_field(
            name="Publishing aliases",
            value=(
                ", ".join(
                    f"`{name}` → `{forum_id}`"
                    for name, forum_id in sorted(aliases.items())
                )
                or "Not configured"
            )[:1024],
            inline=False,
        )
        embed.add_field(
            name="Account linking and membership",
            value=(
                f"Provider: {data.get('membership_url') or 'Not configured'}\n"
                f"{len(data.get('membership_roles', {}))} mapping(s) · "
                f"{'Enabled' if data.get('membership_enabled') else 'Disabled'} · "
                f"client {'stored' if membership_tokens.get('client_id') else 'not configured'}"
            ),
            inline=False,
        )
        publisher_roles = [guild.get_role(role_id) for role_id in data.get("publisher_role_ids", [])]
        embed.add_field(
            name="Optional forum tools",
            value=(
                f"API v1: {'Configured' if shared.get('token') else 'Not configured'} · "
                f"{'Enabled' if data.get('enabled') else 'Disabled'}\n"
                f"Publisher roles: {', '.join(role.mention for role in publisher_roles if role) or 'Server owner only'}\n"
                f"Publishing forums: {', '.join(map(str, data.get('publishing_forum_ids', []))) or 'None'} · "
                f"Announcement feeds: {len(data.get('announcement_feeds', {}))}"
            ), inline=False,
        )
        embed.set_footer(text="Membership uses External Sign-In. Announcements and publishing use the optional API-v1 connector. Tokens are never displayed.")
        return embed

    @mybbset.command(name="status")
    async def mybbset_status(self, ctx):
        await ctx.send(embed=await self.settings_embed(ctx.guild))

    @mybbset.group(name="membership", invoke_without_command=True)
    async def mybbset_membership(self, ctx):
        """Show membership linking and role synchronization configuration."""
        mappings = await self.config.guild(ctx.guild).membership_roles()
        enabled = await self.config.guild(ctx.guild).membership_enabled()
        credentials = await self.bot.get_shared_api_tokens(self.membership_guild_namespace(ctx.guild.id))
        lines = [f"`{name}` → role ID `{role_id}`" for name, role_id in sorted(mappings.items())]
        from redbot.core.utils.chat_formatting import pagify
        message = (
            f"MyBB membership sync: **{'enabled' if enabled else 'disabled'}** · "
            f"client: **{'configured' if credentials.get('client_id') and credentials.get('client_secret') else 'not configured'}**.\n"
            "These are named forum permissions, not numeric MyBB group IDs.\n"
            + ("\n".join(lines) or "No role mappings configured.")
        )
        for page in pagify(message):
            await ctx.send(page, allowed_mentions=discord.AllowedMentions.none())

    @mybbset_membership.command(name="setup")
    async def mybbset_membership_setup(self, ctx):
        """Open private setup for the External Sign-In linking client."""
        from .views import MembershipSetupView

        await ctx.send(
            "Configure the dedicated External Sign-In confidential client. Enable its Account linking service and allow a baseline entitlement such as `discord_link` for every eligible forum member.",
            view=MembershipSetupView(self, ctx.author),
        )

    @mybbset_membership.command(name="enabled")
    async def mybbset_membership_enabled(self, ctx, enabled: bool):
        if enabled:
            tokens = await self.bot.get_shared_api_tokens(self.membership_guild_namespace(ctx.guild.id))
            if not await self.config.guild(ctx.guild).membership_url() or not tokens.get("client_id") or not tokens.get("client_secret"):
                await ctx.send("Configure the External Sign-In client first.")
                return
        await self.config.guild(ctx.guild).membership_enabled.set(enabled)
        await self.config.guild(ctx.guild).membership_next_check.set(0)
        await ctx.send(f"Membership synchronization {'enabled' if enabled else 'disabled'}. Existing roles are unchanged.")

    @mybbset_membership.command(name="map")
    @commands.admin_or_permissions(manage_guild=True, manage_roles=True)
    async def mybbset_membership_map(self, ctx, entitlement: str, role: discord.Role):
        """Map a named MyBB entitlement to a Discord role."""
        try:
            entitlement = entitlement_name(entitlement)
            validate_role(ctx.guild, role, ctx.author)
            async with self._membership_guild_locks[ctx.guild.id]:
                async with self.config.guild(ctx.guild).membership_roles() as mappings:
                    if entitlement not in mappings and len(mappings) >= 50:
                        raise RoleSyncError("At most 50 membership mappings are supported per server.")
                    mappings[entitlement] = role.id
        except RoleSyncError as error:
            await ctx.send(str(error))
            return
        await ctx.send(
            f"Mapped `{entitlement}` → role ID `{role.id}`. Linked members update on their next sync.",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @mybbset_membership.command(name="unmap")
    @commands.admin_or_permissions(manage_guild=True, manage_roles=True)
    async def mybbset_membership_unmap(self, ctx, entitlement: str):
        """Remove a mapping; linked members update on their next sync."""
        async with self._membership_guild_locks[ctx.guild.id]:
            async with self.config.guild(ctx.guild).membership_roles() as mappings:
                removed = mappings.pop(entitlement, None)
        await ctx.send("Mapping removed; no Discord roles changed." if removed else "That mapping does not exist.")

    @mybbset.command(name="enabled")
    @commands.guildowner()
    async def mybbset_enabled(self, ctx, enabled: bool):
        await self.config.guild(ctx.guild).enabled.set(enabled)
        await ctx.send(f"MyBB bridge {'enabled' if enabled else 'disabled'} for this server.")

    @mybbset.command(name="defaultforum")
    @commands.guildowner()
    async def mybbset_defaultforum(self, ctx, forum_id: Optional[int] = None):
        await self.config.guild(ctx.guild).default_forum_id.set(forum_id)
        await ctx.send(f"Default MyBB forum set to `{forum_id}`." if forum_id else "Default forum cleared.")

    @mybbset.group(name="forumalias", aliases=["forumaliases"], invoke_without_command=True)
    @commands.guildowner()
    async def mybbset_forumalias(self, ctx):
        """List friendly publishing destinations."""
        aliases = await self.config.guild(ctx.guild).forum_aliases()
        if not aliases:
            await ctx.send("No MyBB forum aliases are configured.")
            return
        await ctx.send(
            "MyBB forum aliases:\n"
            + "\n".join(
                f"`{name}` → forum `{forum_id}`"
                for name, forum_id in sorted(aliases.items())
            )
        )

    @mybbset_forumalias.command(name="set", aliases=["add"])
    async def mybbset_forumalias_set(self, ctx, alias: str, forum_id: int):
        """Map a friendly publishing name to a forum ID."""
        try:
            alias = self.normalize_forum_alias(alias)
        except ValueError as error:
            await ctx.send(str(error))
            return
        if forum_id <= 0:
            await ctx.send("Enter a positive numeric forum ID.")
            return
        try:
            client = await self.client_for(ctx.guild, ctx.author)
            await client.threads(forum_id, page=1, per_page=1)
        except MyBBAPIError as error:
            await self.send_error(ctx, error)
            return
        async with self.config.guild(ctx.guild).forum_aliases() as aliases:
            aliases[alias] = forum_id
        await ctx.send(f"MyBB forum alias `{alias}` now targets forum `{forum_id}`.")

    @mybbset_forumalias.command(name="remove", aliases=["delete"])
    async def mybbset_forumalias_remove(self, ctx, alias: str):
        """Remove a friendly publishing destination."""
        try:
            alias = self.normalize_forum_alias(alias)
        except ValueError as error:
            await ctx.send(str(error))
            return
        async with self.config.guild(ctx.guild).forum_aliases() as aliases:
            removed = aliases.pop(alias, None)
        await ctx.send(
            f"Removed MyBB forum alias `{alias}`."
            if removed is not None
            else f"MyBB forum alias `{alias}` was not configured."
        )

    @mybbset.command(name="managerrole")
    @commands.guildowner()
    async def mybbset_managerrole(self, ctx, role: Optional[discord.Role] = None):
        """Deprecated compatibility command; use publisherrole add/remove."""
        await ctx.send(f"Use `{ctx.clean_prefix}mybbset publisherrole add @role` instead.")

    @mybbset.group(name="publisherrole", invoke_without_command=True)
    @commands.guildowner()
    async def mybbset_publisherrole(self, ctx):
        roles = [ctx.guild.get_role(role_id) for role_id in await self.config.guild(ctx.guild).publisher_role_ids()]
        await ctx.send("Publisher roles: " + (", ".join(role.mention for role in roles if role) or "None"))

    @mybbset_publisherrole.command(name="add")
    async def mybbset_publisherrole_add(self, ctx, role: discord.Role):
        async with self.config.guild(ctx.guild).publisher_role_ids() as role_ids:
            if role.id not in role_ids:
                role_ids.append(role.id)
        await ctx.send(f"{role.mention} may use reviewed forum publishing.")

    @mybbset_publisherrole.command(name="remove")
    async def mybbset_publisherrole_remove(self, ctx, role: discord.Role):
        async with self.config.guild(ctx.guild).publisher_role_ids() as role_ids:
            if role.id in role_ids:
                role_ids.remove(role.id)
        await ctx.send(f"{role.mention} no longer has forum publishing access.")

    @mybbset.group(name="publishingforum", invoke_without_command=True)
    @commands.guildowner()
    async def mybbset_publishingforum(self, ctx):
        ids = await self.config.guild(ctx.guild).publishing_forum_ids()
        await ctx.send("Allowed publishing forum IDs: " + (", ".join(map(str, ids)) or "None"))

    @mybbset_publishingforum.command(name="add")
    async def mybbset_publishingforum_add(self, ctx, forum_id: int):
        client = await self.client_for(ctx.guild)
        try:
            await client.threads(forum_id, page=1, per_page=1)
        except MyBBAPIError as error:
            await self.send_error(ctx, error)
            return
        async with self.config.guild(ctx.guild).publishing_forum_ids() as ids:
            if forum_id not in ids:
                ids.append(forum_id)
        await ctx.send(f"Forum `{forum_id}` is allowed for reviewed publishing.")

    @mybbset_publishingforum.command(name="remove")
    async def mybbset_publishingforum_remove(self, ctx, forum_id: int):
        async with self.config.guild(ctx.guild).publishing_forum_ids() as ids:
            if forum_id in ids:
                ids.remove(forum_id)
        await ctx.send(f"Forum `{forum_id}` removed from the publishing allowlist.")

    @mybbset.group(name="announcements", aliases=["announcement"], invoke_without_command=True)
    @commands.guildowner()
    async def mybbset_announcements(self, ctx):
        """List MyBB forum announcement routes."""
        feeds = await self.config.guild(ctx.guild).announcement_feeds()
        if not feeds:
            await ctx.send("No MyBB forum announcement routes are configured.")
            return
        lines = []
        for forum_id, feed in sorted(feeds.items(), key=lambda item: int(item[0])):
            lines.append(
                f"Forum `{forum_id}` → <#{feed.get('channel_id')}> · {feed.get('mode', 'link')} · "
                f"{'running' if feed.get('enabled') else 'stopped'}"
            )
        await ctx.send("\n".join(lines), allowed_mentions=discord.AllowedMentions.none())

    @mybbset_announcements.command(name="add")
    async def mybbset_announcements_add(
        self, ctx, forum_id: int, channel: discord.TextChannel, mode: str = "link"
    ):
        """Add a forum route in link or card mode; it starts stopped."""
        mode = mode.casefold()
        if mode not in {"link", "card"}:
            await ctx.send("Mode must be `link` or `card`.")
            return
        try:
            client = await self.client_for(ctx.guild)
            await client.threads(forum_id, page=1, per_page=1)
        except MyBBAPIError as error:
            await self.send_error(ctx, error)
            return
        permissions = channel.permissions_for(ctx.guild.me)
        if not permissions.send_messages or (mode == "card" and not permissions.embed_links):
            await ctx.send("I need Send Messages, plus Embed Links for card mode, in that channel.")
            return
        async with self.config.guild(ctx.guild).announcement_feeds() as feeds:
            previous = feeds.get(str(forum_id), {})
            feeds[str(forum_id)] = {
                "channel_id": channel.id, "mode": mode, "enabled": False,
                "last_thread_id": int(previous.get("last_thread_id", 0)),
            }
        await ctx.send(f"Forum `{forum_id}` will announce in {channel.mention} using `{mode}` mode. Run the start command when ready.")

    @mybbset_announcements.command(name="remove")
    async def mybbset_announcements_remove(self, ctx, forum_id: int):
        async with self.config.guild(ctx.guild).announcement_feeds() as feeds:
            removed = feeds.pop(str(forum_id), None)
        await ctx.send("Announcement route removed." if removed else "That forum route was not configured.")

    @mybbset_announcements.command(name="mode")
    async def mybbset_announcements_mode(self, ctx, forum_id: int, mode: str):
        mode = mode.casefold()
        if mode not in {"link", "card"}:
            await ctx.send("Mode must be `link` or `card`.")
            return
        async with self.config.guild(ctx.guild).announcement_feeds() as feeds:
            feed = feeds.get(str(forum_id))
            if not feed:
                await ctx.send("Add that forum route first.")
                return
            feed["mode"] = mode
        await ctx.send(f"Forum `{forum_id}` now uses `{mode}` announcements.")

    @mybbset_announcements.command(name="start")
    async def mybbset_announcements_start(self, ctx, forum_id: int, recent: int = 0):
        """Start a route, optionally posting up to five recent threads."""
        if recent < 0 or recent > 5:
            await ctx.send("Recent backfill must be between 0 and 5.")
            return
        feeds = await self.config.guild(ctx.guild).announcement_feeds()
        feed = feeds.get(str(forum_id))
        if not feed:
            await ctx.send("Add that forum route first.")
            return
        try:
            client = await self.client_for(ctx.guild)
            threads = sorted(
                self.data_list(await client.threads(forum_id, page=1, per_page=max(recent, 1)), "threads"),
                key=self._thread_id,
            )
            channel = ctx.guild.get_channel(int(feed["channel_id"]))
            if channel is None:
                raise MyBBAPIError("The configured Discord channel no longer exists.", code="missing_channel")
            for thread in (threads[-recent:] if recent else []):
                await self.deliver_forum_thread(channel, thread, feed.get("mode", "link"))
            if threads:
                feed["last_thread_id"] = self._thread_id(threads[-1])
            feed["enabled"] = True
            feeds[str(forum_id)] = feed
            await self.config.guild(ctx.guild).announcement_feeds.set(feeds)
            await self.config.guild(ctx.guild).announcement_next_check.set(int(time.time()) + 900)
        except (MyBBAPIError, discord.HTTPException) as error:
            await ctx.send(f"The route was not started: {error}")
            return
        await ctx.send(
            f"Forum `{forum_id}` announcements are running. "
            + (f"Posted {len(threads[-recent:])} recent thread(s)." if recent else "Existing threads were seeded without flooding the channel.")
        )

    @mybbset_announcements.command(name="stop")
    async def mybbset_announcements_stop(self, ctx, forum_id: int):
        async with self.config.guild(ctx.guild).announcement_feeds() as feeds:
            feed = feeds.get(str(forum_id))
            if not feed:
                await ctx.send("That forum route was not configured.")
                return
            feed["enabled"] = False
        await ctx.send(f"Forum `{forum_id}` announcements stopped; its delivery marker was preserved.")

    @mybbset.command(name="disconnect")
    @commands.guildowner()
    async def mybbset_disconnect(self, ctx, confirmation: str = ""):
        """Disconnect this Discord server from MyBB."""
        if confirmation.casefold() != "confirm":
            await ctx.send(f"Run `{ctx.clean_prefix}mybbset disconnect confirm` to remove the server connector. Personal tokens are removed separately by their owners.")
            return
        await self.bot.remove_shared_api_tokens(self.guild_namespace(ctx.guild.id), "token")
        await self.clear_guild_member_connections(ctx.guild)
        await self.config.guild(ctx.guild).board_url.clear()
        await self.config.guild(ctx.guild).default_forum_id.clear()
        await self.config.guild(ctx.guild).forum_aliases.clear()
        await self.config.guild(ctx.guild).publisher_role_ids.clear()
        await self.config.guild(ctx.guild).publishing_forum_ids.clear()
        await self.config.guild(ctx.guild).announcement_feeds.clear()
        await self.config.guild(ctx.guild).enabled.set(False)
        await ctx.send("The optional API-v1 connector, publishing access, and forum announcements were removed. Membership linking is unchanged.")
