"""Fail-closed public contract for a future Polymarket handoff."""

from dataclasses import dataclass


POLYMARKET_CHAIN_ID = 137
POLYMARKET_NETWORK_KEY = "polygon"
POLYMARKET_COLLATERAL_SYMBOL = "pUSD"


@dataclass(frozen=True, slots=True)
class PolymarketHandoffAvailability:
    """Public state of the intentionally unavailable Polygon handoff."""

    chain_id: int = POLYMARKET_CHAIN_ID
    network: str = POLYMARKET_NETWORK_KEY
    collateral_symbol: str = POLYMARKET_COLLATERAL_SYMBOL
    enabled: bool = False
    reason: str = "Polygon mainnet handoff has not passed the required security, eligibility, and release reviews."
