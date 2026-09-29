import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

from .test_history import HistoryHarness
from ..core.networks import BASE_SEPOLIA


def rss_kib():
    for line in Path("/proc/self/status").read_text().splitlines():
        if line.startswith("VmRSS:"):
            return int(line.split()[1])
    raise RuntimeError("VmRSS is unavailable")


async def run():
    result = {"transactions": [], "has_more": False, "next_page": ""}
    harness = HistoryHarness("etherscan")
    active = 0
    peak_active = 0

    async def load(*args):
        nonlocal active, peak_active
        active += 1
        peak_active = max(peak_active, active)
        await asyncio.sleep(0.005)
        active -= 1
        return result

    harness._load_evm_history = AsyncMock(side_effect=load)
    baseline = rss_kib()
    tasks = [
        asyncio.create_task(
            harness.get_wallet_transaction_history(
                f"0x{index:040x}", BASE_SEPOLIA.key
            )
        )
        for index in range(1, 1001)
    ]
    peak_rss = rss_kib()
    while any(not task.done() for task in tasks):
        peak_rss = max(peak_rss, rss_kib())
        await asyncio.sleep(0.001)
    await asyncio.gather(*tasks)
    settled = rss_kib()

    coalesced = HistoryHarness("etherscan")
    coalesced._load_evm_history = AsyncMock(side_effect=load)
    await asyncio.gather(
        *(
            coalesced.get_wallet_transaction_history(
                "0x0000000000000000000000000000000000000001",
                BASE_SEPOLIA.key,
            )
            for _ in range(1000)
        )
    )

    metrics = {
        "distinct_requests": 1000,
        "distinct_provider_calls": harness._load_evm_history.await_count,
        "identical_requests": 1000,
        "identical_provider_calls": coalesced._load_evm_history.await_count,
        "max_inflight": peak_active,
        "rss_baseline_kib": baseline,
        "rss_peak_kib": peak_rss,
        "rss_settled_kib": settled,
        "rss_peak_delta_kib": peak_rss - baseline,
        "cache_entries": len(harness._history_cache),
    }
    assert metrics["distinct_provider_calls"] == 1000
    assert metrics["identical_provider_calls"] == 1
    assert metrics["max_inflight"] <= 8
    assert metrics["cache_entries"] <= 512
    assert metrics["rss_peak_delta_kib"] < 64 * 1024
    for key, value in metrics.items():
        print(f"{key}={value}")


if __name__ == "__main__":
    asyncio.run(run())
