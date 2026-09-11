from dataclasses import dataclass
from typing import Any

from .constants import CHAIN_ID, NETWORK_KEY


@dataclass(frozen=True, slots=True)
class TokenDraft:
    """Public, non-secret fixed-supply deployment parameters."""

    creator_discord_id: int
    name: str
    symbol: str
    decimals: int
    supply_atomic: int
    wallet_profile_id: str = ""
    owner_address: str = ""
    network: str = NETWORK_KEY
    chain_id: int = CHAIN_ID

    def to_dict(self) -> dict[str, Any]:
        return {
            "creator_discord_id": self.creator_discord_id,
            "wallet_profile_id": self.wallet_profile_id,
            "owner_address": self.owner_address,
            "name": self.name,
            "symbol": self.symbol,
            "decimals": self.decimals,
            "supply_atomic": str(self.supply_atomic),
            "network": self.network,
            "chain_id": self.chain_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TokenDraft":
        return cls(
            creator_discord_id=int(data["creator_discord_id"]),
            wallet_profile_id=str(data.get("wallet_profile_id") or ""),
            owner_address=str(data.get("owner_address") or ""),
            name=str(data["name"]),
            symbol=str(data["symbol"]),
            decimals=int(data["decimals"]),
            supply_atomic=int(data["supply_atomic"]),
            network=str(data.get("network") or NETWORK_KEY),
            chain_id=int(data.get("chain_id") or CHAIN_ID),
        )
