import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

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


class _FakeContent:
    def __init__(self, chunks):
        self.chunks = chunks

    async def iter_chunked(self, size):
        for chunk in self.chunks:
            yield chunk


class _FakeResponse:
    def __init__(self, status, chunks):
        self.status = status
        self.content = _FakeContent(chunks)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class _TimeoutSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def get(self, *args, **kwargs):
        raise TimeoutError


class _FakeSession:
    def __init__(self, status, chunks):
        self.response = _FakeResponse(status, chunks)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def get(self, *args, **kwargs):
        return self.response


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


    async def test_installation_history_concurrency_is_bounded_to_eight(self):
        harness = HistoryHarness("etherscan")
        active = 0
        peak = 0

        async def load(*args):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.01)
            active -= 1
            return {"transactions": [], "has_more": False, "next_page": ""}

        harness._load_evm_history = AsyncMock(side_effect=load)
        await asyncio.gather(
            *(
                harness._load_bounded_evm_history(
                    ADDRESS, BASE_SEPOLIA.key, 10, "etherscan"
                )
                for _ in range(40)
            )
        )
        self.assertEqual(peak, 8)

    async def test_cdp_fallback_concurrency_is_bounded_to_four(self):
        harness = HistoryHarness("cdp")
        active = 0
        peak = 0

        async def load(*args, **kwargs):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.01)
            active -= 1
            return {"transactions": [], "has_more": False, "next_page": ""}

        harness.wallet_provider.get_transaction_history.side_effect = load
        await asyncio.gather(
            *(
                harness._load_evm_history(ADDRESS, BASE_SEPOLIA.key, 10, "cdp")
                for _ in range(24)
            )
        )
        self.assertEqual(peak, 4)

    async def test_expired_cache_entry_is_refreshed(self):
        harness = HistoryHarness("etherscan")
        result = {"transactions": [], "has_more": False, "next_page": ""}
        harness._load_bounded_evm_history = AsyncMock(return_value=result)
        await harness.get_wallet_transaction_history(ADDRESS, BASE_SEPOLIA.key)
        key = next(iter(harness._history_cache))
        harness._history_cache[key] = (harness._history_cache[key][0] - 31, result)
        await harness.get_wallet_transaction_history(ADDRESS, BASE_SEPOLIA.key)
        self.assertEqual(harness._load_bounded_evm_history.await_count, 2)

    async def test_cancellation_releases_slot_and_inflight_entry(self):
        harness = HistoryHarness("etherscan")
        started = asyncio.Event()

        async def blocked(*args):
            started.set()
            await asyncio.Event().wait()

        harness._load_evm_history = AsyncMock(side_effect=blocked)
        task = asyncio.create_task(
            harness.get_wallet_transaction_history(ADDRESS, BASE_SEPOLIA.key)
        )
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(harness._history_request_limit._value, 8)
        self.assertEqual(harness._history_inflight, {})

    async def test_oversized_etherscan_response_is_rejected(self):
        harness = HistoryHarness("etherscan")
        session = _FakeSession(200, [b"x" * (512 * 1024 + 1)])
        with patch("cryptowallet.backend.history.aiohttp.ClientSession", return_value=session):
            with self.assertRaisesRegex(EtherscanHistoryError, "oversized"):
                await harness._etherscan_action(ADDRESS, 84532, "txlist", 10, "key")

    async def test_etherscan_timeout_fails_closed(self):
        harness = HistoryHarness("etherscan")
        with patch(
            "cryptowallet.backend.history.aiohttp.ClientSession",
            return_value=_TimeoutSession(),
        ):
            with self.assertRaisesRegex(EtherscanHistoryError, "could not be reached"):
                await harness._etherscan_action(ADDRESS, 84532, "txlist", 10, "key")

    async def test_invalid_and_throttled_etherscan_responses_fail_closed(self):
        harness = HistoryHarness("etherscan")
        invalid = _FakeSession(200, [b"not-json"])
        with patch("cryptowallet.backend.history.aiohttp.ClientSession", return_value=invalid):
            with self.assertRaisesRegex(EtherscanHistoryError, "invalid"):
                await harness._etherscan_action(ADDRESS, 84532, "txlist", 10, "key")
        throttled = _FakeSession(429, [b'{}'])
        with patch("cryptowallet.backend.history.aiohttp.ClientSession", return_value=throttled):
            with self.assertRaisesRegex(EtherscanHistoryError, "HTTP 429"):
                await harness._etherscan_action(ADDRESS, 84532, "txlist", 10, "key")

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
