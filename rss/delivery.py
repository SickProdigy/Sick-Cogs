import datetime
import logging
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping, Optional, Sequence, Union

import discord
import feedparser

from .models import FeedMode, entry_identity, normalize_mode

@dataclass(frozen=True)
class EntryCandidate:
    entry: Mapping[str, Any]
    identity: str
    title: str
    link: str
    timestamp: Optional[int]

    @classmethod
    def from_entry(cls, entry: Mapping[str, Any], timestamp: Optional[int]):
        return cls(
            entry=entry,
            identity=entry_identity(entry),
            title=str(entry.get("title", "") or ""),
            link=str(entry.get("link", "") or ""),
            timestamp=timestamp,
        )

def _matches_legacy_marker(candidate: EntryCandidate, feed: Mapping[str, Any]) -> bool:
    last_title = str(feed.get("last_title", "") or "")
    last_link = str(feed.get("last_link", "") or "")
    last_time = feed.get("last_time")

    if last_time is not None and candidate.timestamp is not None:
        if candidate.timestamp < last_time:
            return True
        if candidate.timestamp == last_time:
            return candidate.title == last_title and candidate.link == last_link
        return False

    return candidate.title == last_title and candidate.link == last_link

def select_unseen_entries(
    candidates: Sequence[EntryCandidate], feed: Mapping[str, Any]
) -> list[EntryCandidate]:
    """Select unseen candidates from a newest-first sequence.

    Latest mode returns at most the newest unseen entry. Catchup mode returns
    every unseen entry in oldest-first delivery order.
    """
    last_entry_id = feed.get("last_entry_id")
    unseen = []

    for candidate in candidates:
        if last_entry_id:
            if candidate.identity == last_entry_id:
                break
        elif _matches_legacy_marker(candidate, feed):
            break
        unseen.append(candidate)

    if normalize_mode(feed.get("mode")) == FeedMode.LATEST.value:
        return unseen[:1]
    return list(reversed(unseen))

log = logging.getLogger("red.Sick-Cogs.RSS")
GuildMessageable = Union[discord.TextChannel, discord.VoiceChannel, discord.StageChannel, discord.Thread]

class RSSDeliveryMixin:
    """Discord delivery, event dispatch, and marker persistence."""
    async def _update_last_scraped(
        self,
        channel: GuildMessageable,
        feed_name: str,
        current_feed_title: str,
        current_feed_link: str,
        current_feed_time: int,
        current_entry_id: str,
        *,
        delivered: bool = True,
    ):
        """Advance a feed marker after delivery or an intentional filter skip."""
        async with self.config.channel(channel).feeds() as feed_data:
            try:
                stored_feed = feed_data[feed_name]
                stored_feed["last_title"] = current_feed_title
                stored_feed["last_link"] = current_feed_link
                stored_feed["last_time"] = current_feed_time
                stored_feed["last_entry_id"] = current_entry_id
                stored_feed["last_checked_at"] = datetime.datetime.now(
                    datetime.timezone.utc
                ).isoformat()
                if delivered:
                    stored_feed["last_success_at"] = stored_feed["last_checked_at"]
                    stored_feed["last_error"] = None
                    stored_feed["consecutive_failures"] = 0
            except KeyError:
                # The feed was deleted during this check.
                pass

    async def _record_feed_check(self, channel, feed_name: str, error: str = None):
        """Persist feed-check health without changing its delivery marker."""
        async with self.config.channel(channel).feeds() as feed_data:
            try:
                stored_feed = feed_data[feed_name]
                stored_feed["last_checked_at"] = datetime.datetime.now(
                    datetime.timezone.utc
                ).isoformat()
                stored_feed["last_error"] = error
                if error:
                    stored_feed["consecutive_failures"] = (
                        int(stored_feed.get("consecutive_failures", 0)) + 1
                    )
                else:
                    stored_feed["consecutive_failures"] = 0
            except KeyError:
                pass

    def _dispatch_rss_event(self, legacy_name: str, current_name: str, **payload):
        self.bot.dispatch(legacy_name, **payload)
        self.bot.dispatch(current_name, **payload)

    async def get_current_feed(self, channel: GuildMessageable, name: str, rss_feed: dict, *, force: bool = False):
        """Takes an RSS feed and builds an object with all extra tags"""
        log.debug(f"getting feed {name} on cid {channel.id}")
        url = rss_feed["url"]
        if rss_feed.get("paused", False) and not force:
            log.debug(f"Skipping paused feed {name} on cid {channel.id}")
            return
        template = rss_feed["template"]

        feedparser_obj = await self._fetch_feedparser_object(url)
        if not feedparser_obj:
            return
        try:
            error = str(feedparser_obj.error)
        except AttributeError:
            await self._record_feed_check(channel, name)
        else:
            log.debug(f"{error} Channel: {channel.id}")
            await self._record_feed_check(channel, name, error)
            return

        # Build newest-first candidates, then let the delivery policy choose
        # either the newest unseen entry or every unseen entry oldest-first.
        if feedparser_obj.entries:
            sorted_entries = await self._sort_by_post_time(feedparser_obj.entries)
        else:
            sorted_entries = [feedparser_obj.feed]

        candidates = []
        for entry in sorted_entries:
            entry_time = await self._time_tag_validation(entry)
            candidates.append(EntryCandidate.from_entry(entry, entry_time))

        if force:
            selected_entries = candidates[:1]
        else:
            selected_entries = select_unseen_entries(candidates, rss_feed)

        if not selected_entries:
            return

        feedparser_plus_objects = []
        for candidate in selected_entries:
            feedparser_plus_obj = await self._add_to_feedparser_object(candidate.entry, url)
            feedparser_plus_objects.append(feedparser_plus_obj)

        # list of feedparser_plus_objects wrapped in MappingProxyType
        # filled during the loop below
        proxied_dicts = []

        sent_message = False
        for feedparser_plus_obj in feedparser_plus_objects:
            try:
                curr_title = feedparser_plus_obj.title
            except AttributeError:
                curr_title = ""
            except IndexError:
                log.debug(f"No entries found for feed {name} on cid {channel.id}")
                return
            curr_link = feedparser_plus_obj.get("link", "")
            curr_time = feedparser_plus_obj.get("_sick_entry_time")
            curr_entry_id = feedparser_plus_obj.get("_sick_entry_id")

            # allowed tag verification section
            allowed_tags = rss_feed.get("allowed_tags", [])
            if len(allowed_tags) > 0:
                allowed_post_tags = [x.lower() for x in allowed_tags]
                feed_tag_list = [x.lower() for x in feedparser_plus_obj.get("tags_list", [])]
                intersection = list(set(feed_tag_list).intersection(allowed_post_tags))
                if len(intersection) == 0:
                    log.debug(
                        f"{name} feed post in {channel.name} ({channel.id}) was denied because of an allowed tag mismatch."
                    )
                    if not force:
                        await self._update_last_scraped(
                            channel, name, curr_title, curr_link, curr_time, curr_entry_id,
                            delivered=False,
                        )
                    continue

            message = self._renderer.render_message(
                name, template, feedparser_plus_obj, rss_feed.get("limit", 0)
            )
            if not message:
                log.debug(
                    f"{name} feed in {channel.name} ({channel.id}) has no valid tags; not posting."
                )
                return

            announcement_template = rss_feed.get("announcement")
            announcement = self._renderer.render_announcement(
                name, announcement_template, feedparser_plus_obj
            )
            allowed_role_ids = self._renderer.announcement_role_ids(
                announcement_template
            )
            announcement_mentions = discord.AllowedMentions(
                everyone=False,
                users=False,
                roles=(
                    []
                    if force
                    else [discord.Object(id=role_id) for role_id in allowed_role_ids]
                ),
                replied_user=False,
            )

            embed_toggle = rss_feed.get("embed", True)
            red_embed_settings = await self.bot.embed_requested(channel)

            if embed_toggle and red_embed_settings:
                await self._get_current_feed_embed(
                    channel, rss_feed, feedparser_plus_obj, message,
                    announcement, announcement_mentions,
                )
            else:
                combined_message = (
                    f"{announcement}\n{message}" if announcement else message
                )
                for page_index, page in enumerate(pagify(combined_message, delims=["\n"])):
                    allowed_mentions = (
                        announcement_mentions
                        if announcement and page_index == 0
                        else discord.AllowedMentions.none()
                    )
                    await channel.send(page, allowed_mentions=allowed_mentions)
            if not force:
                await self._update_last_scraped(
                    channel, name, curr_title, curr_link, curr_time, curr_entry_id
                )
            sent_message = True

            # This event can be used in 3rd-party using listeners.
            # This may (and most likely will) get changes in the future
            # so I suggest accepting **kwargs in the listeners using this event.
            #
            # channel: Union[discord.TextChannel, discord.VoiceChannel, discord.StageChannel, discord.Thread]
            #     The channel feed alert went to.
            # feed_data: Mapping[str, Any]
            #     Read-only mapping with feed's data.
            #     The available data depends on what this cog needs
            #     and there most likely will be changes here in future.
            #     Available keys include: `name`, `template`, `url`, `embed`, etc.
            # feedparser_dict: Mapping[str, Any]
            #     Read-only mapping with parsed data from the feed.
            #     See documentation of feedparser.FeedParserDict for more information.
            # force: bool
            #     True if the update was forced (through `[p]rss force`), False otherwise.
            feedparser_dict_proxy = MappingProxyType(feedparser_plus_obj)
            proxied_dicts.append(feedparser_dict_proxy)
            self._dispatch_rss_event(
                "aikaternacogs_rss_message",
                "sickcogs_rss_message",
                channel=channel,
                feed_data=MappingProxyType(rss_feed),
                feedparser_dict=feedparser_dict_proxy,
                force=force,
            )

        if not sent_message:
            return

        # This event can be used in 3rd-party using listeners.
        # This may (and most likely will) get changes in the future
        # so I suggest accepting **kwargs in the listeners using this event.
        #
        # channel: Union[discord.TextChannel, discord.VoiceChannel, discord.StageChannel, discord.Thread]
        #     The channel feed alerts went to.
        # feed_data: Mapping[str, Any]
        #     Read-only mapping with feed's data.
        #     The available data depends on what this cog needs
        #     and there most likely will be changes here in future.
        #     Available keys include: `name`, `template`, `url`, `embed`, etc.
        # feedparser_dicts: List[Mapping[str, Any]]
        #     List of read-only mappings with parsed data
        #     from each **new** entry in the feed.
        #     See documentation of feedparser.FeedParserDict for more information.
        # force: bool
        #     True if the update was forced (through `[p]rss force`), False otherwise.
        self._dispatch_rss_event(
            "aikaternacogs_rss_feed_update",
            "sickcogs_rss_feed_update",
            channel=channel,
            feed_data=MappingProxyType(rss_feed),
            feedparser_dicts=proxied_dicts,
            force=force,
        )

    async def _get_current_feed_embed(
        self,
        channel: GuildMessageable,
        rss_feed: dict,
        feedparser_plus_obj: feedparser.util.FeedParserDict,
        message: str,
        announcement: Optional[str],
        allowed_mentions: discord.AllowedMentions,
    ):
        embeds = await self._renderer.build_embeds(
            message, rss_feed, feedparser_plus_obj, self._validate_image
        )
        for embed_index, embed in enumerate(embeds):
            await channel.send(
                content=announcement if embed_index == 0 else None,
                embed=embed,
                allowed_mentions=(
                    allowed_mentions
                    if embed_index == 0
                    else discord.AllowedMentions.none()
                ),
            )
