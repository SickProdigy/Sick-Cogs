import importlib.util
from pathlib import Path
import unittest

_spec = importlib.util.spec_from_file_location(
    "rsspublisher_models_views", Path(__file__).parents[1] / "models.py"
)
_models = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_models)
feed_delivery_style = _models.feed_delivery_style
feed_summary = _models.feed_summary


class RSSFeedViewModelTests(unittest.TestCase):
    def test_delivery_style_distinguishes_embed_native_and_plain_text(self):
        self.assertEqual(feed_delivery_style({"embed": True}), "RSS embed")
        self.assertEqual(
            feed_delivery_style({"embed": False, "template": "  $link\n"}),
            "Native link preview",
        )
        self.assertEqual(
            feed_delivery_style({"embed": False, "template": "${link}"}),
            "Native link preview",
        )
        self.assertEqual(
            feed_delivery_style({"embed": False, "template": "$title\n$link"}),
            "Plain text",
        )

    def test_summary_migrates_legacy_data_and_hides_announcement_content(self):
        summary = feed_summary("news", {
            "url": "https://example.test/feed",
            "embed": False,
            "template": "$link",
            "announcement": "@everyone hidden text",
            "paused": True,
            "mode": "catchup",
        })
        self.assertIn("Delivery: Native link preview", summary)
        self.assertIn("State: Paused · catchup", summary)
        self.assertIn("Announcement: Configured", summary)
        self.assertNotIn("hidden text", summary)

    def test_mixed_channel_summaries_keep_delivery_states_distinct(self):
        channels = {
            "news": {"newsalert": {"url": "https://example.test/news", "embed": False, "template": "$link"}},
            "dev": {"devcorner": {"url": "https://example.test/dev", "embed": True, "paused": True, "mode": "catchup"}},
        }
        rendered = {
            channel: [feed_summary(name, data) for name, data in feeds.items()]
            for channel, feeds in channels.items()
        }
        self.assertIn("Delivery: Native link preview", rendered["news"][0])
        self.assertIn("Delivery: RSS embed", rendered["dev"][0])
        self.assertIn("State: Paused · catchup", rendered["dev"][0])

    def test_summary_uses_safe_defaults_for_partial_legacy_feed(self):
        summary = feed_summary("legacy", {"url": "https://example.test/legacy"})
        self.assertIn("Delivery: RSS embed", summary)
        self.assertIn("State: Active · latest", summary)
        self.assertIn("Announcement: None", summary)
