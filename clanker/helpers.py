import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Tuple

from .constants import (
    ETH_ADDRESS_RE,
    MAX_EXTENSION_PERCENTAGE,
)


def utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def is_eth_address(value: str) -> bool:
    return bool(ETH_ADDRESS_RE.fullmatch((value or "").strip()))


def format_tokens(amount: int) -> str:
    return f"{amount:,}"


def parse_token_amount(value: str, supply: int) -> Tuple[int, str]:
    """Parse either a whole token amount or percentage of supply."""
    raw = (value or "").strip().replace(",", "").replace("_", "")
    if not raw:
        raise ValueError("Amount is required.")
    try:
        if raw.endswith("%"):
            percentage = Decimal(raw[:-1].strip())
            if percentage <= 0:
                raise ValueError("Percentage amounts must be positive.")
            amount = int((Decimal(supply) * percentage) / Decimal(100))
            if amount <= 0:
                raise ValueError("Percentage amount rounds down to zero tokens.")
            return amount, f"{percentage.normalize()}%"
        if "." in raw:
            decimal_amount = Decimal(raw)
            if decimal_amount != decimal_amount.to_integral_value():
                raise ValueError("Fixed token amounts must be whole tokens.")
            amount = int(decimal_amount)
        else:
            amount = int(raw)
    except (InvalidOperation, ValueError) as exc:
        if isinstance(exc, ValueError) and str(exc):
            raise
        raise ValueError("Amount must be a whole token count or a percentage like 1.5%.") from exc
    if amount <= 0:
        raise ValueError("Token amounts must be positive.")
    return amount, format_tokens(amount)


def parse_airdrop_lines(lines: str, supply: int) -> Tuple[List[Dict[str, Any]], int]:
    """Parse Discord airdrop recipient lines into normalized preview rows.

    Accepted examples:
    - 0xabc...=1%
    - 0xabc..., 250000000
    - 0xabc... 0.25%
    """
    recipients = []
    for index, raw_line in enumerate((lines or "").splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        if "=" in line:
            address, amount_text = line.split("=", 1)
        elif "," in line:
            address, amount_text = line.split(",", 1)
        else:
            parts = line.split(None, 1)
            if len(parts) != 2:
                raise ValueError(f"Line {index}: use `address amount`, `address=amount`, or `address, amount`.")
            address, amount_text = parts
        address = address.strip()
        if not is_eth_address(address):
            raise ValueError(f"Line {index}: `{address}` is not a valid EVM address.")
        amount, label = parse_token_amount(amount_text, supply)
        recipients.append({"address": address, "amount": amount, "input": label})
    total = sum(row["amount"] for row in recipients)
    return recipients, total


KECCAK_ROUNDS = 24
KECCAK_RATE_BYTES = 136
KECCAK_ROTATION_OFFSETS = (
    (0, 36, 3, 41, 18),
    (1, 44, 10, 45, 2),
    (62, 6, 43, 15, 61),
    (28, 55, 25, 21, 56),
    (27, 20, 39, 8, 14),
)
KECCAK_ROUND_CONSTANTS = (
    0x0000000000000001,
    0x0000000000008082,
    0x800000000000808A,
    0x8000000080008000,
    0x000000000000808B,
    0x0000000080000001,
    0x8000000080008081,
    0x8000000000008009,
    0x000000000000008A,
    0x0000000000000088,
    0x0000000080008009,
    0x000000008000000A,
    0x000000008000808B,
    0x800000000000008B,
    0x8000000000008089,
    0x8000000000008003,
    0x8000000000008002,
    0x8000000000000080,
    0x000000000000800A,
    0x800000008000000A,
    0x8000000080008081,
    0x8000000000008080,
    0x0000000080000001,
    0x8000000080008008,
)
UINT64_MASK = (1 << 64) - 1


def rotate_left64(value: int, shift: int) -> int:
    shift %= 64
    if shift == 0:
        return value & UINT64_MASK
    return ((value << shift) | (value >> (64 - shift))) & UINT64_MASK


def keccak_f1600(state: List[int]) -> None:
    for round_constant in KECCAK_ROUND_CONSTANTS:
        columns = [
            state[x] ^ state[x + 5] ^ state[x + 10] ^ state[x + 15] ^ state[x + 20]
            for x in range(5)
        ]
        deltas = [
            columns[(x - 1) % 5] ^ rotate_left64(columns[(x + 1) % 5], 1)
            for x in range(5)
        ]
        for x in range(5):
            for y in range(5):
                state[x + 5 * y] ^= deltas[x]

        rotated = [0] * 25
        for x in range(5):
            for y in range(5):
                rotated[y + 5 * ((2 * x + 3 * y) % 5)] = rotate_left64(
                    state[x + 5 * y], KECCAK_ROTATION_OFFSETS[x][y]
                )

        for x in range(5):
            for y in range(5):
                state[x + 5 * y] = rotated[x + 5 * y] ^ (
                    (~rotated[((x + 1) % 5) + 5 * y])
                    & rotated[((x + 2) % 5) + 5 * y]
                )
                state[x + 5 * y] &= UINT64_MASK

        state[0] ^= round_constant


def keccak256(data: bytes) -> bytes:
    """Ethereum Keccak-256 without adding a third-party dependency.

    This uses Keccak's original 0x01 padding, not hashlib.sha3_256's FIPS 202 0x06 padding.
    """
    state = [0] * 25
    offset = 0
    while offset + KECCAK_RATE_BYTES <= len(data):
        block = data[offset : offset + KECCAK_RATE_BYTES]
        for lane in range(KECCAK_RATE_BYTES // 8):
            state[lane] ^= int.from_bytes(block[lane * 8 : (lane + 1) * 8], "little")
        keccak_f1600(state)
        offset += KECCAK_RATE_BYTES

    block = bytearray(KECCAK_RATE_BYTES)
    remaining = data[offset:]
    block[: len(remaining)] = remaining
    block[len(remaining)] ^= 0x01
    block[-1] ^= 0x80
    for lane in range(KECCAK_RATE_BYTES // 8):
        state[lane] ^= int.from_bytes(block[lane * 8 : (lane + 1) * 8], "little")
    keccak_f1600(state)

    output = bytearray()
    while len(output) < 32:
        for lane in range(KECCAK_RATE_BYTES // 8):
            output.extend(state[lane].to_bytes(8, "little"))
            if len(output) >= 32:
                break
        if len(output) < 32:
            keccak_f1600(state)
    return bytes(output[:32])


def encode_uint256(value: int) -> bytes:
    if value < 0:
        raise ValueError("uint256 values cannot be negative.")
    return int(value).to_bytes(32, "big")


def encode_address(value: str) -> bytes:
    if not is_eth_address(value):
        raise ValueError(f"{value} is not a valid EVM address.")
    return int(value, 16).to_bytes(32, "big")


def hash_airdrop_leaf(index: int, address: str, amount: int) -> bytes:
    # Canonical local schema: keccak256(abi.encode(uint256 index, address account, uint256 amount)).
    # The generated export records the schema so claim tooling can match it exactly.
    return keccak256(encode_uint256(index) + encode_address(address) + encode_uint256(amount))


def hash_merkle_pair(left: bytes, right: bytes) -> bytes:
    first, second = sorted((left, right))
    return keccak256(first + second)


def build_airdrop_merkle_tree(recipients: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not recipients:
        raise ValueError("At least one airdrop recipient is required to build a Merkle tree.")
    leaves = []
    normalized = []
    for index, recipient in enumerate(recipients):
        address = str(recipient["address"]).strip()
        amount = int(recipient["amount"])
        if amount <= 0:
            raise ValueError("Airdrop amounts must be positive.")
        leaf = hash_airdrop_leaf(index, address, amount)
        leaves.append(leaf)
        normalized.append({
            "index": index,
            "address": address,
            "amount": amount,
            "input": recipient.get("input") or format_tokens(amount),
            "leaf": "0x" + leaf.hex(),
        })

    layers = [leaves]
    current = leaves
    while len(current) > 1:
        next_layer = []
        for position in range(0, len(current), 2):
            left = current[position]
            right = current[position + 1] if position + 1 < len(current) else left
            next_layer.append(hash_merkle_pair(left, right))
        layers.append(next_layer)
        current = next_layer

    proofs = []
    for leaf_index, recipient in enumerate(normalized):
        proof = []
        position = leaf_index
        for layer in layers[:-1]:
            sibling = position ^ 1
            if sibling >= len(layer):
                sibling = position
            proof.append("0x" + layer[sibling].hex())
            position //= 2
        proofs.append({**recipient, "proof": proof})

    root = layers[-1][0]
    return {
        "schema": "keccak256(abi.encode(uint256 index,address account,uint256 amount)); sorted-pair Merkle tree",
        "root": "0x" + root.hex(),
        "total_amount": sum(int(row["amount"]) for row in normalized),
        "recipient_count": len(normalized),
        "recipients": proofs,
    }

def normalize_seconds(value: str, default: int, minimum: int = 0) -> int:
    text = (value or "").strip().replace(",", "").replace("_", "")
    if not text:
        return default
    try:
        seconds = int(text)
    except ValueError as exc:
        raise ValueError("Durations must be seconds as a whole number.") from exc
    if seconds < minimum:
        raise ValueError(f"Duration must be at least {minimum} seconds.")
    return seconds


