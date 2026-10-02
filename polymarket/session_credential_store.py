"""Authenticated encrypted storage for session-owned Polymarket CLOB credentials."""

from __future__ import annotations

import base64
import json
import secrets
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .order_transport import ClobCredentials, OrderTransportError
from .security_policy import SESSION_KEY_LIFETIME_SECONDS
from .session_key_store import SessionKeyStoreError, _address, _decode, _encode


CREDENTIAL_STATE_VERSION = 1
CREDENTIAL_ALGORITHM = "AES-256-GCM"


def _aad(
    *, deployment_id: str, discord_user_id: int, profile_id: str,
    signer_address: str, account_wallet_address: str, session_address: str,
) -> bytes:
    if not deployment_id or int(discord_user_id) <= 0 or not profile_id:
        raise SessionKeyStoreError("Session credential storage identity is incomplete.")
    return ":".join((
        "polymarket-session-credentials:v1", deployment_id,
        str(int(discord_user_id)), profile_id,
        _address(signer_address, "Signer address"),
        _address(account_wallet_address, "Account wallet address"),
        _address(session_address, "Session address"),
    )).encode("ascii")


@dataclass(frozen=True, slots=True)
class EncryptedSessionCredentials:
    """Ciphertext plus public identity/lifetime metadata; never credential plaintext."""

    profile_id: str
    signer_address: str
    account_wallet_address: str
    session_address: str
    nonce: str
    ciphertext: str
    created_at: int
    expires_at: int
    scope: str = "CLOB"
    version: int = CREDENTIAL_STATE_VERSION
    algorithm: str = CREDENTIAL_ALGORITHM

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "signer_address", _address(self.signer_address, "Signer address")
        )
        object.__setattr__(
            self, "account_wallet_address",
            _address(self.account_wallet_address, "Account wallet address"),
        )
        object.__setattr__(
            self, "session_address", _address(self.session_address, "Session address")
        )
        if (
            not self.profile_id or len(self.profile_id) > 128
            or self.scope != "CLOB"
            or self.version != CREDENTIAL_STATE_VERSION
            or self.algorithm != CREDENTIAL_ALGORITHM
            or self.created_at <= 0
            or self.expires_at != self.created_at + SESSION_KEY_LIFETIME_SECONDS
        ):
            raise SessionKeyStoreError("Session credential metadata is invalid.")
        try:
            nonce, ciphertext = _decode(self.nonce), _decode(self.ciphertext)
        except (TypeError, ValueError, base64.binascii.Error) as exc:
            raise SessionKeyStoreError(
                "Session credential ciphertext encoding is invalid."
            ) from exc
        if len(nonce) != 12 or len(ciphertext) < 17:
            raise SessionKeyStoreError("Session credential ciphertext is invalid.")

    def to_record(self) -> dict:
        return {
            name: getattr(self, name) for name in (
                "version", "algorithm", "profile_id", "signer_address",
                "account_wallet_address", "session_address", "scope", "nonce",
                "ciphertext", "created_at", "expires_at",
            )
        }

    @classmethod
    def from_record(cls, record: dict) -> "EncryptedSessionCredentials":
        expected = {
            "version", "algorithm", "profile_id", "signer_address",
            "account_wallet_address", "session_address", "scope", "nonce",
            "ciphertext", "created_at", "expires_at",
        }
        if not isinstance(record, dict) or set(record) != expected:
            raise SessionKeyStoreError(
                "Session credential record has an invalid shape."
            )
        return cls(**record)


def protect_session_credentials(
    wrapping_key: bytes, credentials: ClobCredentials, *,
    deployment_id: str, discord_user_id: int, profile_id: str,
    signer_address: str, account_wallet_address: str, session_address: str,
    created_at: int, expires_at: int,
) -> EncryptedSessionCredentials:
    if not isinstance(wrapping_key, bytes) or len(wrapping_key) != 32:
        raise SessionKeyStoreError("Session-key wrapping key must contain 256 bits.")
    if not isinstance(credentials, ClobCredentials):
        raise SessionKeyStoreError("Session CLOB credentials are invalid.")
    binding = {
        "deployment_id": deployment_id, "discord_user_id": discord_user_id,
        "profile_id": profile_id, "signer_address": signer_address,
        "account_wallet_address": account_wallet_address,
        "session_address": session_address,
    }
    plaintext = json.dumps({
        "key": credentials.key, "secret": credentials.secret,
        "passphrase": credentials.passphrase,
    }, sort_keys=True, separators=(",", ":")).encode("utf-8")
    nonce = secrets.token_bytes(12)
    ciphertext = AESGCM(wrapping_key).encrypt(nonce, plaintext, _aad(**binding))
    return EncryptedSessionCredentials(
        profile_id, signer_address, account_wallet_address, session_address,
        _encode(nonce), _encode(ciphertext), int(created_at), int(expires_at),
    )


def reveal_session_credentials(
    wrapping_key: bytes, record: EncryptedSessionCredentials | dict, *,
    deployment_id: str, discord_user_id: int, profile_id: str,
    signer_address: str, account_wallet_address: str, session_address: str,
) -> ClobCredentials:
    if not isinstance(wrapping_key, bytes) or len(wrapping_key) != 32:
        raise SessionKeyStoreError("Session-key wrapping key must contain 256 bits.")
    state = (
        record if isinstance(record, EncryptedSessionCredentials)
        else EncryptedSessionCredentials.from_record(record)
    )
    expected = (
        profile_id, _address(signer_address, "Signer address"),
        _address(account_wallet_address, "Account wallet address"),
        _address(session_address, "Session address"),
    )
    actual = (
        state.profile_id, state.signer_address,
        state.account_wallet_address, state.session_address,
    )
    if any(not secrets.compare_digest(a, b) for a, b in zip(actual, expected)):
        raise SessionKeyStoreError(
            "Session credential record does not match this wallet binding."
        )
    try:
        plaintext = AESGCM(wrapping_key).decrypt(
            _decode(state.nonce), _decode(state.ciphertext),
            _aad(
                deployment_id=deployment_id, discord_user_id=discord_user_id,
                profile_id=profile_id, signer_address=signer_address,
                account_wallet_address=account_wallet_address,
                session_address=session_address,
            ),
        )
        values = json.loads(plaintext)
        if not isinstance(values, dict) or set(values) != {
            "key", "secret", "passphrase"
        }:
            raise ValueError("invalid credential payload")
        return ClobCredentials(
            values["key"], values["secret"], values["passphrase"]
        )
    except (
        InvalidTag, UnicodeDecodeError, json.JSONDecodeError,
        OrderTransportError, TypeError, ValueError, base64.binascii.Error,
    ) as exc:
        raise SessionKeyStoreError(
            "Session credential ciphertext could not be authenticated."
        ) from exc
