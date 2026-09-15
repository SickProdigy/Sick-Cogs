"""Exact ABI construction for the pinned Base Sepolia Clanker deployment."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from ..core.clanker import CLANKER_CHAIN_ID, CLANKER_FACTORY, CLANKER_TOKEN_SUPPLY, ClankerDeploymentIntent


DEPLOY_TOKEN_SELECTOR = "df40224a"
LOCKER = "0x824bB048a5EC6e06a09aEd115E9eEA4618DC2c8f"
VAULT = "0xcC80d1226F899a78fC2E459a1500A13C373CE0A5"
AIRDROP = "0x5c68F1560a5913c176Fc5238038098970B567B19"
MEV_MODULE = "0x261fE99C4D0D41EE8d0e594D11aec740E8354ab0"
STATIC_FEE_HOOK_V2 = "0x11b51DBC2f7F683b81CeDa83DC0078D57bA328cc"
ZERO_ADDRESS = "0x" + "00" * 20
FEE_PREFERENCE = {"Both": 0, "Paired": 1, "Clanker": 2}
FEE_LOCKER = "0x42A95190B4088C88Dd904d930c79deC1158bF09D"
LP_LOCKER = LOCKER
COLLECT_REWARDS_SELECTOR = "5763dbd0"
CLAIM_SELECTOR = "21c0b342"


def _address_word(address: str) -> str:
    raw = str(address).lower()
    if len(raw) != 42 or not raw.startswith("0x"):
        raise ValueError("Invalid reward address.")
    int(raw[2:], 16)
    return raw[2:].rjust(64, "0")


def clanker_collect_rewards_call(token: str) -> dict[str, object]:
    """Build the sole allowlisted per-token collection call."""
    return {
        "to": LP_LOCKER,
        "value": 0,
        "data": "0x" + COLLECT_REWARDS_SELECTOR + _address_word(token),
    }


def clanker_claim_call(owner: str, asset: str) -> dict[str, object]:
    """Build one fee-locker withdrawal whose destination is fixed by the contract."""
    return {"to": FEE_LOCKER, "value": 0,
            "data": "0x" + CLAIM_SELECTOR + _address_word(owner) + _address_word(asset)}


def validate_clanker_claim_call(
    owner: str, asset: str, *, to: str, value: int, data: str
) -> None:
    expected = clanker_claim_call(owner, asset)
    if (str(to).lower() != str(expected["to"]).lower() or int(value) != 0
            or str(data).lower() != str(expected["data"]).lower()):
        raise ValueError("Clanker treasury withdrawal call does not match the review.")


def validate_clanker_collect_rewards_call(
    token: str, *, to: str, value: int, data: str
) -> None:
    expected = clanker_collect_rewards_call(token)
    if (
        str(to).lower() != str(expected["to"]).lower()
        or int(value) != 0
        or str(data).lower() != str(expected["data"]).lower()
    ):
        raise ValueError("Clanker reward collection call does not match the reviewed token.")



@dataclass(frozen=True, slots=True)
class TupleType:
    components: tuple[Any, ...]


@dataclass(frozen=True, slots=True)
class ArrayType:
    item: Any


def _dynamic(abi_type: Any) -> bool:
    if abi_type in {"bytes", "string"} or isinstance(abi_type, ArrayType):
        return True
    return isinstance(abi_type, TupleType) and any(_dynamic(item) for item in abi_type.components)


def _static_size(abi_type: Any) -> int:
    if _dynamic(abi_type):
        raise ValueError("Dynamic ABI values do not have a static size.")
    if isinstance(abi_type, TupleType):
        return sum(_static_size(item) for item in abi_type.components)
    return 32


def _word(value: int) -> bytes:
    return value.to_bytes(32, "big")


def _encode_static(abi_type: Any, value: Any) -> bytes:
    if isinstance(abi_type, TupleType):
        return b"".join(_encode_static(item, part) for item, part in zip(abi_type.components, value))
    if abi_type == "address":
        raw = bytes.fromhex(str(value)[2:])
        if len(raw) != 20:
            raise ValueError("Invalid ABI address.")
        return raw.rjust(32, b"\0")
    if abi_type == "bytes32":
        raw = bytes.fromhex(str(value)[2:])
        if len(raw) != 32:
            raise ValueError("Invalid ABI bytes32 value.")
        return raw
    signed = str(abi_type).startswith("int")
    bits = int(str(abi_type)[3 if signed else 4 :])
    number = int(value)
    lower, upper = (-(1 << (bits - 1)), 1 << (bits - 1)) if signed else (0, 1 << bits)
    if not lower <= number < upper:
        raise ValueError(f"ABI {abi_type} value is out of range.")
    return _word(number % (1 << 256))


def _encode_dynamic(abi_type: Any, value: Any) -> bytes:
    if abi_type in {"bytes", "string"}:
        raw = value.encode("utf-8") if abi_type == "string" else bytes(value)
        return _word(len(raw)) + raw.ljust((len(raw) + 31) // 32 * 32, b"\0")
    if isinstance(abi_type, ArrayType):
        values = tuple(value)
        return _word(len(values)) + _encode_sequence((abi_type.item,) * len(values), values)
    if isinstance(abi_type, TupleType):
        return _encode_sequence(abi_type.components, value)
    raise ValueError(f"Unsupported dynamic ABI type: {abi_type!r}")


def _encode_sequence(types: Sequence[Any], values: Sequence[Any]) -> bytes:
    if len(types) != len(values):
        raise ValueError("ABI type and value counts differ.")
    head_size = sum(32 if _dynamic(item) else _static_size(item) for item in types)
    head: list[bytes] = []
    tail = b""
    for abi_type, value in zip(types, values):
        if _dynamic(abi_type):
            head.append(_word(head_size + len(tail)))
            tail += _encode_dynamic(abi_type, value)
        else:
            head.append(_encode_static(abi_type, value))
    return b"".join(head) + tail


def _abi_encode(types: Sequence[Any], values: Sequence[Any]) -> bytes:
    return _encode_sequence(tuple(types), tuple(values))


TOKEN_CONFIG = TupleType(("address", "string", "string", "bytes32", "string", "string", "string", "uint256"))
POOL_CONFIG = TupleType(("address", "address", "int24", "int24", "bytes"))
LOCKER_CONFIG = TupleType(("address", ArrayType("address"), ArrayType("address"), ArrayType("uint16"), ArrayType("int24"), ArrayType("int24"), ArrayType("uint16"), "bytes"))
MEV_CONFIG = TupleType(("address", "bytes"))
EXTENSION_CONFIG = TupleType(("address", "uint256", "uint16", "bytes"))
DEPLOYMENT_CONFIG = TupleType((TOKEN_CONFIG, POOL_CONFIG, LOCKER_CONFIG, MEV_CONFIG, ArrayType(EXTENSION_CONFIG)))


def clanker_deployment_calldata(intent: ClankerDeploymentIntent) -> str:
    """Return the only reviewed ``deployToken`` calldata shape for an immutable intent."""

    fee_data = _abi_encode(("uint24", "uint24"), (intent.pool.clanker_fee_bps * 100, intent.pool.paired_fee_bps * 100))
    pool_data = _abi_encode((TupleType(("address", "bytes", "bytes")),), ((ZERO_ADDRESS, b"", fee_data),))
    locker_data = _abi_encode((TupleType((ArrayType("uint8"),)),), ((tuple(FEE_PREFERENCE[item.token] for item in intent.rewards),),))
    extensions = []
    if intent.vault:
        extensions.append((VAULT, 0, intent.vault.percentage * 100, _abi_encode(("address", "uint256", "uint256"), (intent.vault.recipient, intent.vault.lockup_seconds, intent.vault.vesting_seconds))))
    if intent.airdrop:
        amount_atomic = intent.airdrop.amount_tokens * 10**18
        supply_atomic = CLANKER_TOKEN_SUPPLY * 10**18
        bps = (amount_atomic * 10_000 + supply_atomic - 1) // supply_atomic
        extensions.append((AIRDROP, 0, bps, _abi_encode(("address", "bytes32", "uint256", "uint256"), (intent.airdrop.admin, intent.airdrop.merkle_root, intent.airdrop.lockup_seconds, intent.airdrop.vesting_seconds))))
    config = (
        (intent.token_admin, intent.name, intent.symbol, intent.salt, intent.image, intent.metadata_json, intent.context_json, CLANKER_CHAIN_ID),
        (STATIC_FEE_HOOK_V2, intent.pool.paired_token, intent.pool.tick_if_token0_is_clanker, intent.pool.tick_spacing, pool_data),
        (LOCKER, tuple(item.admin for item in intent.rewards), tuple(item.recipient for item in intent.rewards), tuple(item.bps for item in intent.rewards), tuple(item.tick_lower for item in intent.pool.positions), tuple(item.tick_upper for item in intent.pool.positions), tuple(item.position_bps for item in intent.pool.positions), locker_data),
        (MEV_MODULE, b""),
        tuple(extensions),
    )
    return "0x" + DEPLOY_TOKEN_SELECTOR + _abi_encode((DEPLOYMENT_CONFIG,), (config,)).hex()


def validate_clanker_deployment_call(intent: ClankerDeploymentIntent, *, to: str, value: int, data: str) -> None:
    """Reject any provider call that differs from the immutable reviewed intent."""

    if to.lower() != CLANKER_FACTORY.lower() or value != intent.expected_native_value_wei:
        raise ValueError("Clanker deployment target or native value does not match the intent.")
    if data.lower() != clanker_deployment_calldata(intent).lower():
        raise ValueError("Clanker deployment calldata does not match the intent.")
