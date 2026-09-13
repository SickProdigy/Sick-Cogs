"""Authoritative immutable Clanker v4 launch intent model."""

from __future__ import annotations

import hashlib
import json
import re

from dataclasses import dataclass
from .constants import DEFAULT_CLANKER_SUPPLY
from typing import Any, Mapping, Sequence


CLANKER_LAUNCH_VERSION = 1
CLANKER_LAUNCH_KIND = "clanker-v4-launch"
CLANKER_NETWORK = "base-sepolia"
CLANKER_CHAIN_ID = 84532
CLANKER_FACTORY = "0xE85A59c628F7d27878ACeB4bf3b35733630083a9"
MIN_AIRDROP_TOKENS = DEFAULT_CLANKER_SUPPLY * 25 // 10_000
MAX_AIRDROP_TOKENS = DEFAULT_CLANKER_SUPPLY * 90 // 100
ZERO_SALT = "0x" + "00" * 32
ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
BYTES32_RE = re.compile(r"^0x[0-9a-fA-F]{64}$")
SYMBOL_RE = re.compile(r"^[A-Z0-9]{2,12}$")
FEE_PREFERENCES = frozenset({"Both", "Paired", "Clanker"})


def _address(value: str, field: str) -> str:
    address = str(value or "").strip()
    if not ADDRESS_RE.fullmatch(address) or int(address[2:], 16) == 0:
        raise ValueError(f"{field} must be a nonzero EVM address.")
    return address.lower()


def _bytes32(value: str, field: str, *, allow_zero: bool = True) -> str:
    normalized = str(value or "").strip().lower()
    if not BYTES32_RE.fullmatch(normalized):
        raise ValueError(f"{field} must be a 32-byte hex value.")
    if not allow_zero and int(normalized[2:], 16) == 0:
        raise ValueError(f"{field} cannot be zero.")
    return normalized


def canonical_json(value: Mapping[str, Any]) -> str:
    """Serialize a JSON object identically across restarts and insertion orders."""

    if not isinstance(value, Mapping):
        raise ValueError("Canonical Clanker JSON values must be objects.")
    try:
        encoded = json.dumps(
            dict(value), ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True
        )
        decoded = json.loads(encoded)
    except (TypeError, ValueError) as exc:
        raise ValueError("Clanker metadata and context must contain JSON-safe values.") from exc
    if not isinstance(decoded, dict):
        raise ValueError("Canonical Clanker JSON values must be objects.")
    return encoded


@dataclass(frozen=True, slots=True)
class ClankerReward:
    admin: str
    recipient: str
    bps: int
    token: str = "Both"

    def __post_init__(self) -> None:
        object.__setattr__(self, "admin", _address(self.admin, "Reward admin"))
        object.__setattr__(self, "recipient", _address(self.recipient, "Reward recipient"))
        if not isinstance(self.bps, int) or isinstance(self.bps, bool) or not 0 < self.bps <= 10_000:
            raise ValueError("Reward bps must be a whole number from 1 through 10000.")
        if self.token not in FEE_PREFERENCES:
            raise ValueError("Reward token preference must be Both, Paired, or Clanker.")

    def to_dict(self) -> dict[str, Any]:
        return {"admin": self.admin, "recipient": self.recipient, "bps": self.bps, "token": self.token}


@dataclass(frozen=True, slots=True)
class ClankerPoolPosition:
    tick_lower: int
    tick_upper: int
    position_bps: int

    def __post_init__(self) -> None:
        if self.tick_lower >= self.tick_upper:
            raise ValueError("A pool position lower tick must be below its upper tick.")
        if not 0 < self.position_bps <= 10_000:
            raise ValueError("Pool position bps must be from 1 through 10000.")

    def to_dict(self) -> dict[str, int]:
        return {
            "tick_lower": self.tick_lower,
            "tick_upper": self.tick_upper,
            "position_bps": self.position_bps,
        }


@dataclass(frozen=True, slots=True)
class ClankerPool:
    paired_token: str
    tick_if_token0_is_clanker: int
    tick_spacing: int
    positions: tuple[ClankerPoolPosition, ...]
    fee_type: str = "static"
    clanker_fee_bps: int = 100
    paired_fee_bps: int = 100

    def __post_init__(self) -> None:
        object.__setattr__(self, "paired_token", _address(self.paired_token, "Pool paired token"))
        object.__setattr__(self, "positions", tuple(self.positions))
        if self.tick_spacing <= 0:
            raise ValueError("Pool tick spacing must be positive.")
        if not self.positions or sum(item.position_bps for item in self.positions) != 10_000:
            raise ValueError("Pool position bps must sum to 10000.")
        if not any(item.tick_lower == self.tick_if_token0_is_clanker for item in self.positions):
            raise ValueError("One pool position must touch the starting tick.")
        if any(
            item.tick_lower % self.tick_spacing or item.tick_upper % self.tick_spacing
            for item in self.positions
        ):
            raise ValueError("Pool ticks must be multiples of tick spacing.")
        if self.fee_type != "static":
            raise ValueError("Prototype Clanker intents support only the reviewed static fee shape.")
        if not 0 <= self.clanker_fee_bps <= 2_000 or not 0 <= self.paired_fee_bps <= 2_000:
            raise ValueError("Static pool fees must be from 0 through 2000 bps.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "paired_token": self.paired_token,
            "tick_if_token0_is_clanker": self.tick_if_token0_is_clanker,
            "tick_spacing": self.tick_spacing,
            "positions": [item.to_dict() for item in self.positions],
        }


def standard_base_sepolia_pool() -> ClankerPool:
    """Return the pinned SDK standard Base Sepolia WETH pool."""

    return ClankerPool(
        paired_token="0x4200000000000000000000000000000000000006",
        tick_if_token0_is_clanker=-230_400,
        tick_spacing=200,
        positions=(ClankerPoolPosition(-230_400, -120_000, 10_000),),
    )


@dataclass(frozen=True, slots=True)
class ClankerVault:
    recipient: str
    percentage: int
    lockup_seconds: int
    vesting_seconds: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "recipient", _address(self.recipient, "Vault recipient"))
        if not 1 <= self.percentage <= 90:
            raise ValueError("Vault percentage must be from 1 through 90.")
        if self.lockup_seconds < 7 * 24 * 60 * 60:
            raise ValueError("Vault lockup must be at least seven days.")
        if self.vesting_seconds < 0:
            raise ValueError("Vault vesting duration cannot be negative.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "recipient": self.recipient,
            "percentage": self.percentage,
            "lockup_seconds": self.lockup_seconds,
            "vesting_seconds": self.vesting_seconds,
        }


@dataclass(frozen=True, slots=True)
class ClankerAirdrop:
    admin: str
    merkle_root: str
    amount_tokens: int
    lockup_seconds: int
    vesting_seconds: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "admin", _address(self.admin, "Airdrop admin"))
        object.__setattr__(self, "merkle_root", _bytes32(self.merkle_root, "Airdrop root", allow_zero=False))
        if not MIN_AIRDROP_TOKENS <= self.amount_tokens <= MAX_AIRDROP_TOKENS:
            raise ValueError("Airdrop amount must be between 25 bps and 90% of token supply.")
        if self.lockup_seconds < 24 * 60 * 60:
            raise ValueError("Airdrop lockup must be at least one day.")
        if self.vesting_seconds < 0:
            raise ValueError("Airdrop vesting duration cannot be negative.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "admin": self.admin,
            "merkle_root": self.merkle_root,
            "amount_tokens": str(self.amount_tokens),
            "lockup_seconds": self.lockup_seconds,
            "vesting_seconds": self.vesting_seconds,
        }


@dataclass(frozen=True, slots=True)
class ClankerLaunchIntent:
    """Immutable launch definition shared by internal and external execution routes."""

    launch_id: str
    guild_id: int
    requester_id: int
    token_admin: str
    name: str
    symbol: str
    image: str
    metadata_json: str
    context_json: str
    pool: ClankerPool
    rewards: tuple[ClankerReward, ...]
    created_at: int
    expires_at: int
    vault: ClankerVault | None = None
    airdrop: ClankerAirdrop | None = None
    expected_native_value_wei: int = 0
    supply_tokens: int = DEFAULT_CLANKER_SUPPLY
    salt: str = ZERO_SALT
    version: int = CLANKER_LAUNCH_VERSION
    kind: str = CLANKER_LAUNCH_KIND
    network: str = CLANKER_NETWORK
    chain_id: int = CLANKER_CHAIN_ID
    factory: str = CLANKER_FACTORY

    def __post_init__(self) -> None:
        object.__setattr__(self, "token_admin", _address(self.token_admin, "Token admin"))
        object.__setattr__(self, "factory", _address(self.factory, "Clanker factory"))
        object.__setattr__(self, "salt", _bytes32(self.salt, "Token salt"))
        object.__setattr__(self, "rewards", tuple(self.rewards))
        if self.version != CLANKER_LAUNCH_VERSION or self.kind != CLANKER_LAUNCH_KIND:
            raise ValueError("Unsupported Clanker launch version or kind.")
        if self.network != CLANKER_NETWORK or self.chain_id != CLANKER_CHAIN_ID:
            raise ValueError("Clanker launchs are restricted to Base Sepolia.")
        if self.factory != CLANKER_FACTORY.lower():
            raise ValueError("Clanker launch targets an unreviewed factory.")
        _bytes32(self.launch_id, "Launch ID", allow_zero=False)
        if min(self.guild_id, self.requester_id) <= 0:
            raise ValueError("Guild and requester IDs must be positive.")
        name = " ".join(str(self.name or "").strip().split())
        symbol = str(self.symbol or "").strip().upper().lstrip("$")
        if not name or len(name.encode("utf-8")) > 64:
            raise ValueError("Token name must contain 1 through 64 UTF-8 bytes.")
        if not SYMBOL_RE.fullmatch(symbol):
            raise ValueError("Token symbol must contain 2 through 12 uppercase letters or numbers.")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "symbol", symbol)
        object.__setattr__(self, "metadata_json", canonical_json(json.loads(self.metadata_json or "{}")))
        object.__setattr__(self, "context_json", canonical_json(json.loads(self.context_json or "{}")))
        if not self.rewards or sum(item.bps for item in self.rewards) != 10_000:
            raise ValueError("Clanker reward bps must sum to 10000.")
        if self.created_at <= 0 or self.expires_at <= self.created_at:
            raise ValueError("Clanker intent expiry must follow its creation time.")
        if self.expected_native_value_wei != 0:
            raise ValueError("Prototype Clanker intents do not permit a developer buy or native value.")
        if self.supply_tokens != DEFAULT_CLANKER_SUPPLY:
            raise ValueError("Clanker v4 launches use the fixed 100 billion token supply.")
        extension_percentage = (self.vault.percentage if self.vault else 0)
        if self.airdrop:
            extension_percentage += (self.airdrop.amount_tokens * 100 + DEFAULT_CLANKER_SUPPLY - 1) // DEFAULT_CLANKER_SUPPLY
        if extension_percentage > 90:
            raise ValueError("Clanker vault and airdrop allocations cannot exceed 90% of supply.")

    @classmethod
    def create(
        cls,
        *,
        metadata: Mapping[str, Any] | None = None,
        context: Mapping[str, Any] | None = None,
        rewards: Sequence[ClankerReward],
        **values: Any,
    ) -> "ClankerLaunchIntent":
        return cls(
            metadata_json=canonical_json(metadata or {}),
            context_json=canonical_json(context or {}),
            rewards=tuple(rewards),
            **values,
        )


    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ClankerLaunchIntent":
        """Rebuild a stored intent and reject a changed canonical payload."""

        token = data.get("token") or {}
        pool = data.get("pool") or {}
        fees = data.get("fees") or {}
        supplied_hash = str(data.get("payload_hash") or "").lower()
        intent = cls.create(
            launch_id=str(data["launch_id"]),
            guild_id=int(data["guild_id"]),
            requester_id=int(data["requester_id"]),
            token_admin=str(token["admin"]),
            name=str(token["name"]),
            symbol=str(token["symbol"]),
            image=str(token.get("image") or ""),
            salt=str(token.get("salt") or ZERO_SALT),
            metadata=token.get("metadata") or {},
            context=token.get("context") or {},
            pool=ClankerPool(
                paired_token=str(pool["paired_token"]),
                tick_if_token0_is_clanker=int(pool["tick_if_token0_is_clanker"]),
                tick_spacing=int(pool["tick_spacing"]),
                positions=tuple(
                    ClankerPoolPosition(
                        tick_lower=int(item["tick_lower"]),
                        tick_upper=int(item["tick_upper"]),
                        position_bps=int(item["position_bps"]),
                    )
                    for item in pool["positions"]
                ),
                fee_type=str(fees.get("type") or "static"),
                clanker_fee_bps=int(fees.get("clanker_bps", 100)),
                paired_fee_bps=int(fees.get("paired_bps", 100)),
            ),
            rewards=tuple(
                ClankerReward(
                    admin=str(item["admin"]),
                    recipient=str(item["recipient"]),
                    bps=int(item["bps"]),
                    token=str(item.get("token") or "Both"),
                )
                for item in data["rewards"]
            ),
            vault=(
                ClankerVault(
                    recipient=str(data["vault"]["recipient"]),
                    percentage=int(data["vault"]["percentage"]),
                    lockup_seconds=int(data["vault"]["lockup_seconds"]),
                    vesting_seconds=int(data["vault"].get("vesting_seconds", 0)),
                )
                if data.get("vault") else None
            ),
            airdrop=(
                ClankerAirdrop(
                    admin=str(data["airdrop"]["admin"]),
                    merkle_root=str(data["airdrop"]["merkle_root"]),
                    amount_tokens=int(data["airdrop"]["amount_tokens"]),
                    lockup_seconds=int(data["airdrop"]["lockup_seconds"]),
                    vesting_seconds=int(data["airdrop"].get("vesting_seconds", 0)),
                )
                if data.get("airdrop") else None
            ),
            expected_native_value_wei=int(data.get("expected_native_value_wei", 0)),
            supply_tokens=int(data["supply_tokens"]),
            created_at=int(data["created_at"]),
            expires_at=int(data["expires_at"]),
            version=int(data["version"]),
            kind=str(data["kind"]),
            network=str(data["network"]),
            chain_id=int(data["chain_id"]),
            factory=str(data["factory"]),
        )
        if not supplied_hash or supplied_hash != intent.payload_hash:
            raise ValueError("Stored Clanker intent payload hash does not match its contents.")
        return intent

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "kind": self.kind,
            "launch_id": self.launch_id.lower(),
            "guild_id": str(self.guild_id),
            "requester_id": str(self.requester_id),
            "network": self.network,
            "chain_id": self.chain_id,
            "factory": self.factory,
            "expected_native_value_wei": str(self.expected_native_value_wei),
            "supply_tokens": str(self.supply_tokens),
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "token": {
                "admin": self.token_admin,
                "name": self.name,
                "symbol": self.symbol,
                "image": self.image,
                "salt": self.salt,
                "metadata": json.loads(self.metadata_json),
                "context": json.loads(self.context_json),
            },
            "pool": self.pool.to_dict(),
            "fees": {
                "type": self.pool.fee_type,
                "clanker_bps": self.pool.clanker_fee_bps,
                "paired_bps": self.pool.paired_fee_bps,
            },
            "rewards": [item.to_dict() for item in self.rewards],
            "vault": self.vault.to_dict() if self.vault else None,
            "airdrop": self.airdrop.to_dict() if self.airdrop else None,
        }

    def canonical_bytes(self) -> bytes:
        return json.dumps(
            self.canonical_payload(), ensure_ascii=False, allow_nan=False,
            separators=(",", ":"), sort_keys=True,
        ).encode("utf-8")

    @property
    def payload_hash(self) -> str:
        return "0x" + hashlib.sha256(self.canonical_bytes()).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {**self.canonical_payload(), "payload_hash": self.payload_hash}
