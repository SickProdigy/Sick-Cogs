import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from rocketleague.blast import (
    MAX_BLAST_RESPONSE_BYTES,
    BlastClient,
    BlastError,
    BlastMatch,
    BlastPlayerStat,
    BlastPowerRanking,
    BlastResult,
    BlastTournament,
    parse_blast_result,
    parse_blast_tournaments,
    upcoming_tournaments,
)
from rocketleague.rocketleague import (
    BLAST_HISTORY_MAX_AGE,
    BLAST_HISTORY_LIMIT,
    RocketLeague,
)


CATALOG = """
<html><head>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"ItemList","itemListElement":[
  {"@type":"ListItem","item":{"@type":"SportsEvent","name":"RLCS World Championship 2026","url":"https://blast.tv/rl/tournaments/rlcs-world-championship-2026","location":{"@type":"Place","name":"Fort Worth, Texas"}}},
  {"@type":"ListItem","item":{"@type":"SportsEvent","name":"Unrelated Cup","url":"https://blast.tv/rl/tournaments/unrelated-cup"}}
]}
</script></head><body>
<script>window.data = [\"rlcs-world-championship-2026\",\"RLCS World Championship 2026\",\"2026-09-15T16:00:00.000Z\",\"2026-09-20T23:00:00.000Z\"];</script>
</body></html>
"""


class _ChunkedContent:
    def __init__(self, chunks):
        self.chunks = chunks

    async def iter_chunked(self, size):
        for chunk in self.chunks:
            yield chunk


class _Response:
    status = 200
    content_length = None
    charset = "utf-8"

    def __init__(self, chunks):
        self.content = _ChunkedContent(chunks)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None


class _Session:
    def __init__(self, chunks):
        self.response = _Response(chunks)

    def get(self, *args, **kwargs):
        return self.response


class BlastParserTests(unittest.TestCase):

    @staticmethod
    def _result_root(final_score=(0, 4)):
        return {
            "loaderData": {
                "routes/$gameId.tournaments.$tournamentId_.$view": {
                    "playerStatsPromise": [
                        {"playerName": "Vatira", "gamesPlayed": 25, "rating": 7.386}
                    ],
                    "powerRankingsPromise": {
                        "teams": [{"teamName": "Team Falcons", "rating": 1948.88}]
                    },
                },
                "routes/$gameId.tournaments": {
                    "tournamentTimelineData": {
                        "past": [{
                            "id": "worlds",
                            "name": "RLCS Worlds",
                            "keyMatches": [
                                {"name": "Grand Final", "teamA": {"name": "Spacestation"}, "teamAScore": final_score[0], "teamB": {"name": "Team Falcons"}, "teamBScore": final_score[1]},
                                {"name": "Semi Final 1", "teamA": {"name": "FUT Esports"}, "teamAScore": 2, "teamB": {"name": "Spacestation"}, "teamBScore": 4},
                                {"name": "Semi Final 2", "teamA": {"name": "Karmine Corp"}, "teamAScore": 2, "teamB": {"name": "Team Falcons"}, "teamBScore": 4},
                            ],
                        }]
                    }
                }
            }
        }

    def test_parses_official_final_and_semifinalists(self):
        with patch("rocketleague.blast._hydration_payloads", return_value=[[0]]), patch(
            "rocketleague.blast._unflatten_devalue", return_value=self._result_root()
        ):
            result = parse_blast_result("page", "worlds")
        self.assertEqual(
            result,
            BlastResult(
                "worlds", "RLCS Worlds", "Team Falcons", "Spacestation", 4, 0,
                ("FUT Esports", "Karmine Corp"),
                (
                    BlastMatch("Grand Final", "Spacestation", 0, "Team Falcons", 4),
                    BlastMatch("Semi Final 1", "FUT Esports", 2, "Spacestation", 4),
                    BlastMatch("Semi Final 2", "Karmine Corp", 2, "Team Falcons", 4),
                ),
                (BlastPlayerStat("Vatira", 25, 7.386),),
                (BlastPowerRanking("Team Falcons", 1948.88),),
            ),
        )

    def test_ignores_incomplete_or_tied_final(self):
        with patch("rocketleague.blast._hydration_payloads", return_value=[[0]]), patch(
            "rocketleague.blast._unflatten_devalue", return_value=self._result_root((0, 0))
        ):
            self.assertIsNone(parse_blast_result("page", "worlds"))

    def test_result_round_trip_preserves_fields(self):
        result = BlastResult("worlds", "Worlds", "Falcons", "SSG", 4, 0, ("FUT", "KC"))
        self.assertEqual(BlastResult.from_dict(result.to_dict()), result)

    def test_weekly_result_detail_requests_are_bounded_and_keep_cache(self):
        async def scenario():
            cog = RocketLeague.__new__(RocketLeague)
            existing = BlastResult("saved", "Saved", "One", "Two", 4, 2)
            setting = AsyncMock(return_value={"saved": {**existing.to_dict(), "cached_at": 50}})
            setting.set = AsyncMock()
            cog.config = SimpleNamespace(blast_results=setting)
            client = SimpleNamespace(tournament_result=AsyncMock(return_value=None))
            tournaments = [
                BlastTournament(f"event-{index}", f"Event {index}", index, index + 1, None)
                for index in range(11)
            ]
            await cog._refresh_blast_results(client, tournaments, now=100)
            return client, setting

        client, setting = self.run_async(scenario())
        self.assertEqual(client.tournament_result.await_count, 10)
        saved = setting.set.await_args.args[0]["saved"]
        self.assertEqual(saved["champion"], "One")
        self.assertEqual(saved["cached_at"], 50)

    def test_client_reads_every_response_chunk(self):
        page = CATALOG.encode("utf-8")
        client = BlastClient(_Session([page[:75], page[75:250], page[250:]]))
        tournaments = self.run_async(client.tournaments())
        self.assertEqual(len(tournaments), 1)

    def test_client_rejects_oversized_stream(self):
        client = BlastClient(
            _Session([b"x" * MAX_BLAST_RESPONSE_BYTES, b"x"])
        )
        with self.assertRaises(BlastError):
            self.run_async(client.tournaments())

    @staticmethod
    def run_async(awaitable):
        import asyncio

        return asyncio.run(awaitable)

    def test_parses_dated_rlcs_catalog_entry(self):
        tournaments = parse_blast_tournaments(CATALOG)
        self.assertEqual(len(tournaments), 1)
        tournament = tournaments[0]
        self.assertEqual(tournament.slug, "rlcs-world-championship-2026")
        self.assertEqual(tournament.name, "RLCS World Championship 2026")
        self.assertEqual(tournament.location, "Fort Worth, Texas")
        self.assertEqual(tournament.identity, "blast:rlcs-world-championship-2026")

    def test_ignores_foreign_and_undated_entries(self):
        page = CATALOG.replace("https://blast.tv/rl/tournaments/rlcs-world-championship-2026", "https://example.com/rl/tournaments/rlcs-world-championship-2026")
        self.assertEqual(parse_blast_tournaments(page), [])

    def test_upcoming_filter_is_sorted_and_keeps_active_events(self):
        first = BlastTournament("first", "First", 100, 200, None)
        second = BlastTournament("second", "Second", 300, 400, None)
        self.assertEqual(upcoming_tournaments([second, first], now=150), [first, second])
        self.assertEqual(upcoming_tournaments([second, first], now=250), [second])

    def test_round_trip_preserves_cached_fields(self):
        tournament = BlastTournament("worlds", "Worlds", 100, 200, "Texas")
        self.assertEqual(BlastTournament.from_dict(tournament.to_dict()), tournament)

    def test_history_merge_is_prospective_stable_and_bounded(self):
        now = BLAST_HISTORY_MAX_AGE + 10_000
        previous = BlastTournament("previous", "Previous", now - 200, now - 100, None)
        current = BlastTournament("current", "Current", now + 100, now + 200, None)
        expired = BlastTournament("expired", "Expired", 1, 2, None)
        history = [
            {
                **expired.to_dict(),
                "first_seen": 1,
                "last_seen": 2,
                "final_fingerprint": expired.fingerprint,
            }
        ]

        merged = RocketLeague._merge_blast_history(
            history, [previous], [current], now=now
        )
        self.assertEqual({item["slug"] for item in merged}, {"previous", "current"})
        self.assertTrue(all(item["first_seen"] == now for item in merged))
        self.assertLessEqual(len(merged), BLAST_HISTORY_LIMIT)

        repeated = RocketLeague._merge_blast_history(merged, [], [current], now=now + 60)
        current_record = next(item for item in repeated if item["slug"] == "current")
        self.assertEqual(current_record["first_seen"], now)
        self.assertEqual(current_record["last_seen"], now + 60)

    def test_short_event_id_is_stable_and_slug_scoped(self):
        first = BlastTournament("one", "One", 1, 2, None)
        second = BlastTournament("two", "Two", 1, 2, None)
        self.assertEqual(
            RocketLeague._blast_short_id(first), RocketLeague._blast_short_id(first)
        )
        self.assertNotEqual(
            RocketLeague._blast_short_id(first), RocketLeague._blast_short_id(second)
        )


if __name__ == "__main__":
    unittest.main()
