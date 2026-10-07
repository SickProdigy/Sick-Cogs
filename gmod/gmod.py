"""Garry's Mod server status and official Steam announcements."""

import asyncio
import datetime
import logging
import re
from typing import Optional

import aiohttp
import discord
from discord.ext import tasks
from redbot.core import Config, commands
from redbot.core.bot import Red
from redbot.core.utils import can_user_send_messages_in

from .query import A2SClient, A2SError, normalize_host
from .steam import (
    ANNOUNCEMENTS_URL,
    USER_AGENT,
    SteamNewsClient,
    SteamNewsError,
    image_url,
    item_id,
    new_items,
    plain_text,
    recent_items,
)


log = logging.getLogger("red.sick-cogs.GMod")
CONFIG_IDENTIFIER = 6202609250215001
MIN_INTERVAL_MINUTES = 5
MAX_INTERVAL_MINUTES = 1440
MAX_POSTED_IDS = 500
_NAME_RE = re.compile(r"^[a-z0-9_-]{1,32}$")


def utc_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def due(last_poll_at, interval_minutes: int) -> bool:
    if not last_poll_at:
        return True
    try:
        parsed = datetime.datetime.fromisoformat(last_poll_at)
    except (TypeError, ValueError):
        return True
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return utc_now() >= parsed + datetime.timedelta(minutes=interval_minutes)


def clean_text(value, *, limit=256) -> str:
    text = discord.utils.escape_markdown(str(value or "Unknown"))
    text = "".join(character for character in text if character.isprintable())
    return text[:limit] or "Unknown"


class GMod(commands.Cog):
    """Monitor Garry's Mod servers and publish official Steam announcements."""

    __author__ = ["SickProdigy"]
    __version__ = "0.1.1"

    guild_defaults = {
        "servers": {},
        "default_server": None,
        "status_enabled": False,
        "status_channel_id": None,
        "status_role_id": None,
        "status_interval_minutes": 5,
        "status_last_poll_at": None,
        "updates_enabled": False,
        "updates_channel_id": None,
        "updates_role_id": None,
        "updates_interval_minutes": 10,
        "updates_last_poll_at": None,
        "updates_last_success_at": None,
        "updates_posted_ids": [],
    }

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=CONFIG_IDENTIFIER, force_registration=True)
        self.config.register_guild(**self.guild_defaults)
        self.config.register_global(private_hosts=[])
        self.query = A2SClient(timeout=3.0)
        self.session: Optional[aiohttp.ClientSession] = None
        self._status_locks = {}
        self._updates_locks = {}
        self.poll_loop.start()

    async def red_delete_data_for_user(self, **kwargs):
        """This cog stores no member data."""
        return

    def cog_unload(self):
        self.poll_loop.cancel()
        if self.session and not self.session.closed:
            self.bot.loop.create_task(self.session.close())

    async def steam_client(self):
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30),
                headers={"User-Agent": USER_AGENT},
            )
        return SteamNewsClient(self.session)

    async def private_hosts(self):
        return await self.config.private_hosts()

    async def get_channel(self, guild, channel_id):
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

    @staticmethod
    def connect_address(server: dict) -> str:
        return f"{server['host']}:{int(server['port'])}"

    def status_embed(self, name: str, server: dict, status: Optional[dict], error=None):
        address = self.connect_address(server)
        if status:
            embed = discord.Embed(
                title=clean_text(status.get("name") or name),
                description=f"**Online** • `{address}`",
                color=discord.Color.green(),
                timestamp=utc_now(),
            )
            embed.add_field(
                name="Players",
                value=f"{int(status.get('players', 0))}/{int(status.get('max_players', 0))} "
                f"({int(status.get('bots', 0))} bots)",
            )
            embed.add_field(name="Map", value=clean_text(status.get("map"), limit=128))
            embed.add_field(name="Game", value=clean_text(status.get("game"), limit=128))
            embed.add_field(name="Version", value=clean_text(status.get("version"), limit=128))
            embed.add_field(name="Latency", value=f"{int(status.get('latency_ms', 0))} ms")
            embed.add_field(name="VAC", value="Enabled" if status.get("vac") else "Disabled")
            embed.add_field(name="Password", value="Required" if status.get("password") else "No")
            embed.add_field(
                name="Connect",
                value=f"[Open Steam](steam://connect/{address})",
                inline=False,
            )
            embed.set_footer(text=f"Server key: {name}")
            return embed

        last_good = server.get("last_good")
        embed = discord.Embed(
            title=clean_text((last_good or {}).get("name") or name),
            description=f"**Unreachable** • `{address}`",
            color=discord.Color.red(),
            timestamp=utc_now(),
        )
        embed.add_field(name="Reason", value=clean_text(error or "Query failed", limit=512), inline=False)
        if last_good:
            checked_at = last_good.get("checked_at")
            embed.add_field(
                name="Last verified",
                value=checked_at or "Previously online",
                inline=False,
            )
            embed.add_field(name="Last map", value=clean_text(last_good.get("map"), limit=128))
            embed.add_field(
                name="Last players",
                value=f"{int(last_good.get('players', 0))}/{int(last_good.get('max_players', 0))}",
            )
        embed.set_footer(text=f"Server key: {name} • cached data is stale")
        return embed

    async def query_server(self, name: str, server: dict):
        try:
            status = await self.query.info(
                server["host"],
                int(server["port"]),
                private_hosts=await self.private_hosts(),
            )
        except (A2SError, ValueError) as error:
            return None, str(error)
        status["checked_at"] = utc_now().isoformat()
        return status, None

    async def poll_status(self, guild, *, force=False):
        lock = self._status_locks.setdefault(guild.id, asyncio.Lock())
        async with lock:
            group = self.config.guild(guild)
            settings = await group.all()
            if not settings["status_enabled"] and not force:
                return 0
            interval = max(MIN_INTERVAL_MINUTES, int(settings["status_interval_minutes"]))
            if not force and not due(settings.get("status_last_poll_at"), interval):
                return 0
            channel = None
            if settings.get("status_channel_id"):
                channel = await self.get_channel(guild, settings["status_channel_id"])
            role = guild.get_role(settings["status_role_id"]) if settings.get("status_role_id") else None
            changed = False
            sent = 0
            servers = settings.get("servers", {})
            for name, server in servers.items():
                status, error = await self.query_server(name, server)
                state = "online" if status else "offline"
                server["last_state"] = state
                if status:
                    server["last_good"] = status
                announced = server.get("last_announced_state")
                if announced is None:
                    server["last_announced_state"] = state
                elif channel and announced != state:
                    try:
                        await channel.send(
                            content=role.mention if role else None,
                            embed=self.status_embed(name, server, status, error),
                            allowed_mentions=discord.AllowedMentions(
                                everyone=False,
                                users=False,
                                roles=bool(role),
                            ),
                        )
                    except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                        log.warning("Could not publish GMod status transition for %s in guild %s", name, guild.id)
                    else:
                        server["last_announced_state"] = state
                        sent += 1
                changed = True
            if changed:
                await group.servers.set(servers)
            await group.status_last_poll_at.set(utc_now().isoformat())
            return sent

    def news_embed(self, item: dict):
        timestamp = datetime.datetime.fromtimestamp(
            int(item.get("date") or 0),
            tz=datetime.timezone.utc,
        )
        embed = discord.Embed(
            title=clean_text(item.get("title") or "Garry's Mod announcement"),
            url=str(item.get("url") or ANNOUNCEMENTS_URL),
            description=plain_text(item.get("contents") or "")
            or "Open the official Steam announcement for details.",
            color=discord.Color.from_rgb(58, 110, 165),
            timestamp=timestamp,
        )
        embed.set_author(name="Garry's Mod — Official Steam News", url=ANNOUNCEMENTS_URL)
        image = image_url(item.get("contents") or "")
        if image:
            embed.set_image(url=image)
        return embed

    async def seed_updates(self, guild):
        items = await (await self.steam_client()).fetch(count=100)
        identifiers = [item_id(item) for item in items if item_id(item)]
        group = self.config.guild(guild)
        await group.updates_posted_ids.set(identifiers[-MAX_POSTED_IDS:])
        await group.updates_last_poll_at.set(utc_now().isoformat())
        await group.updates_last_success_at.set(utc_now().isoformat())
        return len(identifiers)

    async def poll_updates(self, guild, *, force=False):
        lock = self._updates_locks.setdefault(guild.id, asyncio.Lock())
        async with lock:
            group = self.config.guild(guild)
            settings = await group.all()
            if not settings["updates_enabled"] and not force:
                return 0
            channel_id = settings.get("updates_channel_id")
            if not channel_id:
                return 0
            interval = max(MIN_INTERVAL_MINUTES, int(settings["updates_interval_minutes"]))
            if not force and not due(settings.get("updates_last_poll_at"), interval):
                return 0
            channel = await self.get_channel(guild, channel_id)
            if channel is None:
                await group.updates_last_poll_at.set(utc_now().isoformat())
                return 0
            items = await (await self.steam_client()).fetch(count=100)
            posted = [str(value) for value in settings.get("updates_posted_ids", [])]
            if not posted:
                posted = [item_id(item) for item in items if item_id(item)]
                await group.updates_posted_ids.set(posted[-MAX_POSTED_IDS:])
                await group.updates_last_poll_at.set(utc_now().isoformat())
                await group.updates_last_success_at.set(utc_now().isoformat())
                return 0
            role = guild.get_role(settings["updates_role_id"]) if settings.get("updates_role_id") else None
            sent = 0
            for item in new_items(items, posted):
                identifier = item_id(item)
                try:
                    await channel.send(
                        content=role.mention if role else None,
                        embed=self.news_embed(item),
                        allowed_mentions=discord.AllowedMentions(
                            everyone=False,
                            users=False,
                            roles=bool(role),
                        ),
                    )
                except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                    log.warning("Could not publish GMod Steam announcement %s", identifier)
                    break
                else:
                    posted.append(identifier)
                    sent += 1
            await group.updates_posted_ids.set(posted[-MAX_POSTED_IDS:])
            await group.updates_last_poll_at.set(utc_now().isoformat())
            await group.updates_last_success_at.set(utc_now().isoformat())
            return sent

    @tasks.loop(minutes=1)
    async def poll_loop(self):
        for guild in list(self.bot.guilds):
            if await self.bot.cog_disabled_in_guild(self, guild):
                continue
            try:
                await self.poll_status(guild)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("GMod status polling failed for guild %s", guild.id)
            try:
                await self.poll_updates(guild)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("GMod Steam polling failed for guild %s", guild.id)

    @poll_loop.before_loop
    async def before_poll_loop(self):
        await self.bot.wait_until_red_ready()

    async def selected_server(self, ctx, name=None):
        servers = await self.config.guild(ctx.guild).servers()
        key = str(name or await self.config.guild(ctx.guild).default_server() or "").casefold()
        if not key or key not in servers:
            await ctx.send(f"Choose a configured server with `{ctx.clean_prefix}gmod servers`.")
            return None, None
        return key, servers[key]

    @commands.group(name="gmod", invoke_without_command=True)
    @commands.guild_only()
    async def gmod(self, ctx, server: Optional[str] = None):
        """Show Garry's Mod server status."""
        await ctx.invoke(self.gmod_status, server=server)

    @gmod.command(name="status")
    async def gmod_status(self, ctx, server: Optional[str] = None):
        name, record = await self.selected_server(ctx, server)
        if record is None:
            return
        status, error = await self.query_server(name, record)
        if status:
            record["last_good"] = status
            record["last_state"] = "online"
        async with self.config.guild(ctx.guild).servers() as servers:
            if name in servers:
                servers[name].update(record)
        await ctx.send(
            embed=self.status_embed(name, record, status, error),
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @gmod.command(name="servers")
    async def gmod_servers(self, ctx):
        settings = await self.config.guild(ctx.guild).all()
        default = settings.get("default_server")
        lines = []
        for name, server in sorted(settings.get("servers", {}).items()):
            marker = " (default)" if name == default else ""
            lines.append(f"**{name}**{marker} — `{self.connect_address(server)}`")
        await ctx.send(
            embed=discord.Embed(
                title="Garry's Mod servers",
                description="\n".join(lines) or "No servers are configured.",
                color=discord.Color.blurple(),
            ),
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @gmod.command(name="connect")
    async def gmod_connect(self, ctx, server: Optional[str] = None):
        name, record = await self.selected_server(ctx, server)
        if record is None:
            return
        address = self.connect_address(record)
        await ctx.send(
            f"**{name}** — `{address}`\n<steam://connect/{address}>",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @gmod.command(name="updates")
    async def gmod_updates(self, ctx, count: int = 5):
        """Show 1 to 10 recent official Steam announcements."""
        if not 1 <= count <= 10:
            await ctx.send("Choose between 1 and 10 announcements.")
            return
        try:
            items = list(reversed(recent_items(await (await self.steam_client()).fetch(), count)))
        except SteamNewsError as error:
            await ctx.send(str(error))
            return
        lines = []
        for item in items:
            title = clean_text(item.get("title") or "Garry's Mod announcement", limit=180)
            lines.append(
                f"• <t:{int(item.get('date') or 0)}:D> — "
                f"[{title}]({str(item.get('url') or ANNOUNCEMENTS_URL)})"
            )
        await ctx.send(
            embed=discord.Embed(
                title="Recent Garry's Mod announcements",
                description="\n".join(lines) or "No announcements were found.",
                color=discord.Color.blurple(),
            )
        )

    @commands.group(name="gmodset", invoke_without_command=True)
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def gmodset(self, ctx):
        """Configure Garry's Mod status and Steam announcements."""
        await ctx.invoke(self.gmodset_info)

    @gmodset.command(name="info", aliases=["status"])
    async def gmodset_info(self, ctx):
        settings = await self.config.guild(ctx.guild).all()
        status_channel = ctx.guild.get_channel_or_thread(settings["status_channel_id"]) if settings["status_channel_id"] else None
        updates_channel = ctx.guild.get_channel_or_thread(settings["updates_channel_id"]) if settings["updates_channel_id"] else None
        embed = discord.Embed(title="Garry's Mod settings", color=discord.Color.blurple())
        embed.add_field(name="Servers", value=str(len(settings.get("servers", {}))))
        embed.add_field(name="Default", value=settings.get("default_server") or "None")
        embed.add_field(name="Status monitoring", value="Running" if settings["status_enabled"] else "Stopped")
        embed.add_field(name="Status channel", value=status_channel.mention if status_channel else "Not configured")
        embed.add_field(name="Steam updates", value="Running" if settings["updates_enabled"] else "Stopped")
        embed.add_field(name="Updates channel", value=updates_channel.mention if updates_channel else "Not configured")
        await ctx.send(embed=embed)

    @gmodset.command(name="add")
    async def gmodset_add(self, ctx, name: str, host: str, port: int = 27015):
        """Add or replace a named GMod server after a successful safe query."""
        name = name.casefold().strip()
        if not _NAME_RE.fullmatch(name):
            await ctx.send("Server names use 1–32 lowercase letters, numbers, underscores, or hyphens.")
            return
        try:
            host = normalize_host(host)
            result = await self.query.info(host, port, private_hosts=await self.private_hosts())
        except (ValueError, A2SError) as error:
            await ctx.send(f"That server was not saved: {error}")
            return
        record = {
            "host": host,
            "port": int(port),
            "last_state": "online",
            "last_announced_state": "online",
            "last_good": {**result, "checked_at": utc_now().isoformat()},
        }
        lock = self._status_locks.setdefault(ctx.guild.id, asyncio.Lock())
        async with lock:
            async with self.config.guild(ctx.guild).servers() as servers:
                servers[name] = record
            if not await self.config.guild(ctx.guild).default_server():
                await self.config.guild(ctx.guild).default_server.set(name)
        await ctx.send(f"Saved **{name}** at `{host}:{port}` after a successful A2S_INFO query.")

    @gmodset.command(name="remove")
    async def gmodset_remove(self, ctx, name: str):
        name = name.casefold().strip()
        group = self.config.guild(ctx.guild)
        removed = False
        lock = self._status_locks.setdefault(ctx.guild.id, asyncio.Lock())
        async with lock:
            async with group.servers() as servers:
                removed = servers.pop(name, None) is not None
                remaining = list(servers)
            if await group.default_server() == name:
                await group.default_server.set(remaining[0] if remaining else None)
        await ctx.send(f"Removed **{name}**." if removed else f"No server named **{name}** exists.")

    @gmodset.command(name="default")
    async def gmodset_default(self, ctx, name: str):
        name = name.casefold().strip()
        if name not in await self.config.guild(ctx.guild).servers():
            await ctx.send("That server is not configured.")
            return
        await self.config.guild(ctx.guild).default_server.set(name)
        await ctx.send(f"**{name}** is now the default GMod server.")

    @gmodset.command(name="statuschannel")
    async def gmodset_statuschannel(self, ctx, channel: Optional[discord.TextChannel] = None):
        if channel and not can_user_send_messages_in(ctx.guild.me, channel):
            await ctx.send("I cannot send messages in that channel.")
            return
        group = self.config.guild(ctx.guild)
        await group.status_channel_id.set(channel.id if channel else None)
        if channel is None:
            await group.status_enabled.set(False)
        await ctx.send(
            f"Status transitions will post in {channel.mention}."
            if channel
            else "Status monitoring was stopped and its channel cleared."
        )

    @gmodset.command(name="monitoring")
    async def gmodset_monitoring(self, ctx, enabled: bool):
        group = self.config.guild(ctx.guild)
        if enabled and not await group.status_channel_id():
            await ctx.send("Set a status channel first.")
            return
        if enabled and not await group.servers():
            await ctx.send("Add at least one server first.")
            return
        await group.status_enabled.set(enabled)
        if enabled:
            await self.poll_status(ctx.guild, force=True)
        await ctx.send(f"GMod status monitoring is now {'running' if enabled else 'stopped'}.")

    @gmodset.command(name="statusinterval")
    async def gmodset_statusinterval(self, ctx, minutes: int):
        if not MIN_INTERVAL_MINUTES <= minutes <= MAX_INTERVAL_MINUTES:
            await ctx.send(f"Choose {MIN_INTERVAL_MINUTES} to {MAX_INTERVAL_MINUTES} minutes.")
            return
        await self.config.guild(ctx.guild).status_interval_minutes.set(minutes)
        await ctx.send(f"GMod servers will be checked every {minutes} minutes.")

    @gmodset.command(name="statusrole")
    async def gmodset_statusrole(self, ctx, role: Optional[discord.Role] = None):
        await self.config.guild(ctx.guild).status_role_id.set(role.id if role else None)
        await ctx.send(f"Status changes will mention {role.mention}." if role else "Status role cleared.")

    @gmodset.command(name="updateschannel")
    async def gmodset_updateschannel(self, ctx, channel: Optional[discord.TextChannel] = None):
        group = self.config.guild(ctx.guild)
        if channel is None:
            await group.updates_enabled.set(False)
            await group.updates_channel_id.set(None)
            await ctx.send("Automatic GMod Steam updates stopped and the channel was cleared.")
            return
        if not can_user_send_messages_in(ctx.guild.me, channel):
            await ctx.send("I cannot send messages in that channel.")
            return
        try:
            seeded = await self.seed_updates(ctx.guild)
        except SteamNewsError as error:
            await ctx.send(f"The channel was not saved because Steam could not be reached: {error}")
            return
        await group.updates_channel_id.set(channel.id)
        await ctx.send(
            f"GMod Steam updates will use {channel.mention}. Seeded {seeded} current item(s); old posts will not flood the channel."
        )

    @gmodset.group(name="updates", invoke_without_command=True)
    async def gmodset_updates(self, ctx):
        running = await self.config.guild(ctx.guild).updates_enabled()
        await ctx.send(f"Automatic GMod Steam updates are {'running' if running else 'stopped'}.")

    @gmodset_updates.command(name="start")
    async def gmodset_updates_start(self, ctx):
        group = self.config.guild(ctx.guild)
        if not await group.updates_channel_id():
            await ctx.send("Set an updates channel first.")
            return
        if not await group.updates_posted_ids():
            try:
                await self.seed_updates(ctx.guild)
            except SteamNewsError as error:
                await ctx.send(f"Automatic posting was not started: {error}")
                return
        await group.updates_enabled.set(True)
        await ctx.send("Automatic GMod Steam update posting is now running.")

    @gmodset_updates.command(name="stop")
    async def gmodset_updates_stop(self, ctx):
        await self.config.guild(ctx.guild).updates_enabled.set(False)
        await ctx.send("Automatic GMod Steam update posting is now stopped.")

    @gmodset.command(name="updatesrole")
    async def gmodset_updatesrole(self, ctx, role: Optional[discord.Role] = None):
        await self.config.guild(ctx.guild).updates_role_id.set(role.id if role else None)
        await ctx.send(f"GMod updates will mention {role.mention}." if role else "Updates role cleared.")

    @gmodset.command(name="updatesinterval")
    async def gmodset_updatesinterval(self, ctx, minutes: int):
        if not MIN_INTERVAL_MINUTES <= minutes <= MAX_INTERVAL_MINUTES:
            await ctx.send(f"Choose {MIN_INTERVAL_MINUTES} to {MAX_INTERVAL_MINUTES} minutes.")
            return
        await self.config.guild(ctx.guild).updates_interval_minutes.set(minutes)
        await ctx.send(f"Steam will be checked every {minutes} minutes.")

    @gmodset.command(name="updatescheck")
    async def gmodset_updatescheck(self, ctx):
        try:
            sent = await self.poll_updates(ctx.guild, force=True)
        except SteamNewsError as error:
            await ctx.send(str(error))
            return
        await ctx.send(f"GMod Steam check complete; published {sent} new announcement(s).")

    @commands.group(name="gmodowner", invoke_without_command=True)
    @commands.is_owner()
    async def gmodowner(self, ctx):
        """Manage bot-owner-only GMod network policy."""
        await ctx.send("Use the privatehost add/remove/list subcommands.")

    @gmodowner.group(name="privatehost", invoke_without_command=True)
    async def gmodowner_privatehost(self, ctx):
        hosts = await self.private_hosts()
        await ctx.send(
            "Approved private/LAN hosts: " + ", ".join(f"`{host}`" for host in hosts)
            if hosts
            else "No private/LAN GMod hosts are approved."
        )

    @gmodowner_privatehost.command(name="add")
    async def gmodowner_privatehost_add(self, ctx, host: str):
        try:
            host = normalize_host(host)
        except ValueError as error:
            await ctx.send(str(error))
            return
        async with self.config.private_hosts() as hosts:
            if host not in hosts:
                hosts.append(host)
                hosts.sort()
        await ctx.send(f"Approved `{host}` for private/LAN GMod resolution.")

    @gmodowner_privatehost.command(name="remove")
    async def gmodowner_privatehost_remove(self, ctx, host: str):
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
