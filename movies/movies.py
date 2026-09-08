import asyncio
import datetime
import logging
import random
from typing import Any, Dict, List, Optional, Union

import aiohttp
import discord
from discord.ext import tasks
from redbot.core import Config, checks, commands
from redbot.core.bot import Red
from redbot.core.utils import can_user_send_messages_in
from redbot.core.utils.chat_formatting import humanize_list

log = logging.getLogger("red.sick-cogs.MovieReleases")

CONFIG_IDENTIFIER = 924771009
TMDB_TOKEN_NAMESPACE = "tmdb"
TMDB_API_URL = "https://api.themoviedb.org/3/discover/movie"
TMDB_SEARCH_URL = "https://api.themoviedb.org/3/search/movie"
TMDB_DETAILS_URL = "https://api.themoviedb.org/3/movie/{movie_id}"
TMDB_POPULAR_URL = "https://api.themoviedb.org/3/movie/popular"
TMDB_IMAGE_BASE = "https://image.tmdb.org/t/p/w500"
TMDB_MOVIE_URL = "https://www.themoviedb.org/movie/{movie_id}"
USER_AGENT = "Sick-Cogs-MovieReleases/1.1.0 (+https://github.com/SickProdigy/Sick-Cogs)"
GuildMessageable = Union[discord.TextChannel, discord.VoiceChannel, discord.StageChannel, discord.Thread]


def utc_today() -> datetime.date:
    return datetime.datetime.now(datetime.timezone.utc).date()


class MovieReleases(commands.Cog):
    """Post new movie release announcements from TMDb."""

    __author__ = ["SickProdigy"]
    __version__ = "1.1.0"

    default_guild = {
        "enabled": False,
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

    async def get_api_key(self) -> Optional[str]:
        """Return the bot owner's TMDb API key from Red's shared token store."""
        tokens = await self.bot.get_shared_api_tokens(TMDB_TOKEN_NAMESPACE)
        api_key = str(tokens.get("api_key") or "").strip()
        return api_key or None

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
        api_key = await self.get_api_key()
        if not settings["enabled"] and not force:
            return 0
        if not api_key or not settings["channel_id"]:
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

        movies = await self.fetch_releases(settings, api_key)
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

    async def fetch_releases(self, settings: Dict[str, Any], api_key: str) -> List[Dict[str, Any]]:
        today = utc_today()
        start = today - datetime.timedelta(days=max(0, int(settings.get("days_back", 0))))
        end = today + datetime.timedelta(days=max(0, int(settings.get("days_ahead", 7))))
        params = {
            "api_key": api_key,
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

    async def search_movies(
        self, api_key: str, query: str, year: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """Search TMDb for movies, optionally limited to a release year."""
        session = await self.get_session()
        params = {
            "api_key": api_key,
            "query": query,
            "language": "en-US",
            "include_adult": "false",
            "page": 1,
        }
        if year is not None:
            params["year"] = year

        async with session.get(TMDB_SEARCH_URL, params=params) as response:
            if response.status == 401:
                raise RuntimeError("TMDb rejected the configured API key.")
            response.raise_for_status()
            data = await response.json()
        return [movie for movie in data.get("results", []) if movie.get("id")]

    async def fetch_movie_suggestions(self, api_key: str, amount: int = 3) -> List[Dict[str, Any]]:
        """Return a random selection from TMDb's current popular movies."""
        session = await self.get_session()
        params = {"api_key": api_key, "language": "en-US", "region": "US", "page": 1}
        async with session.get(TMDB_POPULAR_URL, params=params) as response:
            if response.status == 401:
                raise RuntimeError("TMDb rejected the configured API key.")
            response.raise_for_status()
            data = await response.json()

        movies = [
            movie
            for movie in data.get("results", [])
            if movie.get("id") and movie.get("title") and not movie.get("adult", False)
        ]
        return random.sample(movies, min(amount, len(movies)))

    async def fetch_movie_details(self, api_key: str, movie_id: int) -> Dict[str, Any]:
        """Fetch full details for one TMDb movie ID."""
        session = await self.get_session()
        details_url = TMDB_DETAILS_URL.format(movie_id=movie_id)
        async with session.get(
            details_url, params={"api_key": api_key, "language": "en-US"}
        ) as response:
            if response.status == 401:
                raise RuntimeError("TMDb rejected the configured API key.")
            response.raise_for_status()
            return await response.json()

    @staticmethod
    def movie_lookup_embed(movie: Dict[str, Any]) -> discord.Embed:
        """Build the detailed embed used by the user-facing movie lookup."""
        title = movie.get("title") or "Untitled movie"
        release_date = movie.get("release_date") or "Unknown"
        year = release_date[:4] if release_date != "Unknown" else None
        heading = f"{title} ({year})" if year else title
        overview = movie.get("overview") or "No synopsis is available."

        embed = discord.Embed(
            title=heading,
            url=TMDB_MOVIE_URL.format(movie_id=movie["id"]),
            description=overview[:4096],
            colour=discord.Colour.blurple(),
        )
        tagline = movie.get("tagline")
        if tagline:
            embed.description = f"*{tagline}*\n\n{embed.description}"
            embed.description = embed.description[:4096]

        embed.add_field(name="Release date", value=release_date, inline=True)
        runtime = movie.get("runtime")
        if runtime:
            hours, minutes = divmod(int(runtime), 60)
            runtime_text = f"{hours}h {minutes}m" if hours else f"{minutes}m"
            embed.add_field(name="Runtime", value=runtime_text, inline=True)
        status = movie.get("status")
        if status:
            embed.add_field(name="Status", value=status, inline=True)

        genres = [genre.get("name") for genre in movie.get("genres", []) if genre.get("name")]
        if genres:
            embed.add_field(name="Genres", value=", ".join(genres), inline=False)
        vote_average = movie.get("vote_average")
        vote_count = movie.get("vote_count")
        if vote_average is not None:
            rating = f"{float(vote_average):.1f}/10"
            if vote_count is not None:
                rating += f" ({int(vote_count):,} votes)"
            embed.add_field(name="TMDb rating", value=rating, inline=False)

        poster_path = movie.get("poster_path")
        if poster_path:
            embed.set_thumbnail(url=f"{TMDB_IMAGE_BASE}{poster_path}")
        embed.set_footer(text="Movie data provided by TMDb")
        return embed

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
    @commands.command(name="movies", aliases=["movie", "moviereleases"])
    @commands.bot_has_permissions(embed_links=True)
    async def movies(self, ctx: commands.Context, *, title: Optional[str] = None):
        """Search for a movie by title, or show movie suggestions.

        Use `[p]movies <title>` or the `[p]movie <title>` alias. Add a release
        year when titles are ambiguous, such as `[p]movies dune 1984`.

        Bot owners configure the shared TMDb key with
        `[p]set api tmdb api_key,YOUR_KEY` in a DM or private channel.

        Server moderators configure automatic release announcements with
        `[p]movieset channel`, `[p]movieset role`, and `[p]movieset enable`.
        Use `[p]help movieset` for every announcement setting.
        """
        if title is not None:
            await self._lookup_movie(ctx, title)
            return

        prefix = ctx.clean_prefix
        embed = discord.Embed(
            title="Find a Movie",
            description=(
                f"Search by title with `{prefix}movies <title>`. You can include a year "
                f"for a specific version, like `{prefix}movies dune 1984`.\n\n"
                f"`{prefix}movie <title>` works too."
            ),
            colour=discord.Colour.blurple(),
        )

        api_key = await self.get_api_key()
        if api_key:
            try:
                suggestions = await self.fetch_movie_suggestions(api_key)
            except (RuntimeError, aiohttp.ClientError, asyncio.TimeoutError):
                log.warning("Unable to load TMDb suggestions for guild %s", ctx.guild.id)
                suggestions = []
            if suggestions:
                lines = []
                for movie in suggestions:
                    movie_title = (movie.get("title") or "Untitled").replace("`", "'")
                    release_date = movie.get("release_date")
                    year = release_date[:4] if release_date else "Unknown year"
                    lines.append(f"`{prefix}movies {movie_title}` — {year}")
                embed.add_field(name="Need an idea? Try one of these", value="\n".join(lines), inline=False)
        else:
            embed.add_field(
                name="Movie search unavailable",
                value="The bot's TMDb connection has not been configured yet.",
                inline=False,
            )

        embed.set_footer(text=f"Movie data provided by TMDb • Moderators: {prefix}help movies")
        await ctx.send(embed=embed)

    async def _lookup_movie(self, ctx: commands.Context, title: str):
        """Look up information about a movie by title and optional release year."""
        title = title.strip()
        if len(title) < 2 or len(title) > 200:
            await ctx.send("Movie titles must be between 2 and 200 characters long.")
            return

        query = title
        year = None
        title_part, separator, possible_year = title.rpartition(" ")
        possible_year = possible_year.strip("()")
        if separator and len(possible_year) == 4 and possible_year.isdigit():
            query = title_part.strip()
            year = int(possible_year)

        api_key = await self.get_api_key()
        if not api_key:
            await ctx.send(
                f"The bot owner needs to set the TMDb API key with "
                f"`{ctx.clean_prefix}set api tmdb api_key,YOUR_KEY` first."
            )
            return

        async with ctx.typing():
            try:
                matches = await self.search_movies(api_key, query, year)
            except RuntimeError as exc:
                await ctx.send(str(exc))
                return
            except (aiohttp.ClientError, asyncio.TimeoutError):
                log.exception("TMDb movie search failed for guild %s", ctx.guild.id)
                await ctx.send("TMDb is unavailable right now. Please try again later.")
                return

        if not matches:
            await ctx.send(f"I couldn't find a movie matching **{title}**.")
            return

        exact_matches = [
            match
            for match in matches
            if (match.get("title") or "").casefold() == query.casefold()
            or (match.get("original_title") or "").casefold() == query.casefold()
        ]
        choices = exact_matches if len(exact_matches) > 1 and year is None else matches[:1]
        selected = choices[0]

        if len(choices) > 1:
            choices = choices[:5]
            lines = []
            for index, match in enumerate(choices, start=1):
                release_date = match.get("release_date")
                release_year = release_date[:4] if release_date else "Unknown year"
                lines.append(
                    f"`{index}.` **{match.get('title') or 'Untitled'}** ({release_year})"
                )
            await ctx.send(
                "I found more than one movie with that title. Reply with a number:\n"
                + "\n".join(lines)
                + f"\nYou can also include a year next time, such as `{ctx.clean_prefix}movies dune 1984`."
            )

            def check(message: discord.Message) -> bool:
                return (
                    message.author == ctx.author
                    and message.channel == ctx.channel
                    and message.content.isdigit()
                    and 1 <= int(message.content) <= len(choices)
                )

            try:
                reply = await self.bot.wait_for("message", check=check, timeout=30)
            except asyncio.TimeoutError:
                await ctx.send("Movie selection timed out. Run the command again when you're ready.")
                return
            selected = choices[int(reply.content) - 1]

        async with ctx.typing():
            try:
                movie = await self.fetch_movie_details(api_key, int(selected["id"]))
            except RuntimeError as exc:
                await ctx.send(str(exc))
                return
            except (aiohttp.ClientError, asyncio.TimeoutError):
                log.exception("TMDb movie detail lookup failed for guild %s", ctx.guild.id)
                await ctx.send("TMDb is unavailable right now. Please try again later.")
                return
        await ctx.send(embed=self.movie_lookup_embed(movie))

    @commands.guild_only()
    @commands.group(
        name="movieset", aliases=["moviereleaseset"], invoke_without_command=True
    )
    @checks.mod_or_permissions(manage_guild=True)
    async def movieset(self, ctx: commands.Context):
        """Configure new movie release announcements."""
        await ctx.send_help(ctx.command)

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
    async def movieset_role(self, ctx: commands.Context, role: discord.Role):
        """Set the role mentioned by automatic movie release announcements.

        Members with this role are notified when the scheduled feed posts a new movie.
        The cog does not assign the role to members, and this does not affect manual
        movie lookups. Use `[p]movieset roleclear` to stop mentioning a role.
        """
        author_can_mention = role.mentionable or ctx.author.guild_permissions.mention_everyone
        bot_can_mention = role.mentionable or ctx.guild.me.guild_permissions.mention_everyone
        if not author_can_mention or not bot_can_mention:
            await ctx.send(
                "Both you and I must be allowed to mention that role before it can be used for announcements."
            )
            return

        await self.config.guild(ctx.guild).role_id.set(role.id)
        await ctx.send(
            f"Automatic movie release posts will now mention {role.mention}. "
            "This does not affect manual movie lookups.",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @movieset.command(name="roleclear", aliases=["clearrole"])
    async def movieset_role_clear(self, ctx: commands.Context):
        """Stop mentioning a notification role in automatic release posts."""
        await self.config.guild(ctx.guild).role_id.set(None)
        await ctx.send("Automatic movie release posts will no longer mention a role.")

    @movieset.command(name="enable", aliases=["enabled"])
    async def movieset_enable(self, ctx: commands.Context):
        """Enable hourly automatic movie release posts.

        This starts the scheduled announcement feed. Manual `movies <title>` lookups
        work regardless of whether the feed is enabled.
        """
        settings = await self.config.guild(ctx.guild).all()
        if not settings["channel_id"]:
            await ctx.send(
                f"Set a destination first with `{ctx.clean_prefix}movieset channel`."
            )
            return
        if not await self.get_api_key():
            await ctx.send(
                "The bot owner must configure the TMDb API key with "
                f"`{ctx.clean_prefix}set api tmdb api_key,YOUR_KEY` first."
            )
            return
        await self.config.guild(ctx.guild).enabled.set(True)
        await ctx.send("Hourly automatic movie release posts are now **enabled**.")

    @movieset.command(name="disable", aliases=["disabled"])
    async def movieset_disable(self, ctx: commands.Context):
        """Disable hourly automatic movie release posts.

        This stops only the scheduled announcement feed. Manual `movies <title>`
        lookups remain available.
        """
        await self.config.guild(ctx.guild).enabled.set(False)
        await ctx.send("Hourly automatic movie release posts are now **disabled**.")

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
        api_key = await self.get_api_key()
        if not api_key:
            await ctx.send(
                "The bot owner must configure the TMDb API key with "
                f"`{ctx.clean_prefix}set api tmdb api_key,YOUR_KEY` first."
            )
            return
        async with ctx.typing():
            try:
                movies = await self.fetch_releases(settings, api_key)
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
    @commands.bot_has_permissions(embed_links=True)
    async def movieset_settings(self, ctx: commands.Context):
        """Show current movie release settings."""
        settings = await self.config.guild(ctx.guild).all()
        api_key = await self.get_api_key()
        channel = ctx.guild.get_channel_or_thread(settings["channel_id"]) if settings["channel_id"] else None
        role = ctx.guild.get_role(settings["role_id"]) if settings["role_id"] else None
        embed = discord.Embed(title="Movie release settings", colour=discord.Colour.blurple())
        embed.add_field(name="Enabled", value=str(settings["enabled"]), inline=True)
        embed.add_field(name="Channel", value=channel.mention if channel else "Not set", inline=True)
        embed.add_field(name="Role", value=role.mention if role else "None", inline=True)
        embed.add_field(name="Max per day", value=str(settings["max_per_day"]), inline=True)
        embed.add_field(name="Window", value=f"-{settings['days_back']} / +{settings['days_ahead']} days", inline=True)
        embed.add_field(name="Minimum votes", value=str(settings["min_vote_count"]), inline=True)
        embed.add_field(name="API key", value="Set" if api_key else "Not set", inline=True)
        embed.add_field(name="Last checked", value=settings["last_checked"] or "Never", inline=False)
        await ctx.send(embed=embed)
