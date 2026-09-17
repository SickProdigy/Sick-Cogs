import unittest
from unittest.mock import AsyncMock

from polymarket import setup
from polymarket.handoff import FutureHandoffIntent, MarketSnapshot, MarketSnapshotError
from polymarket.polymarket import (
    CATEGORIES, Polymarket, _active_search_markets, _json_list,
    future_handoff_reasons, market_path, market_url, technically_handoff_ready,
)


class PolymarketModelTests(unittest.TestCase):
    def test_json_list_accepts_api_encoded_arrays(self):
        self.assertEqual(_json_list('["Yes", "No"]'), ["Yes", "No"])
        self.assertEqual(_json_list(["Yes"]), ["Yes"])
        self.assertEqual(_json_list("bad"), [])

    def test_market_url_uses_canonical_event_slug(self):
        self.assertEqual(market_url({"slug": "example-market"}), "https://polymarket.com/event/example-market")

    def test_market_snapshot_rejects_non_ready_or_malformed_markets(self):
        market = {
            "id": "1", "conditionId": "condition", "slug": "example", "question": "Example?",
            "active": True, "closed": False, "enableOrderBook": True, "acceptingOrders": True,
            "outcomes": '["Yes", "No"]', "clobTokenIds": '["yes-token", "no-token"]',
            "outcomePrices": '["0.6", "0.4"]', "orderMinSize": 5,
        }
        snapshot = MarketSnapshot.from_market(market, quote_timestamp=100)
        self.assertEqual(snapshot.outcome_token_ids, ("yes-token", "no-token"))
        self.assertEqual(snapshot.chain_id, 137)
        self.assertEqual(snapshot.collateral_symbol, "pUSD")
        self.assertEqual(snapshot.quote_timestamp, 100)
        self.assertEqual(str(snapshot.minimum_order_size), "5")
        with self.assertRaises(MarketSnapshotError):
            MarketSnapshot.from_market({**market, "acceptingOrders": False}, quote_timestamp=100)
        intent = FutureHandoffIntent.create(
            requester_id=7, snapshot=snapshot, outcome_index=0, max_pusd="12.50",
            created_at=100, expires_at=160,
        )
        self.assertEqual(intent.selected_outcome, "Yes")
        self.assertEqual(len(intent.fingerprint), 64)
        with self.assertRaises(MarketSnapshotError):
            FutureHandoffIntent.create(
                requester_id=7, snapshot=snapshot, outcome_index=2, max_pusd="12.50",
                created_at=100, expires_at=160,
            )

    def test_future_handoff_requires_an_active_order_ready_clob_market(self):
        ready = {
            "active": True, "closed": False, "enableOrderBook": True,
            "acceptingOrders": True, "clobTokenIds": '["yes"]',
        }
        self.assertTrue(technically_handoff_ready(ready))
        self.assertEqual(
            future_handoff_reasons({**ready, "acceptingOrders": False}),
            ("not accepting orders",),
        )

    def test_market_path_accepts_ids_slugs_and_polymarket_links(self):
        self.assertEqual(market_path("42"), "/markets/42")
        self.assertEqual(market_path("example-market"), "/markets/slug/example-market")
        self.assertEqual(
            market_path("https://polymarket.com/event/example-market?x=1"),
            "/markets/slug/example-market",
        )
        self.assertIsNone(market_path("https://example.com/event/example-market"))

    def test_categories_use_verified_public_tag_ids(self):
        self.assertEqual(CATEGORIES["politics"], ("Politics", "2"))
        self.assertEqual(CATEGORIES["crypto"], ("Crypto", "21"))
        self.assertEqual(CATEGORIES["sports"], ("Sports", "1"))

    def test_cog_accepts_red_bot_instance(self):
        bot = object()
        self.assertIs(Polymarket(bot).bot, bot)

    def test_search_filters_closed_and_duplicate_markets(self):
        payload = {"events": [{"markets": [
            {"id": "active", "active": True, "closed": False},
            {"id": "closed", "active": True, "closed": True},
            {"id": "active", "active": True, "closed": False},
        ]}]}
        self.assertEqual(
            _active_search_markets(payload),
            [{"id": "active", "active": True, "closed": False}],
        )


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


class Context:
    def __init__(self):
        self.clean_prefix = "!"
        self.send = AsyncMock()
        self.invoke = AsyncMock()


class PolymarketCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_group_shows_complete_guide_and_has_poly_alias(self):
        ctx = Context()
        await Polymarket.polymarket.callback(Polymarket(object()), ctx)
        embed = ctx.send.await_args.kwargs["embed"]
        self.assertEqual(embed.title, "Polymarket discovery")
        self.assertIn("poly", Polymarket.polymarket.aliases)
        fields = "\n".join(field.name + " " + field.value for field in embed.fields)
        for command in ("search", "trending", "market", "compatible", "readiness", "status"):
            self.assertIn(command, fields)

    async def test_markets_without_words_opens_category_chooser(self):
        ctx = Context()
        cog = Polymarket(object())
        await Polymarket.polymarket_markets.callback(cog, ctx, category="")
        ctx.invoke.assert_awaited_once_with(cog.polymarket_categories)

    async def test_markets_category_routes_to_curated_category(self):
        ctx = Context()
        cog = Polymarket(object())
        await Polymarket.polymarket_markets.callback(cog, ctx, category="crypto")
        ctx.invoke.assert_awaited_once_with(cog.polymarket_category, category="crypto")

    async def test_market_search_uses_public_search(self):
        ctx = Context()
        cog = Polymarket(object())
        cog._get_json = AsyncMock(return_value={"events": [{"markets": [{
            "id": "42", "active": True, "closed": False, "question": "Will bitcoin rise?",
            "slug": "bitcoin-rise", "outcomes": '["Yes", "No"]',
            "outcomePrices": '["0.6", "0.4"]',
        }]}]})
        await Polymarket.polymarket_search.callback(cog, ctx, query="bitcoin")
        cog._get_json.assert_awaited_once_with("/public-search", {"q": "bitcoin"})
        self.assertEqual(ctx.send.await_args.kwargs["embed"].title, "Polymarket search: bitcoin")

    async def test_category_requests_ranked_active_tag(self):
        ctx = Context()
        cog = Polymarket(object())
        cog._get_json = AsyncMock(return_value=[{
            "id": "42", "question": "Crypto question?", "slug": "crypto-question",
            "outcomes": '["Yes", "No"]', "outcomePrices": '["0.6", "0.4"]',
        }])
        await Polymarket.polymarket_category.callback(cog, ctx, category="crypto")
        cog._get_json.assert_awaited_once_with("/markets", {
            "active": "true", "closed": "false", "tag_id": "21", "limit": 10,
            "order": "volume24hr", "ascending": "false",
        })
        self.assertEqual(ctx.send.await_args.kwargs["embed"].title, "Polymarket: Crypto")

    async def test_trending_is_explicit_and_ranked(self):
        ctx = Context()
        cog = Polymarket(object())
        cog._get_json = AsyncMock(return_value=[{
            "id": "42", "question": "Example?", "slug": "example",
            "outcomes": '["Yes", "No"]', "outcomePrices": '["0.6", "0.4"]',
        }])
        await Polymarket.polymarket_trending.callback(cog, ctx)
        cog._get_json.assert_awaited_once_with("/markets", {
            "active": "true", "closed": "false", "limit": 10,
            "order": "volume24hr", "ascending": "false",
        })
        self.assertIn("top", Polymarket.polymarket_trending.aliases)

    async def test_category_word_is_not_treated_as_one_market(self):
        ctx = Context()
        cog = Polymarket(object())
        cog._get_json = AsyncMock()
        await Polymarket.polymarket_market.callback(cog, ctx, reference="crypto")
        cog._get_json.assert_not_awaited()
        message = ctx.send.await_args.args[0]
        self.assertIn("poly markets crypto", message)
        self.assertIn("poly search crypto", message)

    async def test_failed_exact_market_suggests_explicit_search(self):
        ctx = Context()
        cog = Polymarket(object())
        cog._get_json = AsyncMock(side_effect=RuntimeError("not found"))
        await Polymarket.polymarket_market.callback(cog, ctx, reference="bitcoin")
        ctx.invoke.assert_not_awaited()
        self.assertIn("poly search bitcoin", ctx.send.await_args.args[0])
