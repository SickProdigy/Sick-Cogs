from __future__ import annotations

import asyncio
import logging
import random
import time
from datetime import datetime
from typing import Optional
from urllib.parse import quote, urlparse

import aiohttp
import discord
from discord.ext import tasks
from redbot.core import Config, checks, commands
from redbot.core.bot import Red

from .api import RLCSTournament, StartGGClient, StartGGError, normalize_tournament_slug
from .blast import BlastClient, BlastError, BlastTournament, upcoming_tournaments


log = logging.getLogger("red.sick-cogs.RocketLeague")
STARTGG_TOKEN_NAMESPACE = "startgg"
CHALLONGE_TOKEN_NAMESPACE = "challonge"
CHALLONGE_API_URL = "https://api.challonge.com/v2.1/tournaments"
CHALLONGE_TOKEN_URL = "https://api.challonge.com/oauth/token"
USER_AGENT = "Sick-Cogs-RocketLeague/1.1.0 (+https://github.com/SickProdigy/Sick-Cogs)"
BLAST_FETCH_INTERVAL = 7 * 24 * 60 * 60
ANNOUNCEMENT_LOOKAHEAD = 45 * 24 * 60 * 60
COMMUNITY_REFRESH_INTERVAL = 24 * 60 * 60
CHALLONGE_DAILY_TOURNAMENT_LIMIT = 7
CONFIG_IDENTIFIER = 0x5347524C4353


class RocketLeague(commands.Cog):
    """Rocket League esports schedules and tournament information.

    Use ``rocketleague`` for the canonical user command group. The ``rl`` alias opens this canonical command group, while ``rlcs`` remains a
    direct shortcut to the official RLCS schedule.
    Server administrators can configure schedule announcements with
    ``rlcsset``.
    """

    __author__ = ["SickProdigy"]
    __version__ = "1.1.0"

    def __init__(self, bot: Red):
        self.bot = bot
        self.session: Optional[aiohttp.ClientSession] = None
        self.config = Config.get_conf(self, identifier=CONFIG_IDENTIFIER, force_registration=True)
        self.config.register_global(
            blast_last_attempt=0,
            blast_last_success=0,
            blast_tournaments=[],
            blast_last_error=None,
            challonge_refresh_day=0,
            challonge_refresh_count=0,
        )
        self.config.register_guild(
            channel_id=None,
            enabled=False,
            announced={},
            tournament_sources=[],
        )
        self._blast_lock = asyncio.Lock()
        self._challonge_token_lock = asyncio.Lock()
        self._challonge_access_token: Optional[str] = None
        self._challonge_token_expires_at = 0.0
        self.schedule_loop.start()
        self.community_refresh_loop.start()

    async def red_delete_data_for_user(self, **kwargs):
        """This cog stores no user data."""
        return

    def cog_unload(self):
        self.schedule_loop.cancel()
        self.community_refresh_loop.cancel()
        if self.session and not self.session.closed:
            self.bot.loop.create_task(self.session.close())

    async def get_session(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30),
                headers={"User-Agent": USER_AGENT},
            )
        return self.session

    async def get_client(self) -> Optional[StartGGClient]:
        tokens = await self.bot.get_shared_api_tokens(STARTGG_TOKEN_NAMESPACE)
        token = str(tokens.get("token") or tokens.get("api_key") or "").strip()
        if not token:
            return None
        return StartGGClient(await self.get_session(), token)

    async def require_client(self, ctx: commands.Context) -> Optional[StartGGClient]:
        client = await self.get_client()
        if client is None:
            await ctx.send(
                "The bot owner must configure a start.gg developer token privately with "
                f"`{ctx.clean_prefix}set api startgg token,YOUR_TOKEN`."
            )
        return client

    async def _cached_blast(self) -> list[BlastTournament]:
        values = await self.config.blast_tournaments()
        tournaments = []
        for value in values:
            try:
                tournaments.append(BlastTournament.from_dict(value))
            except (KeyError, TypeError, ValueError):
                continue
        return tournaments

    async def _refresh_blast(self) -> tuple[list[BlastTournament], bool]:
        async with self._blast_lock:
            now = int(time.time())
            last_attempt = int(await self.config.blast_last_attempt())
            if last_attempt and now - last_attempt < BLAST_FETCH_INTERVAL:
                return await self._cached_blast(), False
            await self.config.blast_last_attempt.set(now)
            try:
                tournaments = await BlastClient(await self.get_session()).tournaments()
            except BlastError as exc:
                await self.config.blast_last_error.set(str(exc))
                raise
            await self.config.blast_tournaments.set([item.to_dict() for item in tournaments])
            await self.config.blast_last_success.set(now)
            await self.config.blast_last_error.set(None)
            return tournaments, True

    async def _blast_for_display(self) -> list[BlastTournament]:
        cached = await self._cached_blast()
        if not cached:
            cached, _ = await self._refresh_blast()
        return upcoming_tournaments(cached)

    @staticmethod
    def _blast_feature_embed(tournament: BlastTournament) -> discord.Embed:
        description = tournament.description or "The next scheduled RLCS event."
        embed = discord.Embed(
            title=tournament.name[:256],
            url=tournament.url,
            description=description[:4096],
            color=discord.Color.blue(),
        )
        if tournament.prize_pool:
            embed.add_field(name="Prize pool", value=tournament.prize_pool, inline=True)
        if tournament.team_count is not None:
            embed.add_field(name="Teams", value=str(tournament.team_count), inline=True)
        dates = f"<t:{tournament.start_at}:D> - <t:{tournament.end_at}:D>"
        embed.add_field(name="Dates", value=dates, inline=False)
        if tournament.location:
            embed.add_field(name="Location", value=tournament.location, inline=False)
        embed.set_thumbnail(url=tournament.image_url)
        embed.set_footer(text="RLCS Event")
        return embed

    @staticmethod
    def _blast_embed(
        tournaments: list[BlastTournament], *, title: str = "Upcoming RLCS events"
    ) -> discord.Embed:
        embed = discord.Embed(
            title=title,
            description="Official RLCS schedule information cached from BLAST.",
            color=discord.Color.blue(),
        )
        for tournament in tournaments[:10]:
            dates = f"<t:{tournament.start_at}:D>-<t:{tournament.end_at}:D>"
            details = [dates]
            if tournament.location:
                details.append(tournament.location)
            details.append(f"[View on BLAST]({tournament.url})")
            embed.add_field(
                name=tournament.name[:256], value=" - ".join(details), inline=False
            )
        embed.set_footer(text="RLCS Events")
        return embed

    async def _announce_updates(self, tournaments: list[BlastTournament]) -> None:
        now = int(time.time())
        candidates = [
            item
            for item in upcoming_tournaments(tournaments, now=now)
            if item.start_at <= now + ANNOUNCEMENT_LOOKAHEAD
        ]
        if not candidates:
            return
        for guild in self.bot.guilds:
            settings = await self.config.guild(guild).all()
            if not settings["enabled"] or not settings["channel_id"]:
                continue
            channel = guild.get_channel(int(settings["channel_id"]))
            if channel is None:
                continue
            announced = dict(settings.get("announced") or {})
            changed = [
                item
                for item in candidates
                if announced.get(item.identity) != item.fingerprint
            ]
            if not changed:
                continue
            try:
                await channel.send(
                    embed=self._blast_embed(changed, title="RLCS schedule update")
                )
            except (discord.Forbidden, discord.HTTPException):
                log.warning(
                    "Could not deliver RLCS update to guild %s channel %s",
                    guild.id,
                    channel.id,
                )
                continue
            for item in changed:
                announced[item.identity] = item.fingerprint
            active_ids = {item.identity for item in tournaments}
            await self.config.guild(guild).announced.set(
                {key: value for key, value in announced.items() if key in active_ids}
            )

    @tasks.loop(minutes=30)
    async def schedule_loop(self) -> None:
        enabled = False
        for guild in self.bot.guilds:
            if await self.config.guild(guild).enabled():
                enabled = True
                break
        if not enabled:
            return
        try:
            tournaments, refreshed = await self._refresh_blast()
        except BlastError as exc:
            log.warning("Weekly BLAST schedule refresh failed: %s", exc)
            return
        if refreshed:
            await self._announce_updates(tournaments)

    @schedule_loop.before_loop
    async def before_schedule_loop(self) -> None:
        await self.bot.wait_until_red_ready()

    @tasks.loop(hours=1)
    async def community_refresh_loop(self) -> None:
        """Refresh each unique active community tournament at most once per day."""
        now = int(time.time())
        due = {}
        for guild in self.bot.guilds:
            sources = await self._number_tournament_sources(guild)
            for source in sources:
                provider = str(source.get("provider") or "")
                key = str(source.get("key") or "")
                if not provider or not key:
                    continue
                state = str(source.get("state") or "").casefold()
                end_at = int(source.get("end_at") or 0)
                start_at = int(source.get("start_at") or 0)
                complete = state in {"complete", "completed"} or (end_at > 0 and end_at < now)
                if not state and not end_at and start_at > 0 and start_at < now:
                    complete = True
                cached_at = int(source.get("cached_at") or 0)
                if not complete and now - cached_at >= COMMUNITY_REFRESH_INTERVAL:
                    due[(provider, key)] = source
        refreshed = {}
        refresh_day = now // COMMUNITY_REFRESH_INTERVAL
        stored_day = int(await self.config.challonge_refresh_day())
        challonge_count = int(await self.config.challonge_refresh_count())
        if stored_day != refresh_day:
            challonge_count = 0
            await self.config.challonge_refresh_day.set(refresh_day)
            await self.config.challonge_refresh_count.set(0)
        for provider, key in sorted(due):
            if provider == "challonge":
                if challonge_count >= CHALLONGE_DAILY_TOURNAMENT_LIMIT:
                    continue
                challonge_count += 1
                # Reserve the request budget before fetching. Failed requests still count
                # against Challonge's monthly sandbox allowance.
                await self.config.challonge_refresh_count.set(challonge_count)
            try:
                refreshed[(provider, key)] = await self._fetch_tournament_source(provider, key)
            except StartGGError as exc:
                log.warning("Automatic %s tournament refresh failed for %s: %s", provider, key, exc)
        if not refreshed:
            return
        for guild in self.bot.guilds:
            async with self.config.guild(guild).tournament_sources() as stored:
                for index, source in enumerate(stored):
                    identity = (str(source.get("provider") or ""), str(source.get("key") or ""))
                    updated = refreshed.get(identity)
                    if updated is None:
                        continue
                    replacement = dict(updated)
                    replacement["id"] = source.get("id")
                    stored[index] = replacement

    @community_refresh_loop.before_loop
    async def before_community_refresh_loop(self) -> None:
        await self.bot.wait_until_red_ready()
        await asyncio.sleep(random.randint(60, 600))

    @commands.group(name="rlcs", invoke_without_command=True)
    @commands.bot_has_permissions(embed_links=True)
    async def rlcs(self, ctx: commands.Context):
        """Show the next RLCS event (short form of rocketleague rlcs)."""
        await self._send_next_event(ctx)

    async def _send_next_event(self, ctx: commands.Context) -> None:
        async with ctx.typing():
            try:
                tournaments = await self._blast_for_display()
            except BlastError as exc:
                await ctx.send(str(exc))
                return
        if not tournaments:
            await ctx.send(
                "The weekly BLAST cache does not currently contain any upcoming RLCS events."
            )
            return
        await ctx.send(embed=self._blast_feature_embed(tournaments[0]))

    @rlcs.command(name="upcoming")
    @commands.bot_has_permissions(embed_links=True)
    async def rlcs_upcoming(self, ctx: commands.Context, limit: commands.Range[int, 1, 10] = 5):
        """Show later RLCS events (short form of rocketleague rlcs upcoming)."""
        await self._send_upcoming_events(ctx, limit)

    async def _send_upcoming_events(self, ctx: commands.Context, limit: int = 5) -> None:
        async with ctx.typing():
            try:
                tournaments = (await self._blast_for_display())[1 : limit + 1]
            except BlastError as exc:
                await ctx.send(str(exc))
                return

        if not tournaments:
            await ctx.send("No additional upcoming RLCS events are currently scheduled.")
            return
        await ctx.send(embed=self._blast_embed(tournaments, title="Later RLCS events"))

    @commands.group(name="rocketleague", aliases=["rl"], invoke_without_command=True)
    @commands.bot_has_permissions(embed_links=True)
    async def rocketleague(self, ctx: commands.Context):
        """Browse Rocket League commands and announcement administration.

        Use ``rocketleague rlcs`` for event information. Server administrators
        configure announcements with ``rlcsset channel``, ``rlcsset disable``,
        ``rlcsset status``, and ``rlcsset postnow``. Bot owners can also use
        ``rlcsset refresh``. Server tournament URLs are managed with
        ``rocketleagueset tournamentadd``, ``tournamentremove``, and ``tournaments``.
        """
        prefix = ctx.clean_prefix
        embed = discord.Embed(
            title="Rocket League esports",
            description="View upcoming RLCS events from the cached official schedule.",
            color=discord.Color.blue(),
        )
        embed.add_field(
            name="Next event",
            value=f"`{prefix}rocketleague rlcs` (short form: `{prefix}rlcs`)",
            inline=False,
        )
        embed.add_field(
            name="Later events",
            value=f"`{prefix}rocketleague rlcs upcoming [1-10]`",
            inline=False,
        )
        embed.add_field(
            name="Tournament lookup",
            value=f"`{prefix}rocketleague rlcs event <start.gg URL or slug>`",
            inline=False,
        )
        embed.add_field(
            name="Community tournaments",
            value=f"`{prefix}rocketleague tournaments` (recent: `{prefix}rocketleague tournaments recent`)",
            inline=False,
        )
        embed.set_footer(
            text=f"Use {prefix}help rocketleague for the full command and administrator reference."
        )
        await ctx.send(embed=embed)

    @rocketleague.group(name="tournaments", aliases=["tourney", "tourneys"], invoke_without_command=True)
    @commands.guild_only()
    @commands.bot_has_permissions(embed_links=True)
    async def rocketleague_tournaments(self, ctx: commands.Context):
        """Show upcoming tournaments configured for this Discord server."""
        await self._send_configured_tournaments(ctx, past=False)

    @rocketleague_tournaments.command(name="upcoming")
    @commands.bot_has_permissions(embed_links=True)
    async def rocketleague_tournaments_upcoming(self, ctx: commands.Context):
        """Show upcoming configured Rocket League tournaments."""
        await self._send_configured_tournaments(ctx, past=False)

    @rocketleague_tournaments.command(name="recent")
    @commands.bot_has_permissions(embed_links=True)
    async def rocketleague_tournaments_recent(self, ctx: commands.Context):
        """Show recently completed configured Rocket League tournaments."""
        await self._send_configured_tournaments(ctx, past=True)

    async def _send_configured_tournaments(self, ctx: commands.Context, *, past: bool) -> None:
        sources = await self._number_tournament_sources(ctx.guild)
        if not sources:
            await ctx.send(
                "This server has no tournament URLs configured. A server administrator can add "
                f"one with `{ctx.clean_prefix}rocketleagueset tournamentadd <url>`."
            )
            return
        now = int(time.time())
        matching = []
        for source in sources:
            state = str(source.get("state") or "").casefold()
            end_at = int(source.get("end_at") or 0)
            start_at = int(source.get("start_at") or 0)
            is_past = state in {"complete", "completed"} or (end_at > 0 and end_at < now)
            if not state and not end_at and start_at > 0 and start_at < now:
                is_past = True
            if past == is_past:
                matching.append(source)
        ordered = sorted(matching, key=lambda item: int(item.get("start_at") or 0), reverse=past)[:10]
        if not ordered:
            period = "recent" if past else "upcoming"
            await ctx.send(f"No {period} Rocket League tournaments were found in this server’s cached URLs.")
            return
        title = "Recent Rocket League tournaments" if past else "Upcoming Rocket League tournaments"
        description = "Community events selected by this server."
        embed = discord.Embed(title=title, description=description, color=discord.Color.blue())
        for source in ordered:
            provider = "start.gg" if source.get("provider") == "startgg" else "Challonge"
            name = str(source.get("name") or source.get("key"))
            url = str(source.get("url") or "")
            details = self._community_source_details(source, past=past)
            details.append(f"Source: {provider}")
            provider_link = f"[View on {provider}]({url})"
            embed.add_field(
                name=discord.utils.escape_markdown(name)[:256],
                value="\n".join([provider_link, *details])[:1024],
                inline=False,
            )
        embed.set_footer(text="Community tournament listings • Refreshed daily")
        await ctx.send(embed=embed)

    @staticmethod
    def _community_source_details(source: dict, *, past: bool) -> list[str]:
        start_at = int(source.get("start_at") or 0)
        end_at = int(source.get("end_at") or 0)
        if start_at and end_at and end_at != start_at:
            schedule = f"<t:{start_at}:D> – <t:{end_at}:D>"
        elif start_at:
            schedule = f"<t:{start_at}:F>"
        else:
            schedule = "Schedule pending"
        if not past and start_at:
            schedule += f" • <t:{start_at}:R>"
        details = [schedule]
        facts = []
        team_size = source.get("team_size")
        tournament_format = str(source.get("format") or "").strip()
        if team_size:
            facts.append(f"{team_size}v{team_size}")
        elif tournament_format:
            facts.append(tournament_format)
        registered = source.get("registered_entrants")
        capacity = source.get("entrant_capacity")
        entrant_label = str(source.get("entrant_label") or "entrants")
        if registered is not None and capacity:
            facts.append(f"{registered}/{capacity} {entrant_label}")
        elif registered is not None:
            facts.append(f"{registered} {entrant_label}")
        elif capacity:
            facts.append(f"Up to {capacity} {entrant_label}")
        registration_open = source.get("registration_open")
        if registration_open is True:
            facts.append("Registration open")
        elif registration_open is False and not past:
            facts.append("Registration closed")
        if facts:
            details.append(" • ".join(facts))
        location = str(source.get("location") or "").strip()
        if location:
            details.append(location)
        prize = str(source.get("prize") or "").strip()
        if prize:
            details.append(f"Prize: {prize}")
        description = str(source.get("description") or "").strip()
        if description:
            details.append(description[:300])
        return details

    @rocketleague.group(name="rlcs", invoke_without_command=True)
    @commands.bot_has_permissions(embed_links=True)
    async def rocketleague_rlcs(self, ctx: commands.Context):
        """Show the next RLCS event."""
        await self._send_next_event(ctx)

    @rocketleague_rlcs.command(name="upcoming")
    @commands.bot_has_permissions(embed_links=True)
    async def rocketleague_rlcs_upcoming(
        self, ctx: commands.Context, limit: commands.Range[int, 1, 10] = 5
    ):
        """Show RLCS tournaments scheduled after the next event."""
        await self._send_upcoming_events(ctx, limit)

    @rocketleague_rlcs.command(name="event")
    @commands.bot_has_permissions(embed_links=True)
    async def rocketleague_rlcs_event(self, ctx: commands.Context, *, slug_or_url: str):
        """Show a Rocket League tournament from its start.gg URL or slug."""
        await self._send_startgg_event(ctx, slug_or_url)

    @rlcs.command(name="event")
    @commands.bot_has_permissions(embed_links=True)
    async def rlcs_event(self, ctx: commands.Context, *, slug_or_url: str):
        """Look up an event (short form of rocketleague rlcs event)."""
        await self._send_startgg_event(ctx, slug_or_url)

    async def _send_startgg_event(self, ctx: commands.Context, slug_or_url: str) -> None:
        client = await self.require_client(ctx)
        if client is None:
            return
        async with ctx.typing():
            try:
                tournament = await client.tournament(slug_or_url)
            except StartGGError as exc:
                await ctx.send(str(exc))
                return

        if tournament is None:
            await ctx.send("That start.gg tournament was not found or has no Rocket League events.")
            return

        embed = discord.Embed(
            title=tournament.name,
            url=tournament.url,
            description=self._tournament_summary(tournament, include_name=False),
            color=discord.Color.blue(),
        )
        for event in tournament.events[:10]:
            details = []
            if event.start_at:
                details.append(f"Starts <t:{event.start_at}:F> (<t:{event.start_at}:R>)")
            if event.entrants is not None:
                details.append(f"{event.entrants:,} entrants")
            embed.add_field(name=event.name[:256], value=" • ".join(details) or "Details pending", inline=False)
        if len(tournament.events) > 10:
            embed.set_footer(text=f"Source: start.gg • {len(tournament.events) - 10} additional events not shown")
        else:
            embed.set_footer(text="Source: start.gg")
        await ctx.send(embed=embed)

    @commands.group(name="rocketleagueset", aliases=["rlset"], invoke_without_command=True)
    @commands.guild_only()
    @checks.admin_or_permissions(manage_guild=True)
    async def rocketleagueset(self, ctx: commands.Context):
        """Admin: configure server-specific Rocket League tournaments."""
        await ctx.send_help()

    async def _number_tournament_sources(self, guild) -> list[dict]:
        async with self.config.guild(guild).tournament_sources() as sources:
            used = {int(item["id"]) for item in sources if str(item.get("id") or "").isdigit()}
            next_id = 1
            for item in sources:
                if str(item.get("id") or "").isdigit():
                    continue
                while next_id in used:
                    next_id += 1
                item["id"] = next_id
                used.add(next_id)
            return [dict(item) for item in sources]

    async def _fetch_startgg_source(self, key: str) -> dict:
        client = await self.get_client()
        if client is None:
            raise StartGGError("Configure start.gg with `set api startgg token,YOUR_TOKEN`.")
        tournament = await client.tournament(key)
        if tournament is None:
            raise StartGGError("That start.gg URL does not contain a visible Rocket League tournament.")
        entrants = [event.entrants for event in tournament.events if event.entrants is not None]
        team_sizes = {event.entrant_size_min for event in tournament.events if event.entrant_size_min}
        formats = [event.name for event in tournament.events if event.name]
        location = ", ".join(filter(None, (tournament.city, tournament.state, tournament.country)))
        return {
            "provider": "startgg", "key": key, "url": tournament.url,
            "name": tournament.name, "start_at": tournament.start_at,
            "end_at": tournament.end_at, "state": None,
            "description": None, "format": " / ".join(formats[:3]) or None,
            "team_size": next(iter(team_sizes)) if len(team_sizes) == 1 else None,
            "registered_entrants": sum(entrants) if entrants else None,
            "entrant_label": "teams" if team_sizes and max(team_sizes) > 1 else "players",
            "entrant_capacity": None, "registration_open": None,
            "prize": None, "location": location or ("Online" if tournament.is_online else None),
            "cached_at": int(time.time()),
        }

    async def _challonge_access_token_for_request(self) -> str:
        now = time.monotonic()
        if self._challonge_access_token and now < self._challonge_token_expires_at:
            return self._challonge_access_token
        async with self._challonge_token_lock:
            now = time.monotonic()
            if self._challonge_access_token and now < self._challonge_token_expires_at:
                return self._challonge_access_token
            credentials = await self.bot.get_shared_api_tokens(CHALLONGE_TOKEN_NAMESPACE)
            client_id = str(credentials.get("client_id") or "").strip()
            client_secret = str(credentials.get("client_secret") or "").strip()
            if not client_id or not client_secret:
                raise StartGGError(
                    "Configure Challonge with `set api challonge client_id,YOUR_ID "
                    "client_secret,YOUR_SECRET`."
                )
            try:
                async with (await self.get_session()).post(
                    CHALLONGE_TOKEN_URL,
                    data={
                        "grant_type": "client_credentials",
                        "client_id": client_id,
                        "client_secret": client_secret,
                    },
                ) as response:
                    if response.status in {400, 401, 403}:
                        raise StartGGError("Challonge rejected the configured client credentials.")
                    if response.status == 429:
                        raise StartGGError("Challonge is rate limiting requests. Try again shortly.")
                    if response.status >= 500:
                        raise StartGGError("Challonge authentication is temporarily unavailable.")
                    if response.status != 200:
                        raise StartGGError(
                            f"Challonge authentication failed (HTTP {response.status})."
                        )
                    payload = await response.json(content_type=None)
            except StartGGError:
                raise
            except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
                raise StartGGError("Could not authenticate with Challonge. Try again shortly.") from exc
            access_token = str(payload.get("access_token") or "") if isinstance(payload, dict) else ""
            if not access_token:
                raise StartGGError("Challonge returned an unexpected authentication response.")
            try:
                expires_in = max(60, int(payload.get("expires_in") or 3600))
            except (TypeError, ValueError):
                expires_in = 3600
            self._challonge_access_token = access_token
            self._challonge_token_expires_at = time.monotonic() + max(30, expires_in - 60)
            return access_token

    async def _fetch_challonge_source(self, key: str) -> dict:
        access_token = await self._challonge_access_token_for_request()
        endpoint = f"{CHALLONGE_API_URL}/{quote(key, safe=chr(45))}.json"
        headers = {
            "Authorization-Type": "v2",
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
            "Content-Type": "application/vnd.api+json",
        }
        try:
            async with (await self.get_session()).get(endpoint, headers=headers) as response:
                if response.status in {401, 403}:
                    self._challonge_access_token = None
                    self._challonge_token_expires_at = 0.0
                    raise StartGGError("Challonge rejected the application access token.")
                if response.status == 404:
                    raise StartGGError("That Challonge tournament was not found or is not visible to this application.")
                if response.status == 429:
                    raise StartGGError("Challonge is rate limiting requests. Try again shortly.")
                if response.status >= 500:
                    raise StartGGError("Challonge is temporarily unavailable.")
                if response.status != 200:
                    raise StartGGError(f"Challonge could not complete that request (HTTP {response.status}).")
                payload = await response.json(content_type=None)
        except StartGGError:
            raise
        except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
            raise StartGGError("Could not reach Challonge. Try again shortly.") from exc
        resource = payload.get("data") if isinstance(payload, dict) else None
        tournament = resource.get("attributes") if isinstance(resource, dict) else None
        if not isinstance(tournament, dict):
            raise StartGGError("Challonge returned an unexpected tournament response.")
        game = str(tournament.get("game_name") or "")
        if game and "rocket league" not in game.casefold():
            raise StartGGError(f"That Challonge tournament is for {game}, not Rocket League.")
        registered_entrants = None
        participants_endpoint = f"{CHALLONGE_API_URL}/{quote(key, safe=chr(45))}/participants.json"
        try:
            async with (await self.get_session()).get(
                participants_endpoint,
                headers=headers,
                params={"page": 1, "per_page": 256},
            ) as response:
                if response.status == 200:
                    participants_payload = await response.json(content_type=None)
                    participant_data = participants_payload.get("data") if isinstance(participants_payload, dict) else None
                    if isinstance(participant_data, list):
                        registered_entrants = len(participant_data)
                else:
                    log.warning(
                        "Challonge participant count returned HTTP %s for %s",
                        response.status,
                        key,
                    )
        except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
            log.warning("Challonge participant count failed for %s: %s", key, exc)
        url = str(tournament.get("full_challonge_url") or "").strip()
        if not url:
            url_slug = str(tournament.get("url") or key).strip("/")
            url = f"https://challonge.com/{url_slug}"
        registration = tournament.get("registration_options") or {}
        tournament_format = str(tournament.get("tournament_type") or "").replace("_", " ").strip()
        return {
            "provider": "challonge", "key": key, "url": url,
            "name": str(tournament.get("name") or key),
            "start_at": self._iso_timestamp(tournament.get("starts_at")),
            "end_at": self._iso_timestamp(tournament.get("completed_at")),
            "state": tournament.get("state"),
            "description": str(tournament.get("description") or "").strip() or None,
            "format": tournament_format.title() if tournament_format else None,
            "team_size": None, "registered_entrants": registered_entrants,
            "entrant_label": "entrants",
            "entrant_capacity": int(registration.get("signup_cap") or 0) or None,
            "registration_open": registration.get("open_signup"),
            "prize": None, "location": None, "cached_at": int(time.time()),
        }

    async def _fetch_tournament_source(self, provider: str, key: str) -> dict:
        if provider == "startgg":
            return await self._fetch_startgg_source(key)
        return await self._fetch_challonge_source(key)

    @staticmethod
    def _iso_timestamp(value) -> Optional[int]:
        if not value:
            return None
        try:
            return int(datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp())
        except ValueError:
            return None

    @rocketleagueset.command(name="tournamentadd")
    async def rocketleagueset_tournamentadd(self, ctx: commands.Context, *, url: str):
        """Validate, cache, and add a start.gg or Challonge tournament URL."""
        try:
            provider, key = self._tournament_source_from_url(url)
            source = await self._fetch_tournament_source(provider, key)
        except (ValueError, StartGGError) as exc:
            await ctx.send(str(exc))
            return
        await self._number_tournament_sources(ctx.guild)
        async with self.config.guild(ctx.guild).tournament_sources() as sources:
            if any(item.get("provider") == provider and item.get("key") == key for item in sources):
                await ctx.send("**{}** is already configured for this server.".format(source.get("name")))
                return
            if len(sources) >= 25:
                await ctx.send("A server can configure at most 25 tournament URLs.")
                return
            used = [int(item["id"]) for item in sources if str(item.get("id") or "").isdigit()]
            source["id"] = max(used, default=0) + 1
            sources.append(source)
        name = source.get("name")
        source_id = source.get("id")
        provider_name = "start.gg" if provider == "startgg" else "Challonge"
        await ctx.send(
            f"Added **{name}** from {provider_name} with server ID `{source_id}`. "
            f"Remove it with `{ctx.clean_prefix}rocketleagueset tournamentremove {source_id}`."
        )

    @rocketleagueset.command(name="tournamentremove")
    async def rocketleagueset_tournamentremove(self, ctx: commands.Context, *, reference: str):
        """Remove a configured tournament by its short ID or original URL."""
        sources = await self._number_tournament_sources(ctx.guild)
        reference = reference.strip().strip("<>")
        provider = key = None
        if not reference.isdigit():
            try:
                provider, key = self._tournament_source_from_url(reference)
            except ValueError as exc:
                await ctx.send(str(exc))
                return
        matched = next((item for item in sources if
            (reference.isdigit() and int(item.get("id") or 0) == int(reference)) or
            (provider and item.get("provider") == provider and item.get("key") == key)), None)
        if matched is None:
            await ctx.send("That tournament ID or URL is not configured for this server.")
            return
        matched_id = int(matched.get("id"))
        async with self.config.guild(ctx.guild).tournament_sources() as stored:
            stored[:] = [item for item in stored if int(item.get("id") or 0) != matched_id]
        matched_name = matched.get("name") or matched.get("key")
        await ctx.send(f"Removed tournament `{matched_id}`: **{matched_name}**.")

    @rocketleagueset.command(name="tournamentrefresh")
    async def rocketleagueset_tournamentrefresh(self, ctx: commands.Context, tournament_id: Optional[int] = None):
        """Refresh one cached tournament by ID, or every configured tournament."""
        sources = await self._number_tournament_sources(ctx.guild)
        selected = [item for item in sources if tournament_id is None or int(item.get("id") or 0) == tournament_id]
        if not selected:
            await ctx.send("That tournament ID is not configured for this server.")
            return
        refreshed = []
        try:
            for item in selected:
                updated = await self._fetch_tournament_source(str(item.get("provider")), str(item.get("key")))
                updated["id"] = item.get("id")
                refreshed.append(updated)
        except StartGGError as exc:
            await ctx.send(str(exc))
            return
        replacements = {int(item.get("id")): item for item in refreshed}
        async with self.config.guild(ctx.guild).tournament_sources() as stored:
            stored[:] = [replacements.get(int(item.get("id") or 0), item) for item in stored]
        suffix = "s" if len(refreshed) != 1 else ""
        await ctx.send(f"Refreshed {len(refreshed)} cached tournament{suffix}.")

    @rocketleagueset.command(name="tournaments")
    async def rocketleagueset_tournaments(self, ctx: commands.Context):
        """List cached tournaments, IDs, and removal instructions."""
        sources = await self._number_tournament_sources(ctx.guild)
        if not sources:
            await ctx.send("This server has no tournament URLs configured.")
            return
        lines = []
        for item in sources:
            source_id = item.get("id")
            name = item.get("name") or item.get("key")
            provider = item.get("provider")
            url = item.get("url")
            lines.append(f"- ID `{source_id}` • **{name}** • {provider} • <{url}>")
        lines.append(
            f"\nRemove one with `{ctx.clean_prefix}rocketleagueset tournamentremove <id>`. "
            f"Refresh cached details with `{ctx.clean_prefix}rocketleagueset tournamentrefresh [id]`."
        )
        await ctx.send("**Configured Rocket League tournaments**\n" + "\n".join(lines))

    @staticmethod
    def _tournament_source_from_url(value: str) -> tuple[str, str]:
        value = value.strip().strip("<>")
        parsed = urlparse(value)
        host = (parsed.hostname or "").casefold()
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("Enter a complete https URL from start.gg or Challonge.")
        if host in {"start.gg", "www.start.gg"}:
            return "startgg", normalize_tournament_slug(value)
        if host in {"challonge.com", "www.challonge.com"} or host.endswith(".challonge.com"):
            slug = parsed.path.strip("/").split("/", 1)[0]
            if not slug:
                raise ValueError("Enter a direct Challonge tournament URL.")
            subdomain = "" if host in {"challonge.com", "www.challonge.com"} else host.removesuffix(".challonge.com")
            key = slug if not subdomain else f"{subdomain}-{slug}"
            return "challonge", key
        raise ValueError("Use a direct tournament URL from start.gg or Challonge.")

    @commands.group(name="rlcsset", invoke_without_command=True)
    @commands.guild_only()
    @checks.admin_or_permissions(manage_guild=True)
    async def rlcsset(self, ctx: commands.Context):
        """Admin: configure automatic RLCS schedule announcements."""
        await ctx.send_help()

    @rlcsset.command(name="channel")
    async def rlcsset_channel(self, ctx: commands.Context, channel: discord.TextChannel):
        """Enable schedule updates in a channel."""
        permissions = channel.permissions_for(ctx.guild.me)
        if not permissions.send_messages or not permissions.embed_links:
            await ctx.send("I need Send Messages and Embed Links in that channel.")
            return
        await self.config.guild(ctx.guild).channel_id.set(channel.id)
        await self.config.guild(ctx.guild).enabled.set(True)
        await ctx.send(
            f"Weekly cached RLCS schedule updates will be posted in {channel.mention}."
        )

    @rlcsset.command(name="disable")
    async def rlcsset_disable(self, ctx: commands.Context):
        """Disable automatic schedule updates for this server."""
        await self.config.guild(ctx.guild).enabled.set(False)
        await ctx.send("Automatic RLCS schedule updates are disabled for this server.")

    @rlcsset.command(name="status")
    async def rlcsset_status(self, ctx: commands.Context):
        """Show announcement and weekly cache status."""
        settings = await self.config.guild(ctx.guild).all()
        channel = (
            ctx.guild.get_channel(settings["channel_id"])
            if settings["channel_id"]
            else None
        )
        last_attempt = int(await self.config.blast_last_attempt())
        last_success = int(await self.config.blast_last_success())
        error = await self.config.blast_last_error()
        state = "enabled" if settings["enabled"] else "disabled"
        channel_text = channel.mention if channel else "not configured"
        attempt_text = f"<t:{last_attempt}:F>" if last_attempt else "never"
        success_text = f"<t:{last_success}:F>" if last_success else "never"
        lines = [
            f"Announcements: **{state}**",
            f"Channel: {channel_text}",
            f"Last weekly attempt: {attempt_text}",
            f"Last successful refresh: {success_text}",
        ]
        if error:
            lines.append(f"Last error: {error}")
        await ctx.send("\n".join(lines))

    @rlcsset.command(name="refresh")
    @checks.is_owner()
    async def rlcsset_refresh(self, ctx: commands.Context):
        """Refresh BLAST when the seven-day request window permits."""
        previous_attempt = int(await self.config.blast_last_attempt())
        try:
            tournaments, refreshed = await self._refresh_blast()
        except BlastError as exc:
            await ctx.send(str(exc))
            return
        if not refreshed:
            next_at = previous_attempt + BLAST_FETCH_INTERVAL
            await ctx.send(
                f"The BLAST schedule was already requested this week. Next allowed refresh: "
                f"<t:{next_at}:F> (<t:{next_at}:R>)."
            )
            return
        await self._announce_updates(tournaments)
        await ctx.send(
            f"BLAST schedule refreshed and cached with {len(tournaments)} RLCS tournaments."
        )

    @rlcsset.command(name="postnow")
    async def rlcsset_postnow(self, ctx: commands.Context):
        """Post the current cached schedule without scraping BLAST."""
        tournaments = upcoming_tournaments(await self._cached_blast())
        if not tournaments:
            await ctx.send("The weekly BLAST cache has no upcoming RLCS events yet.")
            return
        await ctx.send(embed=self._blast_embed(tournaments, title="RLCS schedule preview"))

    @staticmethod
    def _tournament_summary(tournament: RLCSTournament, *, include_name: bool = True) -> str:
        details = []
        if tournament.start_at:
            if tournament.end_at and tournament.end_at != tournament.start_at:
                details.append(f"<t:{tournament.start_at}:D>–<t:{tournament.end_at}:D>")
            else:
                details.append(f"<t:{tournament.start_at}:F>")
        if tournament.is_online:
            details.append("Online")
        else:
            location = ", ".join(filter(None, (tournament.city, tournament.state, tournament.country)))
            if location:
                details.append(location)
        details.append(f"[View on start.gg]({tournament.url})")
        return " • ".join(details)
