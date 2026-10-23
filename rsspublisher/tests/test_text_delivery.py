import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import feedparser

from rsspublisher.delivery import RSSDeliveryMixin
from rsspublisher.renderer import FeedRenderer


class RSSTextDeliveryTests(unittest.IsolatedAsyncioTestCase):
    def make_harness(self, *, embed=False, red_embeds=True, announcement=None, long=False):
        feed = {
            "url": "https://example.test/feed", "template": "$description",
            "embed": embed, "announcement": announcement, "mode": "latest",
        }
        entry = feedparser.FeedParserDict(
            id="entry-1", title="News", link="https://example.test/1",
            description=("Update details <@&123> @everyone\n" * 150 if long else "Update details"),
            _sick_entry_time=100, _sick_entry_id="id:entry-1",
        )

        @asynccontextmanager
        async def feeds():
            yield {"news": feed}

        harness = object.__new__(RSSDeliveryMixin)
        harness.config = SimpleNamespace(channel=lambda channel: SimpleNamespace(feeds=feeds))
        harness.bot = SimpleNamespace(embed_requested=AsyncMock(return_value=red_embeds), dispatch=Mock())
        harness._renderer = FeedRenderer()
        harness._fetch_feedparser_object = AsyncMock(return_value=SimpleNamespace(entries=[entry]))
        harness._sort_by_post_time = AsyncMock(side_effect=lambda entries: entries)
        harness._time_tag_validation = AsyncMock(return_value=100)
        harness._add_to_feedparser_object = AsyncMock(return_value=entry)
        harness._get_current_feed_embed = AsyncMock()
        channel = SimpleNamespace(id=1, name="updates", send=AsyncMock())
        return harness, channel, feed

    async def test_single_and_multiple_pages_with_explicit_text_and_red_fallback(self):
        for embed, red_embeds in ((False, True), (True, False)):
            for long in (False, True):
                with self.subTest(embed=embed, red_embeds=red_embeds, long=long):
                    harness, channel, feed = self.make_harness(embed=embed, red_embeds=red_embeds, long=long)
                    await harness.get_current_feed(channel, "news", feed)
                    self.assertEqual(channel.send.await_count > 1, long)
                    self.assertGreater(channel.send.await_count, 0)
                    for call in channel.send.await_args_list:
                        self.assertLessEqual(len(call.args[0]), 2000)
                        self.assertEqual(call.kwargs["allowed_mentions"].to_dict(), discord.AllowedMentions.none().to_dict())
                    harness._get_current_feed_embed.assert_not_awaited()
                    self.assertEqual(feed["last_entry_id"], "id:entry-1")
                    self.assertTrue(feed["last_success_at"])

    async def test_announcement_mentions_only_on_first_page(self):
        harness, channel, feed = self.make_harness(announcement="<@&123> New post", long=True)
        await harness.get_current_feed(channel, "news", feed)
        calls = channel.send.await_args_list
        self.assertGreater(len(calls), 1)
        self.assertTrue(calls[0].args[0].startswith("<@&123> New post\n"))
        self.assertEqual(calls[0].kwargs["allowed_mentions"].to_dict(), {"roles": [123], "parse": []})
        for call in calls[1:]:
            self.assertEqual(call.kwargs["allowed_mentions"].to_dict(), {"parse": []})

    async def test_forced_delivery_suppresses_mentions_and_preserves_marker(self):
        harness, channel, feed = self.make_harness(announcement="<@&123> New post", long=True)
        await harness.get_current_feed(channel, "news", feed, force=True)
        self.assertGreater(channel.send.await_count, 1)
        for call in channel.send.await_args_list:
            mentions = call.kwargs["allowed_mentions"].to_dict()
            self.assertEqual(mentions["parse"], [])
            self.assertFalse(mentions.get("roles"))
        self.assertNotIn("last_entry_id", feed)

    async def test_failed_send_retries_and_success_is_deduplicated(self):
        for fail_on in (1, 2):
            with self.subTest(fail_on=fail_on):
                harness, channel, feed = self.make_harness(long=True)
                channel.send.side_effect = [None] * (fail_on - 1) + [
                    discord.HTTPException(SimpleNamespace(status=503, reason="Unavailable"), "retry")
                ]
                with self.assertRaises(discord.HTTPException):
                    await harness.get_current_feed(channel, "news", feed)
                self.assertNotIn("last_entry_id", feed)
                self.assertNotIn("last_success_at", feed)
                harness.bot.dispatch.assert_not_called()
                channel.send.reset_mock(side_effect=True)
                await harness.get_current_feed(channel, "news", feed)
                self.assertGreater(channel.send.await_count, 1)
                self.assertEqual(feed["last_entry_id"], "id:entry-1")
                channel.send.reset_mock()
                await harness.get_current_feed(channel, "news", feed)
                channel.send.assert_not_awaited()
