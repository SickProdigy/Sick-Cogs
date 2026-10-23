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
    "membership_enabled": False,
    "membership_failures": 0,
    "membership_next_check": 0,
    "membership_cursor": 0,
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
    """Connect a Discord server to MyBB and let members optionally use personal access."""

    __author__ = ["SickProdigy"]
    __version__ = "0.2.0"
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

    def cog_unload(self):
        self.membership_loop.cancel()

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

    async def is_manager(self, member):
        role_id = await self.config.guild(member.guild).manager_role_id()
        return member.guild_permissions.manage_guild or (
            role_id is not None and any(role.id == role_id for role in member.roles)
        )

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
        board_url = await self.config.guild(guild).board_url()
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
                result = await client.membership(token)
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
            token = await self.membership_token(guild.id, user_id)
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

    async def client_for(self, guild, member=None, *, authenticated=True, personal_only=False):
        board_url = await self.config.guild(guild).board_url()
        if not board_url:
            raise MyBBAPIError(
                "This Discord server has not connected a MyBB board yet.", code="not_configured"
            )
        token = None
        if authenticated and member is not None:
            personal = await self.bot.get_shared_api_tokens(
                self.member_namespace(guild.id, member.id)
            )
            token = personal.get("token")
        if authenticated and not token and not personal_only:
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
        personal = await self.has_personal_connection(ctx.guild.id, ctx.author.id)
        embed = discord.Embed(
            title="MyBB Bridge",
            description=(
                "Browse the connected board, search its recent thread index, or connect your own "
                "MyBB access for actions performed as you."
            ),
            color=discord.Color.blurple(),
        )
        embed.add_field(name="Board", value=data["board_url"] or "Not connected", inline=False)
        embed.add_field(name="Server access", value="Enabled" if data["enabled"] else "Disabled")
        embed.add_field(name="Your connection", value="Connected" if personal else "Using server access")
        embed.add_field(
            name="Default publishing forum",
            value=f"ID {data['default_forum_id']}" if data["default_forum_id"] else "Not set",
        )
        embed.set_footer(text=f"Connect yourself with {ctx.clean_prefix}mybb connect • Server setup: {ctx.clean_prefix}mybbset setup")
        await ctx.send(embed=embed, view=MyBBHome(self, ctx.author))

    @mybb.command(name="connect")
    async def connect(self, ctx):
        """Open the private personal-connection panel."""
        from .views import PersonalConnectionView

        if not await self.config.guild(ctx.guild).board_url():
            await ctx.send("A server administrator must connect this Discord server to a MyBB board first.")
            return
        connected = await self.has_personal_connection(ctx.guild.id, ctx.author.id)
        await ctx.send(
            "Your personal MyBB connection is active." if connected else "Connect your MyBB account to this server's configured board.",
            view=PersonalConnectionView(self, ctx.author, connected),
        )

    @mybb.command(name="disconnect")
    async def disconnect(self, ctx):
        """Remove your personal MyBB connection from this Discord server."""
        await self.bot.remove_shared_api_tokens(
            self.member_namespace(ctx.guild.id, ctx.author.id), "token"
        )
        await self.config.member(ctx.author).connected.set(False)
        await ctx.send("Your personal MyBB connection has been removed.")

    @mybb.group(name="membership", invoke_without_command=True)
    async def mybb_membership(self, ctx):
        """Link MyBB membership and manage synchronized Discord access."""
        from .views import MembershipView

        link = await self.config.member(ctx.author).membership_link()
        await ctx.send(
            "Open your private membership controls below. Forum API access and verified membership linking are separate connections.",
            view=MembershipView(self, ctx.author, bool(link)),
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
        personal = await self.has_personal_connection(ctx.guild.id, ctx.author.id)
        if not personal and not await self.is_manager(ctx.author):
            await ctx.send(
                f"Connect your MyBB account with `{ctx.clean_prefix}mybb connect`, or ask a configured MyBB manager to publish this."
            )
            return
        try:
            forum_id, forum_alias = await self.resolve_forum_destination(
                ctx.guild, destination
            )
        except ValueError as error:
            await ctx.send(str(error))
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
    async def mybbset_setup(self, ctx):
        """Open the interactive board connector."""
        from .views import MyBBSetupView

        await ctx.send(embed=await self.settings_embed(ctx.guild), view=MyBBSetupView(self, ctx.author))

    async def settings_embed(self, guild):
        data = await self.config.guild(guild).all()
        role = guild.get_role(data["manager_role_id"]) if data["manager_role_id"] else None
        shared = await self.bot.get_shared_api_tokens(self.guild_namespace(guild.id))
        embed = discord.Embed(
            title="MyBB server setup",
            description="Connect this Discord server to its own MyBB board. Members may then add personal access without changing the server connector.",
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
        embed.add_field(name="Manager role", value=role.mention if role else "Manage Server only")
        embed.add_field(
            name="Membership roles",
            value=(
                f"{len(data.get('membership_roles', {}))} mapping(s) · "
                f"{'Enabled' if data.get('membership_enabled') else 'Disabled'} · "
                f"client {'stored' if (await self.bot.get_shared_api_tokens(self.membership_guild_namespace(guild.id))).get('client_id') else 'not configured'}"
            ),
            inline=False,
        )
        embed.set_footer(text="Tokens are never displayed. RSSPublisher remains the better tool for automatic announcements.")
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
            "Configure the dedicated External Sign-In confidential client. Its Account linking service must be enabled.",
            view=MembershipSetupView(self, ctx.author),
        )

    @mybbset_membership.command(name="enabled")
    async def mybbset_membership_enabled(self, ctx, enabled: bool):
        if enabled:
            tokens = await self.bot.get_shared_api_tokens(self.membership_guild_namespace(ctx.guild.id))
            if not await self.config.guild(ctx.guild).board_url() or not tokens.get("client_id") or not tokens.get("client_secret"):
                await ctx.send("Connect the board and configure the External Sign-In client first.")
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
    async def mybbset_enabled(self, ctx, enabled: bool):
        await self.config.guild(ctx.guild).enabled.set(enabled)
        await ctx.send(f"MyBB bridge {'enabled' if enabled else 'disabled'} for this server.")

    @mybbset.command(name="defaultforum")
    async def mybbset_defaultforum(self, ctx, forum_id: Optional[int] = None):
        await self.config.guild(ctx.guild).default_forum_id.set(forum_id)
        await ctx.send(f"Default MyBB forum set to `{forum_id}`." if forum_id else "Default forum cleared.")

    @mybbset.group(name="forumalias", aliases=["forumaliases"], invoke_without_command=True)
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
    async def mybbset_managerrole(self, ctx, role: Optional[discord.Role] = None):
        await self.config.guild(ctx.guild).manager_role_id.set(role.id if role else None)
        await ctx.send(f"{role.mention} can publish with the server connector." if role else "Only members with Manage Server can publish through the server connector.")

    @mybbset.command(name="disconnect")
    async def mybbset_disconnect(self, ctx, confirmation: str = ""):
        """Disconnect this Discord server from MyBB."""
        if confirmation.casefold() != "confirm":
            await ctx.send(f"Run `{ctx.clean_prefix}mybbset disconnect confirm` to remove the server connector. Personal tokens are removed separately by their owners.")
            return
        try:
            await self.clear_guild_memberships(ctx.guild, notify_provider=True)
        except (MembershipError, discord.HTTPException, RoleSyncError) as error:
            await ctx.send(f"Disconnect stopped because membership cleanup could not be confirmed: {error}")
            return
        await self.bot.remove_shared_api_tokens(self.guild_namespace(ctx.guild.id), "token")
        await self.bot.remove_shared_api_tokens(
            self.membership_guild_namespace(ctx.guild.id), "client_id", "client_secret"
        )
        await self.clear_guild_member_connections(ctx.guild)
        await self.config.guild(ctx.guild).board_url.clear()
        await self.config.guild(ctx.guild).default_forum_id.clear()
        await self.config.guild(ctx.guild).forum_aliases.clear()
        await self.config.guild(ctx.guild).membership_roles.clear()
        await self.config.guild(ctx.guild).membership_enabled.set(False)
        await self.config.guild(ctx.guild).enabled.set(False)
        await ctx.send("This Discord server's MyBB board connector has been removed and the bridge disabled.")
