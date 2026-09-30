import json
import re
from dataclasses import asdict, dataclass
from hashlib import sha256

from .constants import BASE_MAINNET_CHAIN_ID, BASE_MAINNET_NETWORK_KEY
from .models import TokenDraft
from .operations import token_operation
from .policy import validate_mainnet_limits
from .validation import normalize_owner_address


HASH_RE = re.compile(r"^0x[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class MainnetTokenReview:
    """Immutable, non-executable disclosure for one owner canary."""

    network: str
    chain_id: int
    name: str
    symbol: str
    decimals: int
    supply_atomic: int
    recipient: str
    request_id: str
    target_factory: str
    calldata_sha256: str
    gas_limit: int
    max_gas_fee_wei: int
    gas_payer: str
    native_value_wei: int
    irreversible: bool = True

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
) -> MainnetTokenReview:
    reviewed_limits = validate_mainnet_limits(limits)
    if draft.network != BASE_MAINNET_NETWORK_KEY or draft.chain_id != BASE_MAINNET_CHAIN_ID:
        raise ValueError("The token draft is not bound to Base mainnet.")
    if draft.supply_atomic > reviewed_limits["max_token_supply_atomic"]:
        raise ValueError("The token supply exceeds the reviewed mainnet canary ceiling.")
    if not HASH_RE.fullmatch(str(request_id or "").lower()):
        raise ValueError("The mainnet request ID is invalid.")
    if (
        int(max_gas_fee_wei) <= 0
        or int(max_gas_fee_wei) > reviewed_limits["max_gas_fee_wei"]
    ):
        raise ValueError("The maximum gas fee exceeds the reviewed canary ceiling.")
    payer = str(gas_payer or "").strip()
    if not payer or len(payer) > 80:
        raise ValueError("The reviewed gas payer is invalid.")

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
        recipient=owner,
        request_id=str(request_id).lower(),
        target_factory=str(operation["to"]).lower(),
        calldata_sha256="0x" + sha256(
            bytes.fromhex(str(operation["data"])[2:])
        ).hexdigest(),
        gas_limit=int(operation["gas_limit"]),
        max_gas_fee_wei=int(max_gas_fee_wei),
        gas_payer=payer,
        native_value_wei=int(operation["value_wei"]),
    )
