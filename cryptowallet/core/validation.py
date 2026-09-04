import re

from .networks import ChainFamily, Network


EVM_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
ETH_AMOUNT_RE = re.compile(r"^(?P<whole>[0-9]+)(?:\.(?P<fraction>[0-9]+))?$")
WEI_PER_ETH = 10**18
MAX_UINT256 = 2**256 - 1


class InvalidAmount(ValueError):
    """Raised when a native-token amount cannot be represented safely."""


def normalize_evm_address(value: str) -> str:
    """Validate an EVM address without silently changing its checksum casing."""

    address = value.strip()
    if not EVM_ADDRESS_RE.fullmatch(address):
        raise ValueError("EVM addresses must contain 0x followed by 40 hexadecimal characters.")
    if int(address[2:], 16) == 0:
        raise ValueError("The zero address cannot receive a wallet transfer.")
    return address


def normalize_address_for_network(value: str, network: Network) -> str:
    """Validate an address using only the explicitly selected chain family."""

    if network.family is ChainFamily.EVM:
        return normalize_evm_address(value)
    if network.family is ChainFamily.SOLANA:
        raise ValueError("Solana address validation is not enabled yet.")
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


def format_atomic_amount(value_atomic: int, network: Network) -> str:
    """Format a native atomic-unit value without scientific notation."""

    atomic_scale = 10 ** network.native_decimals
    whole, remainder = divmod(value_atomic, atomic_scale)
    if not remainder:
        return str(whole)
    return f"{whole}.{remainder:0{network.native_decimals}d}".rstrip("0")


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
