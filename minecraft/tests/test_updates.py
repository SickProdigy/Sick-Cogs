import unittest

from minecraft.updates import (
    MAX_SEEN_UPDATE_IDS,
    body_summary,
    filter_updates,
    parse_feedback_articles,
    sort_updates,
)


class UpdateSourceTests(unittest.TestCase):
    def test_feedback_classifies_stable_editions_and_sanitizes_summary(self):
        payload = {
            "articles": [
                {
                    "id": 1,
                    "title": "Minecraft: Java Edition 1.21.9",
                    "html_url": "https://feedback.minecraft.net/java",
                    "created_at": "2026-01-02T03:04:05Z",
                    "body": "<p>New <strong>features</strong>.</p><script>@everyone</script>",
                },
                {
                    "id": 2,
                    "title": "Minecraft - 1.21.100 (Bedrock)",
                    "html_url": "https://feedback.minecraft.net/bedrock",
                    "created_at": "2026-01-03T03:04:05+00:00",
                    "body": "<p>Bedrock fixes.</p>",
                },
                {"id": 3, "title": "Community News", "html_url": "https://example.com"},
            ]
        }
        items = parse_feedback_articles(payload)
        self.assertEqual([item["edition"] for item in items], ["java", "bedrock"])
        self.assertEqual(items[0]["version"], "1.21.9")
        self.assertEqual(items[0]["summary"], "New features.")

    def test_preview_feed_is_bedrock_preview(self):
        items = parse_feedback_articles(
            {"articles": [{"id": 8, "title": "Minecraft Beta & Preview - 1.2.3", "html_url": "https://feedback.minecraft.net/8", "created_at": "2026-01-03T00:00:00Z", "body": "<p>Preview notes</p>"}]},
            preview=True,
        )
        self.assertEqual((items[0]["edition"], items[0]["channel"]), ("bedrock", "preview"))
        java = parse_feedback_articles(
            {"articles": [{"id": 9, "title": "Minecraft: Java Edition 26.4 Snapshot 4", "html_url": "https://feedback.minecraft.net/9", "created_at": "2026-01-04T00:00:00Z", "body": "<p>Snapshot notes</p>"}]},
            preview=True,
        )
        self.assertEqual((java[0]["edition"], java[0]["channel"]), ("java", "snapshot"))

    def test_filters_default_to_stable_editions(self):
        items = [
            {"id": "j1", "edition": "java", "channel": "release", "published_at": "2026-01-01T00:00:00+00:00"},
            {"id": "j2", "edition": "java", "channel": "snapshot", "published_at": "2026-01-02T00:00:00+00:00"},
            {"id": "b1", "edition": "bedrock", "channel": "release", "published_at": "2026-01-03T00:00:00+00:00"},
            {"id": "b2", "edition": "bedrock", "channel": "preview", "published_at": "2026-01-04T00:00:00+00:00"},
        ]
        self.assertEqual([item["id"] for item in filter_updates(items, {})], ["j1", "b1"])
        enabled = {"updates_java_prerelease": True, "updates_bedrock_preview": True}
        self.assertEqual(len(filter_updates(items, enabled)), 4)

    def test_helpers_reject_malformed_sources_and_sort_oldest_first(self):
        with self.assertRaises(ValueError):
            parse_feedback_articles({"articles": "wrong"})
        items = [{"id": "new", "published_at": "2026-02-01"}, {"id": "old", "published_at": "2026-01-01"}]
        self.assertEqual([item["id"] for item in sort_updates(items)], ["old", "new"])
        self.assertNotIn("hidden", body_summary("<style>hidden</style><p>Visible</p>"))
        self.assertEqual(MAX_SEEN_UPDATE_IDS, 500)


if __name__ == "__main__":
    unittest.main()
