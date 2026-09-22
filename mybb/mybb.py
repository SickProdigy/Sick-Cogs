"""Per-guild and per-member bridge for the SickProdigy MyBB API v1 plugin."""

from typing import Optional

import discord
from redbot.core import Config, commands

from .client import MyBBAPIError, MyBBClient


GUILD_DEFAULTS = {
    "enabled": False,
    "board_url": None,
    "default_forum_id": None,
    "manager_role_id": None,
}
MEMBER_DEFAULTS = {"connected": False}


class MyBB(commands.Cog):
    """Connect a Discord server to MyBB and let members optionally use personal access."""

    __author__ = ["SickProdigy"]
    __version__ = "0.1.0"
    CONFIG_IDENTIFIER = 620260922060001

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=self.CONFIG_IDENTIFIER, force_registration=True)
        self.config.register_guild(**GUILD_DEFAULTS)
        self.config.register_member(**MEMBER_DEFAULTS)

    @staticmethod
    def guild_namespace(guild_id):
        return f"mybb_guild_{int(guild_id)}"

    @staticmethod
    def member_namespace(guild_id, user_id):
        return f"mybb_user_{int(guild_id)}_{int(user_id)}"

    async def red_delete_data_for_user(self, *, user_id: int, **kwargs):
        """Remove member connection markers and their stored MyBB tokens."""
        for guild_id in await self.config.all_guilds():
            await self.config.member_from_ids(guild_id, user_id).clear()
            await self.bot.remove_shared_api_tokens(
                self.member_namespace(guild_id, user_id), "token"
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
            await self.config.member_from_ids(guild.id, user_id).clear()

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
            name="Default forum",
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
    async def draft(self, ctx, forum_id: Optional[int] = None, *, subject: str):
        """Review a replied-to Discord message before publishing it as a MyBB thread."""
        if not await self.ensure_enabled(ctx, ctx.guild):
            return
        personal = await self.has_personal_connection(ctx.guild.id, ctx.author.id)
        if not personal and not await self.is_manager(ctx.author):
            await ctx.send(
                f"Connect your MyBB account with `{ctx.clean_prefix}mybb connect`, or ask a configured MyBB manager to publish this."
            )
            return
        forum_id = forum_id or await self.config.guild(ctx.guild).default_forum_id()
        if not forum_id:
            await ctx.send("Provide a forum ID or configure a default forum.")
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
        embed.add_field(name="Default forum", value=str(data["default_forum_id"] or "Not set"))
        embed.add_field(name="Manager role", value=role.mention if role else "Manage Server only")
        embed.set_footer(text="Tokens are never displayed. RSSPublisher remains the better tool for automatic announcements.")
        return embed

    @mybbset.command(name="status")
    async def mybbset_status(self, ctx):
        await ctx.send(embed=await self.settings_embed(ctx.guild))

    @mybbset.command(name="enabled")
    async def mybbset_enabled(self, ctx, enabled: bool):
        await self.config.guild(ctx.guild).enabled.set(enabled)
        await ctx.send(f"MyBB bridge {'enabled' if enabled else 'disabled'} for this server.")

    @mybbset.command(name="defaultforum")
    async def mybbset_defaultforum(self, ctx, forum_id: Optional[int] = None):
        await self.config.guild(ctx.guild).default_forum_id.set(forum_id)
        await ctx.send(f"Default MyBB forum set to `{forum_id}`." if forum_id else "Default forum cleared.")

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
        await self.bot.remove_shared_api_tokens(self.guild_namespace(ctx.guild.id), "token")
        await self.clear_guild_member_connections(ctx.guild)
        await self.config.guild(ctx.guild).board_url.clear()
        await self.config.guild(ctx.guild).default_forum_id.clear()
        await self.config.guild(ctx.guild).enabled.set(False)
        await ctx.send("This Discord server's MyBB board connector has been removed and the bridge disabled.")
