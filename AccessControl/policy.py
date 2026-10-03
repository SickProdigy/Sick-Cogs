from dataclasses import dataclass
from typing import Iterable, Mapping, Optional


VALID_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789._:-")


@dataclass(frozen=True)
class AccessDecision:
    allowed: bool
    reason: str = ""
    source: str = ""
    capability: Optional[str] = None


def normalize_capability(value: str, *, allow_wildcard: bool = False) -> str:
    value = value.strip().lower().replace(" ", "-")
    if allow_wildcard and value == "*":
        return value
    if not value or len(value) > 80 or not set(value) <= VALID_CHARS:
        raise ValueError(
            "Capability names must contain 1-80 letters, numbers, dots, colons, "
            "underscores, or dashes."
        )
    return value


def normalize_target(target_type: str, target: str) -> str:
    target_type = target_type.strip().lower()
    target = " ".join(target.strip().lower().split())
    if target_type not in {"command", "cog"}:
        raise ValueError("Target type must be command or cog.")
    if not target or len(target) > 200:
        raise ValueError("Target names must contain 1-200 characters.")
    return f"{target_type}:{target}"


def command_capability(
    command_name: str, cog_name: Optional[str], mappings: Mapping[str, str]
) -> Optional[str]:
    parts = command_name.lower().split()
    for length in range(len(parts), 0, -1):
        found = mappings.get("command:" + " ".join(parts[:length]))
        if found:
            return found
    return mappings.get("cog:" + cog_name.lower()) if cog_name else None


def normalize_entitlement(record: object) -> Optional[dict]:
    if not isinstance(record, Mapping):
        return None
    capabilities = record.get("capabilities")
    if not isinstance(capabilities, list):
        return None
    try:
        normalized = sorted(
            {normalize_capability(str(item), allow_wildcard=True) for item in capabilities}
        )
        expires_at = int(record.get("expires_at", 0) or 0)
        sponsor = record.get("sponsor_user_id")
        sponsor_user_id = int(sponsor) if sponsor else None
        issued_at = int(record.get("issued_at", 0) or 0)
        issued_by = int(record.get("issued_by", 0) or 0)
    except (TypeError, ValueError):
        return None
    if expires_at < 0:
        return None
    return {
        "capabilities": normalized,
        "expires_at": expires_at,
        "sponsor_user_id": sponsor_user_id,
        "issued_at": issued_at,
        "issued_by": issued_by,
    }


def evaluate_capability(
    *,
    capability: str,
    user_id: int,
    role_ids: Iterable[int],
    grants: Mapping,
    entitlement: Optional[Mapping],
    now: int,
    sponsor_valid: bool,
) -> AccessDecision:
    capability = normalize_capability(capability)
    grant = grants.get(capability, {}) if isinstance(grants, Mapping) else {}
    if not isinstance(grant, Mapping):
        grant = {}
    if int(user_id) in {int(value) for value in grant.get("users", [])}:
        return AccessDecision(True, source="user grant", capability=capability)
    roles = {int(value) for value in role_ids}
    if roles.intersection(int(value) for value in grant.get("roles", [])):
        return AccessDecision(True, source="role grant", capability=capability)
    entitlement = normalize_entitlement(entitlement)
    if entitlement is None:
        return AccessDecision(
            False, "This server does not have that feature entitlement.", capability=capability
        )
    if entitlement["expires_at"] and entitlement["expires_at"] <= now:
        return AccessDecision(
            False, "This server's feature entitlement has expired.", capability=capability
        )
    if capability not in entitlement["capabilities"] and "*" not in entitlement["capabilities"]:
        return AccessDecision(
            False, "This server does not have that feature entitlement.", capability=capability
        )
    if not sponsor_valid:
        return AccessDecision(
            False, "This server's sponsoring VIP is no longer eligible.", capability=capability
        )
    return AccessDecision(True, source="guild entitlement", capability=capability)
