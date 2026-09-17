import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from rsspublisher.delivery import RSSDeliveryMixin


class RSSFilterDeliveryTests(unittest.IsolatedAsyncioTestCase):
    def make_harness(self):
        harness = object.__new__(RSSDeliveryMixin)
        harness._fetch_feedparser_object = AsyncMock(return_value=SimpleNamespace(entries=[
            {"id": "entry-1", "title": "Spam", "link": "https://example.test/1"}
        ]))
        harness._sort_by_post_time = AsyncMock(side_effect=lambda entries: entries)
        harness._time_tag_validation = AsyncMock(return_value=100)
        harness._add_to_feedparser_object = AsyncMock(return_value={
            "title": "Spam", "link": "https://example.test/1",
            "author": "Other", "tags_list": [],
            "_sick_entry_time": 100, "_sick_entry_id": "id:entry-1",
        })
        harness._record_feed_check = AsyncMock()
        harness._update_last_scraped = AsyncMock()
        return harness

    async def test_automatic_filter_rejection_advances_marker(self):
        harness = self.make_harness()
        channel = SimpleNamespace(id=1, name="news", send=AsyncMock())
        await harness.get_current_feed(channel, "newsalert", {
            "url": "https://example.test/feed", "template": "$link",
            "title_prefix": "News - ", "mode": "latest",
        })
        harness._update_last_scraped.assert_awaited_once()
        self.assertFalse(harness._update_last_scraped.await_args.kwargs["delivered"])
        channel.send.assert_not_awaited()

    async def test_forced_filter_rejection_reports_without_advancing(self):
        harness = self.make_harness()
        channel = SimpleNamespace(id=1, name="news", send=AsyncMock())
        await harness.get_current_feed(channel, "newsalert", {
            "url": "https://example.test/feed", "template": "$link",
            "title_prefix": "News - ", "mode": "latest",
        }, force=True)
        harness._update_last_scraped.assert_not_awaited()
        channel.send.assert_awaited_once()
        self.assertIn("not posted", channel.send.await_args.args[0])
