import unittest

from rocketleague.api import StartGGClient, normalize_tournament_slug


class StartGGAPITests(unittest.TestCase):
    def test_normalize_tournament_slug_from_url(self):
        self.assertEqual(
            normalize_tournament_slug(
                "https://www.start.gg/tournament/rlcs-2026-na-2v2-open/events?x=1"
            ),
            "tournament/rlcs-2026-na-2v2-open",
        )

    def test_normalize_tournament_slug_from_short_name(self):
        self.assertEqual(
            normalize_tournament_slug("rlcs-2026-na-2v2-open"),
            "tournament/rlcs-2026-na-2v2-open",
        )

    def test_rlcs_filter_is_case_insensitive(self):
        self.assertTrue(
            StartGGClient._is_rlcs(
                {"name": "RLCS 2026 Open", "slug": "tournament/example"}
            )
        )
        self.assertFalse(
            StartGGClient._is_rlcs(
                {"name": "Community Cup", "slug": "tournament/community-cup"}
            )
        )


if __name__ == "__main__":
    unittest.main()
