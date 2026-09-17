import unittest

from rsspublisher.models import (
    feed_filter_failures,
    feed_filter_summary,
    migrate_feed_data,
    normalize_author,
)


class RSSFeedFilterModelTests(unittest.TestCase):
    def test_title_prefix_is_case_insensitive(self):
        feed = {"title_prefix": "News - "}
        self.assertEqual(feed_filter_failures({"title": "news - Update"}, feed), ())
        self.assertIn("title", feed_filter_failures({"title": "Steam - Update"}, feed)[0])

    def test_author_allowlist_uses_exact_normalized_plaintext(self):
        feed = {"allowed_authors": ["xSicKxBot"]}
        entry = {"author": "<a href=\"/user/1\">XsIcKxBoT</a>"}
        self.assertEqual(normalize_author(entry["author"]), "XsIcKxBoT")
        self.assertEqual(feed_filter_failures(entry, feed), ())
        self.assertTrue(feed_filter_failures({"author": "xSicKxBotSpam"}, feed))

    def test_filter_families_use_and_behavior(self):
        feed = {
            "title_prefix": "News - ",
            "allowed_authors": ["xSicKxBot"],
            "allowed_tags": ["news"],
        }
        matching = {
            "title": "NEWS - Example",
            "authors": [{"name": "<b>xsickxbot</b>"}],
            "tags_list": ["News"],
        }
        self.assertEqual(feed_filter_failures(matching, feed), ())
        self.assertEqual(len(feed_filter_failures({"title": "Other"}, feed)), 3)

    def test_missing_author_fails_only_when_author_filter_is_configured(self):
        self.assertEqual(feed_filter_failures({"title": "Anything"}, {}), ())
        self.assertTrue(feed_filter_failures({}, {"allowed_authors": ["Author"]}))

    def test_legacy_feeds_receive_empty_filter_defaults(self):
        migrated, changed = migrate_feed_data({"url": "https://example.test/feed"})
        self.assertTrue(changed)
        self.assertIsNone(migrated["title_prefix"])
        self.assertEqual(migrated["allowed_authors"], [])

    def test_filter_summary_does_not_render_author_markup(self):
        summary = feed_filter_summary({
            "title_prefix": "News - ",
            "allowed_authors": ["xSicKxBot"],
        })
        self.assertIn("News -", summary)
        self.assertIn("xSicKxBot", summary)
