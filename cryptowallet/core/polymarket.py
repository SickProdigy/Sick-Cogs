"""Fail-closed public contract for a future Polymarket handoff."""

from dataclasses import dataclass, replace
import hashlib
import secrets


POLYMARKET_CHAIN_ID = 137
POLYMARKET_NETWORK_KEY = "polygon"
POLYMARKET_COLLATERAL_SYMBOL = "pUSD"
POLYMARKET_HANDOFF_PURPOSE = "polymarket-order-review"


@dataclass(frozen=True, slots=True)
class PolymarketHandoffAvailability:
    """Public state of the intentionally unavailable Polygon handoff."""

    chain_id: int = POLYMARKET_CHAIN_ID
    network: str = POLYMARKET_NETWORK_KEY
    collateral_symbol: str = POLYMARKET_COLLATERAL_SYMBOL
    enabled: bool = False
    reason: str = "Polygon mainnet handoff has not passed the required security, eligibility, and release reviews."


@dataclass(frozen=True, slots=True)
class PolymarketHandoffContext:
    """User-scoped public identity and reviewed capabilities; never signer material."""

    requester_id: int
    profile_id: str
    public_address: str | None = None
    chain_id: int = POLYMARKET_CHAIN_ID
    network: str = POLYMARKET_NETWORK_KEY
    collateral_symbol: str = POLYMARKET_COLLATERAL_SYMBOL
    reviewed_capabilities: tuple[str, ...] = ()
    enabled: bool = False

    def __post_init__(self):
        if self.requester_id <= 0 or not self.profile_id:
            raise ValueError("A user-scoped wallet profile is required.")
        if self.public_address is not None:
            address = self.public_address.removeprefix("0x")
            if len(address) != 40 or any(character not in "0123456789abcdefABCDEF" for character in address):
                raise ValueError("Public Polygon address is invalid.")
        if self.enabled or self.reviewed_capabilities:
            raise ValueError("Polygon handoff capabilities have not been reviewed or enabled.")


@dataclass(frozen=True, slots=True)
class PolymarketHandoffSession:
    """Persistent-store-friendly one-time state; it contains no signer or credential."""

    handle_digest: str
    requester_id: int
    profile_id: str
    intent_fingerprint: str
    chain_id: int
    purpose: str
    expires_at: int
    consumed_at: int | None = None

    @classmethod
    def create(cls, requester_id: int, profile_id: str, intent_fingerprint: str, expires_at: int):
        if requester_id <= 0 or not profile_id or len(intent_fingerprint) != 64 or expires_at <= 0:
            raise ValueError("Invalid Polymarket handoff binding.")
        handle = secrets.token_urlsafe(32)
        return handle, cls(
            hashlib.sha256(handle.encode()).hexdigest(), requester_id, profile_id,
            intent_fingerprint, POLYMARKET_CHAIN_ID, POLYMARKET_HANDOFF_PURPOSE, expires_at,
        )

    def consume(self, handle: str, requester_id: int, now: int) -> "PolymarketHandoffSession":
        if self.consumed_at is not None or now >= self.expires_at:
            raise ValueError("Polymarket handoff is unavailable.")
        if requester_id != self.requester_id or not secrets.compare_digest(hashlib.sha256(handle.encode()).hexdigest(), self.handle_digest):
            raise ValueError("Polymarket handoff binding does not match.")
        return replace(self, consumed_at=now)
