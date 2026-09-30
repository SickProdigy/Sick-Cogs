import secrets
import time
from dataclasses import asdict, dataclass
from hashlib import sha256
from typing import Any

from .constants import BASE_MAINNET_CHAIN_ID, BASE_MAINNET_NETWORK_KEY
from .mainnet_review import MainnetTokenReview
from .network_manifest import load_network_manifest
from .policy import validate_mainnet_limits
from .validation import normalize_owner_address

APPROVAL_LIFETIME_SECONDS = 10 * 60


@dataclass(frozen=True, slots=True)
class MainnetCanaryApproval:
    owner_discord_id: int
    review_fingerprint: str
    approved_at: int
    expires_at: int
    discord_confirmed: bool
    consumed_at: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MainnetCanaryApproval":
        return cls(
            owner_discord_id=int(data["owner_discord_id"]),
            review_fingerprint=str(data["review_fingerprint"]),
            approved_at=int(data["approved_at"]),
            expires_at=int(data["expires_at"]),
            discord_confirmed=data.get("discord_confirmed") is True,
            consumed_at=(
                None if data.get("consumed_at") is None
                else int(data["consumed_at"])
            ),
        )


def create_mainnet_canary_approval(
    review: MainnetTokenReview,
    owner_discord_id: int,
    *,
    discord_confirmed: bool,
    now: int | None = None,
) -> MainnetCanaryApproval:
    timestamp = int(time.time() if now is None else now)
    if int(owner_discord_id) != review.owner_discord_id:
        raise ValueError("The approving owner does not match the reviewed canary.")
    if discord_confirmed is not True:
        raise ValueError(
            "Discord confirmation is required for the mainnet canary."
        )
    return MainnetCanaryApproval(
        owner_discord_id=int(owner_discord_id),
        review_fingerprint=review.fingerprint,
        approved_at=timestamp,
        expires_at=timestamp + APPROVAL_LIFETIME_SECONDS,
        discord_confirmed=True,
    )


def consume_mainnet_canary_approval(
    approval: MainnetCanaryApproval,
    review_fingerprint: str,
    *,
    now: int | None = None,
) -> MainnetCanaryApproval:
    """Claim one exact approval once before entering provider submission."""

    timestamp = int(time.time() if now is None else now)
    if approval.consumed_at is not None:
        raise ValueError("The protected canary approval was already consumed.")
    if timestamp < approval.approved_at or timestamp >= approval.expires_at:
        raise ValueError("The protected canary approval has expired.")
    if not secrets.compare_digest(
        approval.review_fingerprint, str(review_fingerprint)
    ):
        raise ValueError("The protected canary approval fingerprint changed.")
    return MainnetCanaryApproval(
        owner_discord_id=approval.owner_discord_id,
        review_fingerprint=approval.review_fingerprint,
        approved_at=approval.approved_at,
        expires_at=approval.expires_at,
        discord_confirmed=approval.discord_confirmed,
        consumed_at=timestamp,
    )


def revalidate_mainnet_pre_submission(
    review: MainnetTokenReview,
    approval: MainnetCanaryApproval,
    operation: dict[str, Any],
    *,
    owner_discord_id: int,
    wallet_profile_id: str,
    signer_address: str,
    live_chain_id: int,
    live_factory_code_hash: str,
    authorization_active: bool,
    operation_state: str,
    limits: dict[str, Any],
    now: int | None = None,
) -> dict[str, Any]:
    """Revalidate every immutable canary binding immediately before submission."""

    timestamp = int(time.time() if now is None else now)
    reviewed_limits = validate_mainnet_limits(limits)
    manifest = load_network_manifest(BASE_MAINNET_NETWORK_KEY)
    if approval.discord_confirmed is not True or approval.consumed_at is not None:
        raise ValueError(
            "The protected canary approval is missing or already consumed."
        )
    if timestamp < approval.approved_at or timestamp >= approval.expires_at:
        raise ValueError("The protected canary approval has expired.")
    if (
        int(owner_discord_id) != review.owner_discord_id
        or approval.owner_discord_id != review.owner_discord_id
    ):
        raise ValueError("The approving owner changed after review.")
    if not secrets.compare_digest(
        approval.review_fingerprint, review.fingerprint
    ):
        raise ValueError("The approved canary fingerprint no longer matches.")
    if str(wallet_profile_id) != review.wallet_profile_id:
        raise ValueError("The wallet profile changed after review.")
    if normalize_owner_address(signer_address) != review.signer_address:
        raise ValueError("The signer changed after review.")
    if live_chain_id != BASE_MAINNET_CHAIN_ID:
        raise ValueError("The live chain is not Base mainnet.")
    if (
        operation.get("network") != BASE_MAINNET_NETWORK_KEY
        or operation.get("chain_id") != BASE_MAINNET_CHAIN_ID
    ):
        raise ValueError("The operation network changed after review.")
    if str(operation.get("to") or "").lower() != review.target_factory:
        raise ValueError("The target factory changed after review.")
    if normalize_owner_address(operation.get("recipient")) != review.recipient:
        raise ValueError("The token recipient changed after review.")
    if str(operation.get("request_id") or "").lower() != review.request_id:
        raise ValueError("The request ID changed after review.")
    data = str(operation.get("data") or "")
    try:
        calldata_hash = "0x" + sha256(bytes.fromhex(data[2:])).hexdigest()
    except (TypeError, ValueError) as exc:
        raise ValueError("The operation calldata is invalid.") from exc
    if not data.startswith("0x") or calldata_hash != review.calldata_sha256:
        raise ValueError("The operation calldata changed after review.")
    if (
        int(operation.get("gas_limit", -1)) != review.gas_limit
        or review.gas_limit != reviewed_limits["token_gas_limit"]
    ):
        raise ValueError("The operation gas limit changed after review.")
    if (
        review.max_gas_fee_wei <= 0
        or review.max_gas_fee_wei > reviewed_limits["max_gas_fee_wei"]
    ):
        raise ValueError("The approved gas maximum violates current policy.")
    if int(operation.get("value_wei", -1)) != 0 or review.native_value_wei != 0:
        raise ValueError("The operation native value must remain zero.")
    expected_hash = str(manifest["factoryRuntimeCodeHash"]).lower()
    if str(live_factory_code_hash or "").lower() != expected_hash:
        raise ValueError(
            "The live factory code hash does not match the reviewed artifact."
        )
    if authorization_active is not True:
        raise ValueError("The mainnet signer authorization is not active.")
    if operation_state != "not-created":
        raise ValueError("A provider operation or transaction already exists.")
    return {
        "review_fingerprint": review.fingerprint,
        "approval_expires_at": approval.expires_at,
        "factory_code_hash": expected_hash,
        "operation_state": "not-created",
    }
