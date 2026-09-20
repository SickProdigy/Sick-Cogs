import asyncio
import json
import sys
import time
from random import choice
from typing import List, Optional, Union
from urllib.parse import urlparse

import aiohttp
import discord
from discord.ext import tasks
from redbot.core import Config, commands
from redbot.core.bot import Red
from redbot.core.i18n import Translator, cog_i18n
from redbot.core.utils.chat_formatting import bold, box

from . import constants as source_data
from .constants import (
    GOOD_EXTENSIONS,
    IMGUR_LINKS,
    MARTINE_API_BASE_URL,
    NOT_EMBED_DOMAINS,
    REDDIT_BASEURL,
    emoji,
)

_ = Translator("Nsfw", __file__)

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=8, connect=3)
MIN_AUTOPOST_MINUTES = 30
SEEN_LIMIT = 100


@cog_i18n(_)
class Core(commands.Cog):

    __author__ = ["SickProdigy", "Predä", "aikaterna"]
    __version__ = "3.1.1"

    async def red_delete_data_for_user(self, **kwargs):
        """Nothing to delete."""
        return

    def __init__(self, bot: Red):
        self.bot = bot
        self.session = aiohttp.ClientSession(
            headers={
                "User-Agent": (
                    f"Sick-Cogs-Nsfw/{self.__version__} "
                    f"(Python/{'.'.join(map(str, sys.version_info[:3]))} aiohttp/{aiohttp.__version__})"
                )
            },
            timeout=REQUEST_TIMEOUT,
        )
        self.config = Config.get_conf(self, identifier=512227974893010954, force_registration=True)
        self.config.register_global(use_reddit_api=False)
        self.config.register_guild(autopost={}, seen=[])
        self._autopost_lock = asyncio.Lock()
        self.autopost_loop.start()

    def cog_unload(self):
        self.autopost_loop.cancel()
        self.bot.loop.create_task(self.session.close())

    @staticmethod
    def _reconcile_autopost(current: dict, channel_id: int, source: str, interval_minutes: int):
        """Create or update the guild's one feed without posting immediately."""
        source = source.strip().casefold()
        interval = interval_minutes * 60
        same = (
            bool(current)
            and int(current.get("channel_id", 0)) == channel_id
            and str(current.get("source", "")).casefold() == source
            and int(current.get("interval", 0)) == interval
            and current.get("enabled", True)
        )
        if same:
            return "unchanged", current
        return ("updated" if current else "added"), {
            "channel_id": channel_id,
            "source": source,
            "provider": (
                "mixed" if source == "random"
                else "nekobot" if source in {"hentai", "porngif"}
                else "configured-reddit"
            ),
            "interval": interval,
            "next_post": time.time() + interval,
            "enabled": True,
            "last_error": "",
        }

    @staticmethod
    def _autopost_destination_error(guild: discord.Guild, channel) -> str:
        if not isinstance(channel, discord.TextChannel):
            return "Choose a Discord text channel for the NSFW feed."
        if not channel.is_nsfw():
            return f"{channel.mention} must be explicitly marked age-restricted (NSFW)."
        permissions = channel.permissions_for(guild.me)
        missing = [
            name.replace("_", " ")
            for name in ("view_channel", "send_messages", "embed_links")
            if not getattr(permissions, name, False)
        ]
        return f"I need {', '.join(missing)} in {channel.mention}." if missing else ""

    @staticmethod
    def _retry_at(now: float, interval: int) -> float:
        return now + max(300, int(interval) // 4)

    @staticmethod
    def _remember_seen(seen: list, url: str):
        return (seen + [url])[-SEEN_LIMIT:]

    @staticmethod
    def _display_prefix(prefixes) -> str:
        configured = [prefix for prefix in prefixes if not prefix.lstrip().startswith("<@")]
        return configured[0] if configured else "[p]"

    @staticmethod
    def autopost_sources():
        return {
            "4k": source_data.FOUR_K, "ahegao": source_data.AHEGAO,
            "anal": source_data.ANAL, "asianporn": source_data.ASIANPORN,
            "ass": source_data.ASS, "bbw": source_data.BBW, "bdsm": source_data.BDSM,
            "blackcock": source_data.BLACKCOCK, "blowjob": source_data.BLOWJOB,
            "boobs": source_data.BOOBS, "bottomless": source_data.BOTTOMLESS,
            "cosplay": source_data.COSPLAY, "cumshot": source_data.CUMSHOTS,
            "cunnilingus": source_data.CUNNI, "deepthroat": source_data.DEEPTHROAT,
            "dick": source_data.DICK, "doublepenetration": source_data.DOUBLE_P,
            "ebony": source_data.EBONY, "facials": source_data.FACIALS,
            "feet": source_data.FEET, "femdom": source_data.FEMDOM,
            "futa": source_data.FUTA, "gay": source_data.GAY_P,
            "gonewild": source_data.WILD, "group": source_data.GROUPS,
            "lesbian": source_data.LESBIANS, "milf": source_data.MILF,
            "oral": source_data.ORAL, "public": source_data.PUBLIC,
            "pussy": source_data.PUSSY, "realgirls": source_data.REAL_GIRLS,
            "redhead": source_data.REDHEADS, "rule34": source_data.RULE_34,
            "squirt": source_data.SQUIRTS, "thigh": source_data.THIGHS,
            "threesome": source_data.THREESOME, "trans": source_data.TRANS,
            "yiff": source_data.YIFF,
        }

    async def _fetch_autopost(self, source: str, seen: set, prefix: str = "[p]"):
        if source == "random":
            source = choice(sorted((*self.autopost_sources(), "hentai", "porngif")))
        if source in {"hentai", "porngif"}:
            kind = source_data.NEKOBOT_HENTAI if source == "hentai" else "pgif"
            data = await self._get_others_imgs(None, source_data.NEKOBOT_URL.format(kind))
            try:
                url = self._safe_url(data["img"]["message"])
            except (KeyError, TypeError):
                url = None
            provider = "Nekobot API"
            origin = provider
        else:
            category = self.autopost_sources().get(source)
            if category is None:
                return None
            url, subreddit = await self._get_imgs(category)
            url = self._safe_url(url)
            provider = "Reddit API" if await self.config.use_reddit_api() else "Martine API"
            origin = f"r/{subreddit}" if isinstance(subreddit, str) and subreddit else provider
        if not url or url in seen:
            return None
        safe_prefix = prefix.replace("`", "")
        command = f"{safe_prefix}{source}"
        if any(domain in url for domain in NOT_EMBED_DOMAINS):
            payload = (
                f"**Random {source} post ... \N{EYES}**\n"
                f"{url}\n\n"
                f"Want another one? Copy and paste: `{command}`\n"
                f"From **{origin}** · via {provider}"
            )
        else:
            payload = discord.Embed(
                color=0x891193,
                title=f"Random {source} post ... \N{EYES}",
                description=(
                    f"[Source link]({url})\n\n"
                    f"Want another one? Copy and paste: `{command}`"
                ),
            )
            payload.set_image(url=url)
            payload.set_footer(text=f"From {origin} · via {provider}")
        return url, payload

    @staticmethod
    async def _send_autopost(channel, payload):
        kwargs = {"allowed_mentions": discord.AllowedMentions.none()}
        if isinstance(payload, discord.Embed):
            kwargs["embed"] = payload
        else:
            kwargs["content"] = payload
        await channel.send(**kwargs)

    @tasks.loop(minutes=1)
    async def autopost_loop(self):
        if self._autopost_lock.locked():
            return
        async with self._autopost_lock:
            now = time.time()
            for guild_id in await self.config.all_guilds():
                guild = self.bot.get_guild(guild_id)
                if guild is None:
                    continue
                group = self.config.guild_from_id(guild_id)
                feed = await group.autopost()
                if not feed or not feed.get("enabled", True) or feed.get("next_post", 0) > now:
                    continue
                channel = guild.get_channel(feed.get("channel_id"))
                error = self._autopost_destination_error(guild, channel)
                if error:
                    feed["enabled"] = False
                    feed["last_error"] = error
                    await group.autopost.set(feed)
                    continue
                seen_list = await group.seen()
                seen = set(seen_list)
                result = None
                try:
                    prefixes = await self.bot.get_valid_prefixes(guild)
                    prefix = self._display_prefix(prefixes)
                    for _ in range(5):
                        result = await self._fetch_autopost(
                            feed.get("source", ""), seen, prefix=prefix
                        )
                        if result:
                            break
                    if result is None:
                        raise RuntimeError("No new media available")
                    url, payload = result
                    await self._send_autopost(channel, payload)
                except (aiohttp.ClientError, asyncio.TimeoutError, discord.HTTPException, RuntimeError):
                    feed["next_post"] = self._retry_at(now, feed.get("interval", 3600))
                    feed["last_error"] = "The media provider is temporarily unavailable; retry scheduled."
                else:
                    seen_list = self._remember_seen(seen_list, url)
                    feed["next_post"] = now + int(feed.get("interval", 3600))
                    feed["last_error"] = ""
                    await group.seen.set(seen_list)
                await group.autopost.set(feed)

    @autopost_loop.before_loop
    async def before_autopost_loop(self):
        await self.bot.wait_until_red_ready()

    async def _get_imgs(self, subs: List[str] = None):
        """Get images from Reddit API."""
        if not subs:
            return None, None
        tries = 0
        while tries < 5:
            sub = choice(subs)
            try:
                if await self.config.use_reddit_api():
                    async with self.session.get(REDDIT_BASEURL.format(sub=sub)) as reddit:
                        if reddit.status != 200:
                            tries += 1
                            continue
                        try:
                            data = await reddit.json(content_type=None)
                            content = data[0]["data"]["children"][0]["data"]
                            url = content["url"]
                            subr = content["subreddit"]
                        except (IndexError, KeyError, TypeError, ValueError, json.decoder.JSONDecodeError):
                            tries += 1
                            continue
                        if url.startswith(IMGUR_LINKS):
                            url = url + ".png"
                        elif url.endswith(".mp4"):
                            url = url[:-3] + "gif"
                        elif url.endswith(".gifv"):
                            url = url[:-1]
                        elif not url.endswith(GOOD_EXTENSIONS) and not url.startswith(
                            "https://gfycat.com"
                        ) or "redgifs" in url:
                            tries += 1
                            continue
                        return url, subr
                else:
                    async with self.session.get(
                        MARTINE_API_BASE_URL, params={"name": sub}
                    ) as resp:
                        if resp.status != 200:
                            tries += 1
                            continue
                        try:
                            data = await resp.json()
                            return data["data"]["image_url"], data["data"]["subreddit"]["name"]
                        except (KeyError, TypeError, json.JSONDecodeError):
                            tries += 1
                            continue
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
                tries += 1
                continue

        return None, None

    async def _get_others_imgs(self, ctx: commands.Context, url: str = None):
        """Get images from all other images APIs."""
        if not self._safe_url(url):
            return None
        try:
            async with self.session.get(url) as resp:
                if resp.status != 200:
                    return None
                try:
                    data = await resp.json(content_type=None)
                except (TypeError, ValueError, json.decoder.JSONDecodeError):
                    return None
            data = dict(img=data)
            return data
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
            return None

    @staticmethod
    def _safe_url(value):
        if not isinstance(value, str):
            return None
        parsed = urlparse(value.strip())
        if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
            return None
        return value.strip()

    async def _version_msg(self, ctx: commands.Context, version: str, authors: List[str]):
        """Cog version message."""
        msg = box(
            _("Nsfw cog version: {version}\nAuthors: {authors}").format(
                version=version, authors=", ".join(authors)
            ),
            lang="py",
        )
        return await ctx.send(msg)

    async def _make_embed(self, ctx: commands.Context, subs: List[str], name: str):
        """Function to make the embed for all Reddit API images."""
        try:
            url, subr = await asyncio.wait_for(self._get_imgs(subs=subs), 5)
        except asyncio.TimeoutError:
            return
        url = self._safe_url(url)
        if not url or not isinstance(subr, str) or not subr.strip():
            return

        if any(wrong in url for wrong in NOT_EMBED_DOMAINS):
            em = (
                _("Here is {name} gif ...")
                + " \N{EYES}\n\n"
                + _("Requested by {req} {emoji} • From {r}\n{url}")
            ).format(
                name=name,
                req=bold(ctx.author.display_name),
                emoji=emoji(),
                r=bold(f"r/{subr}"),
                url=url,
            )
        else:
            em = await self._embed(
                color=0x891193,
                title=(_("Here is {name} image ...") + " \N{EYES}").format(name=name),
                description=bold(
                    _("[Link if you don't see image]({url})").format(url=url),
                    escape_formatting=False,
                ),
                image=url,
                footer=_("Requested by {req} {emoji} • From r/{r}").format(
                    req=ctx.author.display_name, emoji=emoji(), r=subr
                ),
            )

        return em

    async def _make_embed_other(
        self, ctx: commands.Context, name: str, url: str, arg: str, source: str
    ):
        """Function to make the embed for all others APIs images."""
        try:
            data = await asyncio.wait_for(self._get_others_imgs(ctx, url=url), 5)
        except asyncio.TimeoutError:
            return
        if not data:
            return
        try:
            image_url = self._safe_url(data["img"][arg])
        except (KeyError, TypeError):
            return
        if not image_url:
            return
        em = await self._embed(
            color=0x891193,
            title=(_("Here is {name} image ...") + " \N{EYES}").format(name=name),
            description=bold(
                _("[Link if you don't see image]({url})").format(url=image_url),
                escape_formatting=False,
            ),
            image=image_url,
            footer=_("Requested by {req} {emoji} • From {source}").format(
                req=ctx.author.display_name, emoji=emoji(), source=source
            ),
        )
        return em

    async def _maybe_embed(self, ctx: commands.Context, embed: Union[discord.Embed, str]):
        """
        Function to choose if type of the message is an embed or not
        and if not send a simple message.
        """
        if embed is None:
            return await ctx.send(
                _("The image service is unavailable or returned no usable result. Please try again later.")
            )
        try:
            if isinstance(embed, discord.Embed):
                await ctx.send(embed=embed)
            else:
                await ctx.send(embed)
        except discord.HTTPException:
            return

    async def _send_msg(self, ctx: commands.Context, name: str, subs: List[str] = None):
        """Main function called in all Reddit API commands."""
        embed = await self._make_embed(ctx, subs, name)
        return await self._maybe_embed(ctx, embed=embed)

    async def _send_other_msg(
        self, ctx: commands.Context, name: str, arg: str, source: str, url: str = None
    ):
        """Main function called in all others APIs commands."""
        embed = await self._make_embed_other(ctx, name, url, arg, source)
        return await self._maybe_embed(ctx, embed)

    @staticmethod
    async def _embed(
        color: Union[int, discord.Color] = None,
        title: str = None,
        description: str = None,
        image: str = None,
        footer: Optional[str] = None,
    ):
        em = discord.Embed(color=color, title=title, description=description)
        em.set_image(url=image)
        if footer:
            em.set_footer(text=footer)
        return em
