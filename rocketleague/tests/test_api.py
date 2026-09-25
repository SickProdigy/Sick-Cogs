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


    def test_parse_tournament_preserves_registration_and_event_lifecycle(self):
        tournament = StartGGClient._parse_tournament(
            {
                "id": 10,
                "name": "Mixed Rocket League Series",
                "slug": "tournament/mixed",
                "startAt": 100,
                "endAt": 400,
                "isOnline": True,
                "registrationClosesAt": 260,
                "eventRegistrationClosesAt": 250,
                "state": 2,
                "isRegistrationOpen": True,
                "events": [
                    {
                        "id": 11,
                        "name": "Qualifier",
                        "slug": "tournament/mixed/event/qualifier",
                        "startAt": 100,
                        "state": 3,
                        "numEntrants": 8,
                        "entrantSizeMin": 3,
                    },
                    {
                        "id": 12,
                        "name": "Open qualifier",
                        "startAt": 300,
                        "state": 1,
                        "numEntrants": None,
                        "entrantSizeMin": 3,
                    },
                ],
            }
        )

        self.assertEqual(tournament.tournament_state, 2)
        self.assertTrue(tournament.registration_open)
        self.assertEqual(tournament.registration_closes_at, 250)
        self.assertEqual(tournament.events[0].state, 3)
        self.assertEqual(tournament.events[0].slug, "tournament/mixed/event/qualifier")
        self.assertIsNone(tournament.events[1].entrants)

    def test_parse_tournament_keeps_missing_registration_fields_unknown(self):
        tournament = StartGGClient._parse_tournament(
            {"id": 10, "name": "Event", "slug": "tournament/event", "events": []}
        )
        self.assertIsNone(tournament.registration_open)
        self.assertIsNone(tournament.registration_closes_at)


if __name__ == "__main__":
    unittest.main()
