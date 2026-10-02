"""Fail-closed public contract for a future Polymarket handoff."""

from dataclasses import dataclass, replace
import hashlib
import secrets


POLYMARKET_CHAIN_ID = 137
POLYMARKET_NETWORK_KEY = "polygon"
POLYMARKET_COLLATERAL_SYMBOL = "pUSD"
POLYMARKET_HANDOFF_PURPOSE = "polymarket-order-review"


CLOB_AUTH_MESSAGE = "This message attests that I control the given wallet"
CLOB_AUTH_DOMAIN = {"name": "ClobAuthDomain", "version": "1", "chainId": 137}
CLOB_AUTH_TYPES = {
    "ClobAuth": [
        {"name": "address", "type": "address"},
        {"name": "timestamp", "type": "string"},
        {"name": "nonce", "type": "uint256"},
        {"name": "message", "type": "string"},
    ]
}

SESSION_BATCH_TYPES = {
    "Call": [
        {"name": "target", "type": "address"},
        {"name": "value", "type": "uint256"},
        {"name": "data", "type": "bytes"},
    ],
    "Batch": [
        {"name": "wallet", "type": "address"},
        {"name": "nonce", "type": "uint256"},
        {"name": "deadline", "type": "uint256"},
        {"name": "calls", "type": "Call[]"},
    ],
}
SESSION_AUTHORIZE_SELECTOR = "24017fae"
SESSION_REVOKE_SELECTOR = "e63f952f"
SESSION_BATCH_LIFETIME_SECONDS = 5 * 60
SESSION_KEY_LIFETIME_SECONDS = 180 * 24 * 60 * 60


def _polymarket_address(value: str, label: str) -> str:
    address = str(value).lower()
    raw = address.removeprefix("0x")
    if len(raw) != 40 or any(character not in "0123456789abcdef" for character in raw):
        raise ValueError(f"Polymarket {label} is invalid.")
    return "0x" + raw


def _session_calldata(action: str, session_address: str, valid_until: int | None) -> str:
    session = _polymarket_address(session_address, "session signer")
    address_word = "0" * 24 + session[2:]
    if action == "authorize":
        if not isinstance(valid_until, int) or not 0 < valid_until < 2**256:
            raise ValueError("Polymarket session expiration is invalid.")
        return "0x" + SESSION_AUTHORIZE_SELECTOR + address_word + f"{valid_until:064x}"
    if action == "revoke" and valid_until is None:
        return "0x" + SESSION_REVOKE_SELECTOR + address_word
    raise ValueError("Polymarket session action is invalid.")


def validate_polymarket_session_batch_typed_data(
    typed_data: dict, *, wallet_address: str, session_address: str,
    action: str, valid_until: int | None,
) -> tuple[int, int]:
    """Accept only one exact Deposit Wallet session authorization or revocation."""

    wallet = _polymarket_address(wallet_address, "Deposit Wallet")
    session = _polymarket_address(session_address, "session signer")
    if not isinstance(typed_data, dict) or set(typed_data) != {
        "domain", "types", "primaryType", "message"
    }:
        raise ValueError("Polymarket session Batch typed data has an invalid shape.")
    domain = typed_data.get("domain")
    message = typed_data.get("message")
    calls = message.get("calls") if isinstance(message, dict) else None
    if (
        not isinstance(domain, dict)
        or set(domain) != {"name", "version", "chainId", "verifyingContract"}
        or not isinstance(message, dict)
        or set(message) != {"wallet", "nonce", "deadline", "calls"}
        or not isinstance(calls, list) or len(calls) != 1
        or not isinstance(calls[0], dict)
        or set(calls[0]) != {"target", "value", "data"}
    ):
        raise ValueError("Polymarket session Batch typed data has an invalid shape.")
    try:
        nonce = int(message["nonce"])
        deadline = int(message["deadline"])
    except (TypeError, ValueError) as exc:
        raise ValueError("Polymarket session Batch timing is invalid.") from exc
    if not 0 <= nonce < 2**256 or not 0 < deadline < 2**256:
        raise ValueError("Polymarket session Batch timing is invalid.")
    if (
        action == "authorize"
        and valid_until != deadline + (
            SESSION_KEY_LIFETIME_SECONDS - SESSION_BATCH_LIFETIME_SECONDS
        )
    ):
        raise ValueError("Polymarket session expiration policy changed.")
    expected = {
        "domain": {
            "name": "DepositWallet", "version": "1", "chainId": 137,
            "verifyingContract": wallet,
        },
        "types": {
            name: [dict(field) for field in fields]
            for name, fields in SESSION_BATCH_TYPES.items()
        },
        "primaryType": "Batch",
        "message": {
            "wallet": wallet, "nonce": str(nonce), "deadline": str(deadline),
            "calls": [{
                "target": wallet, "value": "0",
                "data": _session_calldata(action, session, valid_until),
            }],
        },
    }
    if typed_data != expected:
        raise ValueError("Polymarket session Batch typed data changed.")
    return nonce, deadline


def polymarket_clob_auth_typed_data(
    signer_address: str, *, timestamp: int, nonce: int
) -> dict:
    """Build the exact current Polymarket L1 ClobAuth EIP-712 payload."""

    address = signer_address.lower()
    raw = address.removeprefix("0x")
    if (
        len(raw) != 40
        or any(character not in "0123456789abcdef" for character in raw)
        or timestamp <= 0
        or not 0 <= nonce < 2**256
    ):
        raise ValueError("Polymarket ClobAuth identity or timing is invalid.")
    return {
        "domain": dict(CLOB_AUTH_DOMAIN),
        "types": {
            "ClobAuth": [dict(field) for field in CLOB_AUTH_TYPES["ClobAuth"]]
        },
        "primaryType": "ClobAuth",
        "message": {
            "address": address,
            "timestamp": str(timestamp),
            "nonce": str(nonce),
            "message": CLOB_AUTH_MESSAGE,
        },
    }


def validate_polymarket_clob_auth_typed_data(
    typed_data: dict, signer_address: str
) -> tuple[int, int]:
    """Reject every EIP-712 payload outside the exact Polymarket proof."""

    if not isinstance(typed_data, dict) or set(typed_data) != {
        "domain", "types", "primaryType", "message"
    }:
        raise ValueError("Polymarket ClobAuth typed data has an invalid shape.")
    message = typed_data.get("message")
    if not isinstance(message, dict) or set(message) != {
        "address", "timestamp", "nonce", "message"
    }:
        raise ValueError("Polymarket ClobAuth message has an invalid shape.")
    try:
        timestamp = int(message["timestamp"])
        nonce = int(message["nonce"])
    except (TypeError, ValueError) as exc:
        raise ValueError("Polymarket ClobAuth timing is invalid.") from exc
    expected = polymarket_clob_auth_typed_data(
        signer_address, timestamp=timestamp, nonce=nonce
    )
    if typed_data != expected:
        raise ValueError("Polymarket ClobAuth typed data changed.")
    return timestamp, nonce


@dataclass(frozen=True, slots=True)
class PolymarketHandoffAvailability:
    """Public state of the intentionally unavailable Polygon handoff."""

    chain_id: int = POLYMARKET_CHAIN_ID
    network: str = POLYMARKET_NETWORK_KEY
    collateral_symbol: str = POLYMARKET_COLLATERAL_SYMBOL
    enabled: bool = False
    reason: str = "Polygon mainnet handoff has not passed the required security, eligibility, and release reviews."



@dataclass(frozen=True, slots=True)
class PolymarketSignerContext:
    """Public CDP ownership evidence for one bot-first Polymarket signer."""

    requester_id: int
    profile_id: str
    provider_user_id: str
    smart_account_address: str
    signer_address: str
    chain_id: int = POLYMARKET_CHAIN_ID
    source: str = "cdp_smart_account_owner"

    def __post_init__(self):
        if (
            self.requester_id <= 0
            or not self.profile_id
            or self.provider_user_id != self.profile_id
            or self.chain_id != POLYMARKET_CHAIN_ID
            or self.source != "cdp_smart_account_owner"
        ):
            raise ValueError("Polymarket signer context identity is invalid.")
        for value in (self.smart_account_address, self.signer_address):
            address = value.removeprefix("0x")
            if (
                len(address) != 40
                or any(character not in "0123456789abcdefABCDEF" for character in address)
            ):
                raise ValueError("Polymarket signer context contains an invalid address.")
        object.__setattr__(
            self, "smart_account_address", self.smart_account_address.lower()
        )
        object.__setattr__(self, "signer_address", self.signer_address.lower())
        if self.smart_account_address == self.signer_address:
            raise ValueError("CDP smart account and owner signer must remain separate.")

    def to_dict(self) -> dict:
        return {
            "requester_id": self.requester_id,
            "profile_id": self.profile_id,
            "provider_user_id": self.provider_user_id,
            "smart_account_address": self.smart_account_address,
            "signer_address": self.signer_address,
            "chain_id": self.chain_id,
            "source": self.source,
        }

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
