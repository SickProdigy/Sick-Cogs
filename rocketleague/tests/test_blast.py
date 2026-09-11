import unittest

from rocketleague.blast import (
    MAX_BLAST_RESPONSE_BYTES,
    BlastClient,
    BlastError,
    BlastTournament,
    parse_blast_tournaments,
    upcoming_tournaments,
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


if __name__ == "__main__":
    unittest.main()
