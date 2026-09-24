import base64
import hashlib
import hmac
import re
import secrets
import struct

from .networks import ChainFamily, Network


EVM_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
SOLANA_ADDRESS_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}")
SOLANA_SIGNATURE_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{64,88}")
BASE58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
ETH_AMOUNT_RE = re.compile(r"^(?P<whole>[0-9]+)(?:\.(?P<fraction>[0-9]+))?$")
WEI_PER_ETH = 10**18
MAX_UINT256 = 2**256 - 1
TOTP_SECRET_RE = re.compile(r"^[A-Z2-7]{32,}$")
TOTP_PERIOD_SECONDS = 30
TOTP_DIGITS = 6


class InvalidAmount(ValueError):
    """Raised when a native-token amount cannot be represented safely."""


def generate_totp_secret() -> str:
    """Generate a 160-bit Base32 secret compatible with standard authenticator apps."""

    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def _decode_totp_secret(secret: str) -> bytes:
    """Decode a canonical Base32 TOTP secret without accepting weak secrets."""

    normalized = secret.strip().upper()
    if not TOTP_SECRET_RE.fullmatch(normalized):
        raise ValueError("TOTP secrets must be canonical Base32 with at least 160 bits.")
    padding = "=" * (-len(normalized) % 8)
    try:
        decoded = base64.b32decode(normalized + padding, casefold=False)
    except (ValueError, base64.binascii.Error) as exc:
        raise ValueError("The TOTP secret is not valid Base32.") from exc
    if len(decoded) < 20:
        raise ValueError("TOTP secrets must contain at least 160 bits of entropy.")
    return decoded


def totp_code(secret: str, timestamp: int | float) -> str:
    """Return the six-digit RFC 6238 SHA-1 code for a Unix timestamp."""

    if isinstance(timestamp, bool) or timestamp < 0:
        raise ValueError("The TOTP timestamp must be non-negative.")
    counter = int(timestamp) // TOTP_PERIOD_SECONDS
    digest = hmac.new(
        _decode_totp_secret(secret), struct.pack(">Q", counter), hashlib.sha1
    ).digest()
    offset = digest[-1] & 0x0F
    truncated = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return f"{truncated % (10**TOTP_DIGITS):0{TOTP_DIGITS}d}"


def verify_totp_code(
    secret: str,
    code: str,
    timestamp: int | float,
    *,
    last_counter: int | None = None,
    window: int = 1,
) -> int | None:
    """Verify a TOTP code and return its counter, rejecting reused counters."""

    if window not in (0, 1):
        raise ValueError("The TOTP verification window must be zero or one step.")
    if not isinstance(code, str) or not re.fullmatch(r"[0-9]{6}", code):
        return None
    if isinstance(timestamp, bool) or timestamp < 0:
        return None
    current_counter = int(timestamp) // TOTP_PERIOD_SECONDS
    for offset in (0, -1, 1) if window else (0,):
        counter = current_counter + offset
        if counter < 0 or (last_counter is not None and counter <= last_counter):
            continue
        try:
            candidate = totp_code(secret, counter * TOTP_PERIOD_SECONDS)
        except (AttributeError, TypeError, ValueError):
            return None
        if hmac.compare_digest(candidate, code):
            return counter
    return None


def normalize_evm_address(value: str) -> str:
    """Validate an EVM address without silently changing its checksum casing."""

    address = value.strip()
    if not EVM_ADDRESS_RE.fullmatch(address):
        raise ValueError("EVM addresses must contain 0x followed by 40 hexadecimal characters.")
    if int(address[2:], 16) == 0:
        raise ValueError("The zero address cannot receive a wallet transfer.")
    return address


def normalize_solana_address(value: str) -> str:
    """Validate canonical base58 text that decodes to one 32-byte Solana public key."""
    address = value.strip()
    if not SOLANA_ADDRESS_RE.fullmatch(address):
        raise ValueError("Solana addresses must be 32 to 44 base58 characters.")
    number = 0
    for character in address:
        number = number * 58 + BASE58_ALPHABET.index(character)
    decoded = number.to_bytes((number.bit_length() + 7) // 8, "big") if number else b""
    leading_zeroes = len(address) - len(address.lstrip("1"))
    if len(b"\x00" * leading_zeroes + decoded) != 32:
        raise ValueError("Solana addresses must decode to a 32-byte public key.")
    return address

def normalize_solana_signature(value: str) -> str:
    """Validate base58 text that decodes to one 64-byte Ed25519 signature."""
    signature = value.strip()
    if not SOLANA_SIGNATURE_RE.fullmatch(signature):
        raise ValueError("Solana transaction signatures must be 64 to 88 base58 characters.")
    number = 0
    for character in signature:
        number = number * 58 + BASE58_ALPHABET.index(character)
    decoded = number.to_bytes((number.bit_length() + 7) // 8, "big") if number else b""
    leading_zeroes = len(signature) - len(signature.lstrip("1"))
    if len(b"\x00" * leading_zeroes + decoded) != 64:
        raise ValueError("Solana transaction signatures must decode to 64 bytes.")
    return signature


def normalize_address_for_network(value: str, network: Network) -> str:
    """Validate an address using only the explicitly selected chain family."""

    if not network.enabled:
        raise ValueError("That network is not enabled.")
    if network.family is ChainFamily.EVM:
        return normalize_evm_address(value)
    if network.family is ChainFamily.SOLANA:
        return normalize_solana_address(value)
    raise ValueError("That network uses an unsupported address format.")


def parse_native_amount(value: str, network: Network) -> int:
    """Convert a positive native-token amount into the network atomic unit."""

    match = ETH_AMOUNT_RE.fullmatch(value.strip())
    if match is None:
        raise InvalidAmount(
            f"Enter the {network.native_symbol} amount as a positive decimal number."
        )
    fraction = match.group("fraction") or ""
    if len(fraction) > network.native_decimals:
        raise InvalidAmount(
            f"{network.native_symbol} amounts may have at most "
            f"{network.native_decimals} decimal places."
        )

    atomic_scale = 10 ** network.native_decimals
    atomic_value = int(match.group("whole")) * atomic_scale
    if fraction:
        atomic_value += int(fraction.ljust(network.native_decimals, "0"))
    if atomic_value <= 0:
        raise InvalidAmount(f"The {network.native_symbol} amount must be greater than zero.")
    if network.family is ChainFamily.EVM and atomic_value > MAX_UINT256:
        raise InvalidAmount("The amount exceeds the EVM transaction limit.")
    return atomic_value


def parse_asset_amount(value: str, symbol: str, decimals: int) -> int:
    """Convert a positive fungible-token amount to its exact atomic unit."""

    if decimals < 0 or decimals > 255:
        raise InvalidAmount("The token decimals are outside the supported range.")
    match = ETH_AMOUNT_RE.fullmatch(value.strip())
    if match is None:
        raise InvalidAmount(f"Enter the {symbol} amount as a positive decimal number.")
    fraction = match.group("fraction") or ""
    if len(fraction) > decimals:
        raise InvalidAmount(f"{symbol} amounts may have at most {decimals} decimal places.")
    atomic_value = int(match.group("whole")) * (10 ** decimals)
    if fraction:
        atomic_value += int(fraction.ljust(decimals, "0"))
    if atomic_value <= 0:
        raise InvalidAmount(f"The {symbol} amount must be greater than zero.")
    if atomic_value > MAX_UINT256:
        raise InvalidAmount("The token amount exceeds the EVM transaction limit.")
    return atomic_value


def format_atomic_amount(
    value_atomic: int, network: Network, *, decimals: int | None = None
) -> str:
    """Format an atomic-unit value without scientific notation."""

    precision = network.native_decimals if decimals is None else decimals
    if precision < 0 or precision > 255:
        raise ValueError("Asset decimals are outside the supported range.")
    atomic_scale = 10 ** precision
    whole, remainder = divmod(value_atomic, atomic_scale)
    if not remainder:
        return str(whole)
    return f"{whole}.{remainder:0{precision}d}".rstrip("0")


def parse_eth_to_wei(value: str) -> int:
    """Convert a positive decimal ETH amount to exact integer wei."""

    match = ETH_AMOUNT_RE.fullmatch(value.strip())
    if match is None:
        raise InvalidAmount("Enter the ETH amount as a positive decimal number.")
    fraction = match.group("fraction") or ""
    if len(fraction) > 18:
        raise InvalidAmount("ETH amounts may have at most 18 decimal places.")

    value_wei = int(match.group("whole")) * WEI_PER_ETH
    if fraction:
        value_wei += int(fraction.ljust(18, "0"))
    if value_wei <= 0:
        raise InvalidAmount("The ETH amount must be greater than zero.")
    if value_wei > MAX_UINT256:
        raise InvalidAmount("The ETH amount exceeds the EVM transaction limit.")
    return value_wei


def format_wei_as_eth(value_wei: int) -> str:
    """Format wei as plain decimal ETH without scientific notation."""

    whole, remainder = divmod(value_wei, WEI_PER_ETH)
    if not remainder:
        return str(whole)
    return f"{whole}.{remainder:018d}".rstrip("0")
