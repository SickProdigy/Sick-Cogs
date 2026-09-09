from __future__ import annotations

import logging
from typing import Optional

import aiohttp
import discord
from redbot.core import commands
from redbot.core.bot import Red

from .api import RLCSTournament, StartGGClient, StartGGError


log = logging.getLogger("red.sick-cogs.RocketLeague")
STARTGG_TOKEN_NAMESPACE = "startgg"
USER_AGENT = "Sick-Cogs-RocketLeague/1.0.0 (+https://github.com/SickProdigy/Sick-Cogs)"


class RocketLeague(commands.Cog):
    """Supported Rocket League esports information."""

    __author__ = ["SickProdigy"]
    __version__ = "1.0.0"

    def __init__(self, bot: Red):
        self.bot = bot
        self.session: Optional[aiohttp.ClientSession] = None

    async def red_delete_data_for_user(self, **kwargs):
        """This cog stores no user data."""
        return

    def cog_unload(self):
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

    @commands.group(name="rlcs", invoke_without_command=True)
    @commands.bot_has_permissions(embed_links=True)
    async def rlcs(self, ctx: commands.Context):
        """Look up supported Rocket League esports information."""
        await ctx.send_help()

    @rlcs.command(name="upcoming")
    @commands.bot_has_permissions(embed_links=True)
    async def rlcs_upcoming(self, ctx: commands.Context, limit: commands.Range[int, 1, 10] = 5):
        """Show upcoming RLCS tournaments listed by start.gg."""
        client = await self.require_client(ctx)
        if client is None:
            return
        async with ctx.typing():
            try:
                tournaments = await client.upcoming_rlcs(limit)
            except StartGGError as exc:
                await ctx.send(str(exc))
                return

        if not tournaments:
            await ctx.send(
                "start.gg does not currently list any upcoming **start.gg-hosted** RLCS "
                "tournaments. This does not mean the RLCS schedule is empty; major LAN events "
                "may only appear on the official schedule: "
                "<https://www.rocketleague.com/competitive/schedule>"
            )
            return

        embed = discord.Embed(
            title="Upcoming RLCS tournaments",
            description="Supported tournament data from start.gg.",
            color=discord.Color.blue(),
        )
        for tournament in tournaments:
            embed.add_field(
                name=tournament.name[:256],
                value=self._tournament_summary(tournament),
                inline=False,
            )
        embed.set_footer(text="Source: start.gg • Times shown in your local Discord timezone")
        await ctx.send(embed=embed)

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
