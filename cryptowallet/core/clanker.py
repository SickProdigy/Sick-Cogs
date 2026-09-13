"""Immutable, testnet-only Clanker deployment intent primitives."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence


CLANKER_INTENT_VERSION = 1
CLANKER_INTENT_KIND = "clanker-deployment"
CLANKER_NETWORK = "base-sepolia"
CLANKER_CHAIN_ID = 84532
CLANKER_FACTORY = "0xE85A59c628F7d27878ACeB4bf3b35733630083a9"
CLANKER_TOKEN_SUPPLY = 100_000_000_000
MIN_AIRDROP_TOKENS = CLANKER_TOKEN_SUPPLY * 25 // 10_000
MAX_AIRDROP_TOKENS = CLANKER_TOKEN_SUPPLY * 90 // 100
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
            "fees": {
                "type": self.fee_type,
                "clanker_bps": self.clanker_fee_bps,
                "paired_bps": self.paired_fee_bps,
            },
        }


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
class ClankerDeploymentIntent:
    """Exact public launch request awaiting protected wallet approval."""

    intent_id: str
    deployment_id: str
    discord_application_id: int
    guild_id: int
    discord_user_id: int
    profile_id: str
    wallet_address: str
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
    estimated_gas_fee_wei: int = 0
    salt: str = ZERO_SALT
    version: int = CLANKER_INTENT_VERSION
    kind: str = CLANKER_INTENT_KIND
    network: str = CLANKER_NETWORK
    chain_id: int = CLANKER_CHAIN_ID
    factory: str = CLANKER_FACTORY

    def __post_init__(self) -> None:
        object.__setattr__(self, "wallet_address", _address(self.wallet_address, "Wallet address"))
        object.__setattr__(self, "token_admin", _address(self.token_admin, "Token admin"))
        object.__setattr__(self, "factory", _address(self.factory, "Clanker factory"))
        object.__setattr__(self, "salt", _bytes32(self.salt, "Token salt"))
        object.__setattr__(self, "rewards", tuple(self.rewards))
        if self.version != CLANKER_INTENT_VERSION or self.kind != CLANKER_INTENT_KIND:
            raise ValueError("Unsupported Clanker deployment intent version or kind.")
        if self.network != CLANKER_NETWORK or self.chain_id != CLANKER_CHAIN_ID:
            raise ValueError("Clanker deployment intents are restricted to Base Sepolia.")
        if self.factory != CLANKER_FACTORY.lower():
            raise ValueError("Clanker deployment intent targets an unreviewed factory.")
        if not _bytes32(self.intent_id, "Intent ID", allow_zero=False):
            raise ValueError("Invalid intent ID.")
        if not self.deployment_id or not self.profile_id:
            raise ValueError("Deployment and wallet profile identity are required.")
        if min(self.discord_application_id, self.guild_id, self.discord_user_id) <= 0:
            raise ValueError("Discord application, guild, and user IDs must be positive.")
        if self.wallet_address != self.token_admin:
            raise ValueError("Prototype token admin must be the requesting CryptoWallet address.")
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
        if self.estimated_gas_fee_wei < 0:
            raise ValueError("Estimated Clanker gas fee cannot be negative.")
        extension_percentage = (self.vault.percentage if self.vault else 0)
        if self.airdrop:
            extension_percentage += (self.airdrop.amount_tokens * 100 + CLANKER_TOKEN_SUPPLY - 1) // CLANKER_TOKEN_SUPPLY
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
    ) -> "ClankerDeploymentIntent":
        return cls(
            metadata_json=canonical_json(metadata or {}),
            context_json=canonical_json(context or {}),
            rewards=tuple(rewards),
            **values,
        )


    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ClankerDeploymentIntent":
        """Rebuild a stored intent and reject a changed canonical payload."""

        token = data.get("token") or {}
        pool = data.get("pool") or {}
        fees = pool.get("fees") or {}
        supplied_hash = str(data.get("payload_hash") or "").lower()
        intent = cls.create(
            intent_id=str(data["intent_id"]),
            deployment_id=str(data["deployment_id"]),
            discord_application_id=int(data["discord_application_id"]),
            guild_id=int(data["guild_id"]),
            discord_user_id=int(data["discord_user_id"]),
            profile_id=str(data["profile_id"]),
            wallet_address=str(data["wallet_address"]),
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
            estimated_gas_fee_wei=int(data.get("estimated_gas_fee_wei", 0)),
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
            "intent_id": self.intent_id.lower(),
            "deployment_id": self.deployment_id,
            "discord_application_id": str(self.discord_application_id),
            "guild_id": str(self.guild_id),
            "discord_user_id": str(self.discord_user_id),
            "profile_id": self.profile_id,
            "wallet_address": self.wallet_address,
            "network": self.network,
            "chain_id": self.chain_id,
            "factory": self.factory,
            "expected_native_value_wei": str(self.expected_native_value_wei),
            "estimated_gas_fee_wei": str(self.estimated_gas_fee_wei),
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


def signing_intent_from_clanker_launch(
    launch: Mapping[str, Any], operation: Mapping[str, Any], *,
    deployment_id: str, discord_application_id: int, profile_id: str,
    wallet_address: str,
) -> ClankerDeploymentIntent:
    """Independently validate and bind a Clanker-owned launch for signing."""

    expected_launch_fields = {
        "version", "kind", "launch_id", "guild_id", "requester_id", "network",
        "chain_id", "factory", "supply_tokens", "expected_native_value_wei",
        "created_at", "expires_at", "token", "pool", "fees", "rewards",
        "vault", "airdrop", "payload_hash",
    }
    if set(launch) != expected_launch_fields:
        raise ValueError("Clanker launch fields do not match the reviewed signer schema.")
    canonical = dict(launch)
    supplied_hash = str(canonical.pop("payload_hash") or "").lower()
    computed_hash = "0x" + hashlib.sha256(json.dumps(
        canonical, ensure_ascii=False, allow_nan=False, separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")).hexdigest()
    if supplied_hash != computed_hash:
        raise ValueError("Clanker source launch payload hash does not match its contents.")
    if set(operation) != {"launch_id", "payload_hash", "chain_id", "to", "value", "data"}:
        raise ValueError("Clanker operation fields do not match the reviewed signer schema.")
    if (
        str(operation["launch_id"]).lower() != str(launch["launch_id"]).lower()
        or str(operation["payload_hash"]).lower() != supplied_hash
        or int(operation["chain_id"]) != CLANKER_CHAIN_ID
        or str(operation["to"]).lower() != CLANKER_FACTORY.lower()
        or int(operation["value"]) != 0
        or not re.fullmatch(r"0x[0-9a-fA-F]+", str(operation["data"]))
    ):
        raise ValueError("Clanker operation does not match its immutable launch binding.")
    token = launch["token"]
    pool = launch["pool"]
    fees = launch["fees"]
    signer = _address(wallet_address, "Signing wallet")
    if (
        int(launch["version"]) != 1
        or str(launch["kind"]) != "clanker-v4-launch"
        or str(launch["network"]) != CLANKER_NETWORK
        or int(launch["chain_id"]) != CLANKER_CHAIN_ID
        or str(launch["factory"]).lower() != CLANKER_FACTORY.lower()
        or int(launch["supply_tokens"]) != CLANKER_TOKEN_SUPPLY
        or int(launch["requester_id"]) <= 0
        or str(token["admin"]).lower() != signer
    ):
        raise ValueError("Clanker launch is outside the internal-wallet signing policy.")
    intent_id = "0x" + hashlib.sha256(
        f"clanker-signing-v1:{str(launch['launch_id']).lower()}:{supplied_hash}".encode("utf-8")
    ).hexdigest()
    vault_data = launch.get("vault")
    airdrop_data = launch.get("airdrop")
    return ClankerDeploymentIntent.create(
        intent_id=intent_id, deployment_id=deployment_id,
        discord_application_id=discord_application_id, guild_id=int(launch["guild_id"]),
        discord_user_id=int(launch["requester_id"]), profile_id=profile_id,
        wallet_address=signer, token_admin=str(token["admin"]),
        name=str(token["name"]), symbol=str(token["symbol"]),
        image=str(token.get("image") or ""), salt=str(token.get("salt") or ZERO_SALT),
        metadata=token.get("metadata") or {}, context=token.get("context") or {},
        pool=ClankerPool(
            str(pool["paired_token"]), int(pool["tick_if_token0_is_clanker"]),
            int(pool["tick_spacing"]), tuple(
                ClankerPoolPosition(int(item["tick_lower"]), int(item["tick_upper"]), int(item["position_bps"]))
                for item in pool["positions"]
            ), str(fees["type"]), int(fees["clanker_bps"]), int(fees["paired_bps"]),
        ),
        rewards=tuple(ClankerReward(
            str(item["admin"]), str(item["recipient"]), int(item["bps"]), str(item["token"])
        ) for item in launch["rewards"]),
        vault=ClankerVault(
            str(vault_data["recipient"]), int(vault_data["percentage"]),
            int(vault_data["lockup_seconds"]), int(vault_data["vesting_seconds"]),
        ) if vault_data else None,
        airdrop=ClankerAirdrop(
            str(airdrop_data["admin"]), str(airdrop_data["merkle_root"]),
            int(airdrop_data["amount_tokens"]), int(airdrop_data["lockup_seconds"]),
            int(airdrop_data["vesting_seconds"]),
        ) if airdrop_data else None,
        expected_native_value_wei=0, estimated_gas_fee_wei=0,
        created_at=int(launch["created_at"]), expires_at=int(launch["expires_at"]),
    )
