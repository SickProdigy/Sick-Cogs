import asyncio
import time
from collections import deque
from datetime import datetime, timezone


CDP_READ_LIMIT_PER_10_SECONDS = 300
CDP_WRITE_LIMIT_PER_10_SECONDS = 250
WALLET_FREE_OPERATIONS = 5_000
WALLET_SAFETY_TARGET = 4_500
NODE_FREE_BILLING_UNITS = 10_000_000
NODE_SAFETY_TARGET = 7_500_000
USAGE_FLUSH_SECONDS = 30


class ProviderUsageMixin:
    """Central CDP traffic limiting and conservative monthly usage estimates."""

    def initialize_provider_usage(self) -> None:
        self.cdp_rate_lock = asyncio.Lock()
        self.cdp_request_times = {"read": deque(), "write": deque()}
        self.cdp_recent_requests = deque()
        self.cdp_retry_until = 0.0
        self.usage_pending = {
            "cdp_reads": 0,
            "cdp_writes": 0,
            "onchain_data_reads": 0,
            "wallet_operations_estimated": 0,
            "node_billing_units_estimated": 0,
        }
        self.usage_flush_lock = asyncio.Lock()
        self.usage_flush_task = self.bot.loop.create_task(self._usage_flush_loop())

    @staticmethod
    def _usage_period() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m")

    async def limit_cdp_request(self, method: str, path: str) -> None:
        retry_delay = self.cdp_retry_until - time.monotonic()
        if retry_delay > 0:
            await asyncio.sleep(retry_delay)
        kind = "read" if method.upper() == "GET" else "write"
        limit = (
            CDP_READ_LIMIT_PER_10_SECONDS
            if kind == "read"
            else CDP_WRITE_LIMIT_PER_10_SECONDS
        )
        timestamps = self.cdp_request_times[kind]
        while True:
            async with self.cdp_rate_lock:
                now = time.monotonic()
                while timestamps and now - timestamps[0] >= 10:
                    timestamps.popleft()
                if len(timestamps) < limit:
                    timestamps.append(now)
                    return
                delay = max(0.01, 10 - (now - timestamps[0]))
            await asyncio.sleep(delay)

    async def record_cdp_request(
        self, method: str, path: str, status: int, retry_after: float = 0
    ) -> None:
        method = method.upper()
        if status == 429:
            self.cdp_retry_until = max(
                self.cdp_retry_until, time.monotonic() + max(1, retry_after)
            )
        key = "cdp_reads" if method == "GET" else "cdp_writes"
        self.usage_pending[key] += 1
        now = time.monotonic()
        self.cdp_recent_requests.append(now)
        while self.cdp_recent_requests and now - self.cdp_recent_requests[0] >= 60:
            self.cdp_recent_requests.popleft()
        if method == "GET" and (
            path.startswith("/v2/evm/token-balances/")
            or path.startswith("/v1/networks/")
        ):
            self.usage_pending["onchain_data_reads"] += 1
        if 200 <= status < 300:
            if method == "POST" and path == "/v2/end-users":
                self.usage_pending["wallet_operations_estimated"] += 1
            elif method == "POST" and (
                path.endswith("/send") or "/send/" in path
            ):
                self.usage_pending["wallet_operations_estimated"] += 3
            elif method == "DELETE" and path.endswith("/delegation"):
                self.usage_pending["wallet_operations_estimated"] += 1

    async def _usage_flush_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(USAGE_FLUSH_SECONDS)
                await self.flush_provider_usage()
            except asyncio.CancelledError:
                raise
            except Exception:
                await asyncio.sleep(USAGE_FLUSH_SECONDS)

    async def flush_provider_usage(self) -> dict:
        async with self.usage_flush_lock:
            delta = dict(self.usage_pending)
            if any(delta.values()):
                for key in self.usage_pending:
                    self.usage_pending[key] = 0
            period = self._usage_period()
            async with self.config.provider_usage() as usage:
                if usage.get("period") != period:
                    usage.clear()
                    usage.update(self._empty_provider_usage(period))
                for key, amount in delta.items():
                    usage[key] = int(usage.get(key, 0) or 0) + amount
                snapshot = dict(usage)
            await self._warn_provider_owners(snapshot)
            return snapshot

    @staticmethod
    def _empty_provider_usage(period: str) -> dict:
        return {
            "period": period,
            "cdp_reads": 0,
            "cdp_writes": 0,
            "onchain_data_reads": 0,
            "wallet_operations_estimated": 0,
            "node_billing_units_estimated": 0,
            "wallet_warning_level": 0,
            "node_warning_level": 0,
        }

    @staticmethod
    def _warning_level(value: int, target: int) -> int:
        if value >= target:
            return 100
        if value >= target * 9 // 10:
            return 90
        if value >= target * 8 // 10:
            return 80
        return 0

    async def _warn_provider_owners(self, usage: dict) -> None:
        wallet_level = self._warning_level(
            int(usage.get("wallet_operations_estimated", 0) or 0),
            WALLET_SAFETY_TARGET,
        )
        node_level = self._warning_level(
            int(usage.get("node_billing_units_estimated", 0) or 0),
            NODE_SAFETY_TARGET,
        )
        old_wallet = int(usage.get("wallet_warning_level", 0) or 0)
        old_node = int(usage.get("node_warning_level", 0) or 0)
        if wallet_level <= old_wallet and node_level <= old_node:
            return
        usage["wallet_warning_level"] = max(wallet_level, old_wallet)
        usage["node_warning_level"] = max(node_level, old_node)
        await self.config.provider_usage.set(usage)
        owner_ids = set(getattr(self.bot, "owner_ids", None) or [])
        owner_id = getattr(self.bot, "owner_id", None)
        if owner_id:
            owner_ids.add(owner_id)
        message = (
            "CryptoWallet provider usage warning for "
            f"{usage.get('period', self._usage_period())}: "
            f"estimated wallet operations "
            f"{usage.get('wallet_operations_estimated', 0)}/{WALLET_SAFETY_TARGET}; "
            f"estimated CDP Node usage "
            f"{usage.get('node_billing_units_estimated', 0)}/{NODE_SAFETY_TARGET} BU. "
            "No operations were stopped. Use your bot's walletset usage command and "
            "verify authoritative totals in the CDP billing portal."
        )
        for user_id in owner_ids:
            try:
                user = self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)
                await user.send(message)
            except Exception:
                continue

    def recent_cdp_request_count(self) -> int:
        now = time.monotonic()
        while self.cdp_recent_requests and now - self.cdp_recent_requests[0] >= 60:
            self.cdp_recent_requests.popleft()
        return len(self.cdp_recent_requests)
