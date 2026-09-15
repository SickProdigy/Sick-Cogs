"""Read-only Clanker v4 reward discovery and gas preflight."""

import asyncio
from typing import Any, Awaitable, Callable

from .helpers import keccak256

FEE_LOCKER = "0x42A95190B4088C88Dd904d930c79deC1158bF09D"
LP_LOCKER = "0x824bB048a5EC6e06a09aEd115E9eEA4618DC2c8f"
WETH = "0x4200000000000000000000000000000000000006"


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
    treasury_rows = [
        {"owner": owner, "asset": asset, "amount_wei": balance_map[(owner, asset)]}
        for owner, asset in balance_keys if balance_map[(owner, asset)] > 0
    ]
    estimated_gas = sum(value or 0 for value in collection_gas) + sum(
        value or 0 for value in claim_gas
    )
    return {
        "launches": launches,
        "treasuries": treasury_rows,
        "gas_price_wei": gas_price,
        "estimated_gas": estimated_gas,
        "estimated_fee_wei": estimated_gas * gas_price,
        "complete_estimate": all(value is not None for value in collection_gas + claim_gas),
    }
