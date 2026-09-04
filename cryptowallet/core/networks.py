from dataclasses import dataclass
from enum import Enum


class ChainFamily(str, Enum):
    """Transaction and address semantics shared by a chain family."""

    EVM = "evm"
    SOLANA = "solana"


class NetworkCapability(str, Enum):
    """Operations that may be independently enabled for one network."""

    BALANCE = "balance"
    SEND = "send"
    HISTORY = "history"
    TRANSACTION_LOOKUP = "transaction_lookup"
    DELEGATION = "delegation"
    RECOVERY = "recovery"
    EXPORT = "export"
    SPONSORSHIP = "sponsorship"


@dataclass(frozen=True, slots=True)
class NetworkCapabilities:
    """Fail-closed feature switches for a blockchain network."""

    balance: bool = False
    send: bool = False
    history: bool = False
    transaction_lookup: bool = False
    delegation: bool = False
    recovery: bool = False
    export: bool = False
    sponsorship: bool = False

    def supports(self, capability: NetworkCapability) -> bool:
        return bool(getattr(self, capability.value, False))

    def enabled(self) -> tuple[NetworkCapability, ...]:
        return tuple(capability for capability in NetworkCapability if self.supports(capability))


@dataclass(frozen=True, slots=True)
class Network:
    """Public chain metadata and explicitly reviewed wallet capabilities."""

    key: str
    name: str
    family: ChainFamily
    native_symbol: str
    native_decimals: int
    explorer_url: str
    testnet: bool
    enabled: bool
    capabilities: NetworkCapabilities
    chain_id: int | None = None
    cluster: str | None = None

    def __post_init__(self) -> None:
        if self.family is ChainFamily.EVM and self.chain_id is None:
            raise ValueError("EVM networks require a numeric chain ID.")
        if self.family is ChainFamily.SOLANA and not self.cluster:
            raise ValueError("Solana networks require an explicit cluster name.")
        if self.native_decimals < 0:
            raise ValueError("Native-token decimals cannot be negative.")

    @property
    def reference(self) -> str:
        """Return the chain ID or cluster without conflating their types."""

        return str(self.chain_id) if self.family is ChainFamily.EVM else str(self.cluster)

    @property
    def reference_label(self) -> str:
        return "chain ID" if self.family is ChainFamily.EVM else "cluster"

    def supports(self, capability: NetworkCapability) -> bool:
        return self.enabled and self.capabilities.supports(capability)


BASE_SEPOLIA = Network(
    key="base-sepolia",
    name="Base Sepolia",
    family=ChainFamily.EVM,
    chain_id=84532,
    native_symbol="ETH",
    native_decimals=18,
    explorer_url="https://sepolia.basescan.org",
    testnet=True,
    enabled=True,
    capabilities=NetworkCapabilities(
        balance=True,
        send=True,
        history=True,
        transaction_lookup=True,
        delegation=True,
        recovery=True,
        export=True,
        sponsorship=True,
    ),
)

ETHEREUM_SEPOLIA = Network(
    key="ethereum-sepolia",
    name="Ethereum Sepolia",
    family=ChainFamily.EVM,
    chain_id=11155111,
    native_symbol="ETH",
    native_decimals=18,
    explorer_url="https://sepolia.etherscan.io",
    testnet=True,
    enabled=False,
    capabilities=NetworkCapabilities(),
)

# KNOWN_NETWORKS includes planned entries for diagnostics and documentation. Only
# independently reviewed, enabled entries are exposed through NETWORKS.
KNOWN_NETWORKS = {
    network.key: network for network in (BASE_SEPOLIA, ETHEREUM_SEPOLIA)
}

# Only reviewed networks belong in NETWORKS. Ethereum Sepolia remains disabled, and
# Solana devnet remains undefined until their milestones satisfy issue #39.
NETWORKS = {
    key: network for key, network in KNOWN_NETWORKS.items() if network.enabled
}
DEFAULT_NETWORK = BASE_SEPOLIA.key
