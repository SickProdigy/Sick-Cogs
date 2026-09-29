import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from ..backend.history import EtherscanHistoryError, WalletHistoryMixin
from ..core.networks import BASE_SEPOLIA
from ..providers import WalletProviderError

ADDRESS = "0x7930fB6E9853B3835Cf047f36855993cb82d4387"


class HistoryHarness(WalletHistoryMixin):
    def __init__(self, mode="auto"):
        self.config = SimpleNamespace(history_provider_mode=AsyncMock(return_value=mode))
        self.bot = SimpleNamespace(
            get_shared_api_tokens=AsyncMock(return_value={"api_key": "test-key"})
        )
        self.wallet_provider = SimpleNamespace(get_transaction_history=AsyncMock())
        self.initialize_wallet_history()


class WalletHistoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_etherscan_only_never_falls_back_to_cdp(self):
        harness = HistoryHarness("etherscan")
        harness._etherscan_history = AsyncMock(
            side_effect=EtherscanHistoryError("Etherscan rejected the history request.")
        )
        with self.assertRaises(WalletProviderError):
            await harness.get_wallet_transaction_history(ADDRESS, BASE_SEPOLIA.key)
        harness.wallet_provider.get_transaction_history.assert_not_awaited()

    async def test_auto_falls_back_to_cdp_once(self):
        harness = HistoryHarness("auto")
        harness._etherscan_history = AsyncMock(
            side_effect=EtherscanHistoryError("Etherscan could not be reached.")
        )
        expected = {"transactions": [], "has_more": False, "next_page": ""}
        harness.wallet_provider.get_transaction_history.return_value = expected
        result = await harness.get_wallet_transaction_history(ADDRESS, BASE_SEPOLIA.key)
        self.assertEqual(result, expected)
        harness.wallet_provider.get_transaction_history.assert_awaited_once()

    async def test_identical_concurrent_requests_are_coalesced_and_cached(self):
        harness = HistoryHarness("etherscan")
        expected = {"transactions": [], "has_more": False, "next_page": ""}

        async def load(*args):
            await asyncio.sleep(0.01)
            return expected

        harness._load_bounded_evm_history = AsyncMock(side_effect=load)
        first, second = await asyncio.gather(
            harness.get_wallet_transaction_history(ADDRESS, BASE_SEPOLIA.key),
            harness.get_wallet_transaction_history(ADDRESS, BASE_SEPOLIA.key),
        )
        third = await harness.get_wallet_transaction_history(ADDRESS, BASE_SEPOLIA.key)
        self.assertIs(first, expected)
        self.assertIs(second, expected)
        self.assertIs(third, expected)
        harness._load_bounded_evm_history.assert_awaited_once()

    async def test_normal_and_internal_activity_are_merged_newest_first(self):
        harness = HistoryHarness("etherscan")
        normal_hash = "0x" + "1" * 64
        internal_hash = "0x" + "2" * 64
        harness._etherscan_action = AsyncMock(side_effect=[
            [{"hash": normal_hash, "timeStamp": "100", "from": ADDRESS,
              "to": "0x" + "3" * 40, "value": "1"}],
            [{"hash": internal_hash, "timeStamp": "200", "from": "0x" + "4" * 40,
              "to": ADDRESS, "value": "2"}],
        ])
        result = await harness._etherscan_history(ADDRESS, BASE_SEPOLIA.key, 10)
        self.assertEqual(
            [item["transaction_hash"] for item in result["transactions"]],
            [internal_hash, normal_hash],
        )
        self.assertEqual(result["source"], "etherscan")
        self.assertEqual(
            [call.args[2] for call in harness._etherscan_action.await_args_list],
            ["txlist", "txlistinternal"],
        )


if __name__ == "__main__":
    unittest.main()
