import asyncio
import itertools
import logging
from typing import Optional, Union
from urllib.parse import urlparse

import aiohttp
from bs4 import BeautifulSoup
import discord
from redbot.core import checks, commands
from redbot.core.utils import can_user_send_messages_in
from redbot.core.utils.chat_formatting import bold, box, pagify

from .color import Color
from .config import RSS_VERSION
from .fetcher import MAX_PAGE_BYTES, NoFeedContent, UnsafeFeedURL, fetch_limited, validate_http_url
from .models import FeedMode, migrate_feed_data, normalize_mode
from .models import INTERNAL_TAGS, TagType
from .renderer import TemplateValidationError, validate_template

log = logging.getLogger("red.Sick-Cogs.RSS")
GuildMessageable = Union[discord.TextChannel, discord.VoiceChannel, discord.StageChannel, discord.Thread]

class RSSCommands:
    """Public RSS command groups and command-specific helpers."""

    @commands.guild_only()
    @commands.group()
    @checks.mod_or_permissions(manage_channels=True)
    async def rss(self, ctx):
        """RSS feed stuff."""
        pass

    @rss.command(name="add")
    async def _rss_add(self, ctx, feed_name: str, channel: Optional[GuildMessageable] = None, *, url: str):
        """
        Add an RSS feed to a channel.

        Defaults to the current channel if no channel is specified.
        """
        if feed_name.startswith("<#"):
            # someone typed a channel name but not a feed name
            msg = "Try again with a feed name included in the right spot so that you can refer to the feed later.\n"
            msg += f"Example: `{ctx.prefix}rss add feed_name channel_name feed_url`"
            await ctx.send(msg)
            return
        channel = channel or ctx.channel
        channel_permission_check = await self._check_channel_permissions(ctx, channel)
        if not channel_permission_check:
            return

        async with ctx.typing():
            try:
                valid_url = await self._valid_url(url)
            except NoFeedContent as e:
                await ctx.send(str(e))
                return

            if valid_url:
                await self._add_feed(ctx, feed_name.lower(), channel, url)
            else:
                await ctx.send("Invalid or unavailable URL.")

    @rss.group(name="embed")
    async def _rss_embed(self, ctx):
        """Embed feed settings."""
        pass

    @_rss_embed.command(name="color", aliases=["colour"])
    async def _rss_embed_color(
        self, ctx, feed_name: str, channel: Optional[GuildMessageable] = None, *, color: str = None
    ):
        """
        Set an embed color for a feed.

        Use this command with no color to reset to the default.
        `color` must be a hex code like #990000, a [Discord color name](https://discordpy.readthedocs.io/en/latest/api.html#colour),
        or a [CSS3 color name](https://www.w3.org/TR/2018/REC-css-color-3-20180619/#svg-color).
        """
        channel = channel or ctx.channel
        rss_feed = await self.config.channel(channel).feeds.get_raw(feed_name, default=None)
        if not rss_feed:
            await ctx.send("That feed name doesn't exist in this channel.")
            return

        embed_toggle = rss_feed["embed"]
        embed_state_message = ""
        if not embed_toggle:
            embed_state_message += (
                f"{bold(feed_name)} is not currently set to be in an embed. "
                f"Toggle it on with `{ctx.prefix}rss embed toggle`.\n"
            )

        if not color:
            async with self.config.channel(channel).feeds() as feed_data:
                feed_data[feed_name]["embed_color"] = None
            await ctx.send(
                f"{embed_state_message}The color for {bold(feed_name)} has been reset. "
                "Use this command with a color argument to set a color for this feed."
            )
            return

        color = color.replace(" ", "_")
        hex_code = await Color()._color_converter(color)
        if not hex_code:
            await ctx.send(
                "Not a valid color code. Use a hex code like #990000, a "
                "Discord color name or a CSS3 color name.\n"
                "<https://discordpy.readthedocs.io/en/latest/api.html#colour>\n"
                "<https://www.w3.org/TR/2018/REC-css-color-3-20180619/#svg-color>"
            )
            return
        user_facing_hex = hex_code.replace("0x", "#")
        color_name = await Color()._hex_to_css3_name(hex_code)

        # 0xFFFFFF actually doesn't show up as white in an embed
        # so let's make it close enough to count
        if hex_code == "0xFFFFFF":
            hex_code = "0xFFFFFE"

        async with self.config.channel(channel).feeds() as feed_data:
            # data is always a 0xFFFFFF style value
            feed_data[feed_name]["embed_color"] = hex_code

        await ctx.send(f"Embed color for {bold(feed_name)} set to {user_facing_hex} ({color_name}).")

    @_rss_embed.command(name="image")
    async def _rss_embed_image(
        self, ctx, feed_name: str, channel: Optional[GuildMessageable] = None, image_tag_name: str = None
    ):
        """
        Set a tag to be a large embed image.

        This image will be applied to the last embed in the paginated list.
        Use this command with no image_tag_name to clear the embed image.
        """
        channel = channel or ctx.channel
        rss_feed = await self.config.channel(channel).feeds.get_raw(feed_name, default=None)
        if not rss_feed:
            await ctx.send("That feed name doesn't exist in this channel.")
            return

        embed_toggle = rss_feed["embed"]
        embed_state_message = ""
        if not embed_toggle:
            embed_state_message += (
                f"{bold(feed_name)} is not currently set to be in an embed. "
                f"Toggle it on with `{ctx.prefix}rss embed toggle`.\n"
            )

        if image_tag_name is not None:
            if image_tag_name.startswith("$"):
                image_tag_name = image_tag_name.strip("$")
            else:
                msg = "You must use a feed tag for this setting. "
                msg += f"Feed tags start with `$` and can be found by using `{ctx.prefix}rss listtags` "
                msg += "with the saved feed name.\nImages that are scraped from feed content are usually "
                msg += "stored under the tags styled similar to `$content_image01`: subsequent scraped images "
                msg += "will be in tags named `$content_image02`, `$content_image03`, etc. Not every feed entry "
                msg += "will have the same amount of scraped image tags. Images can also be found under tags named "
                msg += "`$media_content_plaintext`, if present.\nExperiment with tags by setting them as your "
                msg += (
                    f"template with `{ctx.prefix}rss template` and using `{ctx.prefix}rss force` to view the content."
                )
                await ctx.send(msg)
                return

        async with self.config.channel(channel).feeds() as feed_data:
            feed_data[feed_name]["embed_image"] = image_tag_name

        if image_tag_name:
            await ctx.send(f"{embed_state_message}Embed image set to the ${image_tag_name} tag.")
        else:
            await ctx.send(
                "Embed image has been cleared. Use this command with a tag name if you intended to set an image tag."
            )

    @_rss_embed.command(name="thumbnail")
    async def _rss_embed_thumbnail(
        self, ctx, feed_name: str, channel: Optional[GuildMessageable] = None, thumbnail_tag_name: str = None
    ):
        """
        Set a tag to be a thumbnail image.

        This thumbnail will be applied to the first embed in the paginated list.
        Use this command with no thumbnail_tag_name to clear the embed thumbnail.
        """
        channel = channel or ctx.channel
        rss_feed = await self.config.channel(channel).feeds.get_raw(feed_name, default=None)
        if not rss_feed:
            await ctx.send("That feed name doesn't exist in this channel.")
            return

        embed_toggle = rss_feed["embed"]
        embed_state_message = ""
        if not embed_toggle:
            embed_state_message += (
                f"{bold(feed_name)} is not currently set to be in an embed. "
                f"Toggle it on with `{ctx.prefix}rss embed toggle`.\n"
            )

        if thumbnail_tag_name is not None:
            if thumbnail_tag_name.startswith("$"):
                thumbnail_tag_name = thumbnail_tag_name.strip("$")
            else:
                msg = "You must use a feed tag for this setting. "
                msg += f"Feed tags start with `$` and can be found by using `{ctx.prefix}rss listtags` "
                msg += "with the saved feed name.\nImages that are scraped from feed content are usually "
                msg += "stored under the tags styled similar to `$content_image01`: subsequent scraped images "
                msg += "will be in tags named `$content_image02`, `$content_image03`, etc. Not every feed entry "
                msg += "will have the same amount of scraped image tags. Images can also be found under tags named "
                msg += "`$media_content_plaintext`, if present.\nExperiment with tags by setting them as your "
                msg += (
                    f"template with `{ctx.prefix}rss template` and using `{ctx.prefix}rss force` to view the content."
                )
                await ctx.send(msg)
                return

        async with self.config.channel(channel).feeds() as feed_data:
            feed_data[feed_name]["embed_thumbnail"] = thumbnail_tag_name

        if thumbnail_tag_name:
            await ctx.send(f"{embed_state_message}Embed thumbnail set to the ${thumbnail_tag_name} tag.")
        else:
            await ctx.send(
                "Embed thumbnail has been cleared. "
                "Use this command with a tag name if you intended to set a thumbnail tag."
            )

    @_rss_embed.command(name="toggle")
    async def _rss_embed_toggle(self, ctx, feed_name: str, channel: Optional[GuildMessageable] = None):
        """
        Toggle whether a feed is sent in an embed or not.

        If the bot doesn't have permissions to post embeds,
        the feed will always be plain text, even if the embed
        toggle is set.
        """
        channel = channel or ctx.channel
        rss_feed = await self.config.channel(channel).feeds.get_raw(feed_name, default=None)
        if not rss_feed:
            await ctx.send("That feed name doesn't exist in this channel.")
            return

        embed_toggle = rss_feed["embed"]
        toggle_text = "disabled" if embed_toggle else "enabled"

        async with self.config.channel(channel).feeds() as feed_data:
            feed_data[feed_name]["embed"] = not embed_toggle

        await ctx.send(f"Embeds for {bold(feed_name)} are {toggle_text}.")

    @rss.command(name="find")
    async def _rss_find(self, ctx, website_url: str):
        """
        Attempts to find RSS feeds from a URL/website.

        The site must have identified their feed in the html of the page based on RSS feed type standards.
        """
        async with ctx.typing():
            session = await self._get_http_session()
            try:
                website_url = validate_http_url(website_url)
                response = await fetch_limited(
                    session,
                    website_url,
                    max_bytes=MAX_PAGE_BYTES,
                    allowed_private_hosts=await self.config.private_feed_hosts(),
                )
                if response.status < 200 or response.status >= 300:
                    await ctx.send(f"The website returned HTTP {response.status}.")
                    return
                soup = BeautifulSoup(response.body, "html.parser")
            except UnsafeFeedURL as e:
                await ctx.send(f"Blocked unsafe feed target: {e}")
                return
            except (aiohttp.client_exceptions.ClientConnectorError, aiohttp.client_exceptions.ClientPayloadError):
                await ctx.send("I can't reach that website.")
                return
            except aiohttp.client_exceptions.InvalidURL:
                await ctx.send(
                    "That seems to be an invalid URL. Use a full website URL like `https://www.site.com/`."
                )
                return
            except aiohttp.client_exceptions.ServerDisconnectedError:
                await ctx.send("The server disconnected early without a response.")
                return
            except asyncio.exceptions.TimeoutError:
                await ctx.send("The site didn't respond in time or there was no response.")
                return
            except Exception as e:
                msg = "There was an issue trying to find a feed in that site. "
                msg += "Please check your console for more information."
                log.exception(e, exc_info=e)
                await ctx.send(msg)
                return

        if "403 Forbidden" in soup.get_text():
            await ctx.send("I received a '403 Forbidden' message while trying to reach that site.")
            return
        if not soup:
            await ctx.send("I didn't find anything at all on that link.")
            return

        msg = ""
        url_parse = urlparse(website_url)
        base_url = url_parse.netloc
        url_scheme = url_parse.scheme
        feed_url_types = ["application/rss+xml", "application/atom+xml", "text/xml", "application/rdf+xml"]
        for feed_type in feed_url_types:
            possible_feeds = soup.find_all("link", rel="alternate", type=feed_type, href=True)
            for feed in possible_feeds:
                feed_url = feed.get("href", None)
                ls_feed_url = feed_url.lstrip("/")
                if not feed_url:
                    continue
                if feed_url.startswith("//"):
                    final_url = f"{url_scheme}:{feed_url}"
                elif (not ls_feed_url.startswith(url_scheme)) and (not ls_feed_url.startswith(base_url)):
                    final_url = f"{url_scheme}://{base_url}/{ls_feed_url}"
                elif ls_feed_url.startswith(base_url):
                    final_url = f"{url_scheme}://{base_url}"
                else:
                    final_url = feed_url
                msg += f"[Feed Title]: {feed.get('title', None)}\n"
                msg += f"[Feed URL]: {final_url}\n\n"
        if msg:
            await ctx.send(box(msg, lang="ini"))
        else:
            await ctx.send("No RSS feeds found in the link provided.")

    @rss.group(name="privatehost")
    @checks.is_owner()
    async def _rss_private_host(self, ctx):
        """Manage private feed hosts that the bot owner intentionally trusts."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help()

    @staticmethod
    def _normalize_private_host(value: str) -> Optional[str]:
        candidate = (value or "").strip()
        parsed = urlparse(candidate if "://" in candidate else f"//{candidate}")
        return parsed.hostname.casefold().rstrip(".") if parsed.hostname else None

    @_rss_private_host.command(name="add")
    @checks.is_owner()
    async def _rss_private_host_add(self, ctx, host: str):
        """Allow one private hostname or IP address for RSS requests."""
        normalized = self._normalize_private_host(host)
        if not normalized:
            await ctx.send("Provide a hostname or IP address, not a full feed path.")
            return
        async with self.config.private_feed_hosts() as hosts:
            normalized_hosts = {item.casefold().rstrip(".") for item in hosts}
            if normalized in normalized_hosts:
                await ctx.send(f"`{normalized}` is already allowlisted.")
                return
            hosts.append(normalized)
            hosts.sort()
        await ctx.send(
            f"Private RSS requests to `{normalized}` are now allowed. "
            "Only bot owners can change this list."
        )

    @_rss_private_host.command(name="remove", aliases=["delete"])
    @checks.is_owner()
    async def _rss_private_host_remove(self, ctx, host: str):
        """Remove a private hostname or IP address from the RSS allowlist."""
        normalized = self._normalize_private_host(host)
        if not normalized:
            await ctx.send("Provide a hostname or IP address, not a full feed path.")
            return
        async with self.config.private_feed_hosts() as hosts:
            for saved_host in list(hosts):
                if saved_host.casefold().rstrip(".") == normalized:
                    hosts.remove(saved_host)
                    await ctx.send(f"`{normalized}` was removed from the allowlist.")
                    return
        await ctx.send("That host is not in the private RSS allowlist.")

    @_rss_private_host.command(name="list")
    @checks.is_owner()
    async def _rss_private_host_list(self, ctx):
        """List private RSS hosts explicitly trusted by the bot owner."""
        hosts = await self.config.private_feed_hosts()
        message = "\n".join(sorted(hosts)) if hosts else "No private hosts are allowed."
        await ctx.send(box(message, lang="ini"))

    @rss.command(name="force", aliases=["test", "preview"])
    async def _rss_force(self, ctx, feed_name: str, channel: Optional[GuildMessageable] = None):
        """Preview the newest feed entry without advancing its saved marker."""
        channel = channel or ctx.channel
        channel_permission_check = await self._check_channel_permissions(ctx, channel)
        if not channel_permission_check:
            return

        feeds = await self.config.all_channels()
        try:
            feeds[channel.id]
        except KeyError:
            await ctx.send("There are no feeds in this channel.")
            return

        if feed_name not in feeds[channel.id]["feeds"]:
            await ctx.send("That feed name doesn't exist in this channel.")
            return

        rss_feed = feeds[channel.id]["feeds"][feed_name]
        await self.get_current_feed(channel, feed_name, rss_feed, force=True)

    @rss.command(name="mode")
    async def _rss_mode(
        self,
        ctx,
        feed_name: str,
        channel: Optional[GuildMessageable] = None,
        mode: str = FeedMode.LATEST.value,
    ):
        """Set a feed to post only the latest entry or catch up on every unseen entry."""
        channel = channel or ctx.channel
        if not await self._check_channel_permissions(ctx, channel):
            return
        requested_mode = str(mode).lower()
        if requested_mode not in {item.value for item in FeedMode}:
            await ctx.send("Mode must be `latest` or `catchup`.")
            return
        mode = normalize_mode(requested_mode)
        rss_feed = await self.config.channel(channel).feeds.get_raw(feed_name, default=None)
        if not rss_feed:
            await ctx.send("That feed name does not exist in this channel.")
            return
        async with self.config.channel(channel).feeds() as feed_data:
            feed_data[feed_name]["mode"] = mode
        await ctx.send(f"{bold(feed_name)} will now use `{mode}` posting mode.")

    @rss.command(name="pause")
    async def _rss_pause(
        self, ctx, feed_name: str, channel: Optional[GuildMessageable] = None
    ):
        """Pause automatic checks for a feed without removing its configuration."""
        await self._set_feed_paused(ctx, feed_name, channel or ctx.channel, True)

    @rss.command(name="resume")
    async def _rss_resume(
        self, ctx, feed_name: str, channel: Optional[GuildMessageable] = None
    ):
        """Resume automatic checks for a paused feed."""
        await self._set_feed_paused(ctx, feed_name, channel or ctx.channel, False)

    async def _set_feed_paused(self, ctx, feed_name: str, channel, paused: bool):
        if not await self._check_channel_permissions(ctx, channel):
            return
        rss_feed = await self.config.channel(channel).feeds.get_raw(feed_name, default=None)
        if not rss_feed:
            await ctx.send("That feed name does not exist in this channel.")
            return
        async with self.config.channel(channel).feeds() as feed_data:
            feed_data[feed_name]["paused"] = paused
        state = "paused" if paused else "resumed"
        await ctx.send(f"{bold(feed_name)} has been {state}.")

    @rss.command(name="status")
    async def _rss_status(
        self, ctx, feed_name: str, channel: Optional[GuildMessageable] = None
    ):
        """Show delivery mode and recent health for a feed."""
        channel = channel or ctx.channel
        if not await self._check_channel_permissions(ctx, channel):
            return
        rss_feed = await self.config.channel(channel).feeds.get_raw(feed_name, default=None)
        if not rss_feed:
            await ctx.send("That feed name does not exist in this channel.")
            return
        migrated, _ = migrate_feed_data(rss_feed)
        state = "Paused" if migrated["paused"] else "Active"
        lines = [
            f"[ {feed_name} ]",
            f"State: {state}",
            f"Mode: {migrated['mode']}",
            f"Last checked: {migrated['last_checked_at'] or 'Never'}",
            f"Last successful post: {migrated['last_success_at'] or 'Never'}",
            f"Consecutive failures: {migrated['consecutive_failures']}",
        ]
        if migrated["last_error"]:
            lines.append(f"Last error: {migrated['last_error'][:500]}")
        await ctx.send(box("\n".join(lines), lang="ini"))

    @rss.command(name="limit")
    async def _rss_limit(
        self, ctx, feed_name: str, channel: Optional[GuildMessageable] = None, character_limit: int = None
    ):
        """
        Set a character limit for feed posts. Use 0 for unlimited.

        RSS posts are naturally split at around 2000 characters to fit within the Discord character limit per message.
        If you only want the first embed or first message in a post feed to show, use 2000 or less characters for this setting.

        Note that this setting applies the character limit to the entire post, for all template values on the feed together.
        For example, if the template is `$title\\n$content\\n$link`, and title + content + link is longer than the limit, the link will not show.
        """
        extra_msg = ""

        if character_limit is None:
            await ctx.send_help()
            return

        if character_limit < 0:
            await ctx.send("Character limit cannot be less than zero.")
            return

        if character_limit > 20000:
            character_limit = 0

        if 0 < character_limit < 20:
            extra_msg = "Character limit has a 20 character minimum.\n"
            character_limit = 20

        channel = channel or ctx.channel
        rss_feed = await self.config.channel(channel).feeds.get_raw(feed_name, default=None)
        if not rss_feed:
            await ctx.send("That feed name doesn't exist in this channel.")
            return

        async with self.config.channel(channel).feeds() as feed_data:
            feed_data[feed_name]["limit"] = character_limit

        characters = f"approximately {character_limit}" if character_limit > 0 else "an unlimited amount of"
        await ctx.send(f"{extra_msg}Character limit for {bold(feed_name)} is now {characters} characters.")

    @rss.command(name="list")
    async def _rss_list(self, ctx, channel: GuildMessageable = None):
        """List saved feeds for this channel or a specific channel."""
        channel = channel or ctx.channel
        channel_permission_check = await self._check_channel_permissions(ctx, channel)
        if not channel_permission_check:
            return

        feeds = await self._get_feed_names(channel)
        msg = f"[ Available Feeds for #{channel.name} ]\n\n\t"
        if feeds:
            msg += "\n\t".join(sorted(feeds))
        else:
            msg += "\n\tNone."
        for page in pagify(msg, delims=["\n"], page_length=1800):
            await ctx.send(box(page, lang="ini"))

    @rss.command(name="listall")
    async def _rss_listall(self, ctx):
        """List all saved feeds for this server."""
        all_channels = await self.config.all_channels()
        all_guild_channels = [x.id for x in itertools.chain(ctx.guild.channels, ctx.guild.threads)]
        msg = ""
        for channel_id, data in all_channels.items():
            if channel_id in all_guild_channels:
                channel_obj = ctx.guild.get_channel_or_thread(channel_id)
                feeds = await self._get_feed_names(channel_obj)
                if not feeds:
                    continue
                if feeds == ["None."]:
                    continue
                msg += f"[ Available Feeds for #{channel_obj.name} ]\n\n\t"
                msg += "\n\t".join(sorted(feeds))
                msg += "\n\n"

        for page in pagify(msg, delims=["\n\n", "\n"], page_length=1800):
            await ctx.send(box(page, lang="ini"))

    @rss.command(name="listtags")
    async def _rss_list_tags(self, ctx, feed_name: str, channel: Optional[GuildMessageable] = None):
        """List the tags available from a specific feed."""
        channel = channel or ctx.channel
        channel_permission_check = await self._check_channel_permissions(ctx, channel)
        if not channel_permission_check:
            return

        rss_feed = await self.config.channel(channel).feeds.get_raw(feed_name, default=None)

        if not rss_feed:
            await ctx.send("No feed with that name in this channel.")
            return

        async with ctx.typing():
            await self._rss_list_tags_helper(ctx, rss_feed, feed_name)

    async def _rss_list_tags_helper(self, ctx, rss_feed: dict, feed_name: str):
        """Helper function for rss listtags."""
        msg = f"[ Available Template Tags for {feed_name} ]\n\n\t"
        feedparser_obj = await self._fetch_feedparser_object(rss_feed["url"])

        if not feedparser_obj:
            await ctx.send("Couldn't fetch that feed.")
            return
        if feedparser_obj.entries:
            # this feed has posts
            feedparser_plus_obj = await self._add_to_feedparser_object(feedparser_obj.entries[0], rss_feed["url"])
        else:
            # this feed does not have posts, but it has a header with channel information
            feedparser_plus_obj = await self._add_to_feedparser_object(feedparser_obj.feed, rss_feed["url"])

        for tag_name, tag_content in sorted(feedparser_plus_obj.items()):
            if tag_name in INTERNAL_TAGS:
                # these tags attached to the rss feed object are for internal handling options
                continue

            tag_content_check = await self._get_tag_content_type(tag_content)
            if tag_content_check == TagType.HTML:
                msg += f"[X] ${tag_name}\n\t"
            elif tag_content_check == TagType.DICT:
                msg += f"[\\] ${tag_name}  \n\t"
            elif tag_content_check == TagType.LIST:
                msg += f"[-] ${tag_name}  \n\t"
            elif tag_name in feedparser_plus_obj["is_special"]:
                msg += f"[*] ${tag_name}  \n\t"
            else:
                msg += f"[ ] ${tag_name}  \n\t"
        msg += "\n\n\t[X] = html | [\\] = dictionary | [-] = list | [ ] = plain text"
        msg += "\n\t[*] = specially-generated tag, may not be present in every post"

        for msg_part in pagify(msg, delims=["\n\t", "\n\n"]):
            await ctx.send(box(msg_part, lang="ini"))

    @checks.is_owner()
    @rss.group(name="parse")
    async def _rss_parse(self, ctx):
        """
        Change feed parsing for a specfic domain.

        This is a global change per website.
        The default is to use the feed's updated_parsed tag, and adding a website to this list will change the check to published_parsed.

        Some feeds may spam feed entries as they are updating the updated_parsed slot on their feed, but not updating feed content.
        In this case we can force specific sites to use the published_parsed slot instead by adding the website to this override list.
        """
        pass

    @_rss_parse.command(name="add")
    async def _rss_parse_add(self, ctx, website_url: str):
        """
        Add a website to the list for a time parsing override.

        Use a website link formatted like `www.website.com` or `https://www.website.com`.
        For more information, use `[p]help rss parse`.
        """
        website = self._find_website(website_url)
        if not website:
            msg = f"I can't seem to find a website in `{website_url}`. "
            msg += "Use something like `https://www.website.com/` or `www.website.com`."
            await ctx.send(msg)
            return

        override_list = await self.config.use_published()
        if website in override_list:
            await ctx.send(f"`{website}` is already in the parsing override list.")
        else:
            override_list.append(website)
            await self.config.use_published.set(override_list)
            await ctx.send(f"`{website}` was added to the parsing override list.")

    @_rss_parse.command(name="list")
    async def _rss_parse_list(self, ctx):
        """
        Show the list for time parsing overrides.

        For more information, use `[p]help rss parse`.
        """
        override_list = await self.config.use_published()
        if not override_list:
            msg = "No site overrides saved."
        else:
            msg = "Active for:\n" + "\n".join(override_list)
        await ctx.send(box(msg))

    @_rss_parse.command(name="remove", aliases=["delete", "del"])
    async def _rss_parse_remove(self, ctx, website_url: str = None):
        """
        Remove a website from the list for a time parsing override.

        Use a website link formatted like `www.website.com` or `https://www.website.com`.
        For more information, use `[p]help rss parse`.
        """
        website = self._find_website(website_url)
        override_list = await self.config.use_published()
        if website in override_list:
            override_list.remove(website)
            await self.config.use_published.set(override_list)
            await ctx.send(f"`{website}` was removed from the parsing override list.")
        else:
            await ctx.send(f"`{website}` isn't in the parsing override list.")

    @rss.command(name="remove", aliases=["delete", "del"])
    async def _rss_remove(self, ctx, feed_name: str, channel: Optional[GuildMessageable] = None):
        """
        Removes a feed from a channel.

        Defaults to the current channel if no channel is specified.
        """
        channel = channel or ctx.channel
        channel_permission_check = await self._check_channel_permissions(ctx, channel, addl_send_messages_check=False)
        if not channel_permission_check:
            return

        success = await self._delete_feed(ctx, feed_name, channel)
        if success:
            await ctx.send("Feed deleted.")
        else:
            await ctx.send("Feed not found!")

    @rss.command(name="showtemplate")
    async def _rss_show_template(self, ctx, feed_name: str, channel: Optional[GuildMessageable] = None):
        """Show the template in use for a specific feed."""
        channel = channel or ctx.channel
        channel_permission_check = await self._check_channel_permissions(ctx, channel)
        if not channel_permission_check:
            return

        rss_feed = await self.config.channel(channel).feeds.get_raw(feed_name, default=None)
        if not rss_feed:
            await ctx.send("No feed with that name in this channel.")
            return

        space = "\N{SPACE}"
        embed_toggle = f"[ ] Embed:{space*16}Off" if not rss_feed["embed"] else f"[X] Embed:{space*16}On"
        embed_image = (
            f"[ ] Embed image tag:{space*6}None"
            if not rss_feed["embed_image"]
            else f"[X] Embed image tag:{space*6}${rss_feed['embed_image']}"
        )
        embed_thumbnail = (
            f"[ ] Embed thumbnail tag:{space*2}None"
            if not rss_feed["embed_thumbnail"]
            else f"[X] Embed thumbnail tag:{space*2}${rss_feed['embed_thumbnail']}"
        )
        hex_color = rss_feed.get("embed_color", None)
        if hex_color:
            color_name = await Color()._hex_to_css3_name(hex_color)
            hex_color = hex_color.lstrip("0x")
        embed_color = (
            f"[ ] Embed hex color:{space*6}None"
            if not hex_color
            else f"[X] Embed hex color:{space*6}{hex_color} ({color_name})"
        )

        allowed_tags = rss_feed.get("allowed_tags", [])
        if not allowed_tags:
            tag_msg = "[ ] No restrictions\n\tAll tags are allowed."
        else:
            tag_msg = "[X] Feed is restricted to posts that include:"
            for tag in allowed_tags:
                tag_msg += f"\n\t{await self._title_case(tag)}"

        character_limit = rss_feed.get("limit", 0)
        if character_limit == 0:
            length_msg = "[ ] Feed length is unlimited."
        else:
            length_msg = f"[X] Feed length is capped at {character_limit} characters."

        embed_settings = f"{embed_toggle}\n{embed_color}\n{embed_image}\n{embed_thumbnail}"
        feed_mode = normalize_mode(rss_feed.get("mode"))
        feed_state = "Paused" if rss_feed.get("paused", False) else "Active"
        announcement = rss_feed.get("announcement")
        announcement_setting = (
            announcement.replace("\n", "\\n").replace("\t", "\\t")
            if announcement
            else "None"
        )
        operation_settings = (
            f"Mode: {feed_mode}\nState: {feed_state}\n"
            f"Announcement: {announcement_setting}"
        )
        rss_template = rss_feed["template"].replace("\n", "\\n").replace("\t", "\\t")

        msg = f"Template for {bold(feed_name)}:\n\n`{rss_template}`\n\n{box(embed_settings, lang='ini')}\n{box(tag_msg, lang='ini')}\n{box(length_msg, lang='ini')}\n{box(operation_settings, lang='ini')}"

        for page in pagify(msg, delims=["\n"], page_length=1800):
            await ctx.send(page, allowed_mentions=discord.AllowedMentions.none())

    @rss.group(name="tag")
    async def _rss_tag(self, ctx):
        """RSS post tag qualification."""
        pass

    @_rss_tag.command(name="allow")
    async def _rss_tag_allow(self, ctx, feed_name: str, channel: Optional[GuildMessageable] = None, *, tag: str = None):
        """
        Set an allowed tag for a feed to be posted. The tag must match exactly (without regard to title casing).
        No regex or placeholder qualification.

        Tags can be found in `[p]rss listtags` under `$tags` or `$tags_list` (if tags are present in the feed - not all feeds have tags).
        """
        channel = channel or ctx.channel
        rss_feed = await self.config.channel(channel).feeds.get_raw(feed_name, default=None)
        if not rss_feed:
            await ctx.send("That feed name doesn't exist in this channel.")
            return

        async with self.config.channel(channel).feeds() as feed_data:
            allowed_tags = feed_data[feed_name].get("allowed_tags", [])
            if tag.lower() in [x.lower() for x in allowed_tags]:
                return await ctx.send(
                    f"{bold(await self._title_case(tag))} is already in the allowed list for {bold(feed_name)}."
                )
            allowed_tags.append(tag.lower())
            feed_data[feed_name]["allowed_tags"] = allowed_tags

        await ctx.send(
            f"{bold(await self._title_case(tag))} was added to the list of allowed tags for {bold(feed_name)}. "
            "If a feed post's `$tags` does not include this value, the feed will not post."
        )

    @_rss_tag.command(name="allowlist")
    async def _rss_tag_allowlist(self, ctx, feed_name: str, channel: Optional[GuildMessageable] = None):
        """
        List allowed tags for feed post qualification.
        """
        channel = channel or ctx.channel
        rss_feed = await self.config.channel(channel).feeds.get_raw(feed_name, default=None)
        if not rss_feed:
            await ctx.send("That feed name doesn't exist in this channel.")
            return

        msg = f"[ Allowed Tags for {feed_name} ]\n\n\t"
        allowed_tags = rss_feed.get("allowed_tags", [])
        if not allowed_tags:
            msg += "All tags are allowed."
        else:
            for tag in allowed_tags:
                msg += f"{await self._title_case(tag)}\n"

        await ctx.send(box(msg, lang="ini"))

    @_rss_tag.command(name="remove", aliases=["delete"])
    async def _rss_tag_remove(
        self, ctx, feed_name: str, channel: Optional[GuildMessageable] = None, *, tag: str = None
    ):
        """
        Remove a tag from the allow list. The tag must match exactly (without regard to title casing).
        No regex or placeholder qualification.
        """
        channel = channel or ctx.channel
        rss_feed = await self.config.channel(channel).feeds.get_raw(feed_name, default=None)
        if not rss_feed:
            await ctx.send("That feed name doesn't exist in this channel.")
            return

        async with self.config.channel(channel).feeds() as feed_data:
            allowed_tags = feed_data[feed_name].get("allowed_tags", [])
            try:
                allowed_tags.remove(tag.lower())
                feed_data[feed_name]["allowed_tags"] = allowed_tags
                await ctx.send(
                    f"{bold(await self._title_case(tag))} was removed from the list of allowed tags for {bold(feed_name)}."
                )
            except ValueError:
                await ctx.send(
                    f"{bold(await self._title_case(tag))} was not found in the allow list for {bold(feed_name)}."
                )

    @rss.command(name="template")
    async def _rss_template(
        self, ctx, feed_name: str, channel: Optional[GuildMessageable] = None, *, template: str = None
    ):
        """
        Set a template for the feed alert.

        Each variable must start with $, valid variables can be found with `[p]rss listtags`.
        """
        channel = channel or ctx.channel
        channel_permission_check = await self._check_channel_permissions(ctx, channel)
        if not channel_permission_check:
            return
        if not template:
            await ctx.send_help()
            return
        template = template.replace("\\t", "\t")
        template = template.replace("\\n", "\n")
        rss_feed = await self.config.channel(channel).feeds.get_raw(feed_name, default=None)
        if not rss_feed:
            await ctx.send("Feed not found!")
            return
        try:
            validate_template(template, rss_feed.get("template_tags", []))
        except TemplateValidationError as exc:
            await ctx.send(f"That template was not saved. {exc}")
            return
        success = await self._edit_template(ctx, feed_name, channel, template)
        if success:
            await ctx.send("Template added successfully.")
        else:
            await ctx.send("Feed not found!")

    @rss.command(name="viewtags")
    async def _rss_view_tags(self, ctx, feed_name: str, channel: Optional[GuildMessageable] = None):
        """View a preview of template tag content available from a specific feed."""
        channel = channel or ctx.channel
        channel_permission_check = await self._check_channel_permissions(ctx, channel)
        if not channel_permission_check:
            return

        rss_feed = await self.config.channel(channel).feeds.get_raw(feed_name, default=None)

        if not rss_feed:
            await ctx.send("No feed with that name in this channel.")
            return

        async with ctx.typing():
            await self._rss_view_tags_helper(ctx, rss_feed, feed_name)

    async def _rss_view_tags_helper(self, ctx, rss_feed: dict, feed_name: str):
        """Helper function for rss viewtags."""
        blue_ansi_prefix = "\u001b[1;40;34m"
        reset_ansi_prefix = "\u001b[0m"
        msg = f"{blue_ansi_prefix}[ Template Tag Content Preview for {feed_name} ]{reset_ansi_prefix}\n\n\t"
        feedparser_obj = await self._fetch_feedparser_object(rss_feed["url"])

        if not feedparser_obj:
            await ctx.send("Couldn't fetch that feed.")
            return
        if feedparser_obj.entries:
            # this feed has posts
            feedparser_plus_obj = await self._add_to_feedparser_object(feedparser_obj.entries[0], rss_feed["url"])
        else:
            # this feed does not have posts, but it has a header with channel information
            feedparser_plus_obj = await self._add_to_feedparser_object(feedparser_obj.feed, rss_feed["url"])

        longest_key = max(feedparser_plus_obj, key=len)
        longest_key_len = len(longest_key)
        for tag_name, tag_content in sorted(feedparser_plus_obj.items()):
            if tag_name in INTERNAL_TAGS:
                # these tags attached to the rss feed object are for internal handling options
                continue

            tag_content = str(tag_content).replace("[", "").replace("]", "").replace("\n", " ").replace('"', "")
            tag_content = tag_content.lstrip(" ")

            space = "\N{SPACE}"
            tag_name_padded = (
                f"{blue_ansi_prefix}${tag_name}{reset_ansi_prefix}{space*(longest_key_len - len(tag_name))}"
            )
            if len(tag_content) > 50:
                tag_content = tag_content[:50] + "..."
            msg += f"{tag_name_padded}  {tag_content}\n\t"

        for msg_part in pagify(msg, delims=["\n\t", "\n\n"], page_length=1900):
            await ctx.send(box(msg_part.rstrip("\n\t"), lang="ansi"))

    @rss.command(name="version", hidden=True)
    async def _rss_version(self, ctx):
        """Show the RSS version."""
        await ctx.send(f"RSS version {RSS_VERSION}")

    async def _add_feed(self, ctx, feed_name: str, channel: GuildMessageable, url: str):
        """Helper for rss add."""
        rss_exists = await self._check_feed_existing(ctx, feed_name, channel)
        if not rss_exists:
            feedparser_obj = await self._fetch_feedparser_object(url)
            if not feedparser_obj:
                await ctx.send("Couldn't fetch that feed: there were no feed objects found.")
                return

            # sort everything by time if a time value is present
            if feedparser_obj.entries:
                # this feed has posts
                sorted_feed_by_post_time = await self._sort_by_post_time(feedparser_obj.entries)
            else:
                # this feed does not have posts, but it has a header with channel information
                sorted_feed_by_post_time = [feedparser_obj.feed]

            # add additional tags/images/clean html
            feedparser_plus_obj = await self._add_to_feedparser_object(sorted_feed_by_post_time[0], url)
            rss_object = await self._convert_feedparser_to_rssfeed(feed_name, feedparser_plus_obj, url)

            async with self.config.channel(channel).feeds() as feed_data:
                feed_data[feed_name] = rss_object.to_json()
            msg = (
                f"Feed `{feed_name}` added in channel: {channel.mention}\n"
                f"List the template tags with `{ctx.prefix}rss listtags` "
                f"and modify the template using `{ctx.prefix}rss template`."
            )
            await ctx.send(msg)
        else:
            await ctx.send(f"There is already an existing feed named {bold(feed_name)} in {channel.mention}.")
            return

    async def _check_channel_permissions(self, ctx, channel: GuildMessageable, addl_send_messages_check=True):
        """Helper for rss functions."""
        if not channel.permissions_for(ctx.me).read_messages:
            await ctx.send("I don't have permissions to read that channel.")
            return False
        author_perms = channel.permissions_for(ctx.author)
        if not author_perms.read_messages:
            await ctx.send("You don't have permissions to read that channel.")
            return False
        # bot can only see threads that it has permissions to read messages in so no special handling needed
        # if author has read messages perm, they can read all public threads *but also* private threads they are in
        if isinstance(channel, discord.Thread) and channel.is_private() and not author_perms.manage_threads:
            try:
                await channel.fetch_member(ctx.author.id)
            except discord.NotFound:
                # author is not in a private thread
                return False
        if addl_send_messages_check:
            # check for send messages perm if needed, like on an rss add
            # not needed on something like rss delete
            if not can_user_send_messages_in(ctx.me, channel):
                await ctx.send("I don't have permissions to send messages in that channel.")
                return False
            else:
                return True
        else:
            return True

    async def _check_feed_existing(self, ctx, feed_name: str, channel: GuildMessageable):
        """Helper for rss functions."""
        rss_feed = await self.config.channel(channel).feeds.get_raw(feed_name, default=None)
        if not rss_feed:
            return False
        return True

    async def _delete_feed(self, ctx, feed_name: str, channel: GuildMessageable):
        """Helper for rss delete."""
        rss_exists = await self._check_feed_existing(ctx, feed_name, channel)

        if rss_exists:
            async with self.config.channel(channel).feeds() as rss_data:
                rss_data.pop(feed_name, None)
                return True
        return False

    async def _edit_template(self, ctx, feed_name: str, channel: GuildMessageable, template: str):
        """Helper for rss template."""
        rss_exists = await self._check_feed_existing(ctx, feed_name, channel)

        if rss_exists:
            async with self.config.channel(channel).feeds.all() as feed_data:
                if feed_name not in feed_data:
                    feed_data[feed_name] = {}
                feed_data[feed_name]["template"] = template
                return True
        return False

    @staticmethod
    def _find_website(website_url: str):
        """Helper for rss parse."""
        result = urlparse(website_url)
        if result.scheme:
            # https://www.website.com/...
            if result.netloc:
                website = result.netloc
            else:
                return None
        else:
            # www.website.com/...
            if result.path:
                website = result.path.split("/")[0]
            else:
                return None

        return website
    async def _get_feed_names(self, channel: GuildMessageable):
        """Helper for rss list/listall."""
        feed_list = []
        space = "\N{SPACE}"
        all_feeds = await self.config.channel(channel).feeds.all()
        if not all_feeds:
            return ["None."]
        longest_name_len = len(max(list(all_feeds.keys()), key=len))
        for name, data in all_feeds.items():
            extra_spacing = longest_name_len - len(name)
            feed_list.append(f"{name}{space * extra_spacing}  {data['url']}")
        return feed_list

    @staticmethod
    async def _title_case(phrase: str):
        exceptions = ["a", "and", "in", "of", "or", "on", "the"]
        lowercase_words = re.split(" ", phrase.lower())
        final_words = [lowercase_words[0].capitalize()]
        final_words += [word if word in exceptions else word.capitalize() for word in lowercase_words[1:]]
        return " ".join(final_words)

    @rss.command(name="announce", aliases=["announcement"])
    async def _rss_announce(
        self,
        ctx,
        feed_name: str,
        channel: Optional[GuildMessageable] = None,
        *,
        announcement: str = None,
    ):
        """Set normal content sent with each post; omit it to clear.

        Role mentions written directly here can notify. Feed-provided mentions,
        user mentions, and everyone mentions remain suppressed.
        """
        channel = channel or ctx.channel
        if not await self._check_channel_permissions(ctx, channel):
            return
        rss_feed = await self.config.channel(channel).feeds.get_raw(
            feed_name, default=None
        )
        if not rss_feed:
            await ctx.send("That feed name does not exist in this channel.")
            return

        if announcement:
            announcement = announcement.replace("\\t", "\t").replace("\\n", "\n")
            if len(announcement) > 500:
                await ctx.send("Announcements cannot exceed 500 characters.")
                return
            try:
                validate_template(announcement, rss_feed.get("template_tags", []))
            except TemplateValidationError as exc:
                await ctx.send(f"That announcement was not saved. {exc}")
                return

        async with self.config.channel(channel).feeds() as feeds:
            feeds[feed_name]["announcement"] = announcement or None

        if announcement:
            await ctx.send(
                f"Announcement for {bold(feed_name)} saved. Use `{ctx.prefix}rss force {feed_name}` to preview it."
            )
        else:
            await ctx.send(f"Announcement for {bold(feed_name)} cleared.")
