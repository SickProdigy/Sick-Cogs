"""Minecraft Java and Bedrock server status for Red."""

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

from .network import MinecraftQueryError, normalize_host
from .protocol import query_bedrock, query_java
from .updates import (
    CHANGELOG_URL,
    JAVA_PRERELEASE_URL,
    MAX_SEEN_UPDATE_IDS,
    PREVIEW_URL,
    filter_updates,
    parse_feedback_articles,
    sort_updates,
)


log = logging.getLogger("red.sick-cogs.Minecraft")
CONFIG_IDENTIFIER = 6202609250214001
MIN_INTERVAL_MINUTES = 5
MAX_INTERVAL_MINUTES = 1440
MAX_CONCURRENT_QUERIES = 8
DEFAULT_UPDATE_INTERVAL_MINUTES = 60
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
    __version__ = "0.1.3"

    guild_defaults = {
        "servers": {},
        "default_server": None,
        "monitoring_enabled": False,
        "announcement_channel_id": None,
        "announcement_role_id": None,
        "interval_minutes": 5,
        "last_poll_at": None,
        "show_player_sample": False,
        "updates_enabled": False,
        "updates_channel_id": None,
        "updates_role_id": None,
        "updates_interval_minutes": DEFAULT_UPDATE_INTERVAL_MINUTES,
        "updates_last_poll_at": None,
        "updates_java": True,
        "updates_bedrock": True,
        "updates_java_prerelease": False,
        "updates_bedrock_preview": False,
        "updates_seen_ids": [],
    }

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=CONFIG_IDENTIFIER, force_registration=True)
        self.config.register_guild(**self.guild_defaults)
        self.config.register_global(private_hosts=[])
        self._locks = {}
        self._update_cache = []
        self._update_cache_at = None
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
    def endpoint_key(record):
        edition = str(record.get("edition") or "").casefold()
        host = str(record.get("host") or "").strip().rstrip(".").casefold()
        raw_port = record.get("port")
        if edition == "bedrock":
            port = int(raw_port or 19132)
        else:
            port = int(raw_port) if raw_port is not None else None
        return edition, host, port

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

    async def poll_guild(self, guild, *, force=False, query_results=None):
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
                endpoint = self.endpoint_key(record)
                if query_results is None:
                    status, error = await self.query_server(record)
                elif endpoint in query_results:
                    status, error = query_results[endpoint]
                else:
                    continue
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

    async def poll_once(self):
        due_guilds = []
        endpoints = {}
        for guild in list(self.bot.guilds):
            if await self.bot.cog_disabled_in_guild(self, guild):
                continue
            try:
                settings = await self.config.guild(guild).all()
                if not settings.get("monitoring_enabled"):
                    continue
                interval = max(
                    MIN_INTERVAL_MINUTES, int(settings.get("interval_minutes", 5))
                )
                if not due(settings.get("last_poll_at"), interval):
                    continue
                due_guilds.append(guild)
                for record in settings.get("servers", {}).values():
                    endpoint = self.endpoint_key(record)
                    endpoints.setdefault(endpoint, dict(record))
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception(
                    "Could not prepare Minecraft poll for guild %s", guild.id
                )

        if not due_guilds:
            return

        semaphore = asyncio.Semaphore(MAX_CONCURRENT_QUERIES)

        async def query_endpoint(endpoint, record):
            try:
                async with semaphore:
                    result = await self.query_server(record)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Minecraft endpoint query failed for %r", endpoint)
                result = (None, "Unexpected query failure.")
            return endpoint, result

        query_results = dict(
            await asyncio.gather(
                *(
                    query_endpoint(endpoint, record)
                    for endpoint, record in endpoints.items()
                )
            )
        )
        for guild in due_guilds:
            try:
                await self.poll_guild(guild, query_results=query_results)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Minecraft polling failed for guild %s", guild.id)

    async def fetch_update_items(self, *, force=False):
        cache_at = getattr(self, "_update_cache_at", None)
        if not force and getattr(self, "_update_cache", None) and cache_at:
            if utc_now() < cache_at + datetime.timedelta(minutes=5):
                return list(self._update_cache)
        timeout = aiohttp.ClientTimeout(total=20)
        headers = {"User-Agent": "Sick-Cogs Minecraft/0.1.2"}
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            async def get_json(url):
                async with session.get(url) as response:
                    response.raise_for_status()
                    return await response.json(content_type=None)

            changelog, preview, java_prerelease = await asyncio.gather(
                get_json(CHANGELOG_URL),
                get_json(PREVIEW_URL),
                get_json(JAVA_PRERELEASE_URL),
            )
        items = parse_feedback_articles(changelog)
        items.extend(parse_feedback_articles(preview, preview=True))
        items.extend(parse_feedback_articles(java_prerelease, preview=True))
        result = sort_updates({item["id"]: item for item in items}.values())
        self._update_cache = result
        self._update_cache_at = utc_now()
        return list(result)

    def update_embed(self, item):
        labels = {
            "release": "Stable release",
            "snapshot": "Snapshot",
            "prerelease": "Prerelease",
            "release candidate": "Release candidate",
            "preview": "Preview / beta",
        }
        try:
            timestamp = datetime.datetime.fromisoformat(item["published_at"])
        except (TypeError, ValueError):
            timestamp = None
        embed = discord.Embed(
            title=clean_text(item["title"], limit=256),
            description=clean_text(item["summary"], limit=900),
            url=item["url"],
            color=discord.Color.green() if item["channel"] == "release" else discord.Color.orange(),
            timestamp=timestamp,
        )
        embed.add_field(name="Edition", value=item["edition"].title())
        embed.add_field(name="Channel", value=labels.get(item["channel"], item["channel"].title()))
        embed.add_field(name="Version", value=clean_text(item["version"], limit=128))
        embed.set_footer(text=f"Official source: {item['source']}")
        return embed

    async def poll_update_guild(self, guild, items, *, force=False, seed_only=False):
        lock = self._locks.setdefault(guild.id, asyncio.Lock())
        async with lock:
            group = self.config.guild(guild)
            settings = await group.all()
            if not settings.get("updates_enabled") and not force:
                return 0
            filtered = filter_updates(items, settings)
            seen = list(dict.fromkeys(settings.get("updates_seen_ids", [])))
            if not seen or seed_only:
                seeded = [item["id"] for item in filtered][-MAX_SEEN_UPDATE_IDS:]
                await group.updates_seen_ids.set(seeded)
                return 0
            channel_id = settings.get("updates_channel_id")
            channel = await self.get_channel(guild, channel_id) if channel_id else None
            if channel is None:
                return 0
            role = guild.get_role(settings["updates_role_id"]) if settings.get("updates_role_id") else None
            seen_set = set(seen)
            unseen = [item for item in filtered if item["id"] not in seen_set]
            sent = 0
            for item in sort_updates(unseen):
                try:
                    await channel.send(
                        content=role.mention if role else None,
                        embed=self.update_embed(item),
                        allowed_mentions=discord.AllowedMentions(
                            everyone=False, users=False, roles=bool(role)
                        ),
                    )
                except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                    log.warning("Could not publish Minecraft update %s in guild %s", item["id"], guild.id)
                    break
                seen.append(item["id"])
                sent += 1
            await group.updates_seen_ids.set(list(dict.fromkeys(seen))[-MAX_SEEN_UPDATE_IDS:])
            return sent

    async def poll_updates_once(self):
        due_guilds = []
        for guild in list(self.bot.guilds):
            if await self.bot.cog_disabled_in_guild(self, guild):
                continue
            try:
                settings = await self.config.guild(guild).all()
                interval = max(
                    MIN_INTERVAL_MINUTES,
                    int(settings.get("updates_interval_minutes", DEFAULT_UPDATE_INTERVAL_MINUTES)),
                )
                if settings.get("updates_enabled") and due(
                    settings.get("updates_last_poll_at"), interval
                ):
                    due_guilds.append(guild)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Could not prepare Minecraft update poll for guild %s", guild.id)
        if not due_guilds:
            return
        attempted_at = utc_now().isoformat()
        for guild in due_guilds:
            await self.config.guild(guild).updates_last_poll_at.set(attempted_at)
        try:
            items = await self.fetch_update_items()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Minecraft official update-source fetch failed")
            return
        for guild in due_guilds:
            try:
                await self.poll_update_guild(guild, items)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Minecraft update publishing failed for guild %s", guild.id)

    @tasks.loop(minutes=1)
    async def poll_loop(self):
        try:
            await self.poll_once()
            await self.poll_updates_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Minecraft polling cycle failed")

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

    @minecraft.group(name="updates", invoke_without_command=True)
    @commands.cooldown(2, 30, commands.BucketType.guild)
    async def minecraft_updates(self, ctx, number: int = 1):
        """Show the newest official Minecraft update, or the numbered recent update."""
        if number < 1 or number > 25:
            await ctx.send("Choose an update number from 1 to 25.")
            return
        try:
            items = sort_updates(await self.fetch_update_items(), reverse=True)
        except Exception:
            log.exception("Could not fetch Minecraft updates for a user command")
            await ctx.send("The official Minecraft update sources are unavailable right now.")
            return
        if number > len(items):
            await ctx.send("That recent update is not available from the official sources.")
            return
        await ctx.send(embed=self.update_embed(items[number - 1]), allowed_mentions=discord.AllowedMentions.none())

    @minecraft_updates.command(name="list")
    async def minecraft_updates_list(self, ctx, count: int = 5):
        """List recent official Minecraft updates."""
        count = max(1, min(count, 10))
        try:
            items = sort_updates(await self.fetch_update_items(), reverse=True)[:count]
        except Exception:
            log.exception("Could not fetch the Minecraft update list")
            await ctx.send("The official Minecraft update sources are unavailable right now.")
            return
        lines = [
            f"**{index}.** [{clean_text(item['title'], limit=180)}]({item['url']}) — "
            f"{item['edition'].title()} {item['channel'].title()}"
            for index, item in enumerate(items, 1)
        ]
        await ctx.send(
            embed=discord.Embed(title="Recent Minecraft updates", description="\n".join(lines), color=discord.Color.green()),
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

    @minecraftset.group(name="updates", invoke_without_command=True)
    async def minecraftset_updates(self, ctx):
        """Configure official Minecraft release announcements."""
        settings = await self.config.guild(ctx.guild).all()
        channel = ctx.guild.get_channel_or_thread(settings["updates_channel_id"]) if settings.get("updates_channel_id") else None
        role = ctx.guild.get_role(settings["updates_role_id"]) if settings.get("updates_role_id") else None
        embed = discord.Embed(title="Minecraft update announcements", color=discord.Color.green())
        embed.add_field(name="Status", value="Running" if settings["updates_enabled"] else "Stopped")
        embed.add_field(name="Channel", value=channel.mention if channel else "Not configured")
        embed.add_field(name="Role", value=role.mention if role else "None")
        embed.add_field(name="Interval", value=f"{settings['updates_interval_minutes']} minutes")
        embed.add_field(name="Java stable", value="On" if settings["updates_java"] else "Off")
        embed.add_field(name="Java prereleases", value="On" if settings["updates_java_prerelease"] else "Off")
        embed.add_field(name="Bedrock stable", value="On" if settings["updates_bedrock"] else "Off")
        embed.add_field(name="Bedrock previews", value="On" if settings["updates_bedrock_preview"] else "Off")
        await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())

    @minecraftset_updates.command(name="channel")
    async def minecraftset_updates_channel(self, ctx, channel: Optional[discord.TextChannel] = None):
        if channel and not can_user_send_messages_in(ctx.guild.me, channel):
            await ctx.send("I cannot send messages in that channel.")
            return
        group = self.config.guild(ctx.guild)
        await group.updates_channel_id.set(channel.id if channel else None)
        if channel is None:
            await group.updates_enabled.set(False)
        await ctx.send(f"Minecraft updates will post in {channel.mention}." if channel else "Minecraft update announcements stopped and their channel was cleared.")

    @minecraftset_updates.command(name="role")
    async def minecraftset_updates_role(self, ctx, role: Optional[discord.Role] = None):
        await self.config.guild(ctx.guild).updates_role_id.set(role.id if role else None)
        await ctx.send(f"Minecraft updates will mention {role.mention}." if role else "Minecraft update role cleared.")

    @minecraftset_updates.command(name="interval")
    async def minecraftset_updates_interval(self, ctx, minutes: int):
        if not MIN_INTERVAL_MINUTES <= minutes <= MAX_INTERVAL_MINUTES:
            await ctx.send(f"Choose {MIN_INTERVAL_MINUTES} to {MAX_INTERVAL_MINUTES} minutes.")
            return
        await self.config.guild(ctx.guild).updates_interval_minutes.set(minutes)
        await ctx.send(f"Official Minecraft sources will be checked every {minutes} minutes.")

    async def set_update_filter(self, ctx, key, enabled, label):
        group = self.config.guild(ctx.guild)
        if enabled:
            try:
                items = await self.fetch_update_items()
            except Exception:
                log.exception("Could not seed Minecraft update filter %s", key)
                await ctx.send("The official Minecraft update sources are unavailable; that filter was not enabled.")
                return
            settings = await group.all()
            settings[key] = True
            seen = list(dict.fromkeys(settings.get("updates_seen_ids", [])))
            seen.extend(item["id"] for item in filter_updates(items, settings))
            await group.updates_seen_ids.set(list(dict.fromkeys(seen))[-MAX_SEEN_UPDATE_IDS:])
        await group.set_raw(key, value=enabled)
        await ctx.send(f"{label} announcements are now {'enabled' if enabled else 'disabled'}.")

    @minecraftset_updates.command(name="java")
    async def minecraftset_updates_java(self, ctx, enabled: bool):
        await self.set_update_filter(ctx, "updates_java", enabled, "Java stable")

    @minecraftset_updates.command(name="bedrock")
    async def minecraftset_updates_bedrock(self, ctx, enabled: bool):
        await self.set_update_filter(ctx, "updates_bedrock", enabled, "Bedrock stable")

    @minecraftset_updates.command(name="snapshots", aliases=["prereleases"])
    async def minecraftset_updates_snapshots(self, ctx, enabled: bool):
        await self.set_update_filter(ctx, "updates_java_prerelease", enabled, "Java snapshot and prerelease")

    @minecraftset_updates.command(name="previews", aliases=["betas"])
    async def minecraftset_updates_previews(self, ctx, enabled: bool):
        await self.set_update_filter(ctx, "updates_bedrock_preview", enabled, "Bedrock preview and beta")

    @minecraftset_updates.command(name="sources")
    async def minecraftset_updates_sources(self, ctx):
        await ctx.send(
            "Java and Bedrock release notes use the official Minecraft Feedback changelog feeds. "
            "Mojang's Java manifest was evaluated, but the changelog feed is used because it supplies the official human-readable notes link and summary.",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @minecraftset_updates.command(name="preview", aliases=["recent"])
    async def minecraftset_updates_preview(self, ctx, count: int = 1):
        count = max(1, min(count, 5))
        try:
            items = filter_updates(await self.fetch_update_items(), await self.config.guild(ctx.guild).all())
        except Exception:
            log.exception("Could not preview Minecraft updates")
            await ctx.send("The official Minecraft update sources are unavailable right now.")
            return
        for item in sort_updates(items, reverse=True)[:count]:
            await ctx.send(embed=self.update_embed(item), allowed_mentions=discord.AllowedMentions.none())

    @minecraftset_updates.command(name="start")
    async def minecraftset_updates_start(self, ctx, recent_posts: int = 0):
        """Start announcements, optionally posting 1–10 recent matching updates."""
        if recent_posts and not 1 <= recent_posts <= 10:
            await ctx.send("Choose 1 to 10 recent posts, or omit the count for no backfill.")
            return
        lock = self._locks.setdefault(ctx.guild.id, asyncio.Lock())
        async with lock:
            group = self.config.guild(ctx.guild)
            settings = await group.all()
            if settings["updates_enabled"]:
                await ctx.send(
                    "Minecraft update announcements are already running. "
                    "Use `minecraftset updates stop` before starting with a backfill."
                    if recent_posts else "Minecraft update announcements are already running."
                )
                return
            channel_id = settings.get("updates_channel_id")
            channel = await self.get_channel(ctx.guild, channel_id) if channel_id else None
            if channel is None:
                await ctx.send("Set an available update channel first; announcements were not started.")
                return
            try:
                items = filter_updates(await self.fetch_update_items(), settings)
            except Exception:
                log.exception("Could not fetch Minecraft startup updates")
                await ctx.send("The official Minecraft update sources are unavailable; announcements were not started.")
                return
            seen = list(dict.fromkeys(settings.get("updates_seen_ids", [])))
            if not seen:
                seen = [item["id"] for item in sort_updates(items)][-MAX_SEEN_UPDATE_IDS:]
                await group.updates_seen_ids.set(seen)
            selected = sort_updates(sort_updates(items, reverse=True)[:recent_posts])
            published = 0
            for item in selected:
                try:
                    await channel.send(
                        embed=self.update_embed(item),
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                    await ctx.send(
                        f"Published {published} of {len(selected)} selected recent updates, "
                        "but delivery failed. Announcements remain stopped. Check channel permissions; "
                        "retrying a backfill will repeat its selected posts."
                    )
                    return
                published += 1
                seen = list(dict.fromkeys(seen + [item["id"]]))[-MAX_SEEN_UPDATE_IDS:]
                await group.updates_seen_ids.set(seen)
            await group.updates_enabled.set(True)
            await group.updates_last_poll_at.set(utc_now().isoformat())
            message = "Minecraft update announcements are running."
            if recent_posts:
                message += f" Published {published} recent update(s), oldest to newest, without mentions."
                if published < recent_posts:
                    message += f" Only {len(selected)} matching posts were available (requested {recent_posts})."
            elif not settings.get("updates_seen_ids"):
                message += " Existing official posts were seeded without backfill."
            else:
                message += " Existing update history was preserved."
            await ctx.send(message)

    @minecraftset_updates.command(name="stop")
    async def minecraftset_updates_stop(self, ctx):
        lock = self._locks.setdefault(ctx.guild.id, asyncio.Lock())
        async with lock:
            await self.config.guild(ctx.guild).updates_enabled.set(False)
        await ctx.send("Minecraft update announcements are stopped.")

    @minecraftset_updates.command(name="check")
    async def minecraftset_updates_check(self, ctx):
        try:
            items = await self.fetch_update_items(force=True)
        except Exception:
            log.exception("Could not manually check Minecraft updates")
            await ctx.send("The official Minecraft update sources are unavailable right now.")
            return
        sent = await self.poll_update_guild(ctx.guild, items, force=True)
        await self.config.guild(ctx.guild).updates_last_poll_at.set(utc_now().isoformat())
        await ctx.send(f"Minecraft update check complete; published {sent} new update(s).")

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
