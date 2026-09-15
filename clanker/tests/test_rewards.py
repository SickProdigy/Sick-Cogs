import unittest
from unittest.mock import AsyncMock

from ..rewards import (
    FEE_LOCKER, LP_LOCKER, WETH, available_fees_call, claim_call,
    collect_rewards_call, reward_preflight,
)


ADMIN = "0x1111111111111111111111111111111111111111"
CREATOR = "0x2222222222222222222222222222222222222222"
PLATFORM = "0x3333333333333333333333333333333333333333"
TOKEN = "0x4444444444444444444444444444444444444444"


class RewardOperationTests(unittest.TestCase):
    def test_operations_are_pinned_and_destination_bound(self):
        available = available_fees_call(CREATOR, WETH)
        claim = claim_call(CREATOR, WETH)
        collect = collect_rewards_call(TOKEN)
        self.assertEqual(available["to"].lower(), FEE_LOCKER.lower())
        self.assertEqual(claim["to"].lower(), FEE_LOCKER.lower())
        self.assertEqual(collect["to"].lower(), LP_LOCKER.lower())
        self.assertEqual(len(available["data"]), 2 + 8 + 64 + 64)
        self.assertEqual(len(claim["data"]), 2 + 8 + 64 + 64)
        self.assertEqual(len(collect["data"]), 2 + 8 + 64)
        self.assertIn(CREATOR[2:], claim["data"])
        self.assertIn(WETH[2:].lower(), claim["data"])
        self.assertIn(TOKEN[2:], collect["data"])


class RewardPreflightTests(unittest.IsolatedAsyncioTestCase):
    async def test_preflight_keeps_launches_separate_and_totals_gas(self):
        async def rpc(method, params):
            if method == "eth_call":
                return "0x0"
            if method == "eth_estimateGas":
                return "0x5208"
            if method == "eth_gasPrice":
                return "0x3b9aca00"
            raise AssertionError(method)

        records = [{
            "launch_id": "one-long", "launch_ref": "one", "symbol": "ONE",
            "token_address": TOKEN, "token_admin": ADMIN,
            "creator_reward_recipient": CREATOR, "platform_treasury": PLATFORM,
        }, {
            "launch_id": "two-long", "launch_ref": "two", "symbol": "TWO",
            "token_address": "0x5555555555555555555555555555555555555555",
            "token_admin": ADMIN, "creator_reward_recipient": CREATOR,
            "platform_treasury": PLATFORM,
        }]
        result = await reward_preflight(records, rpc)
        self.assertEqual([item["reference"] for item in result["launches"]], ["one", "two"])
        self.assertEqual(result["estimated_gas"], 42_000)
        self.assertEqual(result["estimated_fee_wei"], 42_000_000_000_000)
        self.assertTrue(result["complete_estimate"])
        self.assertEqual(result["treasuries"], [])

    async def test_duplicate_token_is_not_counted_twice(self):
        record = {
            "launch_id": "one", "symbol": "ONE", "token_address": TOKEN,
            "token_admin": ADMIN, "creator_reward_recipient": CREATOR,
            "platform_treasury": PLATFORM,
        }
        rpc = AsyncMock(side_effect=lambda method, params: {
            "eth_call": "0x0", "eth_estimateGas": "0x5208", "eth_gasPrice": "0x1",
        }[method])
        result = await reward_preflight([record, dict(record)], rpc)
        self.assertEqual(len(result["launches"]), 1)
