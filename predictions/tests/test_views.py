import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from predictions.models import PredictionMarket
from predictions.views import PredictionEntryView, PredictionSetupView


class PredictionViewTests(unittest.IsolatedAsyncioTestCase):
    def market(self, *, stake_mode="none"):
        now = datetime.now(timezone.utc)
        return PredictionMarket(
            7, 55, 10, "Winner?", ["Alpha", "Beta"], now + timedelta(days=1), now,
            stake_mode=stake_mode,
            stake_min=100 if stake_mode != "none" else 0,
            stake_max=500 if stake_mode == "range" else (100 if stake_mode == "fixed" else 0),
        )

    async def test_entry_view_has_stable_outcome_custom_ids(self):
        view = PredictionEntryView(SimpleNamespace(), 55, self.market())
        self.assertIsNone(view.timeout)
        self.assertEqual(
            [item.custom_id for item in view.children],
            ["predictions:pick:55:7:0", "predictions:pick:55:7:1"],
        )
        self.assertEqual([item.label for item in view.children], ["Alpha", "Beta"])

    async def test_setup_disables_stake_choices_until_bank_is_enabled(self):
        disabled = PredictionSetupView(SimpleNamespace(), 10, False)
        enabled = PredictionSetupView(SimpleNamespace(), 10, True)
        self.assertEqual([item.disabled for item in disabled.children], [False, True, True])
        self.assertEqual([item.disabled for item in enabled.children], [False, False, False])

    async def test_fixed_and_ranged_markets_share_persistent_entry_controls(self):
        fixed = PredictionEntryView(SimpleNamespace(), 55, self.market(stake_mode="fixed"))
        ranged = PredictionEntryView(SimpleNamespace(), 55, self.market(stake_mode="range"))
        self.assertEqual(len(fixed.children), 2)
        self.assertEqual(len(ranged.children), 2)
        self.assertTrue(all(item.custom_id for item in fixed.children + ranged.children))
