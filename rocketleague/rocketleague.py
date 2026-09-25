from __future__ import annotations

import asyncio
import hashlib
import inspect
import logging
import random
import re
import time
from datetime import datetime
from typing import Optional
from urllib.parse import quote, urlparse

import aiohttp
import discord
from discord.ext import tasks
from redbot.core import Config, checks, commands
from redbot.core.bot import Red

from .api import (
    RLCSTournament,
    StartGGClient,
    StartGGError,
    normalize_league_slug,
    normalize_tournament_slug,
)
from .blast import BlastClient, BlastError, BlastResult, BlastTournament, upcoming_tournaments
from .clips import ClipProviders, ClipSourceError, clip_identity, detect_clip_source


log = logging.getLogger("red.sick-cogs.RocketLeague")
STARTGG_TOKEN_NAMESPACE = "startgg"
CHALLONGE_TOKEN_NAMESPACE = "challonge"
CHALLONGE_API_URL = "https://api.challonge.com/v2.1/tournaments"
CHALLONGE_TOKEN_URL = "https://api.challonge.com/oauth/token"
USER_AGENT = "Sick-Cogs-RocketLeague/1.6.0 (+https://github.com/SickProdigy/Sick-Cogs)"
BLAST_FETCH_INTERVAL = 7 * 24 * 60 * 60
BLAST_HISTORY_MAX_AGE = 365 * 24 * 60 * 60
BLAST_HISTORY_LIMIT = 100
BLAST_RESULT_DETAIL_LIMIT = 10
BLAST_RESULT_SCHEMA = 3
ANNOUNCEMENT_LOOKAHEAD = 45 * 24 * 60 * 60
COMMUNITY_REFRESH_INTERVAL = 24 * 60 * 60
CHALLONGE_DAILY_TOURNAMENT_LIMIT = 7
CLIP_CACHE_INTERVAL = 24 * 60 * 60
CLIP_DEFAULT_INTERVAL = 12 * 60 * 60
CLIP_DEFAULT_MAX_LENGTH = 180
CLIP_HISTORY_LIMIT = 500
CLIP_SOURCE_LIMIT = 25
CLIP_CACHE_VERSION = 2
CONFIG_IDENTIFIER = 0x5347524C4353


class RocketLeague(commands.Cog):
    """Rocket League esports schedules and tournament information.

    Use ``rocketleague`` for the canonical user command group. The ``rl`` alias opens this canonical command group, while ``rlcs`` remains a
    direct shortcut to the official RLCS schedule.
    Server administrators can configure schedule announcements with
    ``rlcsset``.
    """

    __author__ = ["SickProdigy"]
    __version__ = "1.6.0"

    def __init__(self, bot: Red):
        self.bot = bot
        self.session: Optional[aiohttp.ClientSession] = None
        self.config = Config.get_conf(self, identifier=CONFIG_IDENTIFIER, force_registration=True)
        self.config.register_global(
            blast_last_attempt=0,
            blast_last_success=0,
            blast_tournaments=[],
            blast_history=[],
            blast_results={},
            blast_results_last_attempt=0,
            blast_results_schema=0,
            blast_last_error=None,
            challonge_refresh_day=0,
            challonge_refresh_count=0,
            clip_cache={},
        )
        self.config.register_guild(
            channel_id=None,
            enabled=False,
            announced={},
            tournament_sources=[],
            league_sources=[],
            clip_channel_id=None,
            clip_enabled=False,
            clip_sources=[],
            clip_interval=CLIP_DEFAULT_INTERVAL,
            clip_max_length=CLIP_DEFAULT_MAX_LENGTH,
            clip_last_post=0,
            clip_next_post=0,
            clip_last_source_id=None,
            clip_posted=[],
        )
        self._blast_lock = asyncio.Lock()
        self._challonge_token_lock = asyncio.Lock()
        self._challonge_access_token: Optional[str] = None
        self._challonge_token_expires_at = 0.0
        self._clip_providers: Optional[ClipProviders] = None
        self.schedule_loop.start()
        self.community_refresh_loop.start()
        self.clip_post_loop.start()

    async def red_delete_data_for_user(self, **kwargs):
        """This cog stores no user data."""
        return

    def cog_unload(self):
        self.schedule_loop.cancel()
        self.community_refresh_loop.cancel()
        self.clip_post_loop.cancel()
        if self.session and not self.session.closed:
            self.bot.loop.create_task(self.session.close())

    async def _configured_text_prefix(self, ctx: commands.Context) -> str:
        """Prefer the invoking text prefix and avoid rendering a mention prefix."""
        current = str(getattr(ctx, "clean_prefix", "") or "")
        if current and not current.lstrip().startswith(("<@", "@")):
            return current
        getter = getattr(self.bot, "get_valid_prefixes", None)
        if getter is not None:
            prefixes = getter(getattr(ctx, "guild", None))
            if inspect.isawaitable(prefixes):
                prefixes = await prefixes
            for prefix in prefixes or ():
                candidate = str(prefix or "")
                if candidate and not candidate.lstrip().startswith(("<@", "@")):
                    return candidate
        return current or "[p]"

    async def _provider_error_message(self, ctx: commands.Context, exc: Exception) -> str:
        message = str(exc)
        command = str(getattr(exc, "setup_command", "") or "").strip()
        if not command:
            return message
        prefix = await self._configured_text_prefix(ctx)
        return f"{message} Configure it privately with `{prefix}{command}`."

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
                f"`{await self._configured_text_prefix(ctx)}set api startgg token,YOUR_TOKEN`."
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

    async def _cached_blast_history(self) -> list[dict]:
        records = []
        for value in await self.config.blast_history():
            if not isinstance(value, dict):
                continue
            try:
                tournament = BlastTournament.from_dict(value)
            except (KeyError, TypeError, ValueError):
                continue
            record = tournament.to_dict()
            record["first_seen"] = int(value.get("first_seen") or 0)
            record["last_seen"] = int(value.get("last_seen") or 0)
            record["final_fingerprint"] = str(value.get("final_fingerprint") or tournament.fingerprint)
            records.append(record)
        return records

    async def _cached_blast_results(self) -> dict[str, dict]:
        results = {}
        values = await self.config.blast_results()
        if not isinstance(values, dict):
            return results
        for slug, value in values.items():
            if not isinstance(value, dict):
                continue
            try:
                result = BlastResult.from_dict(value)
            except (KeyError, TypeError, ValueError):
                continue
            results[str(slug)] = {
                **result.to_dict(),
                "cached_at": int(value.get("cached_at") or 0),
                "detail_version": int(value.get("detail_version") or 1),
            }
        return results

    async def _refresh_blast_results(
        self, client: BlastClient, tournaments: list[BlastTournament], *, now: int
    ) -> None:
        cached = await self._cached_blast_results()
        candidates = [
            item for item in tournaments
            if item.end_at < now and (
                item.slug not in cached
                or int(cached[item.slug].get("detail_version") or 1) < BLAST_RESULT_SCHEMA
            )
        ]
        candidates.sort(key=lambda item: item.end_at, reverse=True)
        for tournament in candidates[:BLAST_RESULT_DETAIL_LIMIT]:
            try:
                result = await client.tournament_result(tournament)
            except BlastError as exc:
                log.warning("BLAST result detail failed for %s: %s", tournament.slug, exc)
                continue
            if result is not None:
                cached[tournament.slug] = {
                    **result.to_dict(), "cached_at": now,
                    "detail_version": BLAST_RESULT_SCHEMA,
                }
        await self.config.blast_results.set(cached)

    async def _maybe_refresh_blast_results(
        self, tournaments: list[BlastTournament], *, now: int
    ) -> bool:
        last_attempt = int(await self.config.blast_results_last_attempt())
        schema = int(await self.config.blast_results_schema())
        if schema >= BLAST_RESULT_SCHEMA and last_attempt and now - last_attempt < BLAST_FETCH_INTERVAL:
            return False
        await self.config.blast_results_last_attempt.set(now)
        client = BlastClient(await self.get_session())
        await self._refresh_blast_results(client, tournaments, now=now)
        await self.config.blast_results_schema.set(BLAST_RESULT_SCHEMA)
        return True


    @staticmethod
    def _merge_blast_history(
        history: list[dict],
        previous: list[BlastTournament],
        current: list[BlastTournament],
        *,
        now: int,
    ) -> list[dict]:
        merged = {}
        for value in history:
            try:
                tournament = BlastTournament.from_dict(value)
            except (KeyError, TypeError, ValueError):
                continue
            merged[tournament.slug] = {
                **tournament.to_dict(),
                "first_seen": int(value.get("first_seen") or now),
                "last_seen": int(value.get("last_seen") or now),
                "final_fingerprint": str(value.get("final_fingerprint") or tournament.fingerprint),
            }
        for tournament in [*previous, *current]:
            existing = merged.get(tournament.slug)
            first_seen = int((existing or {}).get("first_seen") or now)
            merged[tournament.slug] = {
                **tournament.to_dict(),
                "first_seen": first_seen,
                "last_seen": now,
                "final_fingerprint": tournament.fingerprint,
            }
        cutoff = now - BLAST_HISTORY_MAX_AGE
        retained = [value for value in merged.values() if int(value.get("end_at") or 0) >= cutoff]
        retained.sort(key=lambda value: (int(value.get("end_at") or 0), str(value.get("slug") or "")), reverse=True)
        return retained[:BLAST_HISTORY_LIMIT]

    async def _refresh_blast(self) -> tuple[list[BlastTournament], bool]:
        async with self._blast_lock:
            now = int(time.time())
            last_attempt = int(await self.config.blast_last_attempt())
            if last_attempt and now - last_attempt < BLAST_FETCH_INTERVAL:
                cached = await self._cached_blast()
                known = {item.slug: item for item in cached}
                for value in await self._cached_blast_history():
                    try:
                        item = BlastTournament.from_dict(value)
                    except (KeyError, TypeError, ValueError):
                        continue
                    known[item.slug] = item
                await self._maybe_refresh_blast_results(list(known.values()), now=now)
                return cached, False
            await self.config.blast_last_attempt.set(now)
            previous = await self._cached_blast()
            history = await self._cached_blast_history()
            try:
                client = BlastClient(await self.get_session())
                tournaments = await client.tournaments()
            except BlastError as exc:
                await self.config.blast_last_error.set(str(exc))
                raise
            await self.config.blast_tournaments.set([item.to_dict() for item in tournaments])
            merged_history = self._merge_blast_history(history, previous, tournaments, now=now)
            await self.config.blast_history.set(merged_history)
            known = {item.slug: item for item in [*previous, *tournaments]}
            for value in merged_history:
                try:
                    item = BlastTournament.from_dict(value)
                except (KeyError, TypeError, ValueError):
                    continue
                known[item.slug] = item
            await self.config.blast_results_last_attempt.set(now)
            await self._refresh_blast_results(client, list(known.values()), now=now)
            await self.config.blast_results_schema.set(BLAST_RESULT_SCHEMA)
            await self.config.blast_last_success.set(now)
            await self.config.blast_last_error.set(None)
            return tournaments, True

    async def _blast_for_display(self) -> list[BlastTournament]:
        cached = await self._cached_blast()
        if not cached:
            cached, _ = await self._refresh_blast()
        return upcoming_tournaments(cached)

    async def _known_blast_tournaments(self) -> list[BlastTournament]:
        known = {item.slug: item for item in await self._cached_blast()}
        for value in await self._cached_blast_history():
            try:
                tournament = BlastTournament.from_dict(value)
            except (KeyError, TypeError, ValueError):
                continue
            known[tournament.slug] = tournament
        return sorted(known.values(), key=lambda item: (item.start_at, item.slug))

    @staticmethod
    def _blast_short_id(tournament: BlastTournament) -> str:
        return hashlib.sha256(tournament.slug.encode("utf-8")).hexdigest()[:8]

    async def _recent_blast_tournaments(self, limit: int = 5) -> list[BlastTournament]:
        now = int(time.time())
        recent = [item for item in await self._known_blast_tournaments() if item.end_at < now]
        recent.sort(key=lambda item: (item.end_at, item.name.casefold()), reverse=True)
        return recent[:limit]

    async def _resolve_cached_blast(self, reference: str) -> Optional[BlastTournament]:
        normalized = reference.strip().strip("<>").rstrip("/")
        if "/rl/tournaments/" in normalized:
            normalized = normalized.rsplit("/", 1)[-1]
        matches = [
            item
            for item in await self._known_blast_tournaments()
            if normalized == item.slug or normalized == self._blast_short_id(item)
        ]
        return matches[0] if len(matches) == 1 else None

    async def _send_blast_event_list(self, ctx: commands.Context) -> None:
        now = int(time.time())
        known = await self._known_blast_tournaments()
        known.sort(key=lambda item: (item.end_at < now, item.start_at))
        if not known:
            await ctx.send("No official RLCS events have been retained in the weekly cache yet.")
            return
        embed = discord.Embed(
            title="Known official RLCS events",
            description="Cache-only BLAST event references accepted by `rlcs event`.",
            color=discord.Color.blue(),
        )
        for item in known[:10]:
            status = "Completed" if item.end_at < now else (
                "Active" if item.start_at <= now else "Upcoming"
            )
            embed.add_field(
                name=item.name[:256],
                value=(
                    f"`{self._blast_short_id(item)}` • `{item.slug}`\n"
                    f"{status} • <t:{item.start_at}:D>–<t:{item.end_at}:D> • "
                    f"[BLAST]({item.url})"
                )[:1024],
                inline=False,
            )
        hidden = len(known) - 10
        embed.set_footer(
            text=(
                f"{hidden} additional retained event(s) not shown"
                if hidden > 0
                else "Source: retained weekly BLAST snapshots"
            )
        )
        await ctx.send(embed=embed)

    async def _send_cached_or_startgg_event(
        self, ctx: commands.Context, reference: Optional[str]
    ) -> None:
        if not reference:
            await self._send_blast_event_list(ctx)
            return
        tournament = await self._resolve_cached_blast(reference)
        if tournament is not None:
            await ctx.send(embed=self._blast_feature_embed(tournament))
            return
        normalized = reference.strip().strip("<>")
        if "/rl/tournaments/" in normalized or (
            len(normalized) == 8 and all(character in "0123456789abcdef" for character in normalized.casefold())
        ):
            prefix = await self._configured_text_prefix(ctx)
            await ctx.send(
                "That cached BLAST event reference is unknown or expired. "
                f"Run `{prefix}rlcs events` to view current references."
            )
            return
        await self._send_startgg_event(ctx, reference)

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
            details = []
            if tournament.location:
                details.append(f"**Location:** {tournament.location}")
            details.append(f"**Dates:** <t:{tournament.start_at}:D> – <t:{tournament.end_at}:D>")
            details.append(f"[View on BLAST]({tournament.url})")
            embed.add_field(
                name=tournament.name[:256], value="\n".join(details), inline=False
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
                state_value = source.get("state")
                state = str(state_value or "").casefold()
                end_at = int(source.get("end_at") or 0)
                start_at = int(source.get("start_at") or 0)
                complete = state in {"complete", "completed"} or state_value == 3 or (end_at > 0 and end_at < now)
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
        await self._refresh_due_leagues(now)
        if refreshed:
            for guild in self.bot.guilds:
                async with self.config.guild(guild).tournament_sources() as stored:
                    stored[:] = self._merge_tournament_refreshes(stored, refreshed)

    async def _refresh_due_leagues(self, now: int) -> None:
        due = {}
        for guild in self.bot.guilds:
            for source in await self.config.guild(guild).league_sources():
                key = str(source.get("key") or "")
                if source.get("enabled", True) and key and now - int(source.get("cached_at") or 0) >= COMMUNITY_REFRESH_INTERVAL:
                    due[key] = source
        refreshed = {}
        for key in sorted(due):
            try:
                refreshed[key] = await self._fetch_league_source(key)
            except StartGGError as exc:
                log.warning("Automatic start.gg league refresh failed for %s: %s", key, exc)
        if not refreshed:
            return
        for guild in self.bot.guilds:
            async with self.config.guild(guild).league_sources() as stored:
                stored[:] = self._merge_league_refreshes(stored, refreshed)

    @staticmethod
    def _merge_league_refreshes(
        stored: list[dict], refreshed: dict[str, dict]
    ) -> list[dict]:
        """Replace successful league snapshots while preserving last-good failures."""
        merged = []
        for source in stored:
            updated = refreshed.get(str(source.get("key") or ""))
            if updated is None:
                merged.append(dict(source))
                continue
            replacement = dict(updated)
            replacement["id"] = source.get("id")
            replacement["enabled"] = source.get("enabled", True)
            merged.append(replacement)
        return merged

    @staticmethod
    def _merge_tournament_refreshes(
        stored: list[dict], refreshed: dict[tuple[str, str], dict]
    ) -> list[dict]:
        """Replace successful refreshes while preserving every last-good failure."""
        merged = []
        for source in stored:
            identity = (str(source.get("provider") or ""), str(source.get("key") or ""))
            updated = refreshed.get(identity)
            if updated is None:
                merged.append(dict(source))
                continue
            replacement = dict(updated)
            replacement["id"] = source.get("id")
            merged.append(replacement)
        return merged

    @community_refresh_loop.before_loop
    async def before_community_refresh_loop(self) -> None:
        await self.bot.wait_until_red_ready()
        await asyncio.sleep(random.randint(60, 600))

    async def get_clip_providers(self) -> ClipProviders:
        if self._clip_providers is None:
            self._clip_providers = ClipProviders(self.bot, await self.get_session())
        return self._clip_providers

    @staticmethod
    def _clip_cache_key(source: dict) -> str:
        return f"{source.get('provider')}:{source.get('kind')}:{source.get('key')}"

    async def _number_clip_sources(self, guild) -> list[dict]:
        async with self.config.guild(guild).clip_sources() as sources:
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

    async def _cached_clips(self, source: dict, *, force: bool = False) -> list[dict]:
        cache = dict(await self.config.clip_cache())
        key = self._clip_cache_key(source)
        entry = cache.get(key) if isinstance(cache.get(key), dict) else {}
        now = int(time.time())
        cached_clips = list(entry.get("clips") or [])
        current_format = int(entry.get("version") or 0) == CLIP_CACHE_VERSION
        if (
            not force
            and current_format
            and now - int(entry.get("cached_at") or 0) < CLIP_CACHE_INTERVAL
        ):
            return cached_clips
        try:
            clips = await (await self.get_clip_providers()).fetch(source)
        except ClipSourceError:
            if entry.get("clips"):
                log.warning("Using stale clip cache for %s after provider refresh failure", key)
                return list(entry.get("clips") or [])
            raise
        cache[key] = {
            "version": CLIP_CACHE_VERSION,
            "cached_at": now,
            "clips": clips[:100],
        }
        await self.config.clip_cache.set(cache)
        return clips[:100]

    @staticmethod
    def _next_clip_post(interval: int) -> int:
        jitter = max(300, int(interval * 0.15))
        return int(time.time()) + interval + random.randint(-jitter, jitter)

    @staticmethod
    def _preferred_clip(clips: list[dict]) -> dict:
        """Prefer the highest-view unseen clip; keep random choice only for tied views."""
        highest_views = max(int(clip.get("views") or 0) for clip in clips)
        top = [clip for clip in clips if int(clip.get("views") or 0) == highest_views]
        return random.choice(top)

    async def _choose_clip(self, guild, *, force_refresh: bool = False):
        settings = await self.config.guild(guild).all()
        sources = await self._number_clip_sources(guild)
        if not sources:
            raise ClipSourceError("This server has no clip sources configured.")
        posted = set(settings.get("clip_posted") or [])
        max_length = int(settings.get("clip_max_length") or CLIP_DEFAULT_MAX_LENGTH)
        candidates = []
        errors = []
        for source in sources:
            try:
                clips = await self._cached_clips(source, force=force_refresh)
            except ClipSourceError as exc:
                errors.append(exc)
                continue
            eligible = [
                clip for clip in clips
                if 0 < float(clip.get("duration") or 0) <= max_length
                and clip_identity(clip) not in posted
            ]
            if eligible:
                candidates.append((source, eligible))
        if not candidates:
            if errors and len(errors) == len(sources):
                raise errors[0]
            raise ClipSourceError("No unseen clips within this server’s length limit are available yet.")
        last_source = int(settings.get("clip_last_source_id") or 0)
        ordered = sorted(candidates, key=lambda item: int(item[0].get("id") or 0))
        source, clips = next(
            (item for item in ordered if int(item[0].get("id") or 0) > last_source),
            ordered[0],
        )
        return source, self._preferred_clip(clips)

    async def _post_clip_for_guild(self, guild, *, force_refresh: bool = False) -> dict:
        settings = await self.config.guild(guild).all()
        channel_id = settings.get("clip_channel_id")
        channel = guild.get_channel(int(channel_id)) if channel_id else None
        if channel is None:
            raise ClipSourceError("Choose a clip channel first.")
        source, clip = await self._choose_clip(guild, force_refresh=force_refresh)
        await channel.send(
            str(clip.get("url") or ""),
            allowed_mentions=discord.AllowedMentions.none(),
        )
        history = list(settings.get("clip_posted") or [])
        history.append(clip_identity(clip))
        await self.config.guild(guild).clip_posted.set(history[-CLIP_HISTORY_LIMIT:])
        await self.config.guild(guild).clip_last_source_id.set(source.get("id"))
        await self.config.guild(guild).clip_last_post.set(int(time.time()))
        interval = int(settings.get("clip_interval") or CLIP_DEFAULT_INTERVAL)
        await self.config.guild(guild).clip_next_post.set(self._next_clip_post(interval))
        return clip

    @tasks.loop(minutes=15)
    async def clip_post_loop(self) -> None:
        now = int(time.time())
        for guild in self.bot.guilds:
            settings = await self.config.guild(guild).all()
            if not settings.get("clip_enabled"):
                continue
            next_post = int(settings.get("clip_next_post") or 0)
            if next_post and next_post > now:
                continue
            try:
                await self._post_clip_for_guild(guild)
            except (ClipSourceError, discord.Forbidden, discord.HTTPException) as exc:
                log.warning("Automatic clip post failed for guild %s: %s", guild.id, exc)
                await self.config.guild(guild).clip_next_post.set(now + CLIP_CACHE_INTERVAL)

    @clip_post_loop.before_loop
    async def before_clip_post_loop(self) -> None:
        await self.bot.wait_until_red_ready()
        await asyncio.sleep(random.randint(30, 180))

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
                await ctx.send(await self._provider_error_message(ctx, exc))
                return
        if not tournaments:
            prefix = await self._configured_text_prefix(ctx)
            await ctx.send(
                "No upcoming RLCS events are currently scheduled. View recently completed "
                f"official events with `{prefix}rlcs recent`, or browse this server's community "
                f"tournaments with `{prefix}rocketleague tournaments`."
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
                await ctx.send(await self._provider_error_message(ctx, exc))
                return

        if not tournaments:
            prefix = await self._configured_text_prefix(ctx)
            await ctx.send(
                "No additional upcoming RLCS events are currently scheduled. "
                f"View `{prefix}rlcs recent` or `{prefix}rlcs events`."
            )
            return
        await ctx.send(embed=self._blast_embed(tournaments, title="Later RLCS events"))

    @rlcs.command(name="recent")
    @commands.bot_has_permissions(embed_links=True)
    async def rlcs_recent(self, ctx: commands.Context, limit: commands.Range[int, 1, 10] = 5):
        """Show recently completed official events from retained weekly snapshots."""
        await self._send_recent_events(ctx, limit)

    @rlcs.command(name="results")
    @commands.guild_only()
    @commands.bot_has_permissions(embed_links=True)
    async def rlcs_results(self, ctx: commands.Context, *, reference: str):
        """Show detailed cached results for a completed Rocket League event.

        ``reference`` accepts an event ID from ``[p]rlcs events``, a BLAST tournament
        slug or URL, or a configured start.gg tournament URL. Official BLAST results
        may include final standings, championship-day scores, player ratings, power
        rankings, and event details. Matched start.gg events show cached standings.

        Use ``[p]rlcs events`` to find retained event IDs and slugs.
        """
        await self._send_results(ctx, reference)

    @rlcs.command(name="events", aliases=["list"])
    @commands.bot_has_permissions(embed_links=True)
    async def rlcs_events(self, ctx: commands.Context):
        """List discoverable official event IDs and slugs from the local cache."""
        await self._send_blast_event_list(ctx)

    @rlcs.command(name="event")
    @commands.bot_has_permissions(embed_links=True)
    async def rlcs_event(self, ctx: commands.Context, *, reference: Optional[str] = None):
        """Show a cached official event or an explicit start.gg tournament."""
        await self._send_cached_or_startgg_event(ctx, reference)

    async def _cached_result_sources(self, guild) -> list[dict]:
        sources = await self._number_tournament_sources(guild)
        for league in await self.config.guild(guild).league_sources():
            if not league.get("enabled", True):
                continue
            sources.extend(
                dict(item)
                for item in (league.get("tournaments") or [])
                if isinstance(item, dict)
            )
        return [item for item in sources if item.get("provider") == "startgg"]

    @staticmethod
    def _placement_summary(source: dict) -> list[str]:
        candidates = []
        for event in source.get("events") or []:
            standings = [item for item in (event.get("standings") or []) if isinstance(item, dict)]
            if standings:
                candidates.append((len(standings), event, standings))
        if not candidates:
            return []
        _, event, standings = max(candidates, key=lambda item: item[0])
        by_place = {}
        for standing in standings:
            placement = int(standing.get("placement") or 0)
            name = str(standing.get("entrant_name") or "").strip()
            if placement > 0 and name:
                by_place.setdefault(placement, []).append(name)
        lines = []
        if by_place.get(1):
            lines.append(f"Champion: **{discord.utils.escape_markdown(by_place[1][0])}**")
        if by_place.get(2):
            lines.append(f"Runner-up: **{discord.utils.escape_markdown(by_place[2][0])}**")
        semifinalists = by_place.get(3, []) + by_place.get(4, [])
        if semifinalists:
            names = ", ".join(discord.utils.escape_markdown(name) for name in semifinalists[:2])
            lines.append(f"Semifinalists: {names}")
        if len([item for item in (source.get("events") or []) if item.get("standings")]) > 1:
            lines.insert(0, f"Results shown for: **{discord.utils.escape_markdown(str(event.get('name') or 'Rocket League event'))}**")
        return lines

    async def _match_cached_result_source(self, guild, reference: str) -> Optional[dict]:
        sources = await self._cached_result_sources(guild)
        cleaned = reference.strip().strip("<>").rstrip("/")
        if "start.gg" in cleaned or cleaned.startswith("tournament/"):
            key = normalize_tournament_slug(cleaned)
            return next((item for item in sources if item.get("key") == key), None)
        direct = next(
            (
                item
                for item in sources
                if cleaned in {str(item.get("provider_id") or ""), str(item.get("key") or "")}
            ),
            None,
        )
        if direct is not None:
            return direct
        blast = await self._resolve_cached_blast(cleaned)
        if blast is None:
            return None
        target = {
            "name": blast.name,
            "start_at": blast.start_at,
            "end_at": blast.end_at,
        }
        return next(
            (item for item in sources if self._confident_event_match(item, target)),
            None,
        )

    @classmethod
    def _results_embed(cls, source: dict) -> discord.Embed:
        embed = discord.Embed(
            title=f"{str(source.get('name') or 'Rocket League tournament')} results"[:256],
            url=str(source.get("url") or "") or None,
            color=discord.Color.blue(),
        )
        shown = 0
        for event in source.get("events") or []:
            standings = [item for item in (event.get("standings") or []) if isinstance(item, dict)]
            if not standings:
                continue
            standings.sort(key=lambda item: int(item.get("placement") or 999))
            lines = []
            for standing in standings[:4]:
                placement = int(standing.get("placement") or 0)
                name = discord.utils.escape_markdown(str(standing.get("entrant_name") or "Unknown entrant"))
                record = standing.get("record") or {}
                record_text = ""
                if isinstance(record, dict):
                    wins = record.get("wins")
                    losses = record.get("losses")
                    if wins is not None and losses is not None:
                        record_text = f" • match record {wins}-{losses}"
                lines.append(f"**#{placement}** {name}{record_text}")
            embed.add_field(
                name=f"Final standings — {str(event.get('name') or 'Rocket League event')}"[:256],
                value="\n".join(lines)[:1024],
                inline=False,
            )
            shown += 1
            if shown >= 4:
                break
        cached_at = int(source.get("cached_at") or 0)
        embed.set_footer(text="Source: start.gg standings")
        if cached_at:
            embed.add_field(name="Cache", value=f"Updated <t:{cached_at}:R>", inline=False)
        return embed

    @staticmethod
    def _blast_results_embed(tournament: BlastTournament, result: dict) -> discord.Embed:
        champion = discord.utils.escape_markdown(str(result["champion"]))
        runner_up = discord.utils.escape_markdown(str(result["runner_up"]))
        embed = discord.Embed(
            title=f"{tournament.name} results"[:256], url=tournament.url,
            color=discord.Color.blue(),
        )
        standings = [f"**#1** {champion}", f"**#2** {runner_up}"]
        semifinalists = result.get("semifinalists") or []
        for index, name in enumerate(semifinalists[:2], start=3):
            standings.append(f"**#{index}** {discord.utils.escape_markdown(str(name))}")
        embed.add_field(
            name="Final standings", value="\n".join(standings), inline=False
        )
        matches = result.get("matches") or []
        if matches:
            matches = sorted(
                matches,
                key=lambda item: ("grand final" in str(item.get("round_name") or "").casefold(), str(item.get("round_name") or "")),
            )
            lines = []
            for match in matches[:3]:
                round_name = discord.utils.escape_markdown(str(match.get("round_name") or "Match"))
                team_a = discord.utils.escape_markdown(str(match.get("team_a") or "TBD"))
                team_b = discord.utils.escape_markdown(str(match.get("team_b") or "TBD"))
                lines.append(
                    f"**{round_name}:** {team_a} {int(match.get('team_a_score') or 0)}–"
                    f"{int(match.get('team_b_score') or 0)} {team_b}"
                )
            embed.add_field(name="Championship day matchups", value="\n".join(lines), inline=False)
        top_players = result.get("top_players") or []
        if top_players:
            lines = [
                f"**#{index} {discord.utils.escape_markdown(str(item.get('player_name') or 'Unknown'))}**"
                f" • {int(item.get('games_played') or 0)} games • {float(item.get('rating') or 0):.2f} rating"
                for index, item in enumerate(top_players[:3], start=1)
            ]
            embed.add_field(name="Top rated players", value="\n".join(lines), inline=False)
        power_rankings = result.get("power_rankings") or []
        if power_rankings:
            lines = [
                f"**#{index}** {discord.utils.escape_markdown(str(item.get('team_name') or 'Unknown'))}"
                f" • {float(item.get('points') or 0):.0f} pts"
                for index, item in enumerate(power_rankings[:5], start=1)
            ]
            embed.add_field(name="Post-tournament power rankings", value="\n".join(lines), inline=False)
        details = [f"**Dates:** <t:{tournament.start_at}:D> – <t:{tournament.end_at}:D>"]
        if tournament.location:
            details.append(f"**Location:** {tournament.location}")
        if tournament.prize_pool:
            details.append(f"**Prize pool:** {tournament.prize_pool}")
        if tournament.team_count is not None:
            details.append(f"**Teams:** {tournament.team_count}")
        embed.add_field(name="Tournament details", value="\n".join(details), inline=False)
        embed.set_thumbnail(url=tournament.image_url)
        cached_at = int(result.get("cached_at") or 0)
        embed.set_footer(text="Source: BLAST official tournament results")
        if cached_at:
            embed.add_field(name="Cache", value=f"Updated <t:{cached_at}:R>", inline=False)
        return embed

    async def _send_results(self, ctx: commands.Context, reference: str) -> None:
        blast = await self._resolve_cached_blast(reference)
        if blast is not None:
            blast_result = (await self._cached_blast_results()).get(blast.slug)
            if blast_result is not None:
                await ctx.send(embed=self._blast_results_embed(blast, blast_result))
                return
        source = await self._match_cached_result_source(ctx.guild, reference)
        if source is None and ("start.gg" in reference or reference.startswith("tournament/")):
            client = await self.require_client(ctx)
            if client is None:
                return
            try:
                tournament = await client.tournament(reference)
            except StartGGError as exc:
                await ctx.send(await self._provider_error_message(ctx, exc))
                return
            if tournament is not None:
                source = self._startgg_source(tournament)
        if source is None:
            await ctx.send(
                "No final results are cached for that event from BLAST or a confidently matched start.gg tournament."
            )
            return
        if not self._placement_summary(source):
            await ctx.send("start.gg has not published final standings for that Rocket League tournament yet.")
            return
        await ctx.send(embed=self._results_embed(source))

    async def _send_recent_events(self, ctx: commands.Context, limit: int = 5) -> None:
        tournaments = await self._recent_blast_tournaments(limit)
        if not tournaments:
            prefix = await self._configured_text_prefix(ctx)
            await ctx.send(
                "No completed official RLCS events have been retained yet. "
                f"Browse known events with `{prefix}rlcs events`."
            )
            return
        sources = await self._cached_result_sources(ctx.guild) if ctx.guild else []
        blast_results = await self._cached_blast_results()
        prefix = await self._configured_text_prefix(ctx)
        embed = self._blast_embed(tournaments, title="Recent official RLCS events")
        for index, (field, tournament) in enumerate(zip(embed.fields, tournaments)):
            official = blast_results.get(tournament.slug)
            target = {
                "name": tournament.name,
                "start_at": tournament.start_at,
                "end_at": tournament.end_at,
            }
            source = next(
                (item for item in sources if self._confident_event_match(item, target)),
                None,
            )
            if official:
                details = [
                    f"Champion: **{discord.utils.escape_markdown(str(official['champion']))}**",
                    f"Runner-up: **{discord.utils.escape_markdown(str(official['runner_up']))}**",
                    f"Grand Final: {int(official['champion_score'])}–{int(official['runner_up_score'])}",
                ]
                semifinalists = official.get("semifinalists") or []
                if semifinalists:
                    details.append("Semifinalists: " + ", ".join(
                        discord.utils.escape_markdown(str(item)) for item in semifinalists[:2]
                    ))
            else:
                details = self._placement_summary(source) if source else []
            if not details:
                details.append("Final results unavailable from the cached providers.")
            embed.set_field_at(
                index,
                name=field.name,
                value=(field.value + "\n" + "\n".join(details))[:1024],
                inline=field.inline,
            )
        embed.set_footer(
            text=(
                f"Use {prefix}rlcs events to find event IDs • "
                f"{prefix}rlcs results <ID> shows more details"
            )
        )
        await ctx.send(embed=embed)

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
            name="Recent and known events",
            value=(
                f"`{prefix}rocketleague rlcs recent [1-10]` • "
                f"`{prefix}rocketleague rlcs events`"
            ),
            inline=False,
        )
        embed.add_field(
            name="Tournament lookup",
            value=(
                f"`{prefix}rocketleague rlcs event [cached-id, BLAST slug, "
                "or start.gg URL]`"
            ),
            inline=False,
        )
        embed.add_field(
            name="Community tournaments",
            value=f"`{prefix}rocketleague tournaments` (recent: `{prefix}rocketleague tournaments recent`)",
            inline=False,
        )
        embed.add_field(
            name="Community clips",
            value=f"`{prefix}rocketleague clips`",
            inline=False,
        )
        embed.set_footer(
            text=f"Use {prefix}help rocketleague for the full command and administrator reference."
        )
        await ctx.send(embed=embed)

    @rocketleague.command(name="clips")
    @commands.guild_only()
    @commands.bot_has_permissions(embed_links=True)
    async def rocketleague_clips(self, ctx: commands.Context):
        """Show this server's automatic community clip feed.

        Displays the posting channel, enabled state, next scheduled clip, and configured sources.
        Server administrators configure the feed with ``rocketleagueset clips``.
        """
        settings = await self.config.guild(ctx.guild).all()
        sources = await self._number_clip_sources(ctx.guild)
        channel_id = settings.get("clip_channel_id")
        channel = ctx.guild.get_channel(int(channel_id)) if channel_id else None
        if not sources:
            await ctx.send("This server has not configured a Rocket League clip feed.")
            return
        status = "enabled" if settings.get("clip_enabled") else "not enabled"
        description = f"This server follows **{len(sources)}** Rocket League clip source{'s' if len(sources) != 1 else ''}."
        embed = discord.Embed(
            title="Rocket League community clips",
            description=description,
            color=discord.Color.blue(),
        )
        embed.add_field(name="Posting", value=status.title(), inline=True)
        embed.add_field(name="Channel", value=channel.mention if channel else "Not configured", inline=True)
        if settings.get("clip_enabled") and settings.get("clip_next_post"):
            embed.add_field(
                name="Next clip",
                value=f"<t:{int(settings['clip_next_post'])}:R>",
                inline=False,
            )
        names = ", ".join(str(source.get("name") or "Unknown source") for source in sources[:10])
        if len(sources) > 10:
            names += f" and {len(sources) - 10} more"
        embed.add_field(name="Sources", value=names[:1024], inline=False)
        embed.add_field(
            name="Coming next",
            value="User clip submissions and community voting are planned. "
            f"Admins manage automatic sources with `{ctx.clean_prefix}rlset clips`.",
            inline=False,
        )
        embed.set_footer(text="Source clips are chosen automatically from cached public feeds.")
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

    @staticmethod
    def _cross_provider_event_key(source: dict) -> tuple[tuple[str, ...], int]:
        name = str(source.get("name") or "").casefold()
        region_aliases = (
            (r"\bnorth[ -]america\b", " na "),
            (r"\bsouth[ -]america\b", " sam "),
            (r"\bmiddle[ -]east(?:[ -]and)?[ -]north[ -]africa\b", " mena "),
            (r"\bsub[ -]saharan[ -]africa\b", " ssa "),
            (r"\basia[ -]pacific\b", " apac "),
            (r"\beurope\b", " eu "),
            (r"\boceania\b", " oce "),
        )
        for pattern, replacement in region_aliases:
            name = re.sub(pattern, replacement, name)
        tokens = tuple(sorted(re.findall(r"[a-z0-9]+", name)))
        start_day = int(source.get("start_at") or 0) // 86400
        return tokens, start_day

    @classmethod
    def _confident_event_match(cls, first: dict, second: dict) -> bool:
        first_tokens, first_day = cls._cross_provider_event_key(first)
        second_tokens, second_day = cls._cross_provider_event_key(second)
        if not first_tokens or first_tokens != second_tokens:
            return False
        first_end = int(first.get("end_at") or first.get("start_at") or 0)
        second_end = int(second.get("end_at") or second.get("start_at") or 0)
        first_start = int(first.get("start_at") or 0)
        second_start = int(second.get("start_at") or 0)
        overlaps = first_start <= second_end and second_start <= first_end
        return overlaps or abs(first_day - second_day) <= 14

    async def _configured_tournament_sources(self, guild) -> list[dict]:
        merged = []

        def add_if_new(value: dict) -> None:
            if not any(self._confident_event_match(existing, value) for existing in merged):
                merged.append(value)

        for tournament in await self._cached_blast():
            add_if_new({
                "provider": "blast",
                "key": tournament.slug,
                "url": tournament.url,
                "name": tournament.name,
                "start_at": tournament.start_at,
                "end_at": tournament.end_at,
                "location": tournament.location,
                "image_url": tournament.image_url,
                "source_origin": "BLAST",
            })
        for item in await self._number_tournament_sources(guild):
            add_if_new(item)
        for league in await self.config.guild(guild).league_sources():
            if not league.get("enabled", True):
                continue
            for item in league.get("tournaments") or []:
                if not isinstance(item, dict):
                    continue
                value = dict(item)
                value["league_name"] = league.get("name")
                add_if_new(value)
        return merged

    async def _send_configured_tournaments(self, ctx: commands.Context, *, past: bool) -> None:
        sources = await self._configured_tournament_sources(ctx.guild)
        if not sources:
            await ctx.send(
                "This server has no tournament URLs configured. A server administrator can add "
                f"one with `{ctx.clean_prefix}rocketleagueset tournamentadd <url>`."
            )
            return
        now = int(time.time())
        matching = []
        for source in sources:
            state_value = source.get("state")
            state = str(state_value or "").casefold()
            end_at = int(source.get("end_at") or 0)
            start_at = int(source.get("start_at") or 0)
            is_past = state in {"complete", "completed"} or state_value == 3 or (end_at > 0 and end_at < now)
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
            provider = {
                "startgg": "start.gg",
                "challonge": "Challonge",
                "blast": "BLAST",
            }.get(source.get("provider"), "provider")
            name = str(source.get("name") or source.get("key"))
            url = str(source.get("url") or "")
            details = self._community_source_details(source, past=past)
            details.append(f"Source: {source.get('source_origin') or provider}")
            if source.get("league_name"):
                details.append(f"Series: {discord.utils.escape_markdown(str(source.get('league_name')))}")
            provider_link = f"[View on {provider}]({url})"
            embed.add_field(
                name=discord.utils.escape_markdown(name)[:256],
                value="\n".join([provider_link, *details])[:1024],
                inline=False,
            )
        embed.set_footer(text="BLAST and server-selected tournament listings • Provider refresh schedules vary")
        await ctx.send(embed=embed)

    @staticmethod
    def _startgg_event_status(event: dict, *, now: int) -> tuple[str, int]:
        """Return a conservative label and display priority for a start.gg event."""
        state = event.get("state")
        if state == 3:
            return "Completed", 3
        if state == 2:
            return "Active", 0
        if state == 1:
            return "Upcoming", 1
        start_at = int(event.get("start_at") or 0)
        if start_at > now:
            return "Scheduled", 2
        return "Status unavailable", 2

    @classmethod
    def _startgg_event_details(cls, source: dict, *, now: int) -> list[str]:
        events = [item for item in (source.get("events") or []) if isinstance(item, dict)]
        ranked = []
        statuses = set()
        for event in events:
            status, priority = cls._startgg_event_status(event, now=now)
            statuses.add(status)
            ranked.append((priority, int(event.get("start_at") or 0), event, status))
        ranked.sort(key=lambda item: (item[0], item[1]))
        lines = []
        if len({item for item in statuses if item != "Status unavailable"}) > 1:
            lines.append("Phases: Mixed lifecycle states")
        for _, _, event, status in ranked[:4]:
            name = discord.utils.escape_markdown(str(event.get("name") or "Rocket League event"))
            facts = [status]
            start_at = int(event.get("start_at") or 0)
            if start_at:
                facts.append(f"<t:{start_at}:F>")
            entrants = event.get("registered_entrants")
            team_size = event.get("team_size")
            if entrants is not None:
                label = "teams" if team_size and int(team_size) > 1 else "players"
                facts.append(f"{entrants} {label}")
            if team_size:
                facts.append(f"{team_size}v{team_size}")
            lines.append(f"**{name[:120]}** — {chr(32).join(facts)}"[:300])
        if len(ranked) > 4:
            omitted = {}
            for _, _, _, status in ranked[4:]:
                omitted[status] = omitted.get(status, 0) + 1
            summary = ", ".join(f"{status}: {count}" for status, count in sorted(omitted.items()))
            lines.append(
                f"And {len(ranked) - 4} more Rocket League phase(s) ({summary})."
            )
        return lines

    @classmethod
    def _community_source_details(cls, source: dict, *, past: bool) -> list[str]:
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
        registration_closes_at = int(source.get("registration_closes_at") or 0)
        if registration_open is True:
            facts.append("Registration open")
            if registration_closes_at:
                facts.append(f"Closes <t:{registration_closes_at}:R>")
        elif registration_open is False and not past:
            facts.append("Registration closed")
        if facts:
            details.append(" • ".join(facts))
        registration_url = str(source.get("registration_url") or "").strip()
        if registration_open is True and registration_url:
            details.append(f"[Register on start.gg]({registration_url})")
        if source.get("provider") == "startgg":
            details.extend(cls._startgg_event_details(source, now=int(time.time())))
        location = str(source.get("location") or "").strip()
        if location:
            details.append(location)
        prize = str(source.get("prize") or "").strip()
        if prize:
            details.append(f"Prize: {prize}")
        description = str(source.get("description") or "").strip()
        if description:
            details.append(description[:300])
        cached_at = int(source.get("cached_at") or 0)
        if cached_at:
            details.append(f"Last refreshed <t:{cached_at}:R>")
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

    @rocketleague_rlcs.command(name="recent")
    @commands.bot_has_permissions(embed_links=True)
    async def rocketleague_rlcs_recent(
        self, ctx: commands.Context, limit: commands.Range[int, 1, 10] = 5
    ):
        """Show recently completed official events from retained weekly snapshots."""
        await self._send_recent_events(ctx, limit)

    @rocketleague_rlcs.command(name="results")
    @commands.guild_only()
    @commands.bot_has_permissions(embed_links=True)
    async def rocketleague_rlcs_results(self, ctx: commands.Context, *, reference: str):
        """Show detailed cached results for a completed Rocket League event.

        ``reference`` accepts an event ID from ``[p]rlcs events``, a BLAST tournament
        slug or URL, or a configured start.gg tournament URL. Official BLAST results
        may include final standings, championship-day scores, player ratings, power
        rankings, and event details. Matched start.gg events show cached standings.

        Use ``[p]rlcs events`` to find retained event IDs and slugs.
        """
        await self._send_results(ctx, reference)

    @rocketleague_rlcs.command(name="events", aliases=["list"])
    @commands.bot_has_permissions(embed_links=True)
    async def rocketleague_rlcs_events(self, ctx: commands.Context):
        """List discoverable official event IDs and slugs from the local cache."""
        await self._send_blast_event_list(ctx)

    @rocketleague_rlcs.command(name="event")
    @commands.bot_has_permissions(embed_links=True)
    async def rocketleague_rlcs_event(
        self, ctx: commands.Context, *, reference: Optional[str] = None
    ):
        """Show a cached official event or an explicit start.gg tournament."""
        await self._send_cached_or_startgg_event(ctx, reference)

    async def _send_startgg_event(self, ctx: commands.Context, slug_or_url: str) -> None:
        client = await self.require_client(ctx)
        if client is None:
            return
        async with ctx.typing():
            try:
                tournament = await client.tournament(slug_or_url)
            except StartGGError as exc:
                await ctx.send(await self._provider_error_message(ctx, exc))
                return

        if tournament is None:
            await ctx.send("That start.gg tournament was not found or has no Rocket League events.")
            return

        description = self._tournament_summary(tournament, include_name=False)
        if tournament.registration_open is True:
            description += "\nRegistration is open"
            if tournament.registration_closes_at:
                description += f" until <t:{tournament.registration_closes_at}:F>"
            description += f" • [Register on start.gg]({tournament.url})"
        elif tournament.registration_open is False:
            description += "\nRegistration is closed"
        embed = discord.Embed(
            title=tournament.name,
            url=tournament.url,
            description=description,
            color=discord.Color.blue(),
        )
        now = int(time.time())
        for event in tournament.events[:10]:
            status, _ = self._startgg_event_status(
                {"state": event.state, "start_at": event.start_at}, now=now
            )
            details = [status]
            if event.start_at:
                details.append(f"Starts <t:{event.start_at}:F> (<t:{event.start_at}:R>)")
            if event.entrants is not None:
                label = "teams" if event.entrant_size_min and event.entrant_size_min > 1 else "players"
                details.append(f"{event.entrants:,} {label}")
            if event.entrant_size_min:
                details.append(f"{event.entrant_size_min}v{event.entrant_size_min}")
            event_url = f"https://www.start.gg/{event.slug}" if event.slug else None
            if event_url:
                details.append(f"[View phase]({event_url})")
            embed.add_field(name=event.name[:256], value=" • ".join(details), inline=False)
        if len(tournament.events) > 10:
            embed.set_footer(text=f"Source: start.gg • {len(tournament.events) - 10} additional events not shown")
        else:
            embed.set_footer(text="Source: start.gg")
        await ctx.send(embed=embed)

    @commands.group(name="rocketleagueset", aliases=["rlset"], invoke_without_command=True)
    @commands.guild_only()
    @checks.admin_or_permissions(manage_guild=True)
    async def rocketleagueset(self, ctx: commands.Context):
        """Admin: configure server-specific tournaments and automatic clips.

        ``rlset`` is the shorter alias for this command group.
        """
        await ctx.send_help()


    @rocketleagueset.group(name="clips", aliases=["clip"], invoke_without_command=True)
    async def rocketleagueset_clips(self, ctx: commands.Context):
        """Admin: configure automatic short Rocket League clips."""
        await ctx.send_help()

    @rocketleagueset_clips.command(name="channel")
    async def rocketleagueset_clips_channel(
        self, ctx: commands.Context, channel: discord.TextChannel
    ):
        """Choose the channel for automatic clip posts."""
        permissions = channel.permissions_for(ctx.guild.me)
        if not permissions.send_messages or not permissions.embed_links:
            await ctx.send("I need Send Messages and Embed Links in that channel.")
            return
        await self.config.guild(ctx.guild).clip_channel_id.set(channel.id)
        await ctx.send(f"Rocket League clips will be posted in {channel.mention} when enabled.")

    @rocketleagueset_clips.command(name="sourceadd")
    async def rocketleagueset_clips_sourceadd(
        self, ctx: commands.Context, *, source_input: str
    ):
        """Add a Medal, Twitch, or YouTube source by URL or provider and name."""
        pieces = source_input.strip().split(maxsplit=1)
        provider = None
        value = source_input
        if len(pieces) == 2 and pieces[0].casefold() in {"medal", "twitch", "youtube", "yt"}:
            provider, value = pieces
        try:
            provider, value = detect_clip_source(value, provider)
            async with ctx.typing():
                source, clips = await (await self.get_clip_providers()).resolve(provider, value)
        except ClipSourceError as exc:
            await ctx.send(await self._provider_error_message(ctx, exc))
            return
        sources = await self._number_clip_sources(ctx.guild)
        identity = (source.get("provider"), source.get("kind"), source.get("key"))
        if any(
            (item.get("provider"), item.get("kind"), item.get("key")) == identity
            for item in sources
        ):
            await ctx.send(f"**{source.get('name')}** is already a clip source for this server.")
            return
        if len(sources) >= CLIP_SOURCE_LIMIT:
            await ctx.send(f"A server can configure at most {CLIP_SOURCE_LIMIT} clip sources.")
            return
        used = [int(item.get("id")) for item in sources if str(item.get("id") or "").isdigit()]
        source["id"] = max(used, default=0) + 1
        async with self.config.guild(ctx.guild).clip_sources() as stored:
            stored.append(source)
        cache = dict(await self.config.clip_cache())
        cache[self._clip_cache_key(source)] = {
            "version": CLIP_CACHE_VERSION,
            "cached_at": int(time.time()),
            "clips": clips[:100],
        }
        await self.config.clip_cache.set(cache)
        max_length = int(await self.config.guild(ctx.guild).clip_max_length())
        eligible = sum(0 < float(clip.get("duration") or 0) <= max_length for clip in clips)
        await ctx.send(
            f"Added clip source ID `{source['id']}`: **{source.get('name')}** ({provider.title()}). "
            f"Cached {eligible} eligible clip{'s' if eligible != 1 else ''}."
        )

    @rocketleagueset_clips.command(name="sourceremove")
    async def rocketleagueset_clips_sourceremove(
        self, ctx: commands.Context, source_id: int
    ):
        """Remove a configured clip source by its server ID."""
        sources = await self._number_clip_sources(ctx.guild)
        matched = next((item for item in sources if int(item.get("id") or 0) == source_id), None)
        if matched is None:
            await ctx.send("That clip source ID is not configured for this server.")
            return
        async with self.config.guild(ctx.guild).clip_sources() as stored:
            stored[:] = [item for item in stored if int(item.get("id") or 0) != source_id]
        await ctx.send(f"Removed clip source `{source_id}`: **{matched.get('name')}**.")

    @rocketleagueset_clips.command(name="sources")
    async def rocketleagueset_clips_sources(self, ctx: commands.Context):
        """List configured clip sources and their removal IDs."""
        sources = await self._number_clip_sources(ctx.guild)
        if not sources:
            await ctx.send("This server has no clip sources configured.")
            return
        lines = [
            f"- ID `{item.get('id')}` • **{item.get('name')}** • {str(item.get('provider')).title()} • <{item.get('url')}>"
            for item in sources
        ]
        lines.append(
            f"\nRemove one with `{ctx.clean_prefix}rocketleagueset clips sourceremove <id>`."
        )
        await ctx.send("**Configured Rocket League clip sources**\n" + "\n".join(lines))

    @rocketleagueset_clips.command(name="interval")
    async def rocketleagueset_clips_interval(
        self, ctx: commands.Context, hours: commands.Range[int, 1, 168]
    ):
        """Set the approximate posting interval in hours (1-168)."""
        seconds = int(hours) * 60 * 60
        await self.config.guild(ctx.guild).clip_interval.set(seconds)
        await self.config.guild(ctx.guild).clip_next_post.set(self._next_clip_post(seconds))
        await ctx.send(f"Automatic clips will post about every **{hours} hour{'s' if hours != 1 else ''}**.")

    @rocketleagueset_clips.command(name="maxlength")
    async def rocketleagueset_clips_maxlength(
        self, ctx: commands.Context, seconds: commands.Range[int, 15, 600]
    ):
        """Set the longest eligible clip in seconds (15-600)."""
        await self.config.guild(ctx.guild).clip_max_length.set(int(seconds))
        await ctx.send(f"Only clips up to **{seconds} seconds** long will be posted.")

    @rocketleagueset_clips.command(name="enable")
    async def rocketleagueset_clips_enable(self, ctx: commands.Context):
        """Enable automatic random clip posts."""
        settings = await self.config.guild(ctx.guild).all()
        if not settings.get("clip_channel_id"):
            await ctx.send(
                f"Choose a channel first with `{ctx.clean_prefix}rocketleagueset clips channel #channel`."
            )
            return
        if not settings.get("clip_sources"):
            await ctx.send(
                f"Add a source first with `{ctx.clean_prefix}rocketleagueset clips sourceadd <url>`."
            )
            return
        interval = int(settings.get("clip_interval") or CLIP_DEFAULT_INTERVAL)
        await self.config.guild(ctx.guild).clip_enabled.set(True)
        await self.config.guild(ctx.guild).clip_next_post.set(self._next_clip_post(interval))
        await ctx.send("Automatic Rocket League clip posts are enabled.")

    @rocketleagueset_clips.command(name="disable")
    async def rocketleagueset_clips_disable(self, ctx: commands.Context):
        """Disable automatic clip posts without removing settings."""
        await self.config.guild(ctx.guild).clip_enabled.set(False)
        await ctx.send("Automatic Rocket League clip posts are disabled.")

    @rocketleagueset_clips.command(name="status")
    async def rocketleagueset_clips_status(self, ctx: commands.Context):
        """Show this server's automatic clip settings."""
        settings = await self.config.guild(ctx.guild).all()
        channel = ctx.guild.get_channel(int(settings["clip_channel_id"])) if settings.get("clip_channel_id") else None
        sources = await self._number_clip_sources(ctx.guild)
        interval = int(settings.get("clip_interval") or CLIP_DEFAULT_INTERVAL) // 3600
        next_post = int(settings.get("clip_next_post") or 0)
        lines = [
            f"Status: **{'enabled' if settings.get('clip_enabled') else 'disabled'}**",
            f"Channel: {channel.mention if channel else 'not configured'}",
            f"Sources: **{len(sources)}**",
            f"Maximum length: **{int(settings.get('clip_max_length') or CLIP_DEFAULT_MAX_LENGTH)} seconds**",
            f"Approximate interval: **{interval} hour{'s' if interval != 1 else ''}**",
        ]
        if settings.get("clip_enabled") and next_post:
            lines.append(f"Next post: <t:{next_post}:R>")
        await ctx.send("**Rocket League clip posting**\n" + "\n".join(lines))

    @rocketleagueset_clips.command(name="refresh")
    async def rocketleagueset_clips_refresh(self, ctx: commands.Context):
        """Refresh all configured clip-source caches now."""
        sources = await self._number_clip_sources(ctx.guild)
        if not sources:
            await ctx.send("This server has no clip sources configured.")
            return
        refreshed = 0
        failures = []
        async with ctx.typing():
            for source in sources:
                try:
                    await self._cached_clips(source, force=True)
                    refreshed += 1
                except ClipSourceError as exc:
                    failures.append(
                        f"{source.get('name')}: "
                        f"{await self._provider_error_message(ctx, exc)}"
                    )
        message = f"Refreshed **{refreshed}/{len(sources)}** clip sources."
        if failures:
            message += "\n" + "\n".join(f"- {item}" for item in failures[:5])
        await ctx.send(message)

    @rocketleagueset_clips.command(name="postnow")
    async def rocketleagueset_clips_postnow(self, ctx: commands.Context):
        """Post one unseen eligible clip in the configured channel now."""
        try:
            async with ctx.typing():
                clip = await self._post_clip_for_guild(ctx.guild)
        except (ClipSourceError, discord.Forbidden, discord.HTTPException) as exc:
            await ctx.send(
                "A clip could not be posted: "
                + await self._provider_error_message(ctx, exc)
            )
            return
        await ctx.send(f"Posted **{discord.utils.escape_markdown(str(clip.get('title') or 'Rocket League clip'))}**.")

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

    @staticmethod
    def _startgg_source(tournament: RLCSTournament) -> dict:
        entrants = [event.entrants for event in tournament.events if event.entrants is not None]
        team_sizes = {event.entrant_size_min for event in tournament.events if event.entrant_size_min}
        formats = [event.name for event in tournament.events if event.name]
        location = ", ".join(filter(None, (tournament.city, tournament.state, tournament.country)))
        fingerprint_data = "|".join(
            [
                str(tournament.id),
                tournament.slug,
                str(tournament.start_at),
                str(tournament.end_at),
                *(
                    f"{event.id}:{event.start_at}:{event.state}:{event.entrants}:"
                    + ",".join(
                        f"{standing.placement}:{standing.entrant_id}:{standing.entrant_name}"
                        for standing in event.standings
                    )
                    for event in tournament.events
                ),
            ]
        )
        cached_at = int(time.time())
        return {
            "provider": "startgg", "key": tournament.slug, "url": tournament.url,
            "source_origin": "direct start.gg tournament",
            "provider_id": tournament.id, "fingerprint": hashlib.sha256(fingerprint_data.encode()).hexdigest(),
            "last_seen": cached_at, "image_url": tournament.image_url,
            "name": tournament.name, "start_at": tournament.start_at,
            "end_at": tournament.end_at, "state": tournament.tournament_state,
            "registration_closes_at": tournament.registration_closes_at,
            "description": None, "format": " / ".join(formats[:3]) or None,
            "team_size": next(iter(team_sizes)) if len(team_sizes) == 1 else None,
            "registered_entrants": sum(entrants) if entrants else None,
            "entrant_label": "teams" if team_sizes and max(team_sizes) > 1 else "players",
            "entrant_capacity": None,
            "registration_open": tournament.registration_open,
            "registration_url": tournament.url if tournament.registration_open else None,
            "events": [
                {
                    "id": event.id,
                    "name": event.name,
                    "slug": event.slug,
                    "start_at": event.start_at,
                    "state": event.state,
                    "registered_entrants": event.entrants,
                    "team_size": event.entrant_size_min,
                    "standings": [
                        {
                            "placement": standing.placement,
                            "entrant_id": standing.entrant_id,
                            "entrant_name": standing.entrant_name,
                            "is_final": standing.is_final,
                            "record": standing.record,
                            "provider_points": standing.provider_points,
                        }
                        for standing in event.standings
                    ],
                }
                for event in tournament.events
            ],
            "prize": None, "location": location or ("Online" if tournament.is_online else None),
            "cached_at": cached_at,
        }

    async def _fetch_startgg_source(self, key: str) -> dict:
        client = await self.get_client()
        if client is None:
            raise StartGGError(
                "A start.gg developer token is not configured.",
                setup_command="set api startgg token,YOUR_TOKEN",
            )
        tournament = await client.tournament(key)
        if tournament is None:
            raise StartGGError("That start.gg URL does not contain a visible Rocket League tournament.")
        return self._startgg_source(tournament)

    async def _fetch_league_source(self, key: str) -> dict:
        client = await self.get_client()
        if client is None:
            raise StartGGError(
                "A start.gg developer token is not configured.",
                setup_command="set api startgg token,YOUR_TOKEN",
            )
        league = await client.league(key)
        if league is None:
            raise StartGGError("That start.gg league could not be found through the supported API.")
        return {
            "key": key,
            "name": league.name,
            "url": league.url,
            "enabled": True,
            "cached_at": int(time.time()),
            "tournaments": [
                {**self._startgg_source(item), "source_origin": "start.gg league"}
                for item in league.tournaments
            ],
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
                    "Challonge credentials are not configured.",
                    setup_command=(
                        "set api challonge client_id,YOUR_ID client_secret,YOUR_SECRET"
                    ),
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

    async def _number_league_sources(self, guild) -> list[dict]:
        async with self.config.guild(guild).league_sources() as sources:
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

    @rocketleagueset.command(name="leagueadd")
    async def rocketleagueset_leagueadd(self, ctx: commands.Context, *, url: str):
        """Subscribe this server to a complete start.gg league URL."""
        try:
            key = self._league_source_from_url(url)
            source = await self._fetch_league_source(key)
        except (ValueError, StartGGError) as exc:
            await ctx.send(await self._provider_error_message(ctx, exc))
            return
        await self._number_league_sources(ctx.guild)
        async with self.config.guild(ctx.guild).league_sources() as sources:
            if any(item.get("key") == key for item in sources):
                await ctx.send(f"**{source['name']}** is already subscribed for this server.")
                return
            if len(sources) >= 10:
                await ctx.send("A server can subscribe to at most 10 start.gg leagues.")
                return
            source["id"] = max((int(item.get("id") or 0) for item in sources), default=0) + 1
            sources.append(source)
        count = len(source.get("tournaments") or [])
        await ctx.send(
            f"Subscribed to **{source['name']}** as league ID `{source['id']}`; "
            f"cached {count} Rocket League tournament{'s' if count != 1 else ''}."
        )

    @rocketleagueset.command(name="leagues")
    async def rocketleagueset_leagues(self, ctx: commands.Context):
        """List this server's start.gg league subscriptions."""
        sources = await self._number_league_sources(ctx.guild)
        if not sources:
            await ctx.send("This server has no start.gg league subscriptions.")
            return
        lines = []
        for item in sources:
            state = "enabled" if item.get("enabled", True) else "disabled"
            count = len(item.get("tournaments") or [])
            lines.append(f"- ID `{item['id']}` • **{item.get('name') or item.get('key')}** • {state} • {count} cached • <{item.get('url')}>")
        await ctx.send("**start.gg league subscriptions**\n" + "\n".join(lines))

    @rocketleagueset.command(name="leaguerefresh")
    async def rocketleagueset_leaguerefresh(self, ctx: commands.Context, league_id: Optional[int] = None):
        """Refresh one league subscription by ID, or every subscription."""
        sources = await self._number_league_sources(ctx.guild)
        selected = [item for item in sources if league_id is None or int(item.get("id") or 0) == league_id]
        if not selected:
            await ctx.send("That league ID is not configured for this server.")
            return
        replacements = {}
        try:
            for item in selected:
                updated = await self._fetch_league_source(str(item.get("key")))
                updated["id"] = item.get("id")
                updated["enabled"] = item.get("enabled", True)
                replacements[int(item["id"])] = updated
        except StartGGError as exc:
            await ctx.send(await self._provider_error_message(ctx, exc))
            return
        async with self.config.guild(ctx.guild).league_sources() as stored:
            stored[:] = [replacements.get(int(item.get("id") or 0), item) for item in stored]
        await ctx.send(f"Refreshed {len(replacements)} start.gg league subscription{'s' if len(replacements) != 1 else ''}.")

    @rocketleagueset.command(name="leaguedisable")
    async def rocketleagueset_leaguedisable(self, ctx: commands.Context, league_id: int):
        """Disable automatic refresh and display for a league subscription."""
        sources = await self._number_league_sources(ctx.guild)
        if not any(int(item.get("id") or 0) == league_id for item in sources):
            await ctx.send("That league ID is not configured for this server.")
            return
        async with self.config.guild(ctx.guild).league_sources() as stored:
            for item in stored:
                if int(item.get("id") or 0) == league_id:
                    item["enabled"] = False
        await ctx.send(f"Disabled start.gg league subscription `{league_id}`.")

    @rocketleagueset.command(name="leagueremove")
    async def rocketleagueset_leagueremove(self, ctx: commands.Context, league_id: int):
        """Remove a start.gg league subscription and its cached tournaments."""
        sources = await self._number_league_sources(ctx.guild)
        matched = next((item for item in sources if int(item.get("id") or 0) == league_id), None)
        if matched is None:
            await ctx.send("That league ID is not configured for this server.")
            return
        async with self.config.guild(ctx.guild).league_sources() as stored:
            stored[:] = [item for item in stored if int(item.get("id") or 0) != league_id]
        await ctx.send(f"Removed league `{league_id}`: **{matched.get('name') or matched.get('key')}**.")

    @rocketleagueset.command(name="tournamentadd")
    async def rocketleagueset_tournamentadd(self, ctx: commands.Context, *, url: str):
        """Validate, cache, and add a start.gg or Challonge tournament URL."""
        try:
            provider, key = self._tournament_source_from_url(url)
            source = await self._fetch_tournament_source(provider, key)
        except (ValueError, StartGGError) as exc:
            await ctx.send(await self._provider_error_message(ctx, exc))
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
                await ctx.send(await self._provider_error_message(ctx, exc))
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
            await ctx.send(await self._provider_error_message(ctx, exc))
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
    def _league_source_from_url(value: str) -> str:
        value = value.strip().strip("<>")
        parsed = urlparse(value)
        if parsed.scheme != "https" or (parsed.hostname or "").casefold() not in {"start.gg", "www.start.gg"}:
            raise ValueError("Enter a complete https start.gg league URL.")
        parts = parsed.path.strip("/").split("/")
        if len(parts) < 2 or parts[0].casefold() != "league" or not parts[1]:
            raise ValueError("Use a direct URL in the form https://www.start.gg/league/name.")
        return normalize_league_slug(value)

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
        results_before = await self._cached_blast_results()
        try:
            tournaments, refreshed = await self._refresh_blast()
        except BlastError as exc:
            await ctx.send(await self._provider_error_message(ctx, exc))
            return
        if not refreshed:
            next_at = previous_attempt + BLAST_FETCH_INTERVAL
            results_after = await self._cached_blast_results()
            updated = sum(
                1 for slug, value in results_after.items()
                if results_before.get(slug) != value
            )
            result_note = (
                f" Official results updated for {updated} event(s)."
                if updated else " No new official results were available."
            )
            await ctx.send(
                f"The BLAST schedule was already requested this week. Next allowed refresh: "
                f"<t:{next_at}:F> (<t:{next_at}:R>).{result_note}"
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
