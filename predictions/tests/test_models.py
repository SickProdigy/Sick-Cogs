from datetime import datetime, timedelta, timezone
import unittest

from predictions.models import PredictionMarket, calculate_payouts


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


class PredictionPayoutTests(unittest.TestCase):
    def test_old_free_market_data_remains_compatible(self):
        now = datetime(2026, 9, 17, tzinfo=timezone.utc)
        old = {
            "market_id": 1, "guild_id": 2, "creator_id": 3,
            "question": "Question", "outcomes": ["Yes", "No"],
            "closes_at": (now + timedelta(days=1)).isoformat(),
            "created_at": now.isoformat(), "votes": {"4": 0},
            "resolved_outcome": None,
        }
        market = PredictionMarket.from_raw(old)
        self.assertFalse(market.uses_bank)
        self.assertEqual(market.entries, {})
        self.assertEqual(market.stake_min, 0)

    def test_proportional_winners_and_zero_default_cut_reconcile(self):
        entries = {
            "10": {"choice": 0, "stake": 100, "state": "funded"},
            "20": {"choice": 0, "stake": 300, "state": "funded"},
            "30": {"choice": 1, "stake": 200, "state": "funded"},
        }
        payouts, cut, refunded = calculate_payouts(entries, 0)
        self.assertEqual(payouts, {"10": 150, "20": 450})
        self.assertEqual(cut, 0)
        self.assertFalse(refunded)
        self.assertEqual(sum(payouts.values()) + cut, 600)

    def test_integer_remainder_is_deterministic(self):
        entries = {
            "10": {"choice": 0, "stake": 1, "state": "funded"},
            "20": {"choice": 0, "stake": 1, "state": "funded"},
            "30": {"choice": 1, "stake": 1, "state": "funded"},
        }
        payouts, cut, _ = calculate_payouts(entries, 0)
        self.assertEqual(payouts, {"10": 2, "20": 1})
        self.assertEqual(sum(payouts.values()) + cut, 3)

    def test_house_cut_only_applies_to_losing_pool(self):
        entries = {
            "10": {"choice": 0, "stake": 100, "state": "funded"},
            "20": {"choice": 1, "stake": 100, "state": "funded"},
        }
        payouts, cut, _ = calculate_payouts(entries, 0, 1000)
        self.assertEqual(payouts, {"10": 190})
        self.assertEqual(cut, 10)

    def test_no_winner_refunds_every_funded_entry(self):
        entries = {
            "10": {"choice": 0, "stake": 75, "state": "funded"},
            "20": {"choice": 1, "stake": 125, "state": "funded"},
        }
        payouts, cut, refunded = calculate_payouts(entries, 2)
        self.assertEqual(payouts, {"10": 75, "20": 125})
        self.assertEqual(cut, 0)
        self.assertTrue(refunded)

    def test_cancelled_market_refunds_and_ignores_unfunded_entries(self):
        entries = {
            "10": {"choice": 0, "stake": 75, "state": "funded"},
            "20": {"choice": 1, "stake": 125, "state": "prepared"},
        }
        payouts, cut, refunded = calculate_payouts(entries, None, 2000)
        self.assertEqual(payouts, {"10": 75})
        self.assertEqual(cut, 0)
        self.assertTrue(refunded)
