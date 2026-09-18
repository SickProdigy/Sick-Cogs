import unittest
from types import SimpleNamespace
from unittest.mock import patch

from meme.meme import Meme


class MemeFeedReconciliationTests(unittest.TestCase):
    def test_create_new_feed(self):
        feeds = {}

        action, feed_id, duplicates = Meme._reconcile_feed(
            feeds, 10, "memeapi", "Memes", 360, toggle_identical=True
        )

        self.assertEqual((action, feed_id, duplicates), ("added", "1", []))
        self.assertEqual(feeds["1"]["source"], "memes")
        self.assertEqual(feeds["1"]["interval"], 21600)

    def test_repeat_identical_feed_toggles_it_off(self):
        feeds = {
            "4": {"channel_id": 10, "provider": "memeapi", "source": "memes",
                  "interval": 21600, "enabled": True}
        }

        action, feed_id, duplicates = Meme._reconcile_feed(
            feeds, 10, "memeapi", "memes", 360, toggle_identical=True
        )

        self.assertEqual((action, feed_id, duplicates), ("removed", "4", []))
        self.assertEqual(feeds, {})

    def test_changed_interval_updates_in_place(self):
        feeds = {
            "4": {"channel_id": 10, "provider": "memeapi", "source": "memes",
                  "interval": 21600, "enabled": True, "next_post": 9999999999}
        }

        action, feed_id, _ = Meme._reconcile_feed(
            feeds, 10, "memeapi", "memes", 180, toggle_identical=True
        )

        self.assertEqual((action, feed_id), ("updated", "4"))
        self.assertEqual(feeds["4"]["interval"], 10800)
        self.assertTrue(feeds["4"]["enabled"])

    def test_existing_duplicates_are_consolidated(self):
        feeds = {
            "2": {"channel_id": 10, "provider": "memeapi", "source": "memes",
                  "interval": 21600, "enabled": True},
            "5": {"channel_id": 10, "provider": "memeapi", "source": "MEMES",
                  "interval": 10800, "enabled": True},
        }

        action, feed_id, duplicates = Meme._reconcile_feed(
            feeds, 10, "memeapi", "memes", 180, toggle_identical=True
        )

        self.assertEqual((action, feed_id, duplicates), ("updated", "2", ["5"]))
        self.assertEqual(list(feeds), ["2"])
        self.assertEqual(feeds["2"]["interval"], 10800)

    def test_advanced_add_updates_identical_feed_instead_of_toggling(self):
        feeds = {
            "1": {"channel_id": 10, "provider": "memeapi", "source": "memes",
                  "interval": 21600, "enabled": True}
        }

        action, feed_id, _ = Meme._reconcile_feed(
            feeds, 10, "memeapi", "memes", 360, toggle_identical=False
        )

        self.assertEqual((action, feed_id), ("updated", "1"))
        self.assertEqual(list(feeds), ["1"])

    def test_destination_permission_failure_is_reported(self):
        class FakeTextChannel:
            mention = "#memes"

            def permissions_for(self, member):
                return SimpleNamespace(view_channel=True, send_messages=False, embed_links=False)

        with patch("meme.meme.discord.TextChannel", FakeTextChannel):
            error = Meme._feed_destination_error(SimpleNamespace(me=object()), FakeTextChannel())

        self.assertIn("send messages", error)
        self.assertIn("embed links", error)

    def test_distinct_source_or_destination_is_preserved(self):
        feeds = {
            "1": {"channel_id": 10, "provider": "memeapi", "source": "memes",
                  "interval": 21600, "enabled": True},
            "2": {"channel_id": 11, "provider": "memeapi", "source": "memes",
                  "interval": 21600, "enabled": True},
            "3": {"channel_id": 10, "provider": "memeapi", "source": "gaming",
                  "interval": 21600, "enabled": True},
        }

        Meme._reconcile_feed(feeds, 10, "memeapi", "memes", 180, toggle_identical=True)

        self.assertEqual(set(feeds), {"1", "2", "3"})
        self.assertEqual(feeds["2"]["channel_id"], 11)
        self.assertEqual(feeds["3"]["source"], "gaming")


if __name__ == "__main__":
    unittest.main()
