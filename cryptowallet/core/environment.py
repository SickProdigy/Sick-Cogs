from enum import Enum


class WalletEnvironment(str, Enum):
    """Installation-wide wallet presentation and command-routing mode."""

    TESTNET = "testnet"
    MAINNET = "mainnet"
    MAINNET_ONLY = "mainnet-only"


def parse_wallet_environment(value: str) -> WalletEnvironment | None:
    try:
        return WalletEnvironment(str(value or "").strip().lower())
    except ValueError:
        return None


def environment_allows_network(
    environment: WalletEnvironment, *, network_is_testnet: bool, explicit_testnet: bool = False
) -> bool:
    """Return whether a network may be routed in the selected operating mode."""

    if environment is WalletEnvironment.TESTNET:
        return network_is_testnet
    if environment is WalletEnvironment.MAINNET_ONLY:
        return not network_is_testnet
    if network_is_testnet:
        return explicit_testnet
    return True
