"""Fail-closed policy records for future protected Polymarket access."""

from dataclasses import dataclass
import re

from .account_connection import AccountConnectionError, WalletType


POLYMARKET_GEOBLOCK_ENDPOINT = "https://polymarket.com/api/geoblock"
SESSION_KEY_LIFETIME_SECONDS = 180 * 24 * 60 * 60
ELIGIBILITY_LIFETIME_SECONDS = 5 * 60
COUNTRY_CODE = re.compile(r"^[A-Z]{2}$")
REGION_CODE = re.compile(r"^[A-Z0-9-]{1,16}$")


@dataclass(frozen=True, slots=True)
class SessionKeyPolicy:
    """Reviewed beta boundary; this policy does not generate or authorize a key."""

    wallet_type: WalletType = WalletType.DEPOSIT_WALLET
    scopes: tuple[str, ...] = ("CLOB",)
    lifetime_seconds: int = SESSION_KEY_LIFETIME_SECONDS
    beta: bool = True
    builder_authorization_required: bool = True
    protected_owner_approval_required: bool = True
    server_secret_store_required: bool = True
    status_confirmation_required: bool = True
    revocation_required: bool = True
    withdrawal_allowed: bool = False
    executable: bool = False


POLYMARKET_SESSION_KEY_POLICY = SessionKeyPolicy()


def validate_session_key_policy(
    policy: SessionKeyPolicy = POLYMARKET_SESSION_KEY_POLICY,
) -> tuple[str, ...]:
    errors = []
    if policy.wallet_type is not WalletType.DEPOSIT_WALLET:
        errors.append("session keys must be limited to Deposit Wallets")
    if policy.scopes != ("CLOB",):
        errors.append("session keys must use only the CLOB scope")
    if policy.lifetime_seconds != SESSION_KEY_LIFETIME_SECONDS:
        errors.append("session-key lifetime must remain 180 days")
    for field in (
        "beta", "builder_authorization_required", "protected_owner_approval_required",
        "server_secret_store_required", "status_confirmation_required", "revocation_required",
    ):
        if not getattr(policy, field):
            errors.append(f"{field} must remain required")
    if policy.withdrawal_allowed:
        errors.append("session-key withdrawals must remain disabled")
    if policy.executable:
        errors.append("session-key execution must remain disabled")
    return tuple(errors)


@dataclass(frozen=True, slots=True)
class EligibilityAttestation:
    """Short-lived result for the protected user's request IP; the IP is never retained."""

    discord_user_id: int
    blocked: bool
    country: str
    region: str
    checked_at: int
    expires_at: int
    source: str = "protected_user_ip"
    endpoint: str = POLYMARKET_GEOBLOCK_ENDPOINT

    def __post_init__(self):
        if self.discord_user_id <= 0:
            raise AccountConnectionError("Eligibility must bind to a Discord user.")
        if not isinstance(self.blocked, bool):
            raise AccountConnectionError("Eligibility blocked state must be explicit.")
        if not COUNTRY_CODE.fullmatch(self.country):
            raise AccountConnectionError("Eligibility country must be an ISO alpha-2 code.")
        if self.region and not REGION_CODE.fullmatch(self.region):
            raise AccountConnectionError("Eligibility region is invalid.")
        if self.source != "protected_user_ip":
            raise AccountConnectionError("Eligibility must use the protected user's IP.")
        if self.endpoint != POLYMARKET_GEOBLOCK_ENDPOINT:
            raise AccountConnectionError("Eligibility endpoint is not the reviewed endpoint.")
        if self.expires_at != self.checked_at + ELIGIBILITY_LIFETIME_SECONDS:
            raise AccountConnectionError("Eligibility must expire after five minutes.")

    def require_current(self, *, discord_user_id: int, now: int) -> None:
        if discord_user_id != self.discord_user_id:
            raise AccountConnectionError("Eligibility belongs to a different Discord user.")
        if now < self.checked_at or now >= self.expires_at:
            raise AccountConnectionError("Eligibility is not current.")
        if self.blocked:
            raise AccountConnectionError("Polymarket reports this location as blocked.")
