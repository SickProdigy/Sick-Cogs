"""Credential-free contracts for protected Polymarket account onboarding."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from typing import Any, Mapping

from .account_connection import (
    AccountConnection,
    AccountConnectionError,
    WalletType,
    normalize_evm_address,
)
from .security_policy import EligibilityAttestation


ONBOARDING_LIFETIME_SECONDS = 5 * 60
FINGERPRINT = re.compile(r"^[0-9a-f]{64}$")
HANDLE = re.compile(r"^[A-Za-z0-9_-]{32,128}$")
PROOF_METHODS = ("eip712_clob_auth", "deposit_wallet_owner")
RELATIONSHIP_SOURCES = (
    "polymarket_secure_client",
    "polygon_contract_read",
)


def _bounded(value: Any, field: str, limit: int = 128) -> str:
    if not isinstance(value, str) or not value or len(value) > limit:
        raise AccountConnectionError(f"{field} is invalid.")
    return value


def _fingerprint(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class ProtectedOnboardingChallenge:
    """Public, signed-companion binding for one existing-account verification."""

    connection_id: str
    result_handle: str
    discord_user_id: int
    signer_address: str
    account_wallet_address: str
    wallet_type: WalletType
    challenge: str
    created_at: int
    expires_at: int
    chain_id: int = 137
    purpose: str = "polymarket_connect"

    def __post_init__(self) -> None:
        _bounded(self.connection_id, "connection_id")
        if not HANDLE.fullmatch(self.result_handle):
            raise AccountConnectionError("result_handle is invalid.")
        if self.discord_user_id <= 0:
            raise AccountConnectionError("A positive Discord user ID is required.")
        object.__setattr__(
            self, "signer_address",
            normalize_evm_address(self.signer_address, "signer_address"),
        )
        object.__setattr__(
            self, "account_wallet_address",
            normalize_evm_address(self.account_wallet_address, "account_wallet_address"),
        )
        if not isinstance(self.wallet_type, WalletType):
            raise AccountConnectionError("A recognized wallet type is required.")
        if self.wallet_type is WalletType.EOA and self.signer_address != self.account_wallet_address:
            raise AccountConnectionError("EOA signer and account wallet must match.")
        if self.wallet_type is not WalletType.EOA and self.signer_address == self.account_wallet_address:
            raise AccountConnectionError("Smart-wallet signer and account wallet must be distinct.")
        if not HANDLE.fullmatch(self.challenge):
            raise AccountConnectionError("challenge is invalid.")
        if self.expires_at != self.created_at + ONBOARDING_LIFETIME_SECONDS:
            raise AccountConnectionError("Onboarding must expire after five minutes.")
        if self.chain_id != 137 or self.purpose != "polymarket_connect":
            raise AccountConnectionError("Onboarding target is invalid.")

    @property
    def fingerprint(self) -> str:
        return _fingerprint(self.to_record())

    def to_record(self) -> dict[str, Any]:
        record = asdict(self)
        record["wallet_type"] = self.wallet_type.value
        return record

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> "ProtectedOnboardingChallenge":
        try:
            values = dict(record)
            values["wallet_type"] = WalletType(values["wallet_type"])
            return cls(**values)
        except (KeyError, TypeError, ValueError) as exc:
            raise AccountConnectionError("Stored onboarding challenge is invalid.") from exc


@dataclass(frozen=True, slots=True)
class ProtectedOnboardingResult:
    """Consumed companion evidence; raw signatures, credentials, and IPs are excluded."""

    challenge_fingerprint: str
    discord_user_id: int
    signer_address: str
    account_wallet_address: str
    wallet_type: WalletType
    proof_method: str
    proof_digest: str
    relationship_source: str
    relationship_evidence_digest: str
    eligibility: EligibilityAttestation
    verified_at: int

    def __post_init__(self) -> None:
        if not FINGERPRINT.fullmatch(self.challenge_fingerprint):
            raise AccountConnectionError("Challenge fingerprint is invalid.")
        if self.discord_user_id <= 0:
            raise AccountConnectionError("A positive Discord user ID is required.")
        object.__setattr__(
            self, "signer_address",
            normalize_evm_address(self.signer_address, "signer_address"),
        )
        object.__setattr__(
            self, "account_wallet_address",
            normalize_evm_address(self.account_wallet_address, "account_wallet_address"),
        )
        if not isinstance(self.wallet_type, WalletType):
            raise AccountConnectionError("A recognized wallet type is required.")
        if self.proof_method not in PROOF_METHODS:
            raise AccountConnectionError("Signer proof method is not reviewed.")
        if self.proof_method == "deposit_wallet_owner" and self.wallet_type is not WalletType.DEPOSIT_WALLET:
            raise AccountConnectionError("Deposit Wallet owner proof requires a Deposit Wallet.")
        if not FINGERPRINT.fullmatch(self.proof_digest):
            raise AccountConnectionError("Signer proof digest is invalid.")
        if self.relationship_source not in RELATIONSHIP_SOURCES:
            raise AccountConnectionError("Account relationship source is not reviewed.")
        if not FINGERPRINT.fullmatch(self.relationship_evidence_digest):
            raise AccountConnectionError("Account relationship evidence is invalid.")
        if not isinstance(self.eligibility, EligibilityAttestation):
            raise AccountConnectionError("Protected eligibility evidence is required.")
        if self.verified_at != self.eligibility.checked_at:
            raise AccountConnectionError("Verification and eligibility timestamps must match.")

    @property
    def evidence_fingerprint(self) -> str:
        return _fingerprint(self.to_record())

    def to_record(self) -> dict[str, Any]:
        return {
            "challenge_fingerprint": self.challenge_fingerprint,
            "discord_user_id": self.discord_user_id,
            "signer_address": self.signer_address,
            "account_wallet_address": self.account_wallet_address,
            "wallet_type": self.wallet_type.value,
            "proof_method": self.proof_method,
            "proof_digest": self.proof_digest,
            "relationship_source": self.relationship_source,
            "relationship_evidence_digest": self.relationship_evidence_digest,
            "eligibility": asdict(self.eligibility),
            "verified_at": self.verified_at,
        }

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> "ProtectedOnboardingResult":
        try:
            values = dict(record)
            values["wallet_type"] = WalletType(values["wallet_type"])
            values["eligibility"] = EligibilityAttestation(**values["eligibility"])
            return cls(**values)
        except (KeyError, TypeError, ValueError) as exc:
            raise AccountConnectionError("Stored onboarding result is invalid.") from exc


def complete_protected_onboarding(
    challenge: ProtectedOnboardingChallenge,
    result: ProtectedOnboardingResult,
    *,
    discord_user_id: int,
    now: int,
) -> AccountConnection:
    """Validate exact protected evidence and return a verified public connection."""

    if now < challenge.created_at or now >= challenge.expires_at:
        raise AccountConnectionError("Onboarding challenge is not current.")
    if discord_user_id != challenge.discord_user_id or result.discord_user_id != discord_user_id:
        raise AccountConnectionError("Onboarding belongs to a different Discord user.")
    if result.challenge_fingerprint != challenge.fingerprint:
        raise AccountConnectionError("Onboarding result does not match its challenge.")
    if (
        result.signer_address != challenge.signer_address
        or result.account_wallet_address != challenge.account_wallet_address
        or result.wallet_type is not challenge.wallet_type
    ):
        raise AccountConnectionError("Onboarding account identity changed.")
    result.eligibility.require_current(discord_user_id=discord_user_id, now=now)
    pending = AccountConnection.pending(
        connection_id=challenge.connection_id,
        discord_user_id=discord_user_id,
        signer_address=challenge.signer_address,
        account_wallet_address=challenge.account_wallet_address,
        wallet_type=challenge.wallet_type,
        created_at=challenge.created_at,
        expires_at=challenge.expires_at,
    )
    return pending.mark_verified(discord_user_id=discord_user_id, now=now)
