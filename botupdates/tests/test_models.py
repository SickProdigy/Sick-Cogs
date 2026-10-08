import unittest

from botupdates.models import (
    add_proposal, apply_proposal, new_draft, normalize_delivery_mode,
    normalize_source, should_deliver,
)


class BotUpdatesModelTests(unittest.TestCase):
    def test_delivery_modes_match_draft_kinds(self):
        self.assertTrue(should_deliver("major-only", "major"))
        self.assertFalse(should_deliver("major-only", "digest"))
        self.assertTrue(should_deliver("twice-monthly", "digest"))
        self.assertFalse(should_deliver("twice-monthly", "routine"))
        self.assertTrue(should_deliver("all-approved", "routine"))

    def test_invalid_delivery_mode_fails(self):
        with self.assertRaises(ValueError):
            normalize_delivery_mode("daily")

    def test_source_requires_safe_https_url(self):
        source = normalize_source(
            "upstream", "Example Cog", "https://example.com/releases/1", "v1"
        )
        self.assertEqual(source["key"], "upstream:https://example.com/releases/1@v1")
        for url in (
            "http://example.com/release",
            "https://user:secret@example.com/release",
            "not-a-url",
            "https://example.com/bad>link",
        ):
            with self.assertRaises(ValueError):
                normalize_source("project", "Example", url, "abc")

    def test_draft_and_proposal_history(self):
        draft = new_draft(1, 10, "major", "Title", "Original", 100)
        proposal = add_proposal(draft, 1, 11, "Human revision", 110)
        self.assertEqual(proposal["status"], "pending")
        apply_proposal(draft, 1, 12, 120)
        self.assertEqual(draft["text"], "Human revision")
        self.assertEqual(draft["history"][0]["text"], "Original")
        self.assertEqual(draft["history"][0]["changed_by"], 12)
        self.assertEqual(proposal["status"], "applied")

    def test_approval_is_invalidated_by_applied_edit(self):
        draft = new_draft(1, 10, "digest", "Title", "Original", 100)
        draft["status"] = "approved"
        draft["approved_by"] = 10
        add_proposal(draft, 1, 11, "Revision", 110)
        apply_proposal(draft, 1, 10, 120)
        self.assertEqual(draft["status"], "draft")
        self.assertEqual(draft["approved_by"], 0)


if __name__ == "__main__":
    unittest.main()
