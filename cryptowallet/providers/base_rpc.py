import json

import aiohttp


BASE_SEPOLIA_RPC_URL = "https://sepolia.base.org"
ENTRY_POINT_V06 = "0x5ff137d4b0fdcd49dca30c7cf57e578a026d2789"
USER_OPERATION_EVENT_TOPIC = (
    "0x49628fd1471006c1482da88028e9ce4dbb080b815c9b0344d39e5a8e6ec1419f"
)
LOOKBACK_BLOCKS = 10_000
MAX_RESPONSE_BYTES = 1024 * 1024


class BaseRpcError(RuntimeError):
    pass


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
    if returned_hash != tx_hash.lower():
        raise BaseRpcError("Base Sepolia returned a mismatched transaction.")
    return {
        "transaction_hash": returned_hash,
        "from_address": from_address,
        "to_address": str(to_address) if to_address is not None else None,
        "value_wei": value_wei,
        "block_number": block_number,
        "success": success,
    }


async def _rpc(method: str, params: list):
    timeout = aiohttp.ClientTimeout(total=15)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                BASE_SEPOLIA_RPC_URL,
                headers={"Content-Type": "application/json"},
                json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
            ) as response:
                raw = await response.content.read(MAX_RESPONSE_BYTES + 1)
                if len(raw) > MAX_RESPONSE_BYTES:
                    raise BaseRpcError("Base Sepolia returned an oversized response.")
                payload = json.loads(raw.decode("utf-8"))
                if (
                    response.status != 200
                    or not isinstance(payload, dict)
                    or "error" in payload
                    or "result" not in payload
                ):
                    raise BaseRpcError("Base Sepolia RPC rejected the request.")
                return payload["result"]
    except (aiohttp.ClientError, TimeoutError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BaseRpcError("Base Sepolia RPC could not complete the request.") from exc


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
