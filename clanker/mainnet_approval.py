"""One-time Discord approval records for exact Clanker mainnet intents."""
import secrets
import time
from dataclasses import asdict, dataclass
from typing import Any
from .mainnet_operations import MainnetOperationIntent

APPROVAL_LIFETIME_SECONDS = 10 * 60
MAINNET_ACKNOWLEDGEMENT = "LAUNCH ON BASE MAINNET"

@dataclass(frozen=True, slots=True)
class MainnetOperationApproval:
    requester_id: int
    intent_fingerprint: str
    approved_at: int
    expires_at: int
    discord_confirmed: bool
    consumed_at: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MainnetOperationApproval":
        return cls(
            requester_id=int(data["requester_id"]),
            intent_fingerprint=str(data["intent_fingerprint"]),
            approved_at=int(data["approved_at"]),
            expires_at=int(data["expires_at"]),
            discord_confirmed=data.get("discord_confirmed") is True,
            consumed_at=int(data["consumed_at"]) if data.get("consumed_at") is not None else None,
        )

def create_mainnet_approval(
    intent: MainnetOperationIntent, requester_id: int, *,
    discord_confirmed: bool, now: int | None = None,
) -> MainnetOperationApproval:
    timestamp = int(time.time() if now is None else now)
    if int(requester_id) != intent.requester_id:
        raise ValueError("The approving user does not own this mainnet intent.")
    if discord_confirmed is not True:
        raise ValueError("Discord confirmation is required for this mainnet intent.")
    expires_at = min(intent.expires_at, timestamp + APPROVAL_LIFETIME_SECONDS)
    if timestamp < intent.created_at or expires_at <= timestamp:
        raise ValueError("The mainnet intent is outside its approval window.")
    return MainnetOperationApproval(
        int(requester_id), intent.fingerprint, timestamp, expires_at, True
    )

def consume_mainnet_approval(
    approval: MainnetOperationApproval, intent: MainnetOperationIntent, *,
    requester_id: int, now: int | None = None,
) -> MainnetOperationApproval:
    timestamp = int(time.time() if now is None else now)
    if approval.consumed_at is not None:
        raise ValueError("The protected mainnet approval was already consumed.")
    if timestamp < approval.approved_at or timestamp >= approval.expires_at:
        raise ValueError("The protected mainnet approval has expired.")
    if approval.requester_id != int(requester_id) or intent.requester_id != int(requester_id):
        raise ValueError("The protected mainnet approval belongs to another user.")
    if not secrets.compare_digest(approval.intent_fingerprint, intent.fingerprint):
        raise ValueError("The protected mainnet approval fingerprint changed.")
    return MainnetOperationApproval(
        approval.requester_id, approval.intent_fingerprint, approval.approved_at,
        approval.expires_at, approval.discord_confirmed, timestamp,
    )
