import asyncio
import unittest
from unittest.mock import AsyncMock

from rocketleague.api import StartGGClient, normalize_league_slug, normalize_tournament_slug


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

    def test_normalize_league_slug_from_url(self):
        self.assertEqual(
            normalize_league_slug("https://www.start.gg/league/rlcs-2026/details?x=1"),
            "league/rlcs-2026",
        )

    def test_league_paginates_filters_and_deduplicates_tournaments(self):
        async def scenario():
            client = StartGGClient(None, "token")
            client._rocket_league_game_id = 10
            tournament = {
                "id": 20,
                "name": "RLCS Open",
                "slug": "tournament/rlcs-open",
                "images": [{"url": "https://images.start.gg/rlcs.png", "type": "profile"}],
                "events": [{"id": 30, "name": "3v3"}],
            }
            client._query = AsyncMock(side_effect=[
                {"league": {"id": 1, "name": "RLCS 2026", "slug": "league/rlcs-2026", "events": {
                    "pageInfo": {"totalPages": 2},
                    "nodes": [
                        {"videogame": {"id": 10}, "tournament": tournament},
                        {"videogame": {"id": 99}, "tournament": {**tournament, "id": 99}},
                    ],
                }}},
                {"league": {"id": 1, "name": "RLCS 2026", "slug": "league/rlcs-2026", "events": {
                    "pageInfo": {"totalPages": 2},
                    "nodes": [{"videogame": {"id": 10}, "tournament": tournament}],
                }}},
            ])
            return await client.league("league/rlcs-2026")

        league = asyncio.run(scenario())
        self.assertEqual(league.name, "RLCS 2026")
        self.assertEqual([item.id for item in league.tournaments], [20])
        self.assertEqual(league.tournaments[0].image_url, "https://images.start.gg/rlcs.png")

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
                "images": [{"url": "https://images.start.gg/mixed.png"}],
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
        self.assertEqual(tournament.image_url, "https://images.start.gg/mixed.png")
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
