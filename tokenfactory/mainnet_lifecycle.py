import secrets
from dataclasses import asdict, dataclass
from typing import Any, Mapping

from .mainnet_review import MainnetTokenReview

HASH_FIELDS = ("user_operation_hash", "transaction_hash")
ACTIVE_STATES = {"prepared", "processing", "submitted", "uncertain", "timed_out"}
TERMINAL_STATES = {"confirmed", "failed", "dropped", "replaced"}
ALL_STATES = ACTIVE_STATES | TERMINAL_STATES
ALLOWED_TRANSITIONS = {
    "prepared": {"processing"},
    "processing": {"submitted", "uncertain"},
    "submitted": {"submitted", "uncertain", "timed_out", "confirmed", "failed", "dropped", "replaced"},
    "uncertain": {"processing", "submitted", "timed_out", "confirmed", "failed", "dropped", "replaced"},
    "timed_out": {"processing", "submitted", "uncertain", "confirmed", "failed", "dropped", "replaced"},
    "confirmed": set(),
    "failed": set(),
    "dropped": set(),
    "replaced": set(),
}


def _hash(value: Any, *, required: bool = False) -> str | None:
    normalized = str(value or "").lower()
    if not normalized and not required:
        return None
    if (
        len(normalized) != 66
        or not normalized.startswith("0x")
        or any(character not in "0123456789abcdef" for character in normalized[2:])
    ):
        raise ValueError("The mainnet lifecycle contains an invalid hash.")
    return normalized


@dataclass(frozen=True, slots=True)
class MainnetCanaryLifecycle:
    review_fingerprint: str
    owner_discord_id: int
    request_id: str
    attempt_id: str
    status: str
    created_at: int
    updated_at: int
    provider_status: str | None = None
    user_operation_hash: str | None = None
    transaction_hash: str | None = None
    replacement_transaction_hash: str | None = None
    block_number: int | None = None
    failure_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "MainnetCanaryLifecycle":
        status = str(data["status"])
        if status not in ALL_STATES:
            raise ValueError("The mainnet canary lifecycle status is invalid.")
        attempt_id = str(data["attempt_id"])
        if not attempt_id or len(attempt_id) > 128:
            raise ValueError("The mainnet canary attempt ID is invalid.")
        return cls(
            review_fingerprint=_hash(data["review_fingerprint"], required=True),
            owner_discord_id=int(data["owner_discord_id"]),
            request_id=_hash(data["request_id"], required=True),
            attempt_id=attempt_id,
            status=status,
            created_at=int(data["created_at"]),
            updated_at=int(data["updated_at"]),
            provider_status=(
                str(data["provider_status"]) if data.get("provider_status") else None
            ),
            user_operation_hash=_hash(data.get("user_operation_hash")),
            transaction_hash=_hash(data.get("transaction_hash")),
            replacement_transaction_hash=_hash(
                data.get("replacement_transaction_hash")
            ),
            block_number=(
                int(data["block_number"]) if data.get("block_number") is not None
                else None
            ),
            failure_reason=(
                str(data["failure_reason"]) if data.get("failure_reason") else None
            ),
        )


def create_mainnet_canary_lifecycle(
    review: MainnetTokenReview,
    attempt_id: str,
    *,
    now: int,
) -> MainnetCanaryLifecycle:
    if not attempt_id or len(str(attempt_id)) > 128:
        raise ValueError("A bounded mainnet canary attempt ID is required.")
    return MainnetCanaryLifecycle(
        review_fingerprint=review.fingerprint,
        owner_discord_id=review.owner_discord_id,
        request_id=review.request_id,
        attempt_id=str(attempt_id),
        status="prepared",
        created_at=int(now),
        updated_at=int(now),
    )


def transition_mainnet_canary_lifecycle(
    current: MainnetCanaryLifecycle,
    status: str,
    *,
    now: int,
    provider_status: str | None = None,
    user_operation_hash: str | None = None,
    transaction_hash: str | None = None,
    replacement_transaction_hash: str | None = None,
    block_number: int | None = None,
    failure_reason: str | None = None,
) -> MainnetCanaryLifecycle:
    target = str(status)
    if target not in ALLOWED_TRANSITIONS[current.status]:
        raise ValueError(
            f"Mainnet canary lifecycle cannot move from {current.status} to {target}."
        )
    operation_hash = _hash(user_operation_hash) or current.user_operation_hash
    transaction = _hash(transaction_hash) or current.transaction_hash
    replacement = (
        _hash(replacement_transaction_hash)
        or current.replacement_transaction_hash
    )
    if (
        current.user_operation_hash
        and operation_hash != current.user_operation_hash
    ):
        raise ValueError("The provider operation hash changed during reconciliation.")
    if current.transaction_hash and transaction != current.transaction_hash:
        raise ValueError("The transaction hash changed during reconciliation.")
    if target in {"submitted", "confirmed"} and not (operation_hash or transaction):
        raise ValueError("Submitted mainnet state requires a provider identifier.")
    if target == "confirmed" and (not transaction or block_number is None):
        raise ValueError("Confirmed mainnet state requires a transaction and block.")
    if target == "replaced" and not replacement:
        raise ValueError("Replaced mainnet state requires the replacement transaction.")
    if target in {"failed", "dropped", "replaced"} and not failure_reason:
        raise ValueError("Terminal mainnet state requires a bounded reason.")
    reason = str(failure_reason) if failure_reason else current.failure_reason
    if reason and len(reason) > 240:
        raise ValueError("The mainnet lifecycle reason is too long.")
    return MainnetCanaryLifecycle(
        review_fingerprint=current.review_fingerprint,
        owner_discord_id=current.owner_discord_id,
        request_id=current.request_id,
        attempt_id=current.attempt_id,
        status=target,
        created_at=current.created_at,
        updated_at=int(now),
        provider_status=(
            str(provider_status) if provider_status is not None
            else current.provider_status
        ),
        user_operation_hash=operation_hash,
        transaction_hash=transaction,
        replacement_transaction_hash=replacement,
        block_number=(
            int(block_number) if block_number is not None
            else current.block_number
        ),
        failure_reason=reason,
    )


def assert_same_mainnet_lifecycle(
    stored: MainnetCanaryLifecycle,
    review_fingerprint: str,
    attempt_id: str,
) -> None:
    if (
        not secrets.compare_digest(
            stored.review_fingerprint, str(review_fingerprint)
        )
        or not secrets.compare_digest(stored.attempt_id, str(attempt_id))
    ):
        raise ValueError("The mainnet canary lifecycle binding changed.")
