import asyncio
import json
import logging
import time

import aiohttp

from ..core.networks import ChainFamily, KNOWN_NETWORKS
from ..core.validation import normalize_evm_address
from ..providers import WalletProviderError

log = logging.getLogger("red.Sick-Cogs.CryptoWallet")
ETHERSCAN_TOKEN_NAMESPACE = "etherscan"
ETHERSCAN_V2_URL = "https://api.etherscan.io/v2/api"
HISTORY_MODES = frozenset({"auto", "etherscan", "cdp", "explorer-only"})
HISTORY_CACHE_SECONDS = 30
MAX_HISTORY_RESPONSE_BYTES = 512 * 1024


class EtherscanHistoryError(RuntimeError):
    pass


class WalletHistoryMixin:
    """Provider-neutral, cached public-address history routing."""

    def initialize_wallet_history(self) -> None:
        self._history_cache = {}
        self._history_inflight = {}
        self._history_lock = asyncio.Lock()
        self._history_request_limit = asyncio.Semaphore(8)
        self._history_cdp_fallback_limit = asyncio.Semaphore(4)

    async def etherscan_history_status(self) -> dict:
        tokens = await self.bot.get_shared_api_tokens(ETHERSCAN_TOKEN_NAMESPACE)
        return {"configured": bool(str(tokens.get("api_key") or "").strip())}

    async def get_wallet_transaction_history(
        self, address: str, network_key: str, *, limit: int = 10
    ) -> dict:
        network = KNOWN_NETWORKS.get(network_key)
        if network is None:
            raise WalletProviderError("Transaction history is unavailable for this network.")
        if network.family is not ChainFamily.EVM:
            return await self.wallet_provider.get_transaction_history(
                address, network_key, limit=limit
            )
        address = normalize_evm_address(address)
        mode = str(await self.config.history_provider_mode() or "auto").lower()
        if mode not in HISTORY_MODES:
            mode = "auto"
        if mode == "explorer-only":
            raise WalletProviderError(
                "In-Discord history is disabled; use the complete-history explorer link."
            )
        key = (mode, network_key, address.lower(), limit)
        cached = self._history_cache.get(key)
        if cached and time.monotonic() - cached[0] < HISTORY_CACHE_SECONDS:
            return cached[1]
        async with self._history_lock:
            task = self._history_inflight.get(key)
            if task is None:
                task = asyncio.create_task(
                    self._load_bounded_evm_history(address, network_key, limit, mode)
                )
                self._history_inflight[key] = task
        try:
            result = await asyncio.shield(task)
            if len(self._history_cache) >= 512:
                oldest = min(self._history_cache, key=lambda item: self._history_cache[item][0])
                self._history_cache.pop(oldest, None)
            self._history_cache[key] = (time.monotonic(), result)
            return result
        finally:
            async with self._history_lock:
                if self._history_inflight.get(key) is task and task.done():
                    self._history_inflight.pop(key, None)

    async def _load_bounded_evm_history(
        self, address: str, network_key: str, limit: int, mode: str
    ) -> dict:
        async with self._history_request_limit:
            return await self._load_evm_history(address, network_key, limit, mode)

    async def _load_evm_history(
        self, address: str, network_key: str, limit: int, mode: str
    ) -> dict:
        if mode in {"auto", "etherscan"}:
            try:
                return await self._etherscan_history(address, network_key, limit)
            except EtherscanHistoryError as exc:
                log.warning("Etherscan history failed network=%s reason=%s", network_key, exc)
                if mode == "etherscan":
                    raise WalletProviderError(str(exc)) from exc
        if mode in {"auto", "cdp"}:
            async with self._history_cdp_fallback_limit:
                return await self.wallet_provider.get_transaction_history(
                    address, network_key, limit=limit
                )
        raise WalletProviderError("No transaction-history provider is enabled.")

    async def _etherscan_history(
        self, address: str, network_key: str, limit: int
    ) -> dict:
        network = KNOWN_NETWORKS[network_key]
        tokens = await self.bot.get_shared_api_tokens(ETHERSCAN_TOKEN_NAMESPACE)
        api_key = str(tokens.get("api_key") or "").strip()
        if not api_key:
            raise EtherscanHistoryError("Etherscan history is not configured.")
        if not network.chain_id or limit < 1 or limit > 100:
            raise EtherscanHistoryError("The Etherscan history request is invalid.")
        normal = await self._etherscan_action(address, network.chain_id, "txlist", limit, api_key)
        internal = await self._etherscan_action(
            address, network.chain_id, "txlistinternal", limit, api_key
        )
        normalized = []
        for item in normal + internal:
            if not isinstance(item, dict):
                continue
            tx_hash = str(item.get("hash") or "").lower()
            if len(tx_hash) != 66 or not tx_hash.startswith("0x"):
                continue
            try:
                int(tx_hash[2:], 16)
                timestamp = int(item.get("timeStamp") or 0)
                value = int(item.get("value") or 0)
            except (TypeError, ValueError):
                continue
            normalized.append((timestamp, {
                "transaction_hash": tx_hash,
                "content": {
                    "hash": tx_hash, "from": str(item.get("from") or ""),
                    "to": str(item.get("to") or ""), "value": str(value),
                    "block_timestamp": (
                        time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(timestamp))
                        if timestamp > 0 else ""
                    ),
                },
            }))
        normalized.sort(key=lambda pair: pair[0], reverse=True)
        transactions = []
        seen = set()
        for _, transaction in normalized:
            identity = (
                transaction["transaction_hash"],
                transaction["content"]["from"].lower(),
                transaction["content"]["to"].lower(),
                transaction["content"]["value"],
            )
            if identity in seen:
                continue
            seen.add(identity)
            transactions.append(transaction)
            if len(transactions) >= limit:
                break
        return {
            "transactions": transactions, "has_more": False,
            "next_page": "", "source": "etherscan",
        }

    async def _etherscan_action(
        self, address: str, chain_id: int, action: str, limit: int, api_key: str
    ) -> list[dict]:
        params = {
            "chainid": str(chain_id), "module": "account", "action": action,
            "address": address, "startblock": "0", "endblock": "9999999999",
            "page": "1", "offset": str(limit), "sort": "desc", "apikey": api_key,
        }
        timeout = aiohttp.ClientTimeout(total=15)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(
                    ETHERSCAN_V2_URL, params=params,
                    headers={
                        "Accept": "application/json",
                        "User-Agent": "Sick-Cogs-CryptoWallet/1.2",
                    },
                ) as response:
                    raw = bytearray()
                    async for chunk in response.content.iter_chunked(64 * 1024):
                        raw.extend(chunk)
                        if len(raw) > MAX_HISTORY_RESPONSE_BYTES:
                            raise EtherscanHistoryError(
                                "Etherscan returned an oversized response."
                            )
                    if response.status != 200:
                        raise EtherscanHistoryError(
                            f"Etherscan returned HTTP {response.status}."
                        )
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise EtherscanHistoryError("Etherscan could not be reached.") from exc
        try:
            payload = json.loads(raw.decode("utf-8"))
            records = payload.get("result")
        except (UnicodeDecodeError, json.JSONDecodeError, AttributeError) as exc:
            raise EtherscanHistoryError("Etherscan returned an invalid response.") from exc
        if payload.get("status") == "0":
            message = str(records or payload.get("message") or "request failed")
            if "No transactions found" in message:
                return []
            raise EtherscanHistoryError("Etherscan rejected the history request.")
        if not isinstance(records, list):
            raise EtherscanHistoryError("Etherscan returned an invalid transaction list.")
        return records
