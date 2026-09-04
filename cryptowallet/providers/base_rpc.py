import json

import aiohttp


EVM_RPC_URLS = {
    "base-sepolia": (
        "https://sepolia.base.org",
        "https://sepolia-preconf.base.org",
    ),
    "ethereum-sepolia": (
        "https://ethereum-sepolia-rpc.publicnode.com",
        "https://rpc.sepolia.org",
    ),
}
BASE_SEPOLIA_RPC_URLS = EVM_RPC_URLS["base-sepolia"]
ENTRY_POINT_V06 = "0x5ff137d4b0fdcd49dca30c7cf57e578a026d2789"
USER_OPERATION_EVENT_TOPIC = (
    "0x49628fd1471006c1482da88028e9ce4dbb080b815c9b0344d39e5a8e6ec1419f"
)
LOOKBACK_BLOCKS = 10_000
MAX_RESPONSE_BYTES = 1024 * 1024


class BaseRpcError(RuntimeError):
    pass


async def get_native_balance(address: str, network: str) -> int:
    """Return an EVM native balance through bounded public read-only RPC."""
    rpc_urls = EVM_RPC_URLS.get(network)
    if rpc_urls is None:
        raise BaseRpcError("Native RPC balance lookup is unavailable for this network.")
    result = await _rpc_with_urls(rpc_urls, "eth_getBalance", [address, "latest"], network)
    try:
        return int(str(result), 16)
    except (TypeError, ValueError) as exc:
        raise BaseRpcError("The network returned an invalid native balance.") from exc


async def get_erc20_asset(
    contract: str, address: str, network: str, *, include_metadata: bool = False
) -> dict:
    """Read ERC-20 metadata and one balance without executing a transaction."""
    rpc_urls = EVM_RPC_URLS.get(network)
    if rpc_urls is None:
        raise BaseRpcError("Token lookup is unavailable for this network.")
    code = await _rpc_with_urls(rpc_urls, "eth_getCode", [contract, "latest"], network)
    if not isinstance(code, str) or code in {"0x", "0x0"}:
        raise BaseRpcError("No token contract exists at that address.")
    address_word = address.lower().removeprefix("0x").rjust(64, "0")
    raw_balance = await _rpc_with_urls(
        rpc_urls, "eth_call", [{"to": contract, "data": "0x70a08231" + address_word}, "latest"], network
    )
    try:
        balance = int(str(raw_balance), 16)
    except (TypeError, ValueError) as exc:
        raise BaseRpcError("The token returned an invalid balance.") from exc
    result = {"contract_address": contract.lower(), "amount_atomic": balance}
    if not include_metadata:
        return result
    raw_decimals = await _rpc_with_urls(
        rpc_urls, "eth_call", [{"to": contract, "data": "0x313ce567"}, "latest"], network
    )
    raw_symbol = await _rpc_with_urls(
        rpc_urls, "eth_call", [{"to": contract, "data": "0x95d89b41"}, "latest"], network
    )
    raw_name = await _rpc_with_urls(
        rpc_urls, "eth_call", [{"to": contract, "data": "0x06fdde03"}, "latest"], network
    )
    try:
        decimals = int(str(raw_decimals), 16)
        symbol = _decode_abi_text(str(raw_symbol))
        name = _decode_abi_text(str(raw_name))
    except (TypeError, ValueError) as exc:
        raise BaseRpcError("The contract does not expose valid ERC-20 metadata.") from exc
    if decimals < 0 or decimals > 255 or not symbol or not name:
        raise BaseRpcError("The contract does not expose valid ERC-20 metadata.")
    result.update({"decimals": decimals, "symbol": symbol[:16], "name": name[:64]})
    return result


def _decode_abi_text(value: str) -> str:
    data = bytes.fromhex(value.removeprefix("0x"))
    if len(data) == 32:
        return data.rstrip(b"\x00").decode("utf-8").strip()
    if len(data) < 96:
        raise ValueError("Invalid ABI string")
    offset = int.from_bytes(data[:32], "big")
    if offset + 32 > len(data):
        raise ValueError("Invalid ABI offset")
    length = int.from_bytes(data[offset:offset + 32], "big")
    text = data[offset + 32:offset + 32 + length]
    if len(text) != length:
        raise ValueError("Invalid ABI length")
    return text.decode("utf-8").strip()


async def get_transaction(tx_hash: str) -> dict | None:
    """Return public Base Sepolia transaction and receipt data by hash."""
    transaction = await _rpc("eth_getTransactionByHash", [tx_hash])
    if transaction is None:
        return None
    if not isinstance(transaction, dict):
        raise BaseRpcError("Base Sepolia returned an invalid transaction.")
    receipt = await _rpc("eth_getTransactionReceipt", [tx_hash])
    if receipt is not None and not isinstance(receipt, dict):
        raise BaseRpcError("Base Sepolia returned an invalid transaction receipt.")
    try:
        returned_hash = str(transaction["hash"]).lower()
        value_wei = int(str(transaction["value"]), 16)
        from_address = str(transaction["from"])
        to_address = transaction.get("to")
        block_hex = transaction.get("blockNumber")
        block_number = int(str(block_hex), 16) if block_hex is not None else None
        receipt_status = receipt.get("status") if receipt else None
        success = int(str(receipt_status), 16) == 1 if receipt_status is not None else None
    except (KeyError, TypeError, ValueError) as exc:
        raise BaseRpcError("Base Sepolia returned malformed transaction data.") from exc
    wallet_transfers = []
    if receipt is not None and success is True and value_wei == 0:
        try:
            wallet_transfers = await _get_wallet_transfers(tx_hash, receipt)
        except BaseRpcError:
            wallet_transfers = None
    if returned_hash != tx_hash.lower():
        raise BaseRpcError("Base Sepolia returned a mismatched transaction.")
    return {
        "transaction_hash": returned_hash,
        "from_address": from_address,
        "to_address": str(to_address) if to_address is not None else None,
        "value_wei": value_wei,
        "block_number": block_number,
        "success": success,
        "wallet_transfers": wallet_transfers,
    }


async def _get_wallet_transfers(tx_hash: str, receipt: dict) -> list[dict]:
    """Find successful native transfers initiated by ERC-4337 wallet senders."""
    senders = set()
    logs = receipt.get("logs")
    if not isinstance(logs, list):
        return []
    for log in logs:
        if not isinstance(log, dict):
            continue
        topics = log.get("topics")
        if (
            str(log.get("address") or "").lower() == ENTRY_POINT_V06
            and isinstance(topics, list)
            and len(topics) >= 3
            and str(topics[0]).lower() == USER_OPERATION_EVENT_TOPIC
        ):
            sender_topic = str(topics[2]).lower().removeprefix("0x")
            if len(sender_topic) == 64:
                senders.add("0x" + sender_topic[-40:])
    if not senders:
        return []

    trace = await _rpc("debug_traceTransaction", [tx_hash, {"tracer": "callTracer"}])
    if not isinstance(trace, dict):
        raise BaseRpcError("Base Sepolia returned an invalid transaction trace.")
    transfers = []
    stack = [trace]
    visited = 0
    while stack:
        call = stack.pop()
        visited += 1
        if visited > 10_000:
            raise BaseRpcError("Base Sepolia returned an oversized transaction trace.")
        children = call.get("calls")
        if isinstance(children, list):
            stack.extend(child for child in children if isinstance(child, dict))
        if (
            str(call.get("type") or "").upper() != "CALL"
            or str(call.get("from") or "").lower() not in senders
            or call.get("error")
        ):
            continue
        try:
            value_wei = int(str(call.get("value") or "0x0"), 16)
        except ValueError as exc:
            raise BaseRpcError("Base Sepolia returned an invalid trace value.") from exc
        to_address = str(call.get("to") or "")
        if value_wei > 0 and len(to_address) == 42:
            transfers.append(
                {
                    "from_address": str(call["from"]),
                    "to_address": to_address,
                    "value_wei": value_wei,
                }
            )
    return transfers


async def _rpc(method: str, params: list):
    return await _rpc_with_urls(BASE_SEPOLIA_RPC_URLS, method, params, "Base Sepolia")


async def _rpc_with_urls(rpc_urls: tuple[str, ...], method: str, params: list, network: str):
    timeout = aiohttp.ClientTimeout(total=15)
    last_error = None
    for rpc_url in rpc_urls:
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    rpc_url,
                    headers={"Content-Type": "application/json"},
                    json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
                ) as response:
                    raw = await response.content.read(MAX_RESPONSE_BYTES + 1)
                    if len(raw) > MAX_RESPONSE_BYTES:
                        raise BaseRpcError(f"{network} returned an oversized response.")
                    payload = json.loads(raw.decode("utf-8"))
                    if (
                        response.status != 200
                        or not isinstance(payload, dict)
                        or "error" in payload
                        or "result" not in payload
                    ):
                        raise BaseRpcError(f"{network} RPC rejected the request.")
                    return payload["result"]
        except (
            aiohttp.ClientError,
            TimeoutError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            BaseRpcError,
        ) as exc:
            last_error = exc
    raise BaseRpcError(f"{network} RPC could not complete the request.") from last_error


async def get_user_operation_receipt(address: str, user_operation_hash: str) -> dict | None:
    """Recover a confirmed ERC-4337 operation without resubmitting it."""
    try:
        latest_block = int(str(await _rpc("eth_blockNumber", [])), 16)
    except (TypeError, ValueError) as exc:
        raise BaseRpcError("Base Sepolia returned an invalid block number.") from exc
    address_topic = "0x" + "0" * 24 + address.lower().removeprefix("0x")
    logs = await _rpc(
        "eth_getLogs",
        [
            {
                "address": ENTRY_POINT_V06,
                "fromBlock": hex(max(0, latest_block - LOOKBACK_BLOCKS)),
                "toBlock": "latest",
                "topics": [
                    USER_OPERATION_EVENT_TOPIC,
                    user_operation_hash.lower(),
                    address_topic,
                ],
            }
        ],
    )
    if not isinstance(logs, list) or len(logs) > 1:
        raise BaseRpcError("Base Sepolia returned invalid or ambiguous operation logs.")
    if not logs:
        return None
    try:
        log = logs[0]
        topics = log["topics"]
        data = str(log["data"]).removeprefix("0x")
        tx_hash = str(log["transactionHash"]).lower()
        block_number = int(str(log["blockNumber"]), 16)
        success_word = data[64:128]
        success = int(success_word, 16) == 1
    except (KeyError, TypeError, ValueError) as exc:
        raise BaseRpcError("Base Sepolia returned an invalid operation event.") from exc
    if (
        len(topics) < 3
        or str(topics[0]).lower() != USER_OPERATION_EVENT_TOPIC
        or str(topics[1]).lower() != user_operation_hash.lower()
        or str(topics[2]).lower() != address_topic
        or len(success_word) != 64
        or len(tx_hash) != 66
    ):
        raise BaseRpcError("Base Sepolia returned a mismatched operation event.")
    return {
        "status": "complete" if success else "failed",
        "userOpHash": user_operation_hash.lower(),
        "transactionHash": tx_hash,
        "receipts": [{"blockNumber": block_number}],
    }
