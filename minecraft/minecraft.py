"""Minecraft Java and Bedrock server status for Red."""

import asyncio
import datetime
import logging
import re
from typing import Optional

import discord
from discord.ext import tasks
from redbot.core import Config, commands
from redbot.core.bot import Red
from redbot.core.utils import can_user_send_messages_in

from .network import MinecraftQueryError, normalize_host
from .protocol import query_bedrock, query_java


log = logging.getLogger("red.sick-cogs.Minecraft")
CONFIG_IDENTIFIER = 6202609250214001
MIN_INTERVAL_MINUTES = 5
MAX_INTERVAL_MINUTES = 1440
_NAME_RE = re.compile(r"^[a-z0-9_-]{1,32}$")


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


def clean_text(value, *, limit=512):
    text = re.sub(r"§[0-9A-FK-OR]", "", str(value or "Unknown"), flags=re.IGNORECASE)
    text = discord.utils.escape_markdown(text)
    text = "".join(character for character in text if character.isprintable())
    return text[:limit] or "Unknown"


class Minecraft(commands.Cog):
    """Show safe status cards for Java and Bedrock Minecraft servers."""

    __author__ = ["SickProdigy"]
    __version__ = "0.1.0"

    guild_defaults = {
        "servers": {},
        "default_server": None,
        "monitoring_enabled": False,
        "announcement_channel_id": None,
        "announcement_role_id": None,
        "interval_minutes": 5,
        "last_poll_at": None,
        "show_player_sample": False,
    }

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=CONFIG_IDENTIFIER, force_registration=True)
        self.config.register_guild(**self.guild_defaults)
        self.config.register_global(private_hosts=[])
        self._locks = {}
        self.poll_loop.start()

    async def red_delete_data_for_user(self, **kwargs):
        """This cog stores no member data."""
        return

    def cog_unload(self):
        self.poll_loop.cancel()

    async def private_hosts(self):
        return await self.config.private_hosts()

    async def query_server(self, record):
        try:
            if record["edition"] == "java":
                result = await query_java(
                    record["host"],
                    record.get("port"),
                    private_hosts=await self.private_hosts(),
                )
            else:
                result = await query_bedrock(
                    record["host"],
                    int(record.get("port") or 19132),
                    private_hosts=await self.private_hosts(),
                )
        except (MinecraftQueryError, ValueError) as error:
            return None, str(error)
        result["checked_at"] = utc_now().isoformat()
        return result, None

    @staticmethod
    def connect_address(record):
        default = 25565 if record["edition"] == "java" else 19132
        port = record.get("port")
        return record["host"] if port in (None, default) else f"{record['host']}:{port}"

    def status_embed(self, name, record, status, error=None, *, show_sample=False):
        address = self.connect_address(record)
        edition = str((status or {}).get("edition") or record["edition"]).title()
        if status:
            embed = discord.Embed(
                title=f"{clean_text(status.get('motd'), limit=180)}",
                description=f"**Online • {edition}**\n`{address}`",
                color=discord.Color.green(),
                timestamp=utc_now(),
            )
            embed.add_field(
                name="Players",
                value=f"{int(status.get('players', 0))}/{int(status.get('max_players', 0))}",
            )
            embed.add_field(name="Version", value=clean_text(status.get("version"), limit=128))
            embed.add_field(name="Protocol", value=str(int(status.get("protocol", 0))))
            embed.add_field(name="Latency", value=f"{int(status.get('latency_ms', 0))} ms")
            if status.get("game_mode"):
                embed.add_field(name="Game mode", value=clean_text(status["game_mode"], limit=128))
            if status.get("motd_line_2"):
                embed.add_field(name="MOTD line 2", value=clean_text(status["motd_line_2"]), inline=False)
            if show_sample and status.get("sample"):
                embed.add_field(
                    name="Player sample",
                    value=", ".join(clean_text(player, limit=64) for player in status["sample"])[:1024],
                    inline=False,
                )
            embed.set_footer(text=f"Server key: {name}")
            return embed
        cached = record.get("last_good")
        embed = discord.Embed(
            title=clean_text((cached or {}).get("motd") or name, limit=180),
            description=f"**Unreachable • {edition}**\n`{address}`",
            color=discord.Color.red(),
            timestamp=utc_now(),
        )
        embed.add_field(name="Reason", value=clean_text(error or "Query failed"), inline=False)
        if cached:
            embed.add_field(name="Last verified", value=cached.get("checked_at") or "Previously online")
            embed.add_field(
                name="Last players",
                value=f"{int(cached.get('players', 0))}/{int(cached.get('max_players', 0))}",
            )
        embed.set_footer(text=f"Server key: {name} • cached data is stale")
        return embed

    async def get_channel(self, guild, channel_id):
        channel = guild.get_channel_or_thread(int(channel_id))
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(int(channel_id))
            except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                return None
        if getattr(channel, "guild", None) != guild or not can_user_send_messages_in(guild.me, channel):
            return None
        return channel

    async def poll_guild(self, guild, *, force=False):
        lock = self._locks.setdefault(guild.id, asyncio.Lock())
        async with lock:
            group = self.config.guild(guild)
            settings = await group.all()
            if not settings["monitoring_enabled"] and not force:
                return 0
            interval = max(MIN_INTERVAL_MINUTES, int(settings["interval_minutes"]))
            if not force and not due(settings.get("last_poll_at"), interval):
                return 0
            channel = None
            if settings.get("announcement_channel_id"):
                channel = await self.get_channel(guild, settings["announcement_channel_id"])
            role = guild.get_role(settings["announcement_role_id"]) if settings.get("announcement_role_id") else None
            sent = 0
            servers = settings.get("servers", {})
            for name, record in servers.items():
                status, error = await self.query_server(record)
                state = "online" if status else "offline"
                record["last_state"] = state
                if status:
                    record["last_good"] = status
                announced = record.get("last_announced_state")
                if announced is None:
                    record["last_announced_state"] = state
                elif channel and announced != state:
                    try:
                        await channel.send(
                            content=role.mention if role else None,
                            embed=self.status_embed(
                                name,
                                record,
                                status,
                                error,
                                show_sample=settings["show_player_sample"],
                            ),
                            allowed_mentions=discord.AllowedMentions(
                                everyone=False,
                                users=False,
                                roles=bool(role),
                            ),
                        )
                    except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                        log.warning("Could not publish Minecraft transition for %s in guild %s", name, guild.id)
                    else:
                        record["last_announced_state"] = state
                        sent += 1
            await group.servers.set(servers)
            await group.last_poll_at.set(utc_now().isoformat())
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
                log.exception("Minecraft polling failed for guild %s", guild.id)

    @poll_loop.before_loop
    async def before_poll_loop(self):
        await self.bot.wait_until_red_ready()

    async def selected_server(self, ctx, name=None):
        settings = await self.config.guild(ctx.guild).all()
        key = str(name or settings.get("default_server") or "").casefold()
        record = settings.get("servers", {}).get(key)
        if record is None:
            await ctx.send(f"Choose a configured server with `{ctx.clean_prefix}minecraft servers`.")
            return None, None
        return key, record

    @commands.group(name="minecraft", aliases=["mc"], invoke_without_command=True)
    @commands.guild_only()
    async def minecraft(self, ctx, server: Optional[str] = None):
        """Show Minecraft server status."""
        await ctx.invoke(self.minecraft_status, server=server)

    @minecraft.command(name="status")
    async def minecraft_status(self, ctx, server: Optional[str] = None):
        name, record = await self.selected_server(ctx, server)
        if record is None:
            return
        status, error = await self.query_server(record)
        if status:
            record["last_good"] = status
            record["last_state"] = "online"
        async with self.config.guild(ctx.guild).servers() as servers:
            if name in servers:
                servers[name].update(record)
        show_sample = await self.config.guild(ctx.guild).show_player_sample()
        await ctx.send(
            embed=self.status_embed(name, record, status, error, show_sample=show_sample),
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @minecraft.command(name="servers")
    async def minecraft_servers(self, ctx):
        settings = await self.config.guild(ctx.guild).all()
        lines = []
        for name, record in sorted(settings.get("servers", {}).items()):
            marker = " (default)" if name == settings.get("default_server") else ""
            lines.append(
                f"**{name}**{marker} — {record['edition'].title()} — "
                f"`{self.connect_address(record)}`"
            )
        await ctx.send(
            embed=discord.Embed(
                title="Minecraft servers",
                description="\n".join(lines) or "No servers are configured.",
                color=discord.Color.blurple(),
            ),
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @minecraft.command(name="connect")
    async def minecraft_connect(self, ctx, server: Optional[str] = None):
        name, record = await self.selected_server(ctx, server)
        if record is None:
            return
        await ctx.send(
            f"**{name}** ({record['edition'].title()}) — `{self.connect_address(record)}`",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @commands.group(name="minecraftset", aliases=["mcset"], invoke_without_command=True)
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def minecraftset(self, ctx):
        """Configure Minecraft servers and transition announcements."""
        await ctx.invoke(self.minecraftset_info)

    @minecraftset.command(name="info", aliases=["settings"])
    async def minecraftset_info(self, ctx):
        settings = await self.config.guild(ctx.guild).all()
        channel = ctx.guild.get_channel_or_thread(settings["announcement_channel_id"]) if settings["announcement_channel_id"] else None
        embed = discord.Embed(title="Minecraft settings", color=discord.Color.blurple())
        embed.add_field(name="Servers", value=str(len(settings.get("servers", {}))))
        embed.add_field(name="Default", value=settings.get("default_server") or "None")
        embed.add_field(name="Monitoring", value="Running" if settings["monitoring_enabled"] else "Stopped")
        embed.add_field(name="Channel", value=channel.mention if channel else "Not configured")
        embed.add_field(name="Interval", value=f"{settings['interval_minutes']} minutes")
        embed.add_field(name="Player sample", value="Shown" if settings["show_player_sample"] else "Hidden")
        await ctx.send(embed=embed)

    @minecraftset.command(name="add")
    async def minecraftset_add(
        self,
        ctx,
        name: str,
        edition: str,
        host: str,
        port: int = 0,
    ):
        """Add a Java or Bedrock server after a successful safe status query."""
        name = name.casefold().strip()
        edition = edition.casefold().strip()
        if not _NAME_RE.fullmatch(name):
            await ctx.send("Server names use 1–32 lowercase letters, numbers, underscores, or hyphens.")
            return
        if edition not in {"java", "bedrock"}:
            await ctx.send("Edition must be `java` or `bedrock`.")
            return
        if port and not 1 <= port <= 65535:
            await ctx.send("The port must be from 1 to 65535, or 0 for the edition default.")
            return
        try:
            host = normalize_host(host)
            configured_port = None if edition == "java" and port == 0 else (port or 19132)
            record = {"edition": edition, "host": host, "port": configured_port}
            status, error = await self.query_server(record)
            if status is None:
                raise MinecraftQueryError(error)
        except (ValueError, MinecraftQueryError) as error:
            await ctx.send(f"That server was not saved: {error}")
            return
        record.update(
            last_state="online",
            last_announced_state="online",
            last_good=status,
        )
        lock = self._locks.setdefault(ctx.guild.id, asyncio.Lock())
        async with lock:
            async with self.config.guild(ctx.guild).servers() as servers:
                servers[name] = record
            if not await self.config.guild(ctx.guild).default_server():
                await self.config.guild(ctx.guild).default_server.set(name)
        await ctx.send(
            f"Saved **{name}** as a {edition.title()} server at "
            f"`{self.connect_address(record)}` after a successful status query."
        )

    @minecraftset.command(name="remove")
    async def minecraftset_remove(self, ctx, name: str):
        name = name.casefold().strip()
        group = self.config.guild(ctx.guild)
        lock = self._locks.setdefault(ctx.guild.id, asyncio.Lock())
        async with lock:
            async with group.servers() as servers:
                removed = servers.pop(name, None) is not None
                remaining = list(servers)
            if await group.default_server() == name:
                await group.default_server.set(remaining[0] if remaining else None)
        await ctx.send(f"Removed **{name}**." if removed else f"No server named **{name}** exists.")

    @minecraftset.command(name="default")
    async def minecraftset_default(self, ctx, name: str):
        name = name.casefold().strip()
        if name not in await self.config.guild(ctx.guild).servers():
            await ctx.send("That server is not configured.")
            return
        await self.config.guild(ctx.guild).default_server.set(name)
        await ctx.send(f"**{name}** is now the default Minecraft server.")

    @minecraftset.command(name="channel")
    async def minecraftset_channel(self, ctx, channel: Optional[discord.TextChannel] = None):
        group = self.config.guild(ctx.guild)
        if channel and not can_user_send_messages_in(ctx.guild.me, channel):
            await ctx.send("I cannot send messages in that channel.")
            return
        await group.announcement_channel_id.set(channel.id if channel else None)
        if channel is None:
            await group.monitoring_enabled.set(False)
        await ctx.send(
            f"Minecraft transitions will post in {channel.mention}."
            if channel
            else "Minecraft monitoring stopped and its channel was cleared."
        )

    @minecraftset.command(name="monitoring")
    async def minecraftset_monitoring(self, ctx, enabled: bool):
        group = self.config.guild(ctx.guild)
        if enabled and not await group.announcement_channel_id():
            await ctx.send("Set an announcement channel first.")
            return
        if enabled and not await group.servers():
            await ctx.send("Add at least one server first.")
            return
        await group.monitoring_enabled.set(enabled)
        if enabled:
            await self.poll_guild(ctx.guild, force=True)
        await ctx.send(f"Minecraft monitoring is now {'running' if enabled else 'stopped'}.")

    @minecraftset.command(name="interval")
    async def minecraftset_interval(self, ctx, minutes: int):
        if not MIN_INTERVAL_MINUTES <= minutes <= MAX_INTERVAL_MINUTES:
            await ctx.send(f"Choose {MIN_INTERVAL_MINUTES} to {MAX_INTERVAL_MINUTES} minutes.")
            return
        await self.config.guild(ctx.guild).interval_minutes.set(minutes)
        await ctx.send(f"Minecraft servers will be checked every {minutes} minutes.")

    @minecraftset.command(name="role")
    async def minecraftset_role(self, ctx, role: Optional[discord.Role] = None):
        await self.config.guild(ctx.guild).announcement_role_id.set(role.id if role else None)
        await ctx.send(f"Minecraft transitions will mention {role.mention}." if role else "Minecraft role cleared.")

    @minecraftset.command(name="playersample")
    async def minecraftset_playersample(self, ctx, enabled: bool):
        await self.config.guild(ctx.guild).show_player_sample.set(enabled)
        await ctx.send(f"Java player samples are now {'shown' if enabled else 'hidden'}.")

    @minecraftset.command(name="check")
    async def minecraftset_check(self, ctx):
        sent = await self.poll_guild(ctx.guild, force=True)
        await ctx.send(f"Minecraft check complete; published {sent} transition(s).")

    @commands.group(name="minecraftowner", aliases=["mcowner"], invoke_without_command=True)
    @commands.is_owner()
    async def minecraftowner(self, ctx):
        """Manage bot-owner-only Minecraft network policy."""
        await ctx.send("Use the privatehost add/remove/list subcommands.")

    @minecraftowner.group(name="privatehost", invoke_without_command=True)
    async def minecraftowner_privatehost(self, ctx):
        hosts = await self.private_hosts()
        await ctx.send(
            "Approved private/LAN hosts: " + ", ".join(f"`{host}`" for host in hosts)
            if hosts
            else "No private/LAN Minecraft hosts are approved."
        )

    @minecraftowner_privatehost.command(name="add")
    async def minecraftowner_privatehost_add(self, ctx, host: str):
        try:
            host = normalize_host(host)
        except ValueError as error:
            await ctx.send(str(error))
            return
        async with self.config.private_hosts() as hosts:
            if host not in hosts:
                hosts.append(host)
                hosts.sort()
        await ctx.send(f"Approved `{host}` for private/LAN Minecraft resolution.")

    @minecraftowner_privatehost.command(name="remove")
    async def minecraftowner_privatehost_remove(self, ctx, host: str):
        try:
            host = normalize_host(host)
        except ValueError as error:
            await ctx.send(str(error))
            return
        async with self.config.private_hosts() as hosts:
            removed = host in hosts
            if removed:
                hosts.remove(host)
        await ctx.send(f"Removed `{host}`." if removed else f"`{host}` was not approved.")
