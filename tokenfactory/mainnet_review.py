import json
import re
from dataclasses import asdict, dataclass
from hashlib import sha256
from typing import Any

from .constants import BASE_MAINNET_CHAIN_ID, BASE_MAINNET_NETWORK_KEY
from .models import TokenDraft
from .operations import token_operation
from .policy import validate_mainnet_limits
from .validation import normalize_owner_address


HASH_RE = re.compile(r"^0x[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class MainnetTokenReview:
    """Immutable disclosure for one requester-owned mainnet deployment."""

    network: str
    chain_id: int
    name: str
    symbol: str
    decimals: int
    supply_atomic: int
    owner_discord_id: int
    wallet_profile_id: str
    signer_address: str
    recipient: str
    request_id: str
    target_factory: str
    calldata_sha256: str
    gas_limit: int
    max_gas_fee_wei: int
    gas_payer: str
    gas_sponsored: bool
    native_value_wei: int
    estimated_gas_fee_wei: int = 0
    irreversible: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MainnetTokenReview":
        return cls(
            network=str(data["network"]),
            chain_id=int(data["chain_id"]),
            name=str(data["name"]),
            symbol=str(data["symbol"]),
            decimals=int(data["decimals"]),
            supply_atomic=int(data["supply_atomic"]),
            owner_discord_id=int(data["owner_discord_id"]),
            wallet_profile_id=str(data["wallet_profile_id"]),
            signer_address=normalize_owner_address(data["signer_address"]),
            recipient=normalize_owner_address(data["recipient"]),
            request_id=str(data["request_id"]),
            target_factory=normalize_owner_address(data["target_factory"]),
            calldata_sha256=str(data["calldata_sha256"]),
            gas_limit=int(data["gas_limit"]),
            estimated_gas_fee_wei=int(data.get("estimated_gas_fee_wei", data["max_gas_fee_wei"])),
            max_gas_fee_wei=int(data["max_gas_fee_wei"]),
            gas_payer=str(data["gas_payer"]),
            gas_sponsored=data.get("gas_sponsored") is True,
            native_value_wei=int(data["native_value_wei"]),
            irreversible=data.get("irreversible") is True,
        )

    @property
    def fingerprint(self) -> str:
        payload = json.dumps(
            asdict(self), sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("utf-8")
        return "0x" + sha256(payload).hexdigest()


def build_mainnet_token_review(
    draft: TokenDraft,
    request_id: str,
    recipient: str,
    *,
    max_gas_fee_wei: int,
    gas_payer: str,
    limits: dict,
    estimated_gas_fee_wei: int | None = None,
) -> MainnetTokenReview:
    reviewed_limits = validate_mainnet_limits(limits)
    if draft.network != BASE_MAINNET_NETWORK_KEY or draft.chain_id != BASE_MAINNET_CHAIN_ID:
        raise ValueError("The token draft is not bound to Base mainnet.")
    if draft.supply_atomic > reviewed_limits["max_token_supply_atomic"]:
        raise ValueError("The token supply exceeds the reviewed mainnet supply ceiling.")
    if not HASH_RE.fullmatch(str(request_id or "").lower()):
        raise ValueError("The mainnet request ID is invalid.")
    estimated_fee = int(estimated_gas_fee_wei if estimated_gas_fee_wei is not None else max_gas_fee_wei)
    if estimated_fee <= 0 or estimated_fee > int(max_gas_fee_wei):
        raise ValueError("The estimated gas fee exceeds the reapproval threshold.")
    if int(max_gas_fee_wei) <= 0:
        raise ValueError("The reapproval threshold must be positive.")
    payer = str(gas_payer or "").strip()
    if payer != "creator wallet":
        raise ValueError("Base mainnet gas must be paid by the creator wallet.")

    profile_id = str(draft.wallet_profile_id or "").strip()
    if not profile_id or len(profile_id) > 160:
        raise ValueError("The reviewed wallet profile is invalid.")
    signer = normalize_owner_address(draft.owner_address)
    owner = normalize_owner_address(recipient)
    operation = token_operation(
        draft, request_id, owner, network=BASE_MAINNET_NETWORK_KEY
    )
    if operation["gas_limit"] != reviewed_limits["token_gas_limit"]:
        raise ValueError("The operation gas limit does not match mainnet policy.")
    if operation["value_wei"] != reviewed_limits["native_value_wei"]:
        raise ValueError("The operation native value does not match mainnet policy.")

    return MainnetTokenReview(
        network=BASE_MAINNET_NETWORK_KEY,
        chain_id=BASE_MAINNET_CHAIN_ID,
        name=draft.name,
        symbol=draft.symbol,
        decimals=draft.decimals,
        supply_atomic=draft.supply_atomic,
        owner_discord_id=int(draft.creator_discord_id),
        wallet_profile_id=profile_id,
        signer_address=signer,
        recipient=owner,
        request_id=str(request_id).lower(),
        target_factory=str(operation["to"]).lower(),
        calldata_sha256="0x" + sha256(
            bytes.fromhex(str(operation["data"])[2:])
        ).hexdigest(),
        gas_limit=int(operation["gas_limit"]),
        estimated_gas_fee_wei=estimated_fee,
        max_gas_fee_wei=int(max_gas_fee_wei),
        gas_payer=payer,
        gas_sponsored=False,
        native_value_wei=int(operation["value_wei"]),
    )
