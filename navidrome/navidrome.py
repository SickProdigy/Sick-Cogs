import asyncio
import datetime
import io
import logging
from typing import Any, Dict, List, Optional, Tuple, Union

import aiohttp
import discord
from discord.ext import tasks
from redbot.core import Config, checks, commands
from redbot.core.bot import Red
from redbot.core.utils import can_user_send_messages_in

from .client import NavidromeClient, NavidromeError, validate_base_url


log = logging.getLogger("red.sick-cogs.Navidrome")
CONFIG_IDENTIFIER = 9172048261
TOKEN_PREFIX = "navidrome_"
USER_AGENT = "Sick-Cogs-Navidrome/0.1.0"
GuildMessageable = Union[discord.TextChannel, discord.VoiceChannel, discord.StageChannel, discord.Thread]


def utc_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def safe_profile_name(value: str) -> str:
    name = value.strip().lower()
    if not name or len(name) > 32 or not all(char.isalnum() or char in "_-" for char in name):
        raise ValueError("Connection names may contain only letters, numbers, underscores, and hyphens.")
    return name


class Navidrome(commands.Cog):
    """Connect each Discord server to its own approved Navidrome library."""

    __author__ = ["SickProdigy"]
    __version__ = "0.1.0"

    default_global = {"connections": {}}
    default_guild = {
        "connection": None,
        "announcement_enabled": False,
        "announcement_channel_id": None,
        "interval_minutes": 60,
        "next_check_at": None,
        "announced_album_ids": [],
        "last_success_at": None,
    }

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=CONFIG_IDENTIFIER, force_registration=True)
        self.config.register_global(**self.default_global)
        self.config.register_guild(**self.default_guild)
        self.session: Optional[aiohttp.ClientSession] = None
        self.poll_loop.start()

    async def red_delete_data_for_user(self, **kwargs):
        """This phase stores no member data."""
        return

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

    async def _guild_client(self, guild: discord.Guild) -> Tuple[str, NavidromeClient]:
        name = await self.config.guild(guild).connection()
        if not name:
            raise NavidromeError("This server has not selected a Navidrome connection.")
        return name, await self._client(name)

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
            if settings.get("announcement_enabled") and settings.get("connection") and self._due(settings):
                due.setdefault(settings["connection"], []).append(guild)
        for name, guilds in due.items():
            try:
                client = await self._client(name)
                albums = await client.newest_albums(25)
                for guild in guilds:
                    try:
                        await self._check_guild(guild, albums, client)
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        log.exception("Navidrome delivery failed for guild %s", guild.id)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("Navidrome poll failed for connection %s: %s", name, type(exc).__name__)

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

    @commands.group(name="navidromeset", invoke_without_command=True)
    @commands.guild_only()
    @checks.admin_or_permissions(manage_guild=True)
    async def navidromeset(self, ctx: commands.Context):
        """Configure this server's Navidrome connection and announcements."""
        await self._send_settings(ctx)

    @navidromeset.command(name="connection")
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
        await self.config.guild(ctx.guild).connection.set(name)
        await self.config.guild(ctx.guild).announcement_enabled.set(False)
        await self.config.guild(ctx.guild).announced_album_ids.set([])
        await ctx.send(f"This server now uses Navidrome connection `{name}`. Announcements remain disabled.")

    @navidromeset.command(name="disconnect")
    async def navidromeset_disconnect(self, ctx: commands.Context):
        """Disconnect this Discord server without deleting owner credentials."""
        await self.config.guild(ctx.guild).clear()
        await ctx.send("Disconnected this server from Navidrome and cleared its announcement history.")

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
        settings = await self.config.guild(ctx.guild).all()
        if not settings.get("connection") or not settings.get("announcement_channel_id"):
            return await ctx.send("Select a connection and announcement channel first.")
        try:
            client = await self._client(settings["connection"])
            albums = await client.newest_albums(25)
        except NavidromeError as exc:
            return await ctx.send(f"Connection test failed: {exc}")
        baseline = [str(album["id"]) for album in albums if album.get("id")]
        await self.config.guild(ctx.guild).announced_album_ids.set(baseline[-500:])
        await self.config.guild(ctx.guild).announcement_enabled.set(True)
        await self.config.guild(ctx.guild).last_success_at.set(utc_now().isoformat())
        await self._set_next_check(ctx.guild, int(settings.get("interval_minutes", 60)))
        await ctx.send("Recently added album announcements are enabled. Current albums were recorded as the baseline and will not flood the channel.")

    @navidromeset.command(name="disable")
    async def navidromeset_disable(self, ctx: commands.Context):
        """Disable scheduled album announcements."""
        await self.config.guild(ctx.guild).announcement_enabled.set(False)
        await ctx.send("Recently added album announcements are disabled.")

    @navidromeset.command(name="preview", aliases=("test",))
    @commands.bot_has_permissions(embed_links=True)
    async def navidromeset_preview(self, ctx: commands.Context):
        """Preview the newest album without changing announcement history."""
        settings = await self.config.guild(ctx.guild).all()
        channel_id = settings.get("announcement_channel_id")
        if not settings.get("connection") or not channel_id:
            return await ctx.send("Select a connection and announcement channel first.")
        channel = await self._channel(ctx.guild, int(channel_id))
        if not channel:
            return await ctx.send("I cannot send messages in the configured channel.")
        try:
            client = await self._client(settings["connection"])
            albums = await client.newest_albums(1)
        except NavidromeError as exc:
            return await ctx.send(str(exc))
        if not albums:
            return await ctx.send("Navidrome returned no albums to preview.")
        await self.send_album(channel, albums[0], client, content="Navidrome announcement preview")
        await ctx.send(f"Sent a preview to {channel.mention}. Announcement history was not changed.")

    @navidromeset.command(name="status", aliases=("settings",))
    @commands.bot_has_permissions(embed_links=True)
    async def navidromeset_status(self, ctx: commands.Context):
        """Show this server's Navidrome settings."""
        await self._send_settings(ctx)

    async def _send_settings(self, ctx: commands.Context):
        settings = await self.config.guild(ctx.guild).all()
        channel = ctx.guild.get_channel_or_thread(settings["announcement_channel_id"]) if settings.get("announcement_channel_id") else None
        embed = discord.Embed(title="Navidrome settings", colour=discord.Colour.blurple())
        embed.add_field(name="Connection", value=settings.get("connection") or "Not selected", inline=True)
        embed.add_field(name="Announcements", value="Enabled" if settings.get("announcement_enabled") else "Disabled", inline=True)
        embed.add_field(name="Channel", value=channel.mention if channel else "Not set", inline=True)
        embed.add_field(name="Interval", value=f"{settings.get('interval_minutes', 60)} minutes", inline=True)
        embed.add_field(name="Last successful check", value=settings.get("last_success_at") or "Never", inline=False)
        await ctx.send(embed=embed)
