"""Versioned TokenFactory-only mainnet terms acceptance records."""

import secrets
import time
from typing import Any

TOKENFACTORY_MAINNET_TERMS_VERSION = "2026-09-30.1"
TOKENFACTORY_TERMS_PRODUCT = "tokenfactory"
_ACCEPTANCE_KEYS = {
    "product", "version", "discord_user_id", "accepted_at", "acceptance_id",
}


def create_tokenfactory_terms_acceptance(
    discord_user_id: int, *, now: int | None = None, acceptance_id: str | None = None,
) -> dict[str, Any]:
    user_id = int(discord_user_id)
    timestamp = int(time.time() if now is None else now)
    identifier = str(
        secrets.token_urlsafe(18) if acceptance_id is None else acceptance_id
    ).strip()
    if user_id <= 0 or timestamp <= 0 or not identifier or len(identifier) > 128:
        raise ValueError("TokenFactory terms acceptance identifiers are invalid.")
    return {
        "product": TOKENFACTORY_TERMS_PRODUCT,
        "version": TOKENFACTORY_MAINNET_TERMS_VERSION,
        "discord_user_id": user_id,
        "accepted_at": timestamp,
        "acceptance_id": identifier,
    }


def is_current_tokenfactory_terms_acceptance(record: Any, discord_user_id: int) -> bool:
    if not isinstance(record, dict) or set(record) != _ACCEPTANCE_KEYS:
        return False
    try:
        user_id = int(discord_user_id)
        accepted_user = int(record["discord_user_id"])
        accepted_at = int(record["accepted_at"])
    except (TypeError, ValueError):
        return False
    return (
        user_id > 0 and accepted_user == user_id and accepted_at > 0
        and record["product"] == TOKENFACTORY_TERMS_PRODUCT
        and record["version"] == TOKENFACTORY_MAINNET_TERMS_VERSION
        and isinstance(record["acceptance_id"], str)
        and 0 < len(record["acceptance_id"]) <= 128
    )
