"""Current SDK-compatible order protocol and exchange selection."""

from dataclasses import dataclass

from .production_manifest import (
    POLYMARKET_PRODUCTION_MANIFEST,
    PolymarketProductionManifest,
)

_UINT256_MAX = (1 << 256) - 1
_V3_POSITION_RESERVED_BITS_MASK = ((1 << 64) - 1) << 40


class OrderProtocolError(ValueError):
    """Raised when a CLOB asset cannot be bound to a reviewed exchange."""


@dataclass(frozen=True, slots=True)
class OrderProtocol:
    version: str
    exchange_address: str


def is_protocol_v3_position_id(asset_id: str) -> bool:
    if not isinstance(asset_id, str) or not asset_id.isdigit():
        return False
    value = int(asset_id)
    return (
        0 <= value <= _UINT256_MAX
        and value & _V3_POSITION_RESERVED_BITS_MASK == 0
    )


def resolve_order_protocol(
    asset_id: str, *, negative_risk: bool,
    manifest: PolymarketProductionManifest = POLYMARKET_PRODUCTION_MANIFEST,
) -> OrderProtocol:
    if not isinstance(negative_risk, bool):
        raise OrderProtocolError("Negative-risk binding must be explicit.")
    if not isinstance(asset_id, str) or not asset_id.isdigit():
        raise OrderProtocolError("CLOB asset ID is invalid.")
    value = int(asset_id)
    if not 0 <= value <= _UINT256_MAX:
        raise OrderProtocolError("CLOB asset ID exceeds uint256.")
    if is_protocol_v3_position_id(asset_id):
        return OrderProtocol("3", manifest.exchange_v3.lower())
    return OrderProtocol(
        "2",
        (
            manifest.neg_risk_exchange
            if negative_risk else manifest.ctf_exchange
        ).lower(),
    )
