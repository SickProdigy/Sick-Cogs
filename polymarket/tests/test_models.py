import unittest

from polymarket.polymarket import Polymarket, _json_list, market_url


class PolymarketModelTests(unittest.TestCase):
    def test_json_list_accepts_api_encoded_arrays(self):
        self.assertEqual(_json_list('["Yes", "No"]'), ["Yes", "No"])
        self.assertEqual(_json_list(["Yes"]), ["Yes"])
        self.assertEqual(_json_list("bad"), [])

    def test_market_url_uses_canonical_event_slug(self):
        self.assertEqual(market_url({"slug": "example-market"}), "https://polymarket.com/event/example-market")

    def test_cog_accepts_red_bot_instance(self):
        bot = object()
        self.assertIs(Polymarket(bot).bot, bot)
