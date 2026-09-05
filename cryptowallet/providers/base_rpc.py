import base64
import json
import struct

import aiohttp

from ..core.validation import (
    BASE58_ALPHABET,
    normalize_solana_address,
    normalize_solana_signature,
)


EVM_RPC_URLS = {
    "base-sepolia": (
        "https://sepolia.base.org",
        "https://sepolia-preconf.base.org",
    ),
    "ethereum-sepolia": (
        "https://ethereum-sepolia-rpc.publicnode.com",
        "https://rpc.sepolia.org",
    ),
    "arbitrum-sepolia": ("https://sepolia-rollup.arbitrum.io/rpc",),
    "polygon-amoy": ("https://polygon-amoy.drpc.org",),
    "avalanche-fuji": ("https://api.avax-test.network/ext/bc/C/rpc",),
}
BASE_SEPOLIA_RPC_URLS = EVM_RPC_URLS["base-sepolia"]
SOLANA_DEVNET_RPC_URLS = ("https://api.devnet.solana.com",)
ENTRY_POINT_V06 = "0x5ff137d4b0fdcd49dca30c7cf57e578a026d2789"
USER_OPERATION_EVENT_TOPIC = (
    "0x49628fd1471006c1482da88028e9ce4dbb080b815c9b0344d39e5a8e6ec1419f"
)
LOOKBACK_BLOCKS = 10_000
MAX_RESPONSE_BYTES = 1024 * 1024


class BaseRpcError(RuntimeError):
    pass


def _decode_solana_public_key(value: str) -> bytes:
    value = normalize_solana_address(value)
    number = 0
    for character in value:
        number = number * 58 + BASE58_ALPHABET.index(character)
    decoded = number.to_bytes((number.bit_length() + 7) // 8, "big") if number else b""
    return b"\x00" * (len(value) - len(value.lstrip("1"))) + decoded


def build_solana_transfer_message(
    from_address: str, to_address: str, lamports: int, recent_blockhash: str
) -> bytes:
    """Build one unsigned legacy System Program transfer message."""
    if lamports <= 0 or lamports > 2**64 - 1:
        raise ValueError("The SOL transfer amount is outside the supported range.")
    sender = _decode_solana_public_key(from_address)
    recipient = _decode_solana_public_key(to_address)
    blockhash = _decode_solana_public_key(recent_blockhash)
    system_program = b"\x00" * 32
    header = bytes((1, 0, 1))
    instruction_data = struct.pack("<IQ", 2, lamports)
    if sender == recipient:
        account_keys = bytes((2,)) + sender + system_program
        instruction = bytes((1, 2, 0, 0, len(instruction_data))) + instruction_data
    else:
        account_keys = bytes((3,)) + sender + recipient + system_program
        instruction = bytes((2, 2, 0, 1, len(instruction_data))) + instruction_data
    return header + account_keys + blockhash + instruction


def serialize_unsigned_solana_transfer(message: bytes) -> str:
    """Serialize a one-signer message with an empty signature for CDP signing."""
    if not message or len(message) > 1232 - 65:
        raise ValueError("The Solana transaction message is invalid or too large.")
    return base64.b64encode(bytes((1,)) + b"\x00" * 64 + message).decode("ascii")


async def quote_solana_transfer(
    from_address: str, to_address: str, lamports: int
) -> dict:
    """Build a native devnet transfer and retrieve its current network fee."""
    latest = await _rpc_with_urls(
        SOLANA_DEVNET_RPC_URLS,
        "getLatestBlockhash",
        [{"commitment": "confirmed"}],
        "Solana Devnet",
    )
    try:
        value = latest["value"]
        blockhash = normalize_solana_address(str(value["blockhash"]))
        last_valid_block_height = int(value["lastValidBlockHeight"])
        if last_valid_block_height <= 0:
            raise ValueError("Invalid block height")
    except (KeyError, TypeError, ValueError) as exc:
        raise BaseRpcError("Solana Devnet returned an invalid recent blockhash.") from exc
    message = build_solana_transfer_message(
        from_address, to_address, lamports, blockhash
    )
    fee_result = await _rpc_with_urls(
        SOLANA_DEVNET_RPC_URLS,
        "getFeeForMessage",
        [base64.b64encode(message).decode("ascii"), {"commitment": "confirmed"}],
        "Solana Devnet",
    )
    try:
        fee = int(fee_result["value"])
        if fee < 0:
            raise ValueError("Invalid fee")
    except (KeyError, TypeError, ValueError) as exc:
        raise BaseRpcError("Solana Devnet returned an invalid transaction fee.") from exc
    return {
        "transaction": serialize_unsigned_solana_transfer(message),
        "fee_atomic": fee,
        "last_valid_block_height": last_valid_block_height,
    }


async def get_solana_native_balance(address: str) -> int:
    """Return a Solana devnet balance in lamports."""
    result = await _rpc_with_urls(
        SOLANA_DEVNET_RPC_URLS, "getBalance", [address, {"commitment": "confirmed"}], "Solana Devnet"
    )
    try:
        value = result["value"]
        if isinstance(value, bool) or int(value) < 0:
            raise ValueError("Invalid lamport balance")
        return int(value)
    except (KeyError, TypeError, ValueError) as exc:
        raise BaseRpcError("Solana Devnet returned an invalid balance.") from exc


async def get_solana_transaction(signature: str) -> dict | None:
    """Return bounded public Solana devnet transaction metadata."""
    result = await _rpc_with_urls(
        SOLANA_DEVNET_RPC_URLS,
        "getTransaction",
        [signature, {"encoding": "jsonParsed", "commitment": "confirmed", "maxSupportedTransactionVersion": 0}],
        "Solana Devnet",
    )
    if result is None:
        return None
    try:
        transaction = result["transaction"]
        message = transaction["message"]
        signatures = transaction["signatures"]
        meta = result["meta"]
        raw_keys = message["accountKeys"]
        raw_instructions = message.get("instructions") or []
        pre_balances = meta["preBalances"]
        post_balances = meta["postBalances"]
        if (
            not isinstance(signatures, list)
            or signature not in signatures
            or not isinstance(raw_keys, list)
            or not isinstance(pre_balances, list)
            or not isinstance(post_balances, list)
            or len(raw_keys) != len(pre_balances)
            or len(raw_keys) != len(post_balances)
            or len(raw_keys) > 256
            or not isinstance(raw_instructions, list)
            or len(raw_instructions) > 256
        ):
            raise ValueError("Invalid Solana transaction arrays")
        account_changes = []
        for raw_key, before, after in zip(raw_keys, pre_balances, post_balances):
            address = str(raw_key.get("pubkey") if isinstance(raw_key, dict) else raw_key)
            before = int(before)
            after = int(after)
            if before < 0 or after < 0:
                raise ValueError("Invalid Solana balance")
            if before != after:
                account_changes.append({"address": address, "change_atomic": after - before})
        fee = int(meta["fee"] or 0)
        native_transfers = []
        for instruction in raw_instructions:
            if not isinstance(instruction, dict) or instruction.get("program") != "system":
                continue
            parsed = instruction.get("parsed") or {}
            info = parsed.get("info") or {}
            if parsed.get("type") != "transfer" or not isinstance(info, dict):
                continue
            source = normalize_solana_address(str(info.get("source") or ""))
            destination = normalize_solana_address(str(info.get("destination") or ""))
            lamports = int(info.get("lamports", 0))
            if lamports <= 0:
                raise ValueError("Invalid Solana transfer amount")
            native_transfers.append({
                "from_address": source,
                "to_address": destination,
                "value_atomic": lamports,
            })
        slot = int(result["slot"] or 0)
        block_time = int(result.get("blockTime") or 0)
        if fee < 0 or slot <= 0 or block_time < 0:
            raise ValueError("Invalid Solana transaction metadata")
        return {
            "signature": signature,
            "slot": slot,
            "block_time": block_time,
            "success": meta.get("err") is None,
            "fee_atomic": fee,
            "account_changes": account_changes,
            "native_transfers": native_transfers,
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise BaseRpcError("Solana Devnet returned malformed transaction data.") from exc


async def get_solana_transaction_history(address: str, limit: int = 10) -> dict:
    """Return up to ten recent Solana devnet signatures with wallet balance changes."""
    if limit < 1 or limit > 10:
        raise BaseRpcError("Solana Devnet history is limited to 10 transactions.")
    result = await _rpc_with_urls(
        SOLANA_DEVNET_RPC_URLS,
        "getSignaturesForAddress",
        [address, {"limit": limit, "commitment": "confirmed"}],
        "Solana Devnet",
    )
    if not isinstance(result, list) or len(result) > limit:
        raise BaseRpcError("Solana Devnet returned invalid transaction history.")
    transactions = []
    for item in result:
        try:
            signature = normalize_solana_signature(str(item["signature"]))
            slot = int(item["slot"] or 0)
            block_time = int(item.get("blockTime") or 0)
            if slot <= 0 or block_time < 0:
                raise ValueError("Invalid Solana history metadata")
        except (KeyError, TypeError, ValueError) as exc:
            raise BaseRpcError("Solana Devnet returned malformed transaction history.") from exc
        try:
            detail = await get_solana_transaction(signature)
        except BaseRpcError:
            detail = None
        transactions.append(detail or {
            "signature": signature,
            "slot": slot,
            "block_time": block_time,
            "success": item.get("err") is None,
            "fee_atomic": 0,
            "account_changes": [],
        })
    return {"transactions": transactions, "has_more": len(result) == limit, "next_page": ""}


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


async def get_transaction(tx_hash: str, network: str = "base-sepolia") -> dict | None:
    """Return public EVM transaction and receipt data by hash."""
    rpc_urls = EVM_RPC_URLS.get(network)
    if rpc_urls is None:
        raise BaseRpcError("Transaction lookup is unavailable for this network.")
    transaction = await _rpc_with_urls(
        rpc_urls, "eth_getTransactionByHash", [tx_hash], network
    )
    if transaction is None:
        return None
    if not isinstance(transaction, dict):
        raise BaseRpcError(f"{network} returned an invalid transaction.")
    receipt = await _rpc_with_urls(
        rpc_urls, "eth_getTransactionReceipt", [tx_hash], network
    )
    if receipt is not None and not isinstance(receipt, dict):
        raise BaseRpcError(f"{network} returned an invalid transaction receipt.")
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
        raise BaseRpcError(f"{network} returned malformed transaction data.") from exc
    wallet_transfers = []
    if receipt is not None and success is True and value_wei == 0:
        try:
            wallet_transfers = await _get_wallet_transfers(tx_hash, receipt, network)
        except BaseRpcError:
            wallet_transfers = None
    if returned_hash != tx_hash.lower():
        raise BaseRpcError(f"{network} returned a mismatched transaction.")
    return {
        "transaction_hash": returned_hash,
        "from_address": from_address,
        "to_address": str(to_address) if to_address is not None else None,
        "value_wei": value_wei,
        "block_number": block_number,
        "success": success,
        "wallet_transfers": wallet_transfers,
    }


async def _get_wallet_transfers(tx_hash: str, receipt: dict, network: str) -> list[dict]:
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

    rpc_urls = EVM_RPC_URLS.get(network)
    if rpc_urls is None:
        raise BaseRpcError("Transaction tracing is unavailable for this network.")
    trace = await _rpc_with_urls(
        rpc_urls, "debug_traceTransaction", [tx_hash, {"tracer": "callTracer"}], network
    )
    if not isinstance(trace, dict):
        raise BaseRpcError(f"{network} returned an invalid transaction trace.")
    transfers = []
    stack = [trace]
    visited = 0
    while stack:
        call = stack.pop()
        visited += 1
        if visited > 10_000:
            raise BaseRpcError(f"{network} returned an oversized transaction trace.")
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
            raise BaseRpcError(f"{network} returned an invalid trace value.") from exc
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
