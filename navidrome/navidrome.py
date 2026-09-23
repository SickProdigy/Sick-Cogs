import asyncio
import datetime
import io
import logging
import random
import re
import secrets
import time
from typing import Any, Dict, List, Optional, Tuple, Union

import aiohttp
import discord
from discord.ext import tasks
from redbot.core import Config, checks, commands
from redbot.core.bot import Red
from redbot.core.utils import can_user_send_messages_in
from redbot.core.utils.chat_formatting import pagify

from .client import NavidromeClient, NavidromeError, validate_base_url
from .setup import NavidromeSetupView


log = logging.getLogger("red.sick-cogs.Navidrome")
CONFIG_IDENTIFIER = 9172048261
TOKEN_PREFIX = "navidrome_"
USER_AGENT = "Sick-Cogs-Navidrome/1.1.0"
GuildMessageable = Union[discord.TextChannel, discord.VoiceChannel, discord.StageChannel, discord.Thread]


def utc_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


async def navidrome_config_permission(ctx: commands.Context) -> bool:
    """Allow administrators or a guild's explicitly delegated Navidrome manager role."""
    if await ctx.bot.is_owner(ctx.author) or ctx.author.guild_permissions.manage_guild:
        return True
    role_id = await ctx.cog.config.guild(ctx.guild).manager_role_id()
    if role_id and any(role.id == role_id for role in ctx.author.roles):
        return True
    raise commands.CheckFailure(
        "Manage Server or the configured Navidrome manager role is required."
    )


def safe_profile_name(value: str) -> str:
    name = value.strip().lower()
    if not name or len(name) > 32 or not all(char.isalnum() or char in "_-" for char in name):
        raise ValueError("Connection names may contain only letters, numbers, underscores, and hyphens.")
    return name


class Navidrome(commands.Cog):
    """Connect each Discord server to its own approved Navidrome library."""

    __author__ = ["SickProdigy"]
    __version__ = "1.1.0"

    default_global = {"connections": {}, "guild_connections_enabled": False}
    default_guild = {
        "connection": None,
        "connection_mode": "owner_managed",
        "guild_connection": None,
        "announcement_enabled": False,
        "announcement_channel_id": None,
        "interval_minutes": 60,
        "next_check_at": None,
        "announced_album_ids": [],
        "last_success_at": None,
        "accounts": {},
        "manager_role_id": None,
    }

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=CONFIG_IDENTIFIER, force_registration=True)
        self.config.register_global(**self.default_global)
        self.config.register_guild(**self.default_guild)
        self.session: Optional[aiohttp.ClientSession] = None
        self._poll_semaphore = asyncio.Semaphore(4)
        self._connection_failures: Dict[str, int] = {}
        self._connection_test_times: Dict[int, float] = {}
        self.poll_loop.start()

    async def red_delete_data_for_user(self, **kwargs):
        """Remove Discord-to-Navidrome mappings without deleting remote accounts."""
        user_id = str(kwargs.get("user_id") or "")
        if not user_id:
            return
        for guild_id, settings in (await self.config.all_guilds()).items():
            accounts = dict(settings.get("accounts", {}))
            if accounts.pop(user_id, None) is not None:
                await self.config.guild_from_id(guild_id).accounts.set(accounts)

    def cog_unload(self):
        self.poll_loop.cancel()
        if self.session and not self.session.closed:
            self.bot.loop.create_task(self.session.close())

    async def get_session(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=20), headers={"User-Agent": USER_AGENT}
            )
        return self.session

    def claim_connection_test(self, guild_id: int) -> bool:
        """Allow one guild-owned credential test per minute per guild."""
        now = time.monotonic()
        previous = self._connection_test_times.get(int(guild_id), 0.0)
        if now - previous < 60:
            return False
        self._connection_test_times[int(guild_id)] = now
        return True

    async def _profile(self, name: str) -> Optional[Dict[str, Any]]:
        return (await self.config.connections()).get(name)

    async def _client(self, name: str) -> NavidromeClient:
        profile = await self._profile(name)
        if not profile:
            raise NavidromeError("This server's Navidrome connection is no longer approved.")
        tokens = await self.bot.get_shared_api_tokens(f"{TOKEN_PREFIX}{name}")
        username = str(tokens.get("username") or "").strip()
        password = str(tokens.get("password") or "")
        if not username or not password:
            raise NavidromeError("The approved connection is missing its credentials.")
        return NavidromeClient(await self.get_session(), profile["base_url"], username, password)

    @staticmethod
    def guild_token_namespace(guild_id: int) -> str:
        return f"{TOKEN_PREFIX}guild_{int(guild_id)}"

    async def _guild_client(self, guild: discord.Guild) -> Tuple[str, NavidromeClient]:
        settings = await self.config.guild(guild).all()
        if settings.get("connection_mode") == "guild_managed":
            profile = settings.get("guild_connection") or {}
            base_url = str(profile.get("base_url") or "")
            if not base_url:
                raise NavidromeError("This server's guild-managed Navidrome connection is missing.")
            tokens = await self.bot.get_shared_api_tokens(self.guild_token_namespace(guild.id))
            username = str(tokens.get("username") or "").strip()
            password = str(tokens.get("password") or "")
            if not username or not password:
                raise NavidromeError("This server's guild-managed connection is missing credentials.")
            client = NavidromeClient(
                await self.get_session(), base_url, username, password, public_only=True
            )
            return "Guild managed", client
        name = settings.get("connection")
        if not name:
            raise NavidromeError("This server has not selected a Navidrome connection.")
        return name, await self._client(name)

    async def _reset_connection_state(self, guild: discord.Guild, *, clear_accounts: bool):
        group = self.config.guild(guild)
        await group.announcement_enabled.set(False)
        await group.announced_album_ids.set([])
        await group.last_success_at.set(None)
        await group.next_check_at.set(None)
        if clear_accounts:
            await group.accounts.set({})

    async def _channel(self, guild: discord.Guild, channel_id: int) -> Optional[GuildMessageable]:
        channel = guild.get_channel_or_thread(channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                return None
        if getattr(channel, "guild", None) != guild or not can_user_send_messages_in(guild.me, channel):
            return None
        return channel

    @staticmethod
    def album_embed(album: Dict[str, Any]) -> discord.Embed:
        title = str(album.get("name") or album.get("album") or "Untitled album")
        artist = str(album.get("artist") or "Unknown artist")
        embed = discord.Embed(title=title, description=artist, colour=discord.Colour.blurple())
        details = []
        if album.get("year"):
            details.append(str(album["year"]))
        if album.get("genre"):
            details.append(str(album["genre"]))
        if details:
            embed.add_field(name="Details", value=" • ".join(details), inline=False)
        if album.get("songCount") is not None:
            embed.add_field(name="Tracks", value=str(album["songCount"]), inline=True)
        duration = int(album.get("duration") or 0)
        if duration:
            hours, remainder = divmod(duration, 3600)
            minutes = remainder // 60
            embed.add_field(name="Duration", value=f"{hours}h {minutes}m" if hours else f"{minutes}m", inline=True)
        embed.set_footer(text="Recently added to Navidrome")
        return embed

    async def send_album(
        self,
        channel: GuildMessageable,
        album: Dict[str, Any],
        client: NavidromeClient,
        *,
        content: Optional[str] = None,
    ):
        embed = self.album_embed(album)
        artwork = await client.cover_art(album.get("coverArt"))
        file = None
        if artwork:
            file = discord.File(io.BytesIO(artwork), filename="cover.jpg")
            embed.set_thumbnail(url="attachment://cover.jpg")
        await channel.send(
            content=content,
            embed=embed,
            file=file,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @staticmethod
    def _due(settings: Dict[str, Any]) -> bool:
        value = settings.get("next_check_at")
        if not value:
            return True
        try:
            parsed = datetime.datetime.fromisoformat(value)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=datetime.timezone.utc)
            return parsed <= utc_now()
        except (TypeError, ValueError):
            return True

    async def _set_next_check(self, guild: discord.Guild, minutes: int):
        value = (utc_now() + datetime.timedelta(minutes=max(15, minutes))).isoformat()
        await self.config.guild(guild).next_check_at.set(value)

    async def setup_embed(self, guild: discord.Guild) -> discord.Embed:
        settings = await self.config.guild(guild).all()
        profiles = await self.config.connections()
        guild_connections_enabled = await self.config.guild_connections_enabled()
        channel = (
            guild.get_channel_or_thread(settings["announcement_channel_id"])
            if settings.get("announcement_channel_id")
            else None
        )
        mode = settings.get("connection_mode", "owner_managed")
        selected = settings.get("connection")
        guild_profile = settings.get("guild_connection") or {}
        active_name = "Guild managed" if mode == "guild_managed" else selected
        ready = bool(guild_profile.get("base_url")) if mode == "guild_managed" else selected in profiles
        embed = discord.Embed(
            title="Navidrome setup",
            description=(
                "Connect a Navidrome server with an administrator account, then manage users. Album "
                "notifications are optional."
            ),
            colour=discord.Colour.blurple(),
        )
        embed.add_field(
            name="Connection",
            value=active_name or ("Not selected" if profiles else "No owner-approved connections"),
            inline=True,
        )
        embed.add_field(
            name="Connection mode",
            value="Guild managed" if mode == "guild_managed" else "Bot-owner managed",
            inline=True,
        )
        embed.add_field(
            name="Connection status",
            value="Ready to test" if ready else "Setup required",
            inline=True,
        )
        embed.add_field(
            name="Announcements",
            value="Enabled" if settings.get("announcement_enabled") else "Disabled",
            inline=True,
        )
        embed.add_field(name="Channel", value=channel.mention if channel else "Not set", inline=True)
        embed.add_field(
            name="Interval", value=f"{settings.get('interval_minutes', 60)} minutes", inline=True
        )
        embed.add_field(
            name="Last successful check",
            value=settings.get("last_success_at") or "Never",
            inline=False,
        )
        if not profiles and mode != "guild_managed":
            embed.add_field(
                name="Owner setup needed",
                value=(
                    "The bot owner can choose **Connect server** below to test and save "
                    "the Navidrome administrator connection."
                ),
                inline=False,
            )
        if guild_connections_enabled:
            embed.add_field(
                name="Guild-managed setup",
                value="Available to members with Manage Server. Public HTTPS endpoints only.",
                inline=False,
            )
        return embed

    async def enable_announcements(self, guild: discord.Guild) -> Tuple[bool, str]:
        settings = await self.config.guild(guild).all()
        if not settings.get("announcement_channel_id"):
            return False, "Select a connection and announcement channel first."
        channel = await self._channel(guild, int(settings["announcement_channel_id"]))
        if not channel:
            return False, "I cannot send messages in the configured channel."
        try:
            _, client = await self._guild_client(guild)
            albums = await client.newest_albums(25)
        except NavidromeError as exc:
            return False, f"Connection test failed: {exc}"
        baseline = [str(album["id"]) for album in albums if album.get("id")]
        group = self.config.guild(guild)
        await group.announced_album_ids.set(baseline[-500:])
        await group.announcement_enabled.set(True)
        await group.last_success_at.set(utc_now().isoformat())
        await self._set_next_check(guild, int(settings.get("interval_minutes", 60)))
        return True, (
            "Recently added album announcements are enabled. Current albums were recorded "
            "as the baseline and will not flood the channel."
        )

    async def preview_announcement(self, guild: discord.Guild) -> Tuple[bool, str]:
        settings = await self.config.guild(guild).all()
        channel_id = settings.get("announcement_channel_id")
        if not channel_id:
            return False, "Select a connection and announcement channel first."
        channel = await self._channel(guild, int(channel_id))
        if not channel:
            return False, "I cannot send messages in the configured channel."
        try:
            _, client = await self._guild_client(guild)
            albums = await client.newest_albums(1)
        except NavidromeError as exc:
            return False, str(exc)
        if not albums:
            return False, "Navidrome returned no albums to preview."
        await self.send_album(channel, albums[0], client, content="Navidrome announcement preview")
        return True, f"Sent a preview to {channel.mention}. Announcement history was not changed."

    async def _check_guild(self, guild: discord.Guild, albums: List[Dict[str, Any]], client: NavidromeClient):
        settings = await self.config.guild(guild).all()
        channel_id = settings.get("announcement_channel_id")
        if not settings.get("announcement_enabled") or not channel_id or not self._due(settings):
            return
        channel = await self._channel(guild, int(channel_id))
        if not channel:
            log.warning("Navidrome announcement channel unavailable for guild %s", guild.id)
            await self._set_next_check(guild, int(settings.get("interval_minutes", 60)))
            return
        seen = list(settings.get("announced_album_ids", []))
        seen_set = set(seen)
        unseen = [
            album
            for album in reversed(albums)
            if album.get("id") and str(album["id"]) not in seen_set
        ]
        original_seen_count = len(seen)
        try:
            for album in unseen[:5]:
                await self.send_album(channel, album, client)
                seen.append(str(album["id"]))
        finally:
            # Persist every successfully delivered ID even if a later Discord send fails.
            if len(seen) != original_seen_count:
                await self.config.guild(guild).announced_album_ids.set(seen[-500:])
        await self.config.guild(guild).last_success_at.set(utc_now().isoformat())
        await self._set_next_check(guild, int(settings.get("interval_minutes", 60)))

    @tasks.loop(minutes=5)
    async def poll_loop(self):
        await self.bot.wait_until_red_ready()
        due: Dict[str, List[discord.Guild]] = {}
        for guild in list(self.bot.guilds):
            if await self.bot.cog_disabled_in_guild(self, guild):
                continue
            settings = await self.config.guild(guild).all()
            configured = (
                bool((settings.get("guild_connection") or {}).get("base_url"))
                if settings.get("connection_mode") == "guild_managed"
                else bool(settings.get("connection"))
            )
            if settings.get("announcement_enabled") and configured and self._due(settings):
                key = (
                    f"guild:{guild.id}"
                    if settings.get("connection_mode") == "guild_managed"
                    else f"owner:{settings['connection']}"
                )
                due.setdefault(key, []).append(guild)
        if due:
            await asyncio.gather(
                *(self._poll_connection(name, guilds) for name, guilds in due.items())
            )

    async def _poll_connection(self, key: str, guilds: List[discord.Guild]):
        async with self._poll_semaphore:
            try:
                if key.startswith("guild:"):
                    _, client = await self._guild_client(guilds[0])
                else:
                    name = key.split(":", 1)[1] if key.startswith("owner:") else key
                    client = await self._client(name)
                albums = await client.newest_albums(25)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                failures = self._connection_failures.get(key, 0) + 1
                self._connection_failures[key] = failures
                delay = min(240, 15 * (2 ** min(failures - 1, 4))) + random.randint(0, 5)
                for guild in guilds:
                    await self._set_next_check(guild, delay)
                log.warning(
                    "Navidrome poll failed for connection %s (%s); retry in about %s minutes",
                    key, type(exc).__name__, delay,
                )
                return
            self._connection_failures.pop(key, None)
            for guild in guilds:
                try:
                    await self._check_guild(guild, albums, client)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception("Navidrome delivery failed for guild %s", guild.id)

    @poll_loop.before_loop
    async def before_poll_loop(self):
        await self.bot.wait_until_red_ready()

    @commands.group(name="navidrome", invoke_without_command=True)
    @commands.guild_only()
    @commands.bot_has_permissions(embed_links=True)
    async def navidrome(self, ctx: commands.Context):
        """Show this server's Navidrome library information."""
        await self._send_info(ctx)

    async def _send_info(self, ctx: commands.Context):
        try:
            name, client = await self._guild_client(ctx.guild)
            summary = await client.library_summary()
        except NavidromeError as exc:
            return await ctx.send(str(exc))
        embed = discord.Embed(title=f"{summary['type']} library", colour=discord.Colour.blurple())
        embed.add_field(name="Connection", value=name, inline=True)
        embed.add_field(name="Server version", value=summary["server_version"], inline=True)
        embed.add_field(name="API version", value=summary["api_version"], inline=True)
        embed.add_field(name="Artists", value=f"{summary['artists']:,}", inline=True)
        embed.add_field(name="Libraries", value=f"{summary['music_folders']:,}", inline=True)
        embed.add_field(name="OpenSubsonic", value="Yes" if summary["open_subsonic"] else "No", inline=True)
        await ctx.send(embed=embed)

    @navidrome.command(name="info", aliases=("stats",))
    @commands.bot_has_permissions(embed_links=True)
    async def navidrome_info(self, ctx: commands.Context):
        """Show non-sensitive Navidrome server and library information."""
        await self._send_info(ctx)

    @navidrome.command(name="account")
    async def navidrome_account(self, ctx: commands.Context):
        """Show your Discord-linked Navidrome account."""
        account = (await self.config.guild(ctx.guild).accounts()).get(str(ctx.author.id))
        if not account:
            return await ctx.send("You do not have a linked Navidrome account in this server.")
        await ctx.send(
            f"Your linked Navidrome username is `{account.get('username', 'unknown')}`. "
            "Ask a server administrator if you need a password reset."
        )

    @navidrome.command(name="recent")
    @commands.bot_has_permissions(embed_links=True)
    async def navidrome_recent(self, ctx: commands.Context, count: int = 5):
        """Show recently added albums (1-10)."""
        count = max(1, min(count, 10))
        try:
            _, client = await self._guild_client(ctx.guild)
            albums = await client.newest_albums(count)
        except NavidromeError as exc:
            return await ctx.send(str(exc))
        if not albums:
            return await ctx.send("No albums were returned by this Navidrome server.")
        for album in albums:
            await self.send_album(ctx.channel, album, client)

    @commands.group(name="navidromeowner", invoke_without_command=True)
    @checks.is_owner()
    async def navidromeowner(self, ctx: commands.Context):
        """Manage bot-owner-approved Navidrome connections."""
        await ctx.send_help()

    @navidromeowner.command(name="guildconnections")
    async def navidromeowner_guildconnections(self, ctx: commands.Context, enabled: bool):
        """Allow or deny guild administrators from storing their own public-HTTPS connection."""
        await self.config.guild_connections_enabled.set(enabled)
        await ctx.send(
            f"Guild-managed Navidrome connections are now {'enabled' if enabled else 'disabled'}."
        )

    @navidromeowner.group(name="connection", invoke_without_command=True)
    async def owner_connection(self, ctx: commands.Context):
        """Manage approved connection profiles."""
        await ctx.send_help()

    @owner_connection.command(name="add")
    async def owner_connection_add(self, ctx: commands.Context, name: str, base_url: str, allow_http: bool = False):
        """Approve a URL after setting navidrome_NAME username/password API tokens."""
        try:
            name = safe_profile_name(name)
            base_url = validate_base_url(base_url, allow_http=allow_http)
        except ValueError as exc:
            return await ctx.send(str(exc))
        tokens = await self.bot.get_shared_api_tokens(f"{TOKEN_PREFIX}{name}")
        if not tokens.get("username") or not tokens.get("password"):
            return await ctx.send(
                f"Set credentials first with `{ctx.clean_prefix}set api {TOKEN_PREFIX}{name} username,YOUR_USER password,YOUR_PASSWORD` in a private channel."
            )
        profiles = await self.config.connections()
        previous = profiles.get(name)
        profiles[name] = {"base_url": base_url, "allow_http": bool(allow_http)}
        await self.config.connections.set(profiles)
        try:
            await (await self._client(name)).ping()
        except NavidromeError as exc:
            if previous is None:
                profiles.pop(name, None)
            else:
                profiles[name] = previous
            await self.config.connections.set(profiles)
            return await ctx.send(f"Connection was not saved: {exc}")
        await ctx.send(f"Approved Navidrome connection `{name}`.")

    @owner_connection.command(name="remove", aliases=("delete",))
    async def owner_connection_remove(self, ctx: commands.Context, name: str):
        """Remove an approved connection profile. Credentials are not deleted."""
        try:
            name = safe_profile_name(name)
        except ValueError as exc:
            return await ctx.send(str(exc))
        profiles = await self.config.connections()
        if name not in profiles:
            return await ctx.send("That connection is not registered.")
        profiles.pop(name)
        await self.config.connections.set(profiles)
        await ctx.send(f"Removed approved connection `{name}`. Guilds using it will stop polling.")

    @owner_connection.command(name="list", aliases=("show",))
    async def owner_connection_list(self, ctx: commands.Context):
        """List approved connection names and redacted endpoints."""
        profiles = await self.config.connections()
        if not profiles:
            return await ctx.send("No Navidrome connections are approved.")
        lines = [f"`{name}` — {profile['base_url']}" for name, profile in sorted(profiles.items())]
        await ctx.send("\n".join(lines))

    @owner_connection.command(name="test")
    async def owner_connection_test(self, ctx: commands.Context, name: str):
        """Test an approved connection without exposing credentials."""
        try:
            name = safe_profile_name(name)
            client = await self._client(name)
            result = await client.ping()
            users = await client.users()
        except (ValueError, NavidromeError) as exc:
            return await ctx.send(f"Connection test failed: {exc}")
        server = str(result.get("type") or "Navidrome")
        version = str(result.get("serverVersion") or "unknown version")
        await ctx.send(
            f"Connection `{name}` is responding as {server} {version}. "
            f"Native user management is available ({len(users)} users visible)."
        )

    @commands.group(name="navidromeset", invoke_without_command=True)
    @commands.guild_only()
    @commands.check(navidrome_config_permission)
    async def navidromeset(self, ctx: commands.Context):
        """Configure this server's Navidrome connection and announcements."""
        await self._send_settings(ctx)

    @navidromeset.command(name="setup", aliases=("interactive",))
    @commands.bot_has_permissions(embed_links=True)
    async def navidromeset_setup(self, ctx: commands.Context):
        """Open the guided Navidrome setup panel."""
        await ctx.send(
            embed=await self.setup_embed(ctx.guild),
            view=await NavidromeSetupView.create(self, ctx.author, ctx.guild),
        )

    @navidromeset.command(name="connection")
    @checks.admin_or_permissions(manage_guild=True)
    async def navidromeset_connection(self, ctx: commands.Context, name: str):
        """Select a bot-owner-approved connection for this server."""
        try:
            name = safe_profile_name(name)
        except ValueError as exc:
            return await ctx.send(str(exc))
        if not await self._profile(name):
            return await ctx.send("That Navidrome connection is not approved by the bot owner.")
        try:
            await (await self._client(name)).ping()
        except NavidromeError as exc:
            return await ctx.send(f"Connection test failed: {exc}")
        group = self.config.guild(ctx.guild)
        previous_mode = await group.connection_mode()
        previous_name = await group.connection()
        changed_server = previous_mode != "owner_managed" or previous_name != name
        if previous_mode == "guild_managed":
            await self.bot.remove_shared_api_tokens(
                self.guild_token_namespace(ctx.guild.id), "username", "password"
            )
            await group.guild_connection.set(None)
        await group.connection_mode.set("owner_managed")
        await group.connection.set(name)
        await self._reset_connection_state(ctx.guild, clear_accounts=changed_server)
        await ctx.send(f"This server now uses Navidrome connection `{name}`. Announcements remain disabled.")

    @navidromeset.command(name="disconnect")
    @checks.admin_or_permissions(manage_guild=True)
    async def navidromeset_disconnect(self, ctx: commands.Context, confirmation: str = ""):
        """Disconnect this server and delete any guild-owned credentials."""
        if confirmation.casefold() != "confirm":
            await ctx.send(
                f"Run `{ctx.clean_prefix}navidromeset disconnect confirm` to disconnect, clear linked-account mappings, and delete this guild's stored connection credentials."
            )
            return
        await self.bot.remove_shared_api_tokens(
            self.guild_token_namespace(ctx.guild.id), "username", "password"
        )
        await self.config.guild(ctx.guild).clear()
        await ctx.send(
            "Disconnected this server from Navidrome, deleted any guild-owned credentials, and cleared its local mappings and announcement history."
        )

    @navidromeset.command(name="managerrole")
    @checks.admin_or_permissions(manage_guild=True)
    async def navidromeset_managerrole(
        self, ctx: commands.Context, role: Optional[discord.Role] = None
    ):
        """Set or clear the role allowed to perform non-secret routine configuration."""
        await self.config.guild(ctx.guild).manager_role_id.set(role.id if role else None)
        if role:
            await ctx.send(
                f"{role.mention} may now use routine Navidrome configuration. "
                "Manage Server is still required to create, replace, or disconnect credentials."
            )
        else:
            await ctx.send("The optional Navidrome manager role is cleared.")

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild):
        await self.bot.remove_shared_api_tokens(
            self.guild_token_namespace(guild.id), "username", "password"
        )
        await self.config.guild(guild).clear()

    @navidromeset.command(name="channel", aliases=("announcechannel",))
    async def navidromeset_channel(self, ctx: commands.Context, channel: Optional[GuildMessageable] = None):
        """Set the recently-added album channel. Defaults to this channel."""
        channel = channel or ctx.channel
        if not await self._channel(ctx.guild, channel.id):
            return await ctx.send("I cannot send messages in that channel.")
        await self.config.guild(ctx.guild).announcement_channel_id.set(channel.id)
        await ctx.send(f"Recently added albums will be posted in {channel.mention} after announcements are enabled.")

    @navidromeset.command(name="interval")
    async def navidromeset_interval(self, ctx: commands.Context, minutes: int):
        """Set album polling interval in minutes (15-1440)."""
        if not 15 <= minutes <= 1440:
            return await ctx.send("The interval must be between 15 and 1440 minutes.")
        await self.config.guild(ctx.guild).interval_minutes.set(minutes)
        await self._set_next_check(ctx.guild, minutes)
        await ctx.send(f"Navidrome will be checked every {minutes} minutes.")

    @navidromeset.command(name="enable")
    async def navidromeset_enable(self, ctx: commands.Context):
        """Enable album announcements and establish a no-flood baseline."""
        ok, message = await self.enable_announcements(ctx.guild)
        await ctx.send(message)

    @navidromeset.command(name="disable")
    async def navidromeset_disable(self, ctx: commands.Context):
        """Disable scheduled album announcements."""
        await self.config.guild(ctx.guild).announcement_enabled.set(False)
        await ctx.send("Recently added album announcements are disabled.")

    @navidromeset.command(name="preview", aliases=("test",))
    @commands.bot_has_permissions(embed_links=True)
    async def navidromeset_preview(self, ctx: commands.Context):
        """Preview the newest album without changing announcement history."""
        ok, message = await self.preview_announcement(ctx.guild)
        await ctx.send(message)

    @navidromeset.group(name="user", invoke_without_command=True)
    async def navidromeset_user(self, ctx: commands.Context):
        """Manage Discord-linked Navidrome accounts for this server."""
        await ctx.send_help()

    @navidromeset_user.command(name="list")
    async def navidromeset_user_list(self, ctx: commands.Context):
        """List accounts provisioned through this Discord server."""
        accounts = await self.config.guild(ctx.guild).accounts()
        if not accounts:
            return await ctx.send("No Discord-linked Navidrome accounts are recorded.")
        lines = []
        for discord_id, account in sorted(
            accounts.items(), key=lambda item: str(item[1].get("username", "")).casefold()
        ):
            member = ctx.guild.get_member(int(discord_id))
            who = member.mention if member else f"Discord user `{discord_id}`"
            lines.append(f"{who} - `{account.get('username', 'unknown')}`")
        for page in pagify("\n".join(lines), delims=["\n"], page_length=1800):
            await ctx.send(page, allowed_mentions=discord.AllowedMentions.none())

    @navidromeset_user.command(name="info")
    async def navidromeset_user_info(self, ctx: commands.Context, member: discord.Member):
        """Show the current remote record for a linked member."""
        account = (await self.config.guild(ctx.guild).accounts()).get(str(member.id))
        if not account:
            return await ctx.send("That member has no linked Navidrome account in this server.")
        try:
            _, client = await self._guild_client(ctx.guild)
            user = await client.user_by_username(str(account.get("username") or ""))
        except NavidromeError as exc:
            return await ctx.send(f"Could not read the Navidrome user: {exc}")
        if not user:
            return await ctx.send(
                "The linked Navidrome user no longer exists. Use the unlink command to clear "
                "the stale Discord mapping."
            )
        embed = discord.Embed(title="Navidrome account", colour=discord.Colour.blurple())
        embed.add_field(name="Discord member", value=member.mention, inline=False)
        embed.add_field(name="Username", value=str(user.get("userName") or "Unknown"), inline=True)
        embed.add_field(name="Display name", value=str(user.get("name") or "Not set"), inline=True)
        embed.add_field(name="Email", value=str(user.get("email") or "Not set"), inline=True)
        embed.add_field(name="Administrator", value="Yes" if user.get("isAdmin") else "No", inline=True)
        await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())

    @navidromeset_user.command(name="create")
    async def navidromeset_user_create(
        self, ctx: commands.Context, member: discord.Member, username: str, email: str = ""
    ):
        """Create a non-admin Navidrome user and privately send its temporary password."""
        username = username.strip()
        if not 3 <= len(username) <= 64 or not re.fullmatch(r"[A-Za-z0-9._-]+", username):
            return await ctx.send(
                "Usernames must be 3-64 characters using letters, numbers, dots, underscores, or hyphens."
            )
        if email and ("@" not in email or len(email) > 254):
            return await ctx.send("Enter a valid email address or omit it.")
        group = self.config.guild(ctx.guild)
        accounts = await group.accounts()
        if str(member.id) in accounts:
            return await ctx.send("That member already has a linked Navidrome account.")
        try:
            _, client = await self._guild_client(ctx.guild)
            if await client.user_by_username(username):
                return await ctx.send("That Navidrome username already exists.")
            password = secrets.token_urlsafe(18)
            user = await client.create_user(
                username, password, name=member.display_name[:100], email=email
            )
            try:
                await member.send(
                    f"Your Navidrome account for **{ctx.guild.name}** is ready.\n"
                    f"Username: `{username}`\nTemporary password: `{password}`\n"
                    "Sign in and change this password as soon as possible."
                )
            except discord.HTTPException:
                await client.delete_user(str(user.get("id") or ""))
                return await ctx.send(
                    "I could not DM that member, so the newly created Navidrome account was rolled back."
                )
        except NavidromeError as exc:
            return await ctx.send(f"Navidrome account creation failed: {exc}")
        accounts[str(member.id)] = {
            "id": str(user.get("id") or ""),
            "username": str(user.get("userName") or username),
            "created_at": utc_now().isoformat(),
        }
        await group.accounts.set(accounts)
        await ctx.send(
            f"Created Navidrome user `{username}` for {member.mention}; credentials were sent by DM.",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @navidromeset_user.command(name="name")
    async def navidromeset_user_name(
        self, ctx: commands.Context, member: discord.Member, *, display_name: str
    ):
        """Change a linked user's Navidrome display name."""
        await self._update_linked_user(ctx, member, name=display_name.strip()[:100])

    @navidromeset_user.command(name="email")
    async def navidromeset_user_email(
        self, ctx: commands.Context, member: discord.Member, email: str
    ):
        """Change a linked user's Navidrome email address."""
        if "@" not in email or len(email) > 254:
            return await ctx.send("Enter a valid email address.")
        await self._update_linked_user(ctx, member, email=email)

    @navidromeset_user.command(name="password", aliases=("resetpassword",))
    async def navidromeset_user_password(self, ctx: commands.Context, member: discord.Member):
        """Reset a linked user's password and send it privately."""
        account = (await self.config.guild(ctx.guild).accounts()).get(str(member.id))
        if not account:
            return await ctx.send("That member has no linked Navidrome account in this server.")
        try:
            await member.send(
                f"A Navidrome password reset was requested for **{ctx.guild.name}**. "
                "Your temporary password will follow in a second message."
            )
        except discord.HTTPException:
            return await ctx.send("I cannot DM that member, so their password was not changed.")
        try:
            _, client = await self._guild_client(ctx.guild)
            user = await client.user_by_username(str(account.get("username") or ""))
            if not user:
                return await ctx.send("The linked Navidrome user no longer exists.")
            password = secrets.token_urlsafe(18)
            await client.update_user(user, password=password)
            await member.send(
                f"New Navidrome temporary password: `{password}`\n"
                "Sign in and change it as soon as possible."
            )
        except (NavidromeError, discord.HTTPException) as exc:
            if isinstance(exc, NavidromeError):
                return await ctx.send(f"Navidrome password reset failed: {exc}")
            return await ctx.send(
                "The password changed, but the final DM failed. Run the reset command again."
            )
        await ctx.send(f"Reset {member.mention}'s Navidrome password and sent it by DM.")

    @navidromeset_user.command(name="unlink")
    async def navidromeset_user_unlink(self, ctx: commands.Context, member: discord.Member):
        """Remove only the Discord mapping; keep the Navidrome account."""
        group = self.config.guild(ctx.guild)
        accounts = await group.accounts()
        if accounts.pop(str(member.id), None) is None:
            return await ctx.send("That member has no linked Navidrome account in this server.")
        await group.accounts.set(accounts)
        await ctx.send("Removed the Discord mapping. The Navidrome account was not deleted.")

    @navidromeset_user.command(name="delete")
    async def navidromeset_user_delete(
        self, ctx: commands.Context, member: discord.Member, confirmation: str = ""
    ):
        """Permanently delete a linked Navidrome account; append `confirm`."""
        if confirmation.casefold() != "confirm":
            return await ctx.send(
                f"This permanently deletes the linked Navidrome user. Run "
                f"`{ctx.clean_prefix}navidromeset user delete {member} confirm` to continue."
            )
        group = self.config.guild(ctx.guild)
        accounts = await group.accounts()
        account = accounts.get(str(member.id))
        if not account:
            return await ctx.send("That member has no linked Navidrome account in this server.")
        try:
            _, client = await self._guild_client(ctx.guild)
            await client.delete_user(str(account.get("id") or ""))
        except NavidromeError as exc:
            return await ctx.send(f"Navidrome user deletion failed: {exc}")
        accounts.pop(str(member.id), None)
        await group.accounts.set(accounts)
        await ctx.send("Deleted the Navidrome user and removed its Discord mapping.")

    async def _update_linked_user(
        self, ctx: commands.Context, member: discord.Member, **changes: Any
    ):
        account = (await self.config.guild(ctx.guild).accounts()).get(str(member.id))
        if not account:
            return await ctx.send("That member has no linked Navidrome account in this server.")
        try:
            _, client = await self._guild_client(ctx.guild)
            user = await client.user_by_username(str(account.get("username") or ""))
            if not user:
                return await ctx.send("The linked Navidrome user no longer exists.")
            await client.update_user(user, **changes)
        except NavidromeError as exc:
            return await ctx.send(f"Navidrome user update failed: {exc}")
        await ctx.send(f"Updated {member.mention}'s Navidrome account.")

    @navidromeset.command(name="status", aliases=("settings",))
    @commands.bot_has_permissions(embed_links=True)
    async def navidromeset_status(self, ctx: commands.Context):
        """Show this server's Navidrome settings."""
        await self._send_settings(ctx)

    async def _send_settings(self, ctx: commands.Context):
        await ctx.send(embed=await self.setup_embed(ctx.guild))
