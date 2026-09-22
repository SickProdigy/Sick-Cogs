"""Read-only Clanker v4 reward discovery and gas preflight."""

import asyncio
from typing import Any, Awaitable, Callable

from .helpers import keccak256

FEE_LOCKER = "0x42A95190B4088C88Dd904d930c79deC1158bF09D"
LP_LOCKER = "0x824bB048a5EC6e06a09aEd115E9eEA4618DC2c8f"
WETH = "0x4200000000000000000000000000000000000006"
CLAIMED_REWARDS_TOPIC = "0x21d15f71483b597e8f0009e83b90b2117f6f98c185d7173857dddcae5eb8546a"
CLAIM_TOKENS_TOPIC = "0xf98eaa9c1f790e5c18b1f227bd5bade62600f9f3e3587c7644b90c50b9bf13c5"


def decode_claimed_rewards_log(
    log: dict[str, Any], token: str, recipient_count: int
) -> dict[str, Any]:
    """Decode one pinned ClaimedRewards event with bounded dynamic arrays."""
    topics = log.get("topics")
    data = str(log.get("data") or "")
    if (str(log.get("address") or "").lower() != LP_LOCKER.lower()
            or not isinstance(topics, list) or len(topics) != 2
            or str(topics[0]).lower() != CLAIMED_REWARDS_TOPIC
            or str(topics[1]).lower() != "0x" + _address_word(token)
            or not data.startswith("0x")):
        raise ValueError("The reward receipt event does not match this token.")
    try:
        raw = bytes.fromhex(data[2:])
        if len(raw) < 128 or len(raw) % 32:
            raise ValueError
        words = [int.from_bytes(raw[i:i + 32], "big") for i in range(0, len(raw), 32)]
        amount0, amount1, offset0, offset1 = words[:4]
        arrays = []
        for offset in (offset0, offset1):
            if offset % 32 or offset < 128 or offset // 32 >= len(words):
                raise ValueError
            start = offset // 32
            length = words[start]
            if length != recipient_count or start + 1 + length > len(words):
                raise ValueError
            arrays.append(words[start + 1:start + 1 + length])
    except (TypeError, ValueError) as exc:
        raise ValueError("The reward receipt contains malformed amounts.") from exc
    token0_is_clanker = int(token, 16) < int(WETH, 16)
    return {"token": token.lower(), "asset0": token.lower() if token0_is_clanker else WETH.lower(),
            "asset1": WETH.lower() if token0_is_clanker else token.lower(),
            "amount0_wei": amount0, "amount1_wei": amount1,
            "rewards0_wei": arrays[0], "rewards1_wei": arrays[1]}


async def verify_external_collection(
    transaction_hash: str, token: str, token_admin: str, recipient_count: int,
    rpc: Callable[[str, list[Any]], Awaitable[Any]],
) -> dict[str, Any]:
    """Verify an external admin signed only the selected-token collection call."""
    transaction = await rpc("eth_getTransactionByHash", [transaction_hash])
    if transaction is None:
        return {"status": "pending"}
    expected = collect_rewards_call(token)
    try:
        if (str(transaction.get("hash") or "").lower() != transaction_hash.lower()
                or str(transaction.get("from") or "").lower() != token_admin.lower()
                or str(transaction.get("to") or "").lower() != str(expected["to"]).lower()
                or int(str(transaction.get("value") or "0x0"), 16) != 0
                or str(transaction.get("input") or transaction.get("data") or "").lower() != str(expected["data"]).lower()):
            raise ValueError("The external reward transaction does not match the reviewed admin collection.")
    except (TypeError, ValueError) as exc:
        raise ValueError("The external reward transaction is malformed.") from exc
    return await reconcile_collection_receipt(transaction_hash, token, recipient_count, rpc)


async def reconcile_collection_receipt(
    transaction_hash: str, token: str, recipient_count: int,
    rpc: Callable[[str, list[Any]], Awaitable[Any]],
) -> dict[str, Any]:
    receipt = await rpc("eth_getTransactionReceipt", [transaction_hash])
    if receipt is None:
        return {"status": "pending"}
    try:
        if str(receipt.get("transactionHash") or "").lower() != transaction_hash.lower():
            raise ValueError("The reward receipt transaction does not match.")
        if int(str(receipt["status"]), 16) != 1:
            return {"status": "failed", "transaction_hash": transaction_hash.lower()}
        matches = [item for item in receipt.get("logs") or []
                   if isinstance(item, dict)
                   and str(item.get("address") or "").lower() == LP_LOCKER.lower()
                   and str((item.get("topics") or [None])[0]).lower() == CLAIMED_REWARDS_TOPIC]
        if len(matches) != 1:
            raise ValueError("The reward receipt lacks one exact ClaimedRewards event.")
        decoded = decode_claimed_rewards_log(matches[0], token, recipient_count)
        return {"status": "confirmed", "transaction_hash": transaction_hash.lower(),
                "block_number": int(str(receipt["blockNumber"]), 16), **decoded}
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("The confirmed reward receipt could not be reconciled.") from exc



def _selector(signature: str) -> str:
    return keccak256(signature.encode("ascii"))[:4].hex()


def _address_word(address: str) -> str:
    value = str(address).lower()
    if len(value) != 42 or not value.startswith("0x"):
        raise ValueError("Reward address is invalid.")
    int(value[2:], 16)
    return value[2:].rjust(64, "0")


def available_fees_call(fee_owner: str, reward_token: str) -> dict[str, str]:
    return {
        "to": FEE_LOCKER,
        "data": "0x" + _selector("availableFees(address,address)")
        + _address_word(fee_owner) + _address_word(reward_token),
    }


def collect_rewards_call(clanker_token: str) -> dict[str, str]:
    return {
        "to": LP_LOCKER,
        "data": "0x" + _selector("collectRewards(address)") + _address_word(clanker_token),
    }


def claim_call(fee_owner: str, reward_token: str) -> dict[str, str]:
    return {
        "to": FEE_LOCKER,
        "data": "0x" + _selector("claim(address,address)")
        + _address_word(fee_owner) + _address_word(reward_token),
    }


async def _uint_call(rpc: Callable[[str, list[Any]], Awaitable[Any]], call: dict[str, str]) -> int:
    value = await rpc("eth_call", [call, "latest"])
    if not isinstance(value, str) or not value.startswith("0x"):
        raise RuntimeError("Base Sepolia returned an invalid reward balance.")
    return int(value, 16)


async def _gas(
    rpc: Callable[[str, list[Any]], Awaitable[Any]], caller: str, call: dict[str, str]
) -> int | None:
    try:
        value = await rpc("eth_estimateGas", [{**call, "from": caller, "value": "0x0"}])
        return int(str(value), 16)
    except (TypeError, ValueError, RuntimeError):
        return None


async def reconcile_withdrawal_receipt(
    transaction_hash: str, claims: list[dict[str, str]],
    rpc: Callable[[str, list[Any]], Awaitable[Any]],
) -> dict[str, Any]:
    """Reconcile every exact ClaimTokens event from an atomic withdrawal batch."""
    receipt = await rpc("eth_getTransactionReceipt", [transaction_hash])
    if receipt is None:
        return {"status": "pending"}
    try:
        if str(receipt.get("transactionHash") or "").lower() != transaction_hash.lower():
            raise ValueError
        if int(str(receipt["status"]), 16) != 1:
            return {"status": "failed", "transaction_hash": transaction_hash.lower()}
        expected = {(str(item["owner"]).lower(), str(item["asset"]).lower()) for item in claims}
        amounts = {}
        for log in receipt.get("logs") or []:
            topics = log.get("topics") if isinstance(log, dict) else None
            if (str(log.get("address") or "").lower() != FEE_LOCKER.lower()
                    or not isinstance(topics, list) or len(topics) != 3
                    or str(topics[0]).lower() != CLAIM_TOKENS_TOPIC):
                continue
            owner = "0x" + str(topics[1])[-40:].lower()
            asset = "0x" + str(topics[2])[-40:].lower()
            data = str(log.get("data") or "")
            if (owner, asset) not in expected or len(data) != 66:
                raise ValueError
            amounts[(owner, asset)] = int(data, 16)
        if set(amounts) != expected:
            raise ValueError
        return {"status": "confirmed", "transaction_hash": transaction_hash.lower(),
                "block_number": int(str(receipt["blockNumber"]), 16),
                "claims": [{"owner": owner, "asset": asset, "amount_wei": amounts[(owner, asset)]}
                           for owner, asset in sorted(expected)]}
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("The confirmed treasury withdrawal receipt could not be reconciled.") from exc



async def reward_preflight(
    records: list[dict[str, Any]],
    rpc: Callable[[str, list[Any]], Awaitable[Any]],
) -> dict[str, Any]:
    """Return public deposited balances and bounded gas estimates for confirmed launches."""
    launches = []
    seen_tokens: set[str] = set()
    for record in records:
        token = str(record.get("token_address") or "").lower()
        if token in seen_tokens or len(token) != 42:
            continue
        seen_tokens.add(token)
        launches.append({
            "reference": str(record.get("launch_ref") or record.get("launch_id") or token),
            "symbol": str(record.get("symbol") or "?").upper(),
            "token": token,
            "admin": str(record.get("token_admin") or "").lower(),
            "creator": str(record.get("creator_reward_recipient") or record.get("token_admin") or "").lower(),
            "platform": str(record.get("platform_treasury") or "").lower(),
        })
    if not launches:
        return {"launches": [], "treasuries": [], "gas_price_wei": 0, "estimated_gas": 0}

    balance_keys: list[tuple[str, str]] = []
    for launch in launches:
        for owner in (launch["creator"], launch["platform"]):
            for asset in (WETH.lower(), launch["token"]):
                key = (owner, asset)
                if key not in balance_keys:
                    balance_keys.append(key)
    balances = await asyncio.gather(*[
        _uint_call(rpc, available_fees_call(owner, asset))
        for owner, asset in balance_keys
    ])
    balance_map = dict(zip(balance_keys, balances))
    caller = launches[0]["admin"]
    collection_gas = await asyncio.gather(*[
        _gas(rpc, caller, collect_rewards_call(item["token"])) for item in launches
    ])
    claim_keys = [key for key in balance_keys if balance_map[key] > 0]
    claim_gas = await asyncio.gather(*[
        _gas(rpc, caller, claim_call(owner, asset)) for owner, asset in claim_keys
    ])
    gas_price_raw = await rpc("eth_gasPrice", [])
    gas_price = int(str(gas_price_raw), 16)
    for launch, estimate in zip(launches, collection_gas):
        launch["collection_gas"] = estimate
        launch["creator_token_wei"] = balance_map[(launch["creator"], launch["token"])]
        launch["platform_token_wei"] = balance_map[(launch["platform"], launch["token"])]
    claim_gas_map = dict(zip(claim_keys, claim_gas))
    treasury_rows = [
        {"owner": owner, "asset": asset, "amount_wei": balance_map[(owner, asset)],
         "claim_gas": claim_gas_map.get((owner, asset))}
        for owner, asset in balance_keys if balance_map[(owner, asset)] > 0
    ]
    collection_estimated_gas = sum(value or 0 for value in collection_gas)
    claim_estimated_gas = sum(value or 0 for value in claim_gas)
    estimated_gas = collection_estimated_gas + claim_estimated_gas
    return {
        "launches": launches,
        "treasuries": treasury_rows,
        "gas_price_wei": gas_price,
        "estimated_gas": estimated_gas,
        "collection_estimated_gas": collection_estimated_gas,
        "claim_estimated_gas": claim_estimated_gas,
        "claim_estimated_fee_wei": claim_estimated_gas * gas_price,
        "estimated_fee_wei": estimated_gas * gas_price,
        "complete_estimate": all(value is not None for value in collection_gas + claim_gas),
    }
