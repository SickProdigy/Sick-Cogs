import asyncio
import datetime
import logging
from typing import Any, Dict, List, Optional, Union

import aiohttp
import discord
from discord.ext import tasks
from redbot.core import Config, checks, commands
from redbot.core.bot import Red
from redbot.core.utils import can_user_send_messages_in
from redbot.core.utils.chat_formatting import humanize_list

log = logging.getLogger("red.Sick-Cogs.MovieReleases")

CONFIG_IDENTIFIER = 924771009
TMDB_API_URL = "https://api.themoviedb.org/3/discover/movie"
TMDB_IMAGE_BASE = "https://image.tmdb.org/t/p/w500"
TMDB_MOVIE_URL = "https://www.themoviedb.org/movie/{movie_id}"
USER_AGENT = "Sick-Cogs-MovieReleases/1.0 (+https://gitea.rcs1.top/sickprodigy/Sick-Cogs)"
GuildMessageable = Union[discord.TextChannel, discord.VoiceChannel, discord.StageChannel, discord.Thread]


def utc_today() -> datetime.date:
    return datetime.datetime.now(datetime.timezone.utc).date()


class MovieReleases(commands.Cog):
    """Post new movie release announcements from TMDb."""

    __author__ = ["SickProdigy", "chatgpt-codex"]
    __version__ = "1.0.0"

    default_guild = {
        "enabled": False,
        "api_key": None,
        "channel_id": None,
        "role_id": None,
        "max_per_day": 3,
        "days_back": 0,
        "days_ahead": 7,
        "min_vote_count": 5,
        "last_checked": None,
        "posted_ids": [],
        "posted_today": {"date": None, "count": 0},
    }

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=CONFIG_IDENTIFIER, force_registration=True)
        self.config.register_guild(**self.default_guild)
        self.session: Optional[aiohttp.ClientSession] = None
        self.release_loop.start()

    async def red_delete_data_for_user(self, **kwargs):
        """This cog stores no user data."""
        return

    def cog_unload(self):
        self.release_loop.cancel()
        if self.session and not self.session.closed:
            self.bot.loop.create_task(self.session.close())

    async def get_session(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            timeout = aiohttp.ClientTimeout(total=30)
            self.session = aiohttp.ClientSession(timeout=timeout, headers={"User-Agent": USER_AGENT})
        return self.session

    @tasks.loop(hours=1)
    async def release_loop(self):
        await self.bot.wait_until_red_ready()
        for guild in list(self.bot.guilds):
            if await self.bot.cog_disabled_in_guild(self, guild):
                continue
            try:
                await self.check_guild(guild)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Movie release check failed for guild %s", guild.id)

    @release_loop.before_loop
    async def before_release_loop(self):
        await self.bot.wait_until_red_ready()

    async def check_guild(self, guild: discord.Guild, *, force: bool = False) -> int:
        settings = await self.config.guild(guild).all()
        if not settings["enabled"] and not force:
            return 0
        if not settings["api_key"] or not settings["channel_id"]:
            return 0

        channel = await self.get_channel(guild, int(settings["channel_id"]))
        if not channel:
            return 0

        today = utc_today().isoformat()
        posted_today = settings.get("posted_today") or {"date": None, "count": 0}
        if posted_today.get("date") != today:
            posted_today = {"date": today, "count": 0}

        remaining = max(0, int(settings["max_per_day"]) - int(posted_today.get("count", 0)))
        if remaining <= 0 and not force:
            return 0

        movies = await self.fetch_releases(settings)
        posted_ids = [int(movie_id) for movie_id in settings.get("posted_ids", [])]
        new_movies = [movie for movie in movies if int(movie["id"]) not in posted_ids]
        if not new_movies:
            await self.config.guild(guild).last_checked.set(datetime.datetime.now(datetime.timezone.utc).isoformat())
            return 0

        limit = 1 if force else remaining
        sent = 0
        for movie in new_movies[:limit]:
            await self.send_movie(channel, movie, settings.get("role_id"))
            posted_ids.append(int(movie["id"]))
            posted_today["count"] = int(posted_today.get("count", 0)) + 1
            sent += 1

        # Keep a bounded history so old releases do not grow config forever.
        posted_ids = posted_ids[-500:]
        await self.config.guild(guild).posted_ids.set(posted_ids)
        await self.config.guild(guild).posted_today.set(posted_today)
        await self.config.guild(guild).last_checked.set(datetime.datetime.now(datetime.timezone.utc).isoformat())
        return sent

    async def get_channel(self, guild: discord.Guild, channel_id: int) -> Optional[GuildMessageable]:
        channel = guild.get_channel_or_thread(channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                return None
        if getattr(channel, "guild", None) != guild:
            return None
        if can_user_send_messages_in(guild.me, channel):
            return channel
        return None

    async def fetch_releases(self, settings: Dict[str, Any]) -> List[Dict[str, Any]]:
        today = utc_today()
        start = today - datetime.timedelta(days=max(0, int(settings.get("days_back", 0))))
        end = today + datetime.timedelta(days=max(0, int(settings.get("days_ahead", 7))))
        params = {
            "api_key": settings["api_key"],
            "language": "en-US",
            "region": "US",
            "sort_by": "primary_release_date.asc",
            "include_adult": "false",
            "include_video": "false",
            "page": 1,
            "primary_release_date.gte": start.isoformat(),
            "primary_release_date.lte": end.isoformat(),
            "vote_count.gte": max(0, int(settings.get("min_vote_count", 5))),
            "with_release_type": "2|3",
        }
        session = await self.get_session()
        async with session.get(TMDB_API_URL, params=params) as response:
            if response.status == 401:
                raise RuntimeError("TMDb rejected the configured API key.")
            response.raise_for_status()
            data = await response.json()

        movies = data.get("results", [])
        return [movie for movie in movies if movie.get("id") and movie.get("title")]

    async def send_movie(self, channel: GuildMessageable, movie: Dict[str, Any], role_id: Optional[int]):
        title = movie.get("title") or "Untitled movie"
        release_date = movie.get("release_date") or "Unknown date"
        overview = movie.get("overview") or "No description available."
        if len(overview) > 350:
            overview = f"{overview[:347]}..."

        embed = discord.Embed(
            title=title,
            url=TMDB_MOVIE_URL.format(movie_id=movie["id"]),
            description=overview,
            colour=discord.Colour.blurple(),
        )
        embed.add_field(name="Release date", value=release_date, inline=True)
        vote_average = movie.get("vote_average")
        if vote_average:
            embed.add_field(name="TMDb rating", value=f"{float(vote_average):.1f}/10", inline=True)
        embed.set_footer(text="Movie data from TMDb")
        poster_path = movie.get("poster_path")
        if poster_path:
            embed.set_thumbnail(url=f"{TMDB_IMAGE_BASE}{poster_path}")

        content = None
        if role_id:
            role = channel.guild.get_role(int(role_id))
            if role:
                content = role.mention
        await channel.send(content=content, embed=embed, allowed_mentions=discord.AllowedMentions(roles=True))

    @commands.guild_only()
    @commands.group(name="movieset", aliases=["moviereleaseset"])
    @checks.mod_or_permissions(manage_guild=True)
    async def movieset(self, ctx: commands.Context):
        """Configure new movie release announcements."""
        pass

    @movieset.command(name="apikey")
    async def movieset_apikey(self, ctx: commands.Context, api_key: str):
        """Set the TMDb API key used to fetch movie release data."""
        await self.config.guild(ctx.guild).api_key.set(api_key.strip())
        try:
            await ctx.message.delete()
        except discord.HTTPException:
            pass
        await ctx.send("TMDb API key saved. Enable releases after setting a channel.")

    @movieset.command(name="channel")
    async def movieset_channel(self, ctx: commands.Context, channel: Optional[GuildMessageable] = None):
        """Set the channel that receives movie release posts. Defaults to this channel."""
        channel = channel or ctx.channel
        if not await self.get_channel(ctx.guild, channel.id):
            await ctx.send("I cannot send messages in that channel.")
            return
        await self.config.guild(ctx.guild).channel_id.set(channel.id)
        await ctx.send(f"Movie releases will be posted in {channel.mention}.")

    @movieset.command(name="role")
    async def movieset_role(self, ctx: commands.Context, role: Optional[discord.Role] = None):
        """Set or clear the role mentioned on movie release posts."""
        await self.config.guild(ctx.guild).role_id.set(role.id if role else None)
        if role:
            await ctx.send(f"Movie release posts will mention {role.mention}.")
        else:
            await ctx.send("Movie release posts will not mention a role.")

    @movieset.command(name="enabled")
    async def movieset_enabled(self, ctx: commands.Context, enabled: bool):
        """Enable or disable scheduled movie release posts."""
        settings = await self.config.guild(ctx.guild).all()
        if enabled and (not settings["api_key"] or not settings["channel_id"]):
            await ctx.send("Set both an API key and channel before enabling movie releases.")
            return
        await self.config.guild(ctx.guild).enabled.set(enabled)
        await ctx.send(f"Movie release announcements are now {'enabled' if enabled else 'disabled'}.")

    @movieset.command(name="maxperday")
    async def movieset_maxperday(self, ctx: commands.Context, amount: commands.Range[int, 1, 25]):
        """Set the maximum automatic release posts per day."""
        await self.config.guild(ctx.guild).max_per_day.set(amount)
        await ctx.send(f"Movie release posts are limited to {amount} per day.")

    @movieset.command(name="window")
    async def movieset_window(
        self,
        ctx: commands.Context,
        days_back: commands.Range[int, 0, 30],
        days_ahead: commands.Range[int, 0, 60],
    ):
        """Set the release date window checked by the cog."""
        await self.config.guild(ctx.guild).days_back.set(days_back)
        await self.config.guild(ctx.guild).days_ahead.set(days_ahead)
        await ctx.send(f"Movie release search window set to {days_back} days back and {days_ahead} days ahead.")

    @movieset.command(name="minvotes")
    async def movieset_minvotes(self, ctx: commands.Context, amount: commands.Range[int, 0, 10000]):
        """Set the minimum TMDb vote count required before posting a movie."""
        await self.config.guild(ctx.guild).min_vote_count.set(amount)
        await ctx.send(f"Movies need at least {amount} TMDb vote(s) before posting.")

    @movieset.command(name="clearhistory")
    async def movieset_clearhistory(self, ctx: commands.Context):
        """Clear remembered movie IDs so releases can be posted again."""
        await self.config.guild(ctx.guild).posted_ids.set([])
        await self.config.guild(ctx.guild).posted_today.set({"date": utc_today().isoformat(), "count": 0})
        await ctx.send("Movie release post history cleared.")

    @movieset.command(name="force")
    async def movieset_force(self, ctx: commands.Context):
        """Check now and post the next unposted release even if scheduled posting is disabled."""
        async with ctx.typing():
            try:
                sent = await self.check_guild(ctx.guild, force=True)
            except RuntimeError as exc:
                await ctx.send(str(exc))
                return
        await ctx.send(f"Posted {sent} new movie release announcement(s).")

    @movieset.command(name="preview")
    async def movieset_preview(self, ctx: commands.Context):
        """Preview matching TMDb releases without posting them."""
        settings = await self.config.guild(ctx.guild).all()
        if not settings["api_key"]:
            await ctx.send("Set a TMDb API key first.")
            return
        async with ctx.typing():
            try:
                movies = await self.fetch_releases(settings)
            except RuntimeError as exc:
                await ctx.send(str(exc))
                return
        if not movies:
            await ctx.send("No matching movie releases found in the configured window.")
            return
        lines = [
            f"**{movie.get('title', 'Untitled')}** ({movie.get('release_date') or 'unknown date'})"
            for movie in movies[:10]
        ]
        await ctx.send("Upcoming/recent releases: " + humanize_list(lines))

    @movieset.command(name="settings")
    async def movieset_settings(self, ctx: commands.Context):
        """Show current movie release settings."""
        settings = await self.config.guild(ctx.guild).all()
        channel = ctx.guild.get_channel_or_thread(settings["channel_id"]) if settings["channel_id"] else None
        role = ctx.guild.get_role(settings["role_id"]) if settings["role_id"] else None
        embed = discord.Embed(title="Movie release settings", colour=discord.Colour.blurple())
        embed.add_field(name="Enabled", value=str(settings["enabled"]), inline=True)
        embed.add_field(name="Channel", value=channel.mention if channel else "Not set", inline=True)
        embed.add_field(name="Role", value=role.mention if role else "None", inline=True)
        embed.add_field(name="Max per day", value=str(settings["max_per_day"]), inline=True)
        embed.add_field(name="Window", value=f"-{settings['days_back']} / +{settings['days_ahead']} days", inline=True)
        embed.add_field(name="Minimum votes", value=str(settings["min_vote_count"]), inline=True)
        embed.add_field(name="API key", value="Set" if settings["api_key"] else "Not set", inline=True)
        embed.add_field(name="Last checked", value=settings["last_checked"] or "Never", inline=False)
        await ctx.send(embed=embed)
