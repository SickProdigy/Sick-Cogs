from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

import aiohttp
import discord
from discord.ext import tasks
from redbot.core import Config, checks, commands
from redbot.core.bot import Red

from .api import RLCSTournament, StartGGClient, StartGGError
from .blast import BlastClient, BlastError, BlastTournament, upcoming_tournaments


log = logging.getLogger("red.sick-cogs.RocketLeague")
STARTGG_TOKEN_NAMESPACE = "startgg"
USER_AGENT = "Sick-Cogs-RocketLeague/1.0.0 (+https://github.com/SickProdigy/Sick-Cogs)"
BLAST_FETCH_INTERVAL = 7 * 24 * 60 * 60
ANNOUNCEMENT_LOOKAHEAD = 45 * 24 * 60 * 60
CONFIG_IDENTIFIER = 0x5347524C4353


class RocketLeague(commands.Cog):
    """Supported Rocket League esports information."""

    __author__ = ["SickProdigy"]
    __version__ = "1.0.0"

    def __init__(self, bot: Red):
        self.bot = bot
        self.session: Optional[aiohttp.ClientSession] = None
        self.config = Config.get_conf(self, identifier=CONFIG_IDENTIFIER, force_registration=True)
        self.config.register_global(
            blast_last_attempt=0,
            blast_last_success=0,
            blast_tournaments=[],
            blast_last_error=None,
        )
        self.config.register_guild(channel_id=None, enabled=False, announced={})
        self._blast_lock = asyncio.Lock()
        self.schedule_loop.start()

    async def red_delete_data_for_user(self, **kwargs):
        """This cog stores no user data."""
        return

    def cog_unload(self):
        self.schedule_loop.cancel()
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

    @commands.group(name="rlcs", aliases=["rl"], invoke_without_command=True)
    @commands.bot_has_permissions(embed_links=True)
    async def rlcs(self, ctx: commands.Context):
        """Show the next RLCS event."""
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
        """Show RLCS tournaments scheduled after the next event."""
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

    @commands.group(name="rocketleague", invoke_without_command=True)
    async def rocketleague(self, ctx: commands.Context):
        """Show Rocket League commands."""
        await ctx.send_help()

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

    @rlcs.command(name="event")
    @commands.bot_has_permissions(embed_links=True)
    async def rlcs_event(self, ctx: commands.Context, *, slug_or_url: str):
        """Show a Rocket League tournament from its start.gg URL or slug."""
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

    @commands.group(name="rlcsset", invoke_without_command=True)
    @commands.guild_only()
    @checks.admin_or_permissions(manage_guild=True)
    async def rlcsset(self, ctx: commands.Context):
        """Configure automatic RLCS schedule announcements."""
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
