import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from predictions.models import PredictionMarket
from predictions.views import (
    MarketBrowserView, PredictionEntryView, PredictionHomeView, PredictionManageView,
    PredictionReviewView, PredictionStartView,
)


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
            [
                "predictions:pick:55:7:0", "predictions:pick:55:7:1",
                "predictions:manage:55:7",
            ],
        )
        self.assertEqual([item.label for item in view.children], ["Alpha", "Beta", "Manage"])

    async def test_setup_disables_stake_choices_until_bank_is_enabled(self):
        disabled = PredictionStartView(SimpleNamespace(), 10, False)
        enabled = PredictionStartView(SimpleNamespace(), 10, True)
        self.assertEqual([item.disabled for item in disabled.children], [False, True])
        self.assertEqual([item.disabled for item in enabled.children], [False, False])
        self.assertEqual([item.label for item in enabled.children], ["Free prediction", "Credit pool"])

    async def test_review_card_controls_are_persistent(self):
        view = PredictionReviewView(SimpleNamespace(), 55, self.market(stake_mode="range"))
        self.assertIsNone(view.timeout)
        self.assertEqual(
            [item.label for item in view.children],
            ["Approve: Alpha", "Approve: Beta", "Refund all"],
        )
        self.assertTrue(all(item.custom_id for item in view.children))

    async def test_manager_panel_approves_paid_outcome(self):
        view = PredictionManageView(
            SimpleNamespace(), 10, self.market(stake_mode="fixed"), is_manager=True
        )
        self.assertEqual([item.label for item in view.children[:2]], ["Approve: Alpha", "Approve: Beta"])

    async def test_member_card_hides_outcomes_after_entry(self):
        market = self.market(stake_mode="range")
        market.entries = {"10": {"choice": 0, "stake": 100, "state": "funded"}}
        market.votes = {"10": 0}
        view = PredictionEntryView(SimpleNamespace(), 55, market, viewer_id=10)
        self.assertEqual([item.label for item in view.children], ["Manage"])

    async def test_noncreator_card_has_no_actions_after_entry(self):
        market = self.market(stake_mode="range")
        market.entries = {"20": {"choice": 0, "stake": 100, "state": "funded"}}
        market.votes = {"20": 0}
        view = PredictionEntryView(SimpleNamespace(), 55, market, viewer_id=20)
        self.assertEqual(view.children, [])

    async def test_closed_market_keeps_manage_and_disables_outcomes(self):
        market = self.market()
        market.closes_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        view = PredictionEntryView(SimpleNamespace(), 55, market)
        self.assertEqual([item.disabled for item in view.children], [True, True, False])

    async def test_fixed_and_ranged_markets_share_persistent_entry_controls(self):
        fixed = PredictionEntryView(SimpleNamespace(), 55, self.market(stake_mode="fixed"))
        ranged = PredictionEntryView(SimpleNamespace(), 55, self.market(stake_mode="range"))
        self.assertEqual(len(fixed.children), 3)
        self.assertEqual(len(ranged.children), 3)
        self.assertTrue(all(item.custom_id for item in fixed.children + ranged.children))

    async def test_home_panel_exposes_primary_actions(self):
        view = PredictionHomeView(SimpleNamespace(), 10, True)
        self.assertEqual(
            [item.label for item in view.children],
            ["Start", "Open", "Recent", "Mine", "Leaderboard"],
        )

    async def test_home_panel_shows_review_only_to_managers(self):
        member = PredictionHomeView(SimpleNamespace(), 10, True)
        manager = PredictionHomeView(SimpleNamespace(), 10, True, viewer_can_manage=True)
        self.assertNotIn("Review", [item.label for item in member.children])
        self.assertIn("Review", [item.label for item in manager.children])

    async def test_manage_panel_exposes_resolution_cancel_and_audit(self):
        view = PredictionManageView(SimpleNamespace(), 10, self.market(stake_mode="fixed"))
        self.assertEqual(
            [item.label for item in view.children],
            ["Propose: Alpha", "Propose: Beta", "Cancel and refund", "View audit"],
        )

    async def test_mine_browser_separates_created_and_entered_markets(self):
        created = self.market()
        entered = self.market()
        entered.market_id = 8
        entered.creator_id = 99
        entered.votes = {"10": 1}
        view = MarketBrowserView(SimpleNamespace(), 10, [created, entered], "mine", "Gcreds")
        self.assertEqual(view.markets, [created])
        self.assertEqual([item.label for item in view.children[-2:]], ["Created", "Entered"])
        view.mine_tab = "entered"
        self.assertEqual(view._markets_for_tab(), [entered])

    async def test_browser_selects_markets_and_describes_pool_activity(self):
        market = self.market(stake_mode="range")
        market.entries = {"20": {"choice": 0, "stake": 125, "state": "funded"}}
        market.votes = {"20": 0}
        view = MarketBrowserView(SimpleNamespace(), 10, [market], "open", "Gcreds")
        self.assertEqual(len(view.children), 1)
        self.assertIn("1 people", view.children[0].options[0].description)
        self.assertIn("125 Gcreds", view.embed().description)
