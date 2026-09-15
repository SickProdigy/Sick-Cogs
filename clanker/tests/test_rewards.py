import unittest
from unittest.mock import AsyncMock

from ..rewards import (
    FEE_LOCKER, LP_LOCKER, WETH, available_fees_call, claim_call,
    CLAIMED_REWARDS_TOPIC, CLAIM_TOKENS_TOPIC, collect_rewards_call,
    decode_claimed_rewards_log, reconcile_collection_receipt,
    reconcile_withdrawal_receipt, reward_preflight,
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


class RewardReceiptTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def event(rewards0=(8, 2), rewards1=(80, 20)):
        words = [100, 10, 128, 224, len(rewards0), *rewards0, len(rewards1), *rewards1]
        return {"address": LP_LOCKER, "topics": [CLAIMED_REWARDS_TOPIC, "0x" + TOKEN[2:].rjust(64, "0")],
                "data": "0x" + "".join(format(item, "064x") for item in words)}

    async def test_decodes_and_reconciles_exact_collection_event(self):
        decoded = decode_claimed_rewards_log(self.event(), TOKEN, 2)
        self.assertEqual(decoded["rewards0_wei"], [8, 2])
        transaction = "0x" + "a" * 64
        rpc = AsyncMock(return_value={"transactionHash": transaction, "status": "0x1",
                                      "blockNumber": "0x10", "logs": [self.event()]})
        result = await reconcile_collection_receipt(transaction, TOKEN, 2, rpc)
        self.assertEqual(result["status"], "confirmed")
        self.assertEqual(result["block_number"], 16)

    async def test_reconciles_every_exact_treasury_claim(self):
        transaction = "0x" + "b" * 64
        claims = [{"owner": CREATOR, "asset": WETH}, {"owner": PLATFORM, "asset": TOKEN}]
        logs = []
        for index, item in enumerate(claims, 1):
            logs.append({"address": FEE_LOCKER, "topics": [CLAIM_TOKENS_TOPIC,
                "0x" + item["owner"][2:].rjust(64, "0"),
                "0x" + item["asset"][2:].rjust(64, "0")],
                "data": "0x" + format(index, "064x")})
        rpc = AsyncMock(return_value={"transactionHash": transaction, "status": "0x1",
                                      "blockNumber": "0x20", "logs": logs})
        result = await reconcile_withdrawal_receipt(transaction, claims, rpc)
        self.assertEqual(result["status"], "confirmed")
        self.assertEqual(sum(item["amount_wei"] for item in result["claims"]), 3)

    async def test_rejects_wrong_token_or_recipient_shape(self):
        with self.assertRaises(ValueError):
            decode_claimed_rewards_log(self.event(), ADMIN, 2)
        with self.assertRaises(ValueError):
            decode_claimed_rewards_log(self.event(), TOKEN, 3)



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
