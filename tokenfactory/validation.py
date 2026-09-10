import re

from .constants import (
    MAX_DECIMALS,
    MAX_NAME_LENGTH,
    MAX_SYMBOL_LENGTH,
    MAX_WHOLE_SUPPLY,
    NAME_RE,
    SYMBOL_RE,
)


AMOUNT_RE = re.compile(r"^(?P<whole>[0-9]+)(?:\.(?P<fraction>[0-9]+))?$")
EVM_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")


def normalize_name(value: str) -> str:
    if any(ord(character) < 32 for character in value):
        raise ValueError("Token names cannot contain control characters.")
    name = " ".join(value.strip().split())
    if not name or len(name) > MAX_NAME_LENGTH or not NAME_RE.fullmatch(name):
        raise ValueError(
            f"Token names must be 1-{MAX_NAME_LENGTH} characters and use ordinary text."
        )
    return name


def normalize_symbol(value: str) -> str:
    symbol = value.strip().upper().lstrip("$")
    if len(symbol) > MAX_SYMBOL_LENGTH or not SYMBOL_RE.fullmatch(symbol):
        raise ValueError("Token symbols must be 2-10 uppercase letters or numbers.")
    return symbol


def normalize_decimals(value: str | int) -> int:
    try:
        decimals = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Token decimals must be a whole number from 0 through 18.") from exc
    if decimals < 0 or decimals > MAX_DECIMALS:
        raise ValueError("Token decimals must be a whole number from 0 through 18.")
    return decimals


def parse_supply(value: str, decimals: int) -> int:
    raw = value.strip().replace(",", "").replace("_", "")
    match = AMOUNT_RE.fullmatch(raw)
    if match is None:
        raise ValueError("Token supply must be a positive decimal number.")
    fraction = match.group("fraction") or ""
    if len(fraction) > decimals:
        raise ValueError(f"Token supply may have at most {decimals} decimal places.")
    whole = int(match.group("whole"))
    if whole > MAX_WHOLE_SUPPLY:
        raise ValueError("Token supply cannot exceed 1,000,000,000,000,000,000 tokens.")
    atomic = whole * (10**decimals)
    if fraction:
        atomic += int(fraction.ljust(decimals, "0"))
    if atomic <= 0 or atomic >= 2**256:
        raise ValueError("Token supply is outside the supported fixed-supply range.")
    return atomic


def normalize_owner_address(value: str) -> str:
    address = value.strip()
    if not EVM_ADDRESS_RE.fullmatch(address) or int(address[2:], 16) == 0:
        raise ValueError("CryptoWallet returned an invalid Base Sepolia owner address.")
    return address
