"""Encrypted, identity-bound storage for Polymarket CLOB session keys."""

import base64
from dataclasses import dataclass
import re
import secrets

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .security_policy import SESSION_KEY_LIFETIME_SECONDS

SESSION_KEY_TOKEN_NAMESPACE = "polymarket_session_keys"
SESSION_KEY_STATE_VERSION = 1
SESSION_KEY_ALGORITHM = "AES-256-GCM"
ADDRESS = re.compile(r"^0x[0-9a-f]{40}$")


class SessionKeyStoreError(ValueError):
    """Raised when session-key material or its identity binding is invalid."""


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode(value: str) -> bytes:
    return base64.b64decode(
        value + "=" * (-len(value) % 4), altchars=b"-_", validate=True
    )


def encode_wrapping_key(key: bytes) -> str:
    if not isinstance(key, bytes) or len(key) != 32:
        raise SessionKeyStoreError("Session-key wrapping key must contain 256 bits.")
    return _encode(key)


def decode_wrapping_key(encoded: str) -> bytes:
    try:
        key = _decode(encoded)
    except (TypeError, ValueError, base64.binascii.Error) as exc:
        raise SessionKeyStoreError("Session-key wrapping key is invalid.") from exc
    if len(key) != 32:
        raise SessionKeyStoreError("Session-key wrapping key must contain 256 bits.")
    return key


def _address(value: str, field: str) -> str:
    canonical = str(value or "").strip().lower()
    if not ADDRESS.fullmatch(canonical):
        raise SessionKeyStoreError(f"{field} is not a canonical EVM address.")
    return canonical


def _aad(*, deployment_id: str, discord_user_id: int, profile_id: str,
         signer_address: str, account_wallet_address: str,
         session_address: str) -> bytes:
    if not deployment_id or int(discord_user_id) <= 0 or not profile_id:
        raise SessionKeyStoreError("Session-key storage identity is incomplete.")
    return ":".join((
        "polymarket-session:v1", deployment_id, str(int(discord_user_id)), profile_id,
        _address(signer_address, "Signer address"),
        _address(account_wallet_address, "Account wallet address"),
        _address(session_address, "Session address"),
    )).encode("ascii")


@dataclass(frozen=True, slots=True)
class EncryptedSessionKey:
    """Authenticated ciphertext and public lifecycle metadata only."""

    profile_id: str
    signer_address: str
    account_wallet_address: str
    session_address: str
    nonce: str
    ciphertext: str
    created_at: int
    expires_at: int
    scope: str = "CLOB"
    version: int = SESSION_KEY_STATE_VERSION
    algorithm: str = SESSION_KEY_ALGORITHM

    def __post_init__(self):
        object.__setattr__(self, "signer_address", _address(self.signer_address, "Signer address"))
        object.__setattr__(self, "account_wallet_address", _address(self.account_wallet_address, "Account wallet address"))
        object.__setattr__(self, "session_address", _address(self.session_address, "Session address"))
        if not self.profile_id or len(self.profile_id) > 128:
            raise SessionKeyStoreError("Session key must bind to a CryptoWallet profile.")
        if self.scope != "CLOB":
            raise SessionKeyStoreError("Session key scope must be CLOB-only.")
        if self.version != 1 or self.algorithm != SESSION_KEY_ALGORITHM:
            raise SessionKeyStoreError("Session-key storage format is unsupported.")
        if (
            self.created_at <= 0
            or self.expires_at != self.created_at + SESSION_KEY_LIFETIME_SECONDS
        ):
            raise SessionKeyStoreError("Session-key lifetime is invalid.")
        try:
            nonce, ciphertext = _decode(self.nonce), _decode(self.ciphertext)
        except (TypeError, ValueError, base64.binascii.Error) as exc:
            raise SessionKeyStoreError("Session-key ciphertext encoding is invalid.") from exc
        if len(nonce) != 12 or len(ciphertext) < 17:
            raise SessionKeyStoreError("Session-key ciphertext is invalid.")

    def to_record(self) -> dict:
        return {name: getattr(self, name) for name in (
            "version", "algorithm", "profile_id", "signer_address",
            "account_wallet_address", "session_address", "scope", "nonce",
            "ciphertext", "created_at", "expires_at",
        )}

    @classmethod
    def from_record(cls, record: dict) -> "EncryptedSessionKey":
        expected = {
            "version", "algorithm", "profile_id", "signer_address",
            "account_wallet_address", "session_address", "scope", "nonce",
            "ciphertext", "created_at", "expires_at",
        }
        if not isinstance(record, dict) or set(record) != expected:
            raise SessionKeyStoreError("Session-key record has an invalid shape.")
        return cls(**record)


def protect_session_private_key(
    wrapping_key: bytes, private_key: bytes, *, deployment_id: str,
    discord_user_id: int, profile_id: str, signer_address: str,
    account_wallet_address: str, session_address: str, created_at: int,
    expires_at: int,
) -> EncryptedSessionKey:
    if not isinstance(private_key, bytes) or len(private_key) != 32 or not any(private_key):
        raise SessionKeyStoreError("Session private key must be a nonzero 256-bit value.")
    if not isinstance(wrapping_key, bytes) or len(wrapping_key) != 32:
        raise SessionKeyStoreError("Session-key wrapping key must contain 256 bits.")
    nonce = secrets.token_bytes(12)
    binding = dict(
        deployment_id=deployment_id, discord_user_id=discord_user_id,
        profile_id=profile_id, signer_address=signer_address,
        account_wallet_address=account_wallet_address,
        session_address=session_address,
    )
    ciphertext = AESGCM(wrapping_key).encrypt(nonce, private_key, _aad(**binding))
    return EncryptedSessionKey(
        profile_id, signer_address, account_wallet_address, session_address,
        _encode(nonce), _encode(ciphertext), int(created_at), int(expires_at),
    )


def reveal_session_private_key(
    wrapping_key: bytes, record: EncryptedSessionKey | dict, *, deployment_id: str,
    discord_user_id: int, profile_id: str, signer_address: str,
    account_wallet_address: str, session_address: str,
) -> bytes:
    state = record if isinstance(record, EncryptedSessionKey) else EncryptedSessionKey.from_record(record)
    expected = (profile_id, _address(signer_address, "Signer address"),
                _address(account_wallet_address, "Account wallet address"),
                _address(session_address, "Session address"))
    actual = (state.profile_id, state.signer_address,
              state.account_wallet_address, state.session_address)
    if any(not secrets.compare_digest(a, b) for a, b in zip(actual, expected)):
        raise SessionKeyStoreError("Session-key record does not match this wallet binding.")
    try:
        plaintext = AESGCM(wrapping_key).decrypt(
            _decode(state.nonce), _decode(state.ciphertext),
            _aad(deployment_id=deployment_id, discord_user_id=discord_user_id,
                 profile_id=profile_id, signer_address=signer_address,
                 account_wallet_address=account_wallet_address,
                 session_address=session_address),
        )
    except (InvalidTag, TypeError, ValueError, base64.binascii.Error) as exc:
        raise SessionKeyStoreError("Session-key ciphertext could not be authenticated.") from exc
    if len(plaintext) != 32 or not any(plaintext):
        raise SessionKeyStoreError("Session-key plaintext is invalid.")
    return plaintext
