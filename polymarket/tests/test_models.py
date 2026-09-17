import unittest
from unittest.mock import AsyncMock

from polymarket import setup
from polymarket.handoff import FutureHandoffIntent, MarketSnapshot, MarketSnapshotError

from polymarket.polymarket import Polymarket, _active_search_markets, _json_list, future_handoff_reasons, market_path, market_url, technically_handoff_ready


class PolymarketModelTests(unittest.TestCase):
    def test_json_list_accepts_api_encoded_arrays(self):
        self.assertEqual(_json_list('["Yes", "No"]'), ["Yes", "No"])
        self.assertEqual(_json_list(["Yes"]), ["Yes"])
        self.assertEqual(_json_list("bad"), [])

    def test_market_url_uses_canonical_event_slug(self):
        self.assertEqual(market_url({"slug": "example-market"}), "https://polymarket.com/event/example-market")


    def test_market_snapshot_rejects_non_ready_or_malformed_markets(self):
        market = {"id": "1", "conditionId": "condition", "slug": "example", "question": "Example?", "active": True, "closed": False, "enableOrderBook": True, "acceptingOrders": True, "outcomes": '["Yes", "No"]', "clobTokenIds": '["yes-token", "no-token"]', "outcomePrices": '["0.6", "0.4"]', "orderMinSize": 5}
        snapshot = MarketSnapshot.from_market(market)
        self.assertEqual(snapshot.outcome_token_ids, ("yes-token", "no-token"))
        with self.assertRaises(MarketSnapshotError):
            MarketSnapshot.from_market({**market, "acceptingOrders": False})
        intent = FutureHandoffIntent.create(requester_id=7, snapshot=snapshot, outcome_index=0, max_pusd="12.50", created_at=100, expires_at=160)
        self.assertEqual(intent.selected_outcome, "Yes")
        self.assertEqual(len(intent.fingerprint), 64)
        with self.assertRaises(MarketSnapshotError):
            FutureHandoffIntent.create(requester_id=7, snapshot=snapshot, outcome_index=2, max_pusd="12.50", created_at=100, expires_at=160)

    def test_future_handoff_requires_an_active_order_ready_clob_market(self):
        ready = {"active": True, "closed": False, "enableOrderBook": True, "acceptingOrders": True, "clobTokenIds": '["yes"]'}
        self.assertTrue(technically_handoff_ready(ready))
        self.assertEqual(future_handoff_reasons({**ready, "acceptingOrders": False}), ("not accepting orders",))

    def test_market_path_accepts_ids_slugs_and_polymarket_links(self):
        self.assertEqual(market_path("42"), "/markets/42")
        self.assertEqual(market_path("example-market"), "/markets/slug/example-market")
        self.assertEqual(market_path("https://polymarket.com/event/example-market?x=1"), "/markets/slug/example-market")
        self.assertIsNone(market_path("https://example.com/event/example-market"))

    def test_cog_accepts_red_bot_instance(self):
        bot = object()
        self.assertIs(Polymarket(bot).bot, bot)

    def test_search_filters_closed_and_duplicate_markets(self):
        payload = {"events": [{"markets": [
            {"id": "active", "active": True, "closed": False},
            {"id": "closed", "active": True, "closed": True},
            {"id": "active", "active": True, "closed": False},
        ]}]}
        self.assertEqual(_active_search_markets(payload), [{"id": "active", "active": True, "closed": False}])


class PolymarketSetupTests(unittest.IsolatedAsyncioTestCase):
    async def test_setup_adds_a_polymarket_cog(self):
        class Bot:
            def __init__(self):
                self.cogs = []

            async def add_cog(self, cog):
                self.cogs.append(cog)

        bot = Bot()
        await setup(bot)
        self.assertEqual(len(bot.cogs), 1)
        self.assertIsInstance(bot.cogs[0], Polymarket)


class PolymarketCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_markets_requests_active_24_hour_volume_ranking(self):
        class Context:
            def __init__(self):
                self.send = AsyncMock()

        cog = Polymarket(object())
        cog._get_json = AsyncMock(return_value=[{
            "id": "42",
            "question": "Example question?",
            "slug": "example-question",
            "outcomes": '["Yes", "No"]',
            "outcomePrices": '["0.6", "0.4"]',
        }])
        ctx = Context()
        await Polymarket.polymarket_markets.callback(cog, ctx, query="")
        cog._get_json.assert_awaited_once_with(
            "/markets",
            {"active": "true", "closed": "false", "limit": 50, "order": "volume24hr", "ascending": "false"},
        )
        ctx.send.assert_awaited_once()
        self.assertIn("trending", Polymarket.polymarket_markets.aliases)

    async def test_group_shows_a_read_only_discovery_card(self):
        class Context:
            def __init__(self):
                self.send = AsyncMock()

        ctx = Context()
        await Polymarket.polymarket.callback(Polymarket(object()), ctx)
        ctx.send.assert_awaited_once()
        self.assertEqual(ctx.send.await_args.kwargs["embed"].title, "Polymarket discovery")
