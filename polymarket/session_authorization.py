"""Exact non-executable session-key authorization and revocation contracts."""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from typing import Literal

from cryptography.hazmat.primitives.asymmetric import ec
from eth_hash.auto import keccak

from .account_connection import AccountConnectionError, normalize_evm_address
from .security_policy import SESSION_KEY_LIFETIME_SECONDS
from .signer_proof import CURVE_N


BATCH_LIFETIME_SECONDS = 5 * 60
AUTHORIZATION_PATH = "/v1/session-signers/authorizations"
REVOCATION_PATH = "/v1/session-signers/revocations"
SESSION_LIST_PATH = "/v1/user/session-signers"
WALLET_PARAMS_PATH = "/v1/account/transactions/params"
IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9_-]{32,128}$")
SIGNATURE = re.compile(r"^0x[0-9a-fA-F]{130}$")
BATCH_TYPES = {
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


def session_address_from_private_key(private_key: bytes) -> str:
    """Derive the canonical EVM address for one nonzero secp256k1 scalar."""

    if not isinstance(private_key, bytes) or len(private_key) != 32:
        raise AccountConnectionError("Session private key is invalid.")
    scalar = int.from_bytes(private_key, "big")
    if not 1 <= scalar < CURVE_N:
        raise AccountConnectionError("Session private key is invalid.")
    public = ec.derive_private_key(scalar, ec.SECP256K1()).public_key().public_numbers()
    encoded = public.x.to_bytes(32, "big") + public.y.to_bytes(32, "big")
    return "0x" + keccak(encoded)[-20:].hex()


def generate_session_key() -> tuple[bytes, str]:
    """Generate one local EOA keypair; only the address may leave secret storage."""

    while True:
        private_key = secrets.token_bytes(32)
        scalar = int.from_bytes(private_key, "big")
        if 1 <= scalar < CURVE_N:
            return private_key, session_address_from_private_key(private_key)


def _address_word(address: str) -> bytes:
    return bytes(12) + bytes.fromhex(normalize_evm_address(address, "address")[2:])


def _uint_word(value: int) -> bytes:
    if not 0 <= value < 2**256:
        raise AccountConnectionError("Session-key integer is outside uint256.")
    return value.to_bytes(32, "big")


def session_call_data(
    action: Literal["authorize", "revoke"], session_address: str, valid_until: int | None = None
) -> str:
    """Encode only the two reviewed Deposit Wallet session-signer calls."""

    address = normalize_evm_address(session_address, "session signer")
    if action == "authorize":
        if valid_until is None or valid_until <= 0:
            raise AccountConnectionError("Session-key expiration is invalid.")
        selector = keccak(b"authorizeSessionSigner(address,uint256)")[:4]
        encoded = selector + _address_word(address) + _uint_word(valid_until)
    elif action == "revoke":
        if valid_until is not None:
            raise AccountConnectionError("Revocation cannot include an expiration.")
        selector = keccak(b"revokeSessionSigner(address)")[:4]
        encoded = selector + _address_word(address)
    else:
        raise AccountConnectionError("Session-key action is invalid.")
    return "0x" + encoded.hex()


@dataclass(frozen=True, slots=True)
class SessionKeyOwnerApproval:
    """Immutable owner-signing request for one CLOB-only session lifecycle action."""

    action: Literal["authorize", "revoke"]
    discord_user_id: int
    profile_id: str
    owner_address: str
    wallet_address: str
    session_address: str
    nonce: int
    created_at: int
    deadline: int
    idempotency_key: str
    valid_until: int | None = None
    scopes: tuple[str, ...] = ("CLOB",)
    chain_id: int = 137

    def __post_init__(self) -> None:
        for field in ("owner_address", "wallet_address", "session_address"):
            object.__setattr__(self, field, normalize_evm_address(getattr(self, field), field))
        if (
            self.discord_user_id <= 0 or not self.profile_id
            or self.chain_id != 137 or self.scopes != ("CLOB",)
            or self.nonce < 0 or self.created_at <= 0
            or self.deadline != self.created_at + BATCH_LIFETIME_SECONDS
            or not IDEMPOTENCY_KEY.fullmatch(self.idempotency_key)
        ):
            raise AccountConnectionError("Session-key approval binding is invalid.")
        if self.action == "authorize":
            if self.valid_until != self.created_at + SESSION_KEY_LIFETIME_SECONDS:
                raise AccountConnectionError("Session key must expire after exactly 180 days.")
        elif self.action == "revoke":
            if self.valid_until is not None:
                raise AccountConnectionError("Session-key revocation cannot have an expiration.")
        else:
            raise AccountConnectionError("Session-key action is invalid.")
        if len({self.owner_address, self.wallet_address, self.session_address}) != 3:
            raise AccountConnectionError("Session-key identities must remain separate.")

    @property
    def calldata(self) -> str:
        return session_call_data(self.action, self.session_address, self.valid_until)

    def typed_data(self) -> dict:
        return {
            "domain": {
                "name": "DepositWallet", "version": "1",
                "chainId": 137, "verifyingContract": self.wallet_address,
            },
            "types": {name: [dict(field) for field in fields] for name, fields in BATCH_TYPES.items()},
            "primaryType": "Batch",
            "message": {
                "wallet": self.wallet_address,
                "nonce": str(self.nonce),
                "deadline": str(self.deadline),
                "calls": [{
                    "target": self.wallet_address, "value": "0", "data": self.calldata,
                }],
            },
        }

    def request_body(self, signature: str) -> dict:
        if not isinstance(signature, str) or not SIGNATURE.fullmatch(signature):
            raise AccountConnectionError("Session-key owner signature is invalid.")
        body = {
            "walletAddress": self.wallet_address,
            "sessionSignerAddress": self.session_address,
        }
        if self.action == "authorize":
            body.update(scopes=list(self.scopes), validUntil=str(self.valid_until))
        body.update(nonce=str(self.nonce), deadline=str(self.deadline), signature=signature)
        return body

    @property
    def endpoint(self) -> str:
        return AUTHORIZATION_PATH if self.action == "authorize" else REVOCATION_PATH
