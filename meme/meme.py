from __future__ import annotations

import asyncio
import time
from typing import Optional

import aiohttp
import discord
from discord.ext import tasks
from redbot.core import Config, checks, commands

from .models import MemeResult
from .providers import (
    ImgurProvider,
    MemeApiProvider,
    ProviderError,
    RedditProvider,
    choose_result,
)

MIN_FEED_MINUTES = 30
SEEN_LIMIT = 250


class Meme(commands.Cog):
    """Find online memes and GIFs and publish scheduled feeds."""

    __author__ = ["SickProdigy"]
    __version__ = "1.0.0"

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=927440181113, force_registration=True)
        self.config.register_guild(feeds={}, seen=[])
        self.session: Optional[aiohttp.ClientSession] = None
        self.reddit: Optional[RedditProvider] = None
        self.meme_api: Optional[MemeApiProvider] = None
        self.imgur: Optional[ImgurProvider] = None
        self._feed_lock = asyncio.Lock()

    async def cog_load(self):
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20))
        self.reddit = RedditProvider(
            self.session, "Sick-Cogs:Meme/1.0 (by /u/SickProdigy)"
        )
        self.meme_api = MemeApiProvider(self.session)
        self.imgur = ImgurProvider(self.session)
        self.feed_loop.start()

    def cog_unload(self):
        self.feed_loop.cancel()
        if self.session and not self.session.closed:
            asyncio.create_task(self.session.close())

    async def red_delete_data_for_user(self, **kwargs):
        """This cog does not store user data."""

    def format_help_for_context(self, ctx: commands.Context) -> str:
        result = super().format_help_for_context(ctx)
        return f"{result}\n\nAuthor: {self.__author__[0]}\nCog Version: {self.__version__}"

    async def _tokens(self, provider: str) -> dict:
        return await self.bot.get_shared_api_tokens(provider)

    async def _results(
        self, provider: str, source: str = "", query: str = "", sort: str = "hot"
    ):
        provider = provider.casefold()
        if provider == "memeapi":
            return await self.meme_api.fetch(source or "memes")
        if provider in {"imgur", "imgur-gif"}:
            tokens = await self._tokens("imgur")
            client_id = tokens.get("client_id")
            if not client_id:
                raise ProviderError(
                    "Imgur is not configured. The bot owner must run "
                    "`[p]set api imgur client_id,<value>`, replacing `[p]` with the bot prefix."
                )
            return await self.imgur.fetch(
                client_id, query=query or source, gifs_only=provider == "imgur-gif"
            )
        if provider == "reddit":
            tokens = await self._tokens("reddit")
            client_id, secret = tokens.get("client_id"), tokens.get("client_secret")
            if not client_id or not secret:
                raise ProviderError(
                    "Reddit is not configured. The bot owner must run "
                    "`[p]set api reddit client_id,<value> client_secret,<value>`, "
                    "replacing `[p]` with the bot prefix."
                )
            return await self.reddit.fetch(
                client_id, secret, subreddit=source or "memes", query=query, sort=sort
            )
        raise ProviderError("That online provider is not supported.")

    @staticmethod
    def _channel_allows(result: MemeResult, channel) -> bool:
        return not result.nsfw or (
            isinstance(channel, discord.TextChannel) and channel.is_nsfw()
        )

    async def _pick(
        self, channel, provider: str, source: str = "", query: str = "", seen=None
    ) -> MemeResult:
        results = await self._results(provider, source, query)
        results = [item for item in results if self._channel_allows(item, channel)]
        return choose_result(results, seen)

    @staticmethod
    async def _send(channel, result: MemeResult):
        embed = discord.Embed(
            title=result.title[:256],
            url=result.source_url,
            description=result.description[:4096] or None,
            color=discord.Color.blurple(),
        )
        embed.set_image(url=result.media_url)
        footer = result.provider + (f" · {result.author}" if result.author else "")
        embed.set_footer(text=footer[:2048])
        await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())

    async def _run_interactive(self, ctx, provider: str, source="", query=""):
        async with ctx.typing():
            try:
                result = await self._pick(ctx.channel, provider, source, query)
            except (ProviderError, aiohttp.ClientError, asyncio.TimeoutError) as exc:
                await ctx.send(str(exc) or "The meme provider did not respond.")
                return
        await self._send(ctx.channel, result)

    @commands.group(name="meme", aliases=["memes"], invoke_without_command=True)
    @commands.bot_has_permissions(embed_links=True)
    async def meme(self, ctx):
        """Post or search for online memes and GIFs.

        Run this command without a subcommand for a random meme from r/memes.
        To add captions to templates, avatars, or attached photos, use
        `[p]memeify` (alias: `[p]imgflip`).

        Administrators can start automatic posting with
        `[p]memeset autopost [community] [minutes]` in the destination channel.
        """
        await self._run_interactive(ctx, "memeapi", "memes")

    @meme.command(name="search")
    @commands.bot_has_permissions(embed_links=True)
    async def meme_search(self, ctx, *, query: str):
        """Search public Imgur posts by title and tags."""
        await self._run_interactive(ctx, "imgur", query=query)

    @meme.command(name="community", aliases=["reddit"])
    @commands.bot_has_permissions(embed_links=True)
    async def meme_community(self, ctx, subreddit: str = "memes"):
        """Get a random Meme API result from a subreddit.

        Meme API is an independent service and is not affiliated with Reddit.
        """
        await self._run_interactive(ctx, "memeapi", subreddit)

    @meme.command(name="gif", aliases=["imgurgif"])
    @commands.bot_has_permissions(embed_links=True)
    async def meme_gif(self, ctx, *, query: str = ""):
        """Post an animated Imgur result, optionally searching by query."""
        await self._run_interactive(ctx, "imgur-gif", query=query)

    @meme.command(name="imgur")
    @commands.bot_has_permissions(embed_links=True)
    async def meme_imgur(self, ctx, *, query: str = ""):
        """Post a random public Imgur result, optionally searching by query."""
        await self._run_interactive(ctx, "imgur", query=query)

    @meme.command(name="sources")
    async def meme_sources(self, ctx):
        """Show online providers and configuration status."""
        reddit, imgur = await asyncio.gather(
            self._tokens("reddit"), self._tokens("imgur")
        )
        reddit_ready = bool(reddit.get("client_id") and reddit.get("client_secret"))
        await ctx.send(
            "**Meme providers**\n"
            "- Meme API: ready (no key required)\n"
            f"- Imgur: {'ready' if imgur.get('client_id') else 'not configured'}\n"
            f"- Official Reddit: {'ready' if reddit_ready else 'not configured'}"
        )

    @commands.group(name="memeset")
    @commands.guild_only()
    @checks.admin_or_permissions(manage_guild=True)
    async def memeset(self, ctx):
        """Configure automatic meme feeds.

        Run this in the destination channel: `[p]memeset autopost [community] [minutes]`

        Example: `[p]memeset autopost pcmasterrace 180`

        Use `[p]memeset feed` for advanced providers and feed management.
        """

    @memeset.group(name="feed")
    async def memeset_feed(self, ctx):
        """Manage automatic meme feeds."""

    async def _create_feed(
        self, ctx, channel: discord.TextChannel, provider: str, source: str,
        interval_minutes: int,
    ):
        provider = provider.casefold()
        if provider not in {"memeapi", "imgur", "imgur-gif", "reddit"}:
            raise commands.BadArgument(
                "Provider must be `memeapi`, `imgur`, `imgur-gif`, or `reddit`."
            )
        if interval_minutes < MIN_FEED_MINUTES:
            raise commands.BadArgument(f"The minimum interval is {MIN_FEED_MINUTES} minutes.")
        feeds = await self.config.guild(ctx.guild).feeds()
        feed_id = str(max((int(key) for key in feeds), default=0) + 1)
        feeds[feed_id] = {
            "channel_id": channel.id,
            "provider": provider,
            "source": source,
            "interval": interval_minutes * 60,
            "next_post": time.time(),
            "enabled": True,
        }
        await self.config.guild(ctx.guild).feeds.set(feeds)
        await ctx.send(
            f"Added feed `{feed_id}`: {provider} `{source}` → {channel.mention} "
            f"every {interval_minutes} minutes."
        )

    @memeset.command(name="autopost")
    async def memeset_autopost(
        self, ctx, community: str = "memes", interval_minutes: int = 360,
    ):
        """Automatically post memes to a channel.

        `community` is a subreddit name exposed by Meme API, such as `memes`,
        `wholesomememes`, or `pcmasterrace`. The interval is in minutes,
        defaults to 360 (six hours), and must be at least 30.

        The destination is the Discord channel where this command is run.

        Examples:
        - `[p]memeset autopost`
        - `[p]memeset autopost wholesomememes 180`
        - `[p]memeset autopost pcmasterrace 120`
        """
        await self._create_feed(ctx, ctx.channel, "memeapi", community, interval_minutes)

    @memeset.command(name="gifpost")
    async def memeset_gifpost(
        self, ctx, query: str = "gaming", interval_minutes: int = 360,
    ):
        """Automatically post animated Imgur results to a channel.

        Imgur must be configured. `query` is the GIF search phrase and the
        interval is in minutes.

        The destination is the Discord channel where this command is run.

        Example: `[p]memeset gifpost gaming 240`
        """
        await self._create_feed(ctx, ctx.channel, "imgur-gif", query, interval_minutes)

    @memeset_feed.command(name="add")
    async def feed_add(
        self, ctx, channel: discord.TextChannel, provider: str, source: str,
        interval_minutes: int = 360,
    ):
        """Add an advanced scheduled feed.

        Providers and source values:
        - `memeapi`: a subreddit/community name.
        - `imgur`: an Imgur search phrase.
        - `imgur-gif`: an animated-GIF search phrase.
        - `reddit`: approved official Reddit API access only.

        The interval is in minutes, defaults to 360, and must be at least 30.

        Examples:
        - `[p]memeset feed add #memes memeapi memes 360`
        - `[p]memeset feed add #gaming memeapi pcmasterrace 180`
        - `[p]memeset feed add #gifs imgur-gif gaming 240`

        For simple setup, use `[p]memeset autopost`.
        """
        await self._create_feed(ctx, channel, provider, source, interval_minutes)

    @memeset_feed.command(name="remove", aliases=["delete"])
    async def feed_remove(self, ctx, feed_id: str):
        """Remove a feed by ID."""
        feeds = await self.config.guild(ctx.guild).feeds()
        if feeds.pop(feed_id, None) is None:
            await ctx.send("No feed has that ID.")
            return
        await self.config.guild(ctx.guild).feeds.set(feeds)
        await ctx.send(f"Removed feed `{feed_id}`.")

    @memeset_feed.command(name="list")
    async def feed_list(self, ctx):
        """List configured feeds."""
        feeds = await self.config.guild(ctx.guild).feeds()
        if not feeds:
            await ctx.send("No meme feeds are configured.")
            return
        lines = []
        for feed_id, feed in feeds.items():
            minutes = int(feed.get("interval", 3600) / 60)
            lines.append(
                f"`{feed_id}` <#{feed['channel_id']}> · {feed['provider']} "
                f"`{feed['source']}` · every {minutes}m"
            )
        await ctx.send("**Meme feeds**\n" + "\n".join(lines))

    @tasks.loop(minutes=5)
    async def feed_loop(self):
        if self._feed_lock.locked():
            return
        async with self._feed_lock:
            now = time.time()
            for guild_id in await self.config.all_guilds():
                guild = self.bot.get_guild(guild_id)
                if guild is None:
                    continue
                group = self.config.guild_from_id(guild_id)
                feeds, seen_list = await asyncio.gather(group.feeds(), group.seen())
                seen = set(seen_list)
                changed = False
                for feed in feeds.values():
                    if not feed.get("enabled", True) or feed.get("next_post", 0) > now:
                        continue
                    channel = guild.get_channel(feed.get("channel_id"))
                    if not isinstance(channel, discord.TextChannel):
                        continue
                    try:
                        result = await self._pick(
                            channel, feed.get("provider", "memeapi"),
                            feed.get("source", ""), seen=seen,
                        )
                        await self._send(channel, result)
                    except (
                        ProviderError, aiohttp.ClientError, asyncio.TimeoutError,
                        discord.HTTPException,
                    ):
                        feed["next_post"] = now + max(300, feed.get("interval", 3600) // 4)
                    else:
                        seen_list.append(result.post_id)
                        seen.add(result.post_id)
                        feed["next_post"] = now + feed.get("interval", 3600)
                        seen_list = seen_list[-SEEN_LIMIT:]
                    changed = True
                if changed:
                    await group.feeds.set(feeds)
                    await group.seen.set(seen_list)

    @feed_loop.before_loop
    async def before_feed_loop(self):
        await self.bot.wait_until_red_ready()
