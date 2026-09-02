import asyncio
import calendar
import copy
import datetime
import ipaddress
import io
import logging
import re
import time
import socket
from types import SimpleNamespace
from dataclasses import dataclass
from typing import Collection, Mapping, Optional
from urllib.parse import urljoin, urlparse

import feedparser
import filetype
from bs4 import BeautifulSoup
from redbot.core.utils.chat_formatting import box, escape, humanize_list

from .models import INTERNAL_TAGS, RssFeed, TagType, entry_identity

import aiohttp

log = logging.getLogger("red.Sick-Cogs.RSS")
IPV4_RE = re.compile("\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}")
IPV6_RE = re.compile("([a-f0-9:]+:+)+[a-f0-9]+")

MAX_FEED_BYTES = 5 * 1024 * 1024
MAX_PAGE_BYTES = 2 * 1024 * 1024
MAX_IMAGE_PROBE_BYTES = 261
MAX_REDIRECTS = 5
REDIRECT_STATUSES = {301, 302, 303, 307, 308}

class NoFeedContent(ValueError):
    """Raised when a URL does not produce a usable feed."""

class ResponseTooLarge(ValueError):
    pass

class UnsafeFeedURL(ValueError):
    pass

@dataclass(frozen=True)
class FetchResponse:
    status: int
    body: bytes
    url: str
    content_type: Optional[str]

def validate_http_url(url: str) -> str:
    """Validate and normalize an HTTP(S) URL."""
    value = (url or "").strip()
    parsed = urlparse(value)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise aiohttp.InvalidURL(value)
    return value

def _is_private_target(
    address: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> bool:
    return not address.is_global

async def validate_network_target(
    url: str, allowed_private_hosts: Collection[str] = ()
) -> str:
    """Reject local/private targets unless the bot owner allowlisted the host."""
    value = validate_http_url(url)
    parsed = urlparse(value)
    hostname = parsed.hostname.casefold()
    allowed = {host.casefold().strip().rstrip(".") for host in allowed_private_hosts}
    if hostname.rstrip(".") in allowed:
        return value

    try:
        addresses = [ipaddress.ip_address(hostname)]
    except ValueError:
        port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
        try:
            records = await asyncio.get_running_loop().getaddrinfo(
                hostname, port, type=socket.SOCK_STREAM
            )
        except socket.gaierror as exc:
            raise UnsafeFeedURL(f"Could not resolve feed host {hostname}.") from exc
        addresses = list({ipaddress.ip_address(record[4][0]) for record in records})

    if not addresses or any(_is_private_target(address) for address in addresses):
        raise UnsafeFeedURL(
            f"Feed host {hostname} resolves to a private, local, or reserved address."
        )
    return value

async def read_limited_response(
    response: aiohttp.ClientResponse, max_bytes: int = MAX_FEED_BYTES
) -> bytes:
    """Read a response without allowing an unbounded allocation."""
    content_length = response.content_length
    if content_length is not None and content_length > max_bytes:
        raise ResponseTooLarge(
            f"Response declared {content_length} bytes; limit is {max_bytes} bytes."
        )

    chunks = []
    total = 0
    async for chunk in response.content.iter_chunked(64 * 1024):
        total += len(chunk)
        if total > max_bytes:
            raise ResponseTooLarge(f"Response exceeded the {max_bytes}-byte limit.")
        chunks.append(chunk)
    return b"".join(chunks)

async def fetch_limited(
    session: aiohttp.ClientSession,
    url: str,
    *,
    headers: Optional[Mapping[str, str]] = None,
    max_bytes: int = MAX_FEED_BYTES,
    prefix_bytes: Optional[int] = None,
    allowed_private_hosts: Collection[str] = (),
) -> FetchResponse:
    """Fetch an HTTP resource while validating every redirect target."""
    current_url = url
    for redirect_count in range(MAX_REDIRECTS + 1):
        current_url = await validate_network_target(
            current_url, allowed_private_hosts
        )
        async with session.get(
            current_url, headers=headers, allow_redirects=False
        ) as response:
            if response.status in REDIRECT_STATUSES:
                location = response.headers.get("Location")
                if not location:
                    return FetchResponse(
                        response.status,
                        b"",
                        str(response.url),
                        response.content_type,
                    )
                if redirect_count >= MAX_REDIRECTS:
                    raise UnsafeFeedURL(
                        f"Feed exceeded the {MAX_REDIRECTS}-redirect limit."
                    )
                current_url = urljoin(str(response.url), location)
                continue

            if prefix_bytes is not None:
                body = await response.content.read(prefix_bytes)
            else:
                body = await read_limited_response(response, max_bytes)
            return FetchResponse(
                response.status,
                body,
                str(response.url),
                response.content_type,
            )

    raise UnsafeFeedURL("Feed redirect validation failed.")

class RSSFetcherMixin:
    """HTTP lifecycle, feed parsing, normalization, and validation."""
    def _add_content_images(self, bs4_soup: BeautifulSoup, rss_object: feedparser.util.FeedParserDict):
        """
        $content_images should always be marked as a special tag as the tags will
        be dynamically generated based on the content included in the latest post.
        """
        content_images = bs4_soup.find_all("img")
        if content_images:
            for i, image in enumerate(content_images):
                tag_name = f"content_image{str(i + 1).zfill(2)}"
                try:
                    rss_object[tag_name] = image["src"]
                    rss_object["is_special"].append(tag_name)
                except KeyError:
                    pass
        return rss_object
    def _add_generic_html_plaintext(self, bs4_soup: BeautifulSoup):
        """
        Bs4's .text attribute on a soup strips newlines and spaces
        This provides newlines and more readable content.
        """
        text = ""
        for element in bs4_soup.descendants:
            if isinstance(element, str):
                text += element
            elif element.name == "br" or element.name == "p" or element.name == "li":
                text += "\n"
        text = re.sub("\\n+", "\n", text)
        text = text.replace("*", "\\*")
        text = text.replace("SC_OFF", "").replace("SC_ON", "\n")
        text = text.replace("[link]", "").replace("[comments]", "")

        return escape(text)

    async def _append_bs4_tags(self, rss_object: feedparser.util.FeedParserDict, url: str):
        """Append bs4-discovered tags to an rss_feed/feedparser object."""
        rss_object["is_special"] = []
        soup = None
        tags_list = []

        temp_rss_obect = copy.deepcopy(rss_object)
        for tag_name, tag_content in temp_rss_obect.items():
            if tag_name in INTERNAL_TAGS:
                continue

            tag_content_check = await self._get_tag_content_type(tag_content)

            if tag_content_check == TagType.HTML:
                # this is a tag that is only html content
                try:
                    soup = BeautifulSoup(tag_content, "html.parser")
                except TypeError:
                    pass

                # this is a standard html format summary_detail tag
                # the tag was determined to be html through the type attrib that
                # was attached from the feed publisher but it's really a dict.
                try:
                    soup = BeautifulSoup(tag_content["value"], "html.parser")
                except (KeyError, TypeError):
                    pass

                # this is a standard html format content or summary tag
                try:
                    soup = BeautifulSoup(tag_content[0]["value"], "html.parser")
                except (KeyError, TypeError):
                    pass

                if soup:
                    rss_object[f"{tag_name}_plaintext"] = self._add_generic_html_plaintext(soup)

            if tag_content_check == TagType.LIST:
                tags_content_counter = 0

                for list_item in tag_content:
                    list_item_check = await self._get_tag_content_type(list_item)

                    # for common "links" format or when "content" is a list
                    list_html_content_counter = 0
                    if list_item_check == TagType.HTML:
                        list_tags = ["value", "href"]
                        for tag in list_tags:
                            try:
                                url_check = await self._valid_url(list_item[tag], feed_check=False)
                                if not url_check:
                                    # bs4 will cry if you try to give it a url to parse, so let's only
                                    # parse non-url content
                                    tag_content = BeautifulSoup(list_item[tag], "html.parser")
                                    tag_content = self._add_generic_html_plaintext(tag_content)
                                else:
                                    tag_content = list_item[tag]
                                list_html_content_counter += 1
                                name = f"{tag_name}_plaintext{str(list_html_content_counter).zfill(2)}"
                                rss_object[name] = tag_content
                                rss_object["is_special"].append(name)
                            except (KeyError, TypeError):
                                pass

                    if list_item_check == TagType.DICT:
                        authors_content_counter = 0
                        enclosure_content_counter = 0
                        enclosure_url_counter = 0

                        # common "authors" tag format
                        try:
                            authors_content_counter += 1
                            name = f"{tag_name}_plaintext{str(authors_content_counter).zfill(2)}"
                            tag_content = BeautifulSoup(list_item["name"], "html.parser")
                            rss_object[name] = tag_content.get_text()
                            rss_object["is_special"].append(name)
                        except KeyError:
                            pass

                        # common "enclosure" tag image format
                        # note: this is not adhering to RSS feed specifications
                        # proper enclosure tags should have `length`, `type`, `url`
                        # and not `href`, `type`, `rel`
                        # but, this is written for the first feed I have seen with an "enclosure" tag
                        try:
                            image_url = list_item["href"]
                            image_type = list_item["type"]
                            image_rel = list_item["rel"]
                            enclosure_content_counter += 1
                            name = f"media_plaintext{str(enclosure_content_counter).zfill(2)}"
                            rss_object[name] = image_url
                            rss_object["is_special"].append(name)
                        except KeyError:
                            pass

                        # special tag for enclosure["url"] so that users can differentiate them
                        # from image urls found in enclosure["href"]
                        try:
                            image_url = list_item["url"]
                            enclosure_url_counter += 1
                            name = f"media_url{str(enclosure_url_counter).zfill(2)}"
                            rss_object[name] = image_url
                            rss_object["is_special"].append(name)
                        except KeyError:
                            pass

                        # common "tags" tag format
                        try:
                            tag = list_item["term"]
                            tags_content_counter += 1
                            name = f"{tag_name}_plaintext{str(tags_content_counter).zfill(2)}"
                            rss_object[name] = tag
                            rss_object["is_special"].append(name)
                            tags_list.append(tag) if tag not in tags_list else tags_list
                        except KeyError:
                            pass

                if len(tags_list) > 0:
                    rss_object["tags_list"] = tags_list
                    rss_object["tags_plaintext_list"] = humanize_list(tags_list)
                    rss_object["is_special"].append("tags_list")
                    rss_object["is_special"].append("tags_plaintext_list")

        # if image dict tag exists, check for an image
        try:
            rss_object["image_plaintext"] = rss_object["image"]["href"]
            rss_object["is_special"].append("image_plaintext")
        except KeyError:
            pass

        # if media_thumbnail or media_content exists, return the first friendly url
        try:
            rss_object["media_content_plaintext"] = rss_object["media_content"][0]["url"]
            rss_object["is_special"].append("media_content_plaintext")
        except KeyError:
            pass
        try:
            rss_object["media_thumbnail_plaintext"] = rss_object["media_thumbnail"][0]["url"]
            rss_object["is_special"].append("media_thumbnail_plaintext")
        except KeyError:
            pass

        # change published_parsed and updated_parsed into a datetime object for embed footers
        for time_tag in ["updated_parsed", "published_parsed"]:
            try:
                if isinstance(rss_object[time_tag], time.struct_time):
                    rss_object[f"{time_tag}_datetime"] = datetime.datetime(*rss_object[time_tag][:6])
            except KeyError:
                pass

        if soup:
            rss_object = self._add_content_images(soup, rss_object)

        # add special tag/special site formatter here if needed in the future

        return rss_object
    async def _get_tag_content_type(self, tag_content):
        """
        Tag content type can be:
            str, list, dict (FeedParserDict), bool, datetime.datetime object or time.struct_time
        """
        try:
            if tag_content["type"] == "text/html":
                return TagType(2)
        except (KeyError, TypeError):
            html_tags = ["<a>", "<a href", "<img", "<p>", "<b>", "</li>", "</ul>"]
            if any(word in str(tag_content) for word in html_tags):
                return TagType(2)

        if isinstance(tag_content, dict):
            return TagType(3)
        elif isinstance(tag_content, list):
            return TagType(4)
        else:
            return TagType(1)

    async def _get_http_session(self):
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers=self._headers,
                timeout=aiohttp.ClientTimeout(total=20),
            )
        return self._session

    async def _get_url_content(self, url):
        """Helper for rss add/_valid_url."""
        try:
            url = validate_http_url(url)
            # force github.com to serve us xml instead of json
            headers = dict(self._headers)
            if "github.com" in url:
                headers["Accept"] = "application/vnd.github+xml"

            session = await self._get_http_session()
            response = await fetch_limited(
                session,
                url,
                headers=headers,
                allowed_private_hosts=await self.config.private_feed_hosts(),
            )
            if response.status == 404:
                friendly_msg = "The server returned 404 Not Found. Check your url and try again."
                return None, friendly_msg
            if response.status < 200 or response.status >= 300:
                friendly_msg = f"The server returned HTTP {response.status}."
                return None, friendly_msg
            html = response.body
            return html, None
        except aiohttp.client_exceptions.ClientConnectorError:
            friendly_msg = "There was an OSError or the connection failed."
            msg = f"aiohttp failure accessing feed at url:\n\t{url}"
            log.error(msg, exc_info=True)
            return None, friendly_msg
        except UnsafeFeedURL as e:
            friendly_msg = str(e)
            log.warning("Blocked unsafe RSS target %s: %s", url, e)
            return None, friendly_msg
        except ResponseTooLarge as e:
            friendly_msg = str(e)
            log.warning("Oversized RSS response from %s: %s", url, e)
            return None, friendly_msg
        except aiohttp.client_exceptions.ClientPayloadError as e:
            friendly_msg = "The website closed the connection prematurely or the response was malformed.\n"
            friendly_msg += f"The error returned was: `{str(e)}`\n"
            friendly_msg += "For more technical information, check your bot's console or logs."
            msg = f"content error while reading feed at url:\n\t{url}"
            log.error(msg, exc_info=True)
            return None, friendly_msg
        except asyncio.exceptions.TimeoutError:
            friendly_msg = "The bot timed out while trying to access that content."
            msg = f"asyncio timeout while accessing feed at url:\n\t{url}"
            log.error(msg, exc_info=True)
            return None, friendly_msg
        except aiohttp.client_exceptions.ServerDisconnectedError:
            friendly_msg = "The target server disconnected early without a response."
            msg = f"server disconnected while accessing feed at url:\n\t{url}"
            log.error(msg, exc_info=True)
            return None, friendly_msg
        except Exception:
            friendly_msg = "There was an unexpected error. Check your console for more information."
            msg = f"General failure accessing feed at url:\n\t{url}"
            log.error(msg, exc_info=True)
            return None, friendly_msg

    async def _fetch_feedparser_object(self, url: str):
        """Get a full feedparser object from a url: channel header + items."""
        html, error_msg = await self._get_url_content(url)
        if not html:
            return SimpleNamespace(entries=None, error=error_msg, url=url)

        feedparser_obj = feedparser.parse(html)
        if feedparser_obj.bozo:
            error_msg = f"Bozo feed: feedparser is unable to parse the response from {url}.\n"
            error_msg += f"Feedparser error message: `{feedparser_obj.bozo_exception}`"
            return SimpleNamespace(entries=None, error=error_msg, url=url)

        return feedparser_obj

    async def _add_to_feedparser_object(self, feedparser_obj: feedparser.util.FeedParserDict, url: str):
        """
        Input: A feedparser object
        Process: Append custom tags to the object from the custom formatters
        Output: A feedparser object with additional attributes
        """
        feedparser_plus_obj = await self._append_bs4_tags(feedparser_obj, url)
        feedparser_plus_obj["template_tags"] = sorted(feedparser_plus_obj.keys())
        feedparser_plus_obj["_sick_entry_id"] = entry_identity(feedparser_obj)
        feedparser_plus_obj["_sick_entry_time"] = await self._time_tag_validation(feedparser_obj)

        return feedparser_plus_obj

    async def _convert_feedparser_to_rssfeed(
        self, feed_name: str, feedparser_plus_obj: feedparser.util.FeedParserDict, url: str
    ):
        """
        Converts any feedparser/feedparser_plus object to an RssFeed object.
        Used in rss add when saving a new feed.
        """
        entry_time = await self._time_tag_validation(feedparser_plus_obj)

        # sometimes there's no title or no link attribute and feedparser doesn't really play nice with that
        try:
            feedparser_plus_obj_title = feedparser_plus_obj["title"]
        except KeyError:
            feedparser_plus_obj_title = ""
        try:
            feedparser_plus_obj_link = feedparser_plus_obj["link"]
        except KeyError:
            feedparser_plus_obj_link = ""

        rss_object = RssFeed(
            name=feed_name.lower(),
            last_title=feedparser_plus_obj_title,
            last_link=feedparser_plus_obj_link,
            last_time=entry_time,
            template="$title\n$link",
            url=url,
            template_tags=feedparser_plus_obj["template_tags"],
            is_special=feedparser_plus_obj["is_special"],
            embed=True,
        )

        return rss_object

    async def _sort_by_post_time(self, feedparser_obj: feedparser.util.FeedParserDict):
        base_url = urlparse(feedparser_obj[0].get("link")).netloc
        use_published_parsed_override = await self.config.use_published()

        if base_url in use_published_parsed_override:
            time_tag = ["published_parsed"]
        else:
            time_tag = ["updated_parsed", "published_parsed"]

        for tag in time_tag:
            try:
                baseline_time = time.struct_time((2021, 1, 1, 12, 0, 0, 4, 1, -1))
                sorted_feed_by_post_time = sorted(feedparser_obj, key=lambda x: x.get(tag, baseline_time), reverse=True)
                break
            except TypeError:
                sorted_feed_by_post_time = feedparser_obj

        return sorted_feed_by_post_time

    async def _time_tag_validation(self, entry: feedparser.util.FeedParserDict):
        """Gets a unix timestamp if it's available from a single feedparser post entry."""
        feed_link = entry.get("link", None)
        if feed_link:
            base_url = urlparse(feed_link).netloc
        else:
            return None

        # check for a feed time override, if a feed is being problematic regarding updated_parsed
        # usage (i.e. a feed entry keeps reposting with no perceived change in content)
        use_published_parsed_override = await self.config.use_published()
        if base_url in use_published_parsed_override:
            entry_time = entry.get("published_parsed", None)
        else:
            entry_time = entry.get("updated_parsed", None)
            if not entry_time:
                entry_time = entry.get("published_parsed", None)

        if isinstance(entry_time, time.struct_time):
            entry_time = calendar.timegm(entry_time)
        if entry_time:
            return int(entry_time)
        return None
    async def _valid_url(self, url: str, feed_check=True):
        """Helper for rss add."""
        try:
            result = urlparse(url)
        except Exception as e:
            log.exception(e, exc_info=e)
            return False

        if result.scheme.lower() in {"http", "https"} and result.netloc:
            if feed_check:
                text, error_msg = await self._get_url_content(url)
                if not text:
                    raise NoFeedContent(error_msg)
                    return False

                rss = feedparser.parse(text)
                if rss.bozo:
                    error_message = rss.feed.get("summary", str(rss))[:1500]
                    error_message = re.sub(IPV4_RE, "[REDACTED IP ADDRESS]", error_message)
                    error_message = re.sub(IPV6_RE, "[REDACTED IP ADDRESS]", error_message)
                    msg = f"Bozo feed: feedparser is unable to parse the response from {url}.\n\n"
                    msg += "Received content preview:\n"
                    msg += box(error_message)
                    raise NoFeedContent(msg)
                    return False
                else:
                    return True
            else:
                return True
        else:
            return False

    async def _validate_image(self, url: str):
        """Helper for _get_current_feed_embed."""
        try:
            session = await self._get_http_session()
            response = await fetch_limited(
                session,
                url,
                prefix_bytes=MAX_IMAGE_PROBE_BYTES,
                allowed_private_hosts=await self.config.private_feed_hosts(),
            )
            image = response.body
            img = io.BytesIO(image)
            file_type = filetype.guess(img)
            if not file_type:
                return None
            return file_type.extension
        except aiohttp.client_exceptions.InvalidURL:
            return None
        except asyncio.exceptions.TimeoutError:
            log.error(f"asyncio timeout while accessing image at url:\n\t{url}", exc_info=True)
            return None
        except Exception:
            log.error(f"Failure accessing image in embed feed at url:\n\t{url}", exc_info=True)
            return None
