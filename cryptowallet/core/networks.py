from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Network:
    """Public metadata for a supported blockchain network."""

    key: str
    name: str
    chain_id: int
    native_symbol: str
    explorer_url: str
    testnet: bool


BASE_SEPOLIA = Network(
    key="base-sepolia",
    name="Base Sepolia",
    chain_id=84532,
    native_symbol="ETH",
    explorer_url="https://sepolia.basescan.org",
    testnet=True,
)

# Mainnet networks are intentionally absent until the prototype receives review.
NETWORKS = {BASE_SEPOLIA.key: BASE_SEPOLIA}
DEFAULT_NETWORK = BASE_SEPOLIA.key
