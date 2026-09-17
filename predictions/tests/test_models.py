from datetime import datetime, timedelta, timezone
import unittest

from predictions.models import PredictionMarket


class PredictionMarketTests(unittest.TestCase):
    def test_round_trip_and_vote_counts(self):
        now = datetime(2026, 9, 17, tzinfo=timezone.utc)
        market = PredictionMarket(1, 2, 3, "Will it rain?", ["Yes", "No"], now + timedelta(days=1), now, {"4": 0, "5": 1})
        self.assertEqual(market.vote_counts(), [1, 1])
        self.assertEqual(PredictionMarket.from_raw(market.to_raw()), market)

    def test_rejects_duplicate_outcomes(self):
        now = datetime(2026, 9, 17, tzinfo=timezone.utc)
        with self.assertRaises(ValueError):
            PredictionMarket(1, 2, 3, "Question", ["Yes", "yes"], now + timedelta(days=1), now)
