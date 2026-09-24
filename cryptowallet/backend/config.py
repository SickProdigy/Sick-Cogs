import time

from redbot.core import Config

from ..core.models import IntentStatus
from ..core.networks import DEFAULT_NETWORK

CONFIG_IDENTIFIER = 9365048217
MAX_STORED_INTENTS = 25
MAINNET_ENABLE_ACKNOWLEDGEMENT = "I UNDERSTAND REAL FUNDS MAY BE PERMANENTLY LOST"
BASE_MAINNET_POLICY_DEFAULT = {
    "enabled": False,
    "paused": True,
    "owner_only": True,
    "experimental": True,
    "enabled_by": None,
    "enabled_at": 0,
    "limits_atomic": {
        "per_transaction": "0",
        "per_user_day": "0",
        "installation_day": "0",
    },
    "capabilities": {
        "balance": False,
        "send": False,
        "history": False,
        "transaction_lookup": False,
        "delegation": False,
        "recovery": False,
        "export": False,
        "sponsorship": False,
    },
}


def base_mainnet_operation_allowed(
    policy: dict, capability: str, *, actor_is_owner: bool, value_atomic: int,
    user_daily_atomic: int = 0, installation_daily_atomic: int = 0,
) -> tuple[bool, str]:
    """Evaluate every production gate; missing or malformed state denies access."""
    if not isinstance(policy, dict) or not policy.get("enabled"):
        return False, "Base mainnet is disabled."
    if policy.get("paused", True):
        return False, "Base mainnet is emergency-paused."
    if not policy.get("owner_only", True) or not actor_is_owner:
        return False, "Base mainnet experimental access is bot-owner-only."
    if not policy.get("experimental", False):
        return False, "Base mainnet experimental labeling is invalid."
    capabilities = policy.get("capabilities")
    if not isinstance(capabilities, dict) or not capabilities.get(capability, False):
        return False, f"Base mainnet {capability} capability is disabled."
    limits = policy.get("limits_atomic")
    if not isinstance(limits, dict):
        return False, "Base mainnet limits are not configured."
    try:
        per_transaction = int(limits.get("per_transaction", 0))
        per_user_day = int(limits.get("per_user_day", 0))
        installation_day = int(limits.get("installation_day", 0))
        value_atomic = int(value_atomic)
        user_daily_atomic = int(user_daily_atomic)
        installation_daily_atomic = int(installation_daily_atomic)
    except (TypeError, ValueError):
        return False, "Base mainnet limits are invalid."
    if min(per_transaction, per_user_day, installation_day) <= 0:
        return False, "Base mainnet limits must all be positive."
    if value_atomic <= 0 or min(user_daily_atomic, installation_daily_atomic) < 0:
        return False, "Base mainnet accounting values are invalid."
    if value_atomic > per_transaction:
        return False, "Base mainnet per-transaction limit exceeded."
    if user_daily_atomic + value_atomic > per_user_day:
        return False, "Base mainnet per-user daily limit exceeded."
    if installation_daily_atomic + value_atomic > installation_day:
        return False, "Base mainnet installation-wide daily limit exceeded."
    return True, "Base mainnet policy checks passed."


def create_config(cog) -> Config:
    """Create and register the cog's backward-compatible configuration."""
    config = Config.get_conf(cog, identifier=CONFIG_IDENTIFIER, force_registration=True)
    config.register_global(
        deployment_id=None,
        approval_base_url=None,
        provider="unconfigured",
        default_network=DEFAULT_NETWORK,
        provider_paused=False,
        base_mainnet_policy=BASE_MAINNET_POLICY_DEFAULT,
        provider_usage={},
        token_registry={},
        network_emojis={},
        send_limits_atomic={},
        delegation_duration_days=365,
        delegation_max_duration_days=365,
    )
    config.register_user(
        profile=None,
        selected_network=DEFAULT_NETWORK,
        selected_environment="testnet",
        claimed_at=0,
        intents={},
        notifications_enabled=True,
        default_send_asset=None,
        security_locked=False,
        security_locked_at=0,
        security_lock_source=None,
    )
    return config


class WalletConfigMixin:
    """Stored-data helpers shared by wallet command and relay layers."""

    async def expire_and_trim_intents(self, user) -> dict:
        now = int(time.time())
        async with self.config.user(user).intents() as intents:
            for data in intents.values():
                if (
                    data.get("status") == IntentStatus.PENDING.value
                    and int(data.get("expires_at", 0) or 0) <= now
                ):
                    data["status"] = IntentStatus.EXPIRED.value
            ordered = sorted(
                intents.items(),
                key=lambda item: int(item[1].get("created_at", 0) or 0),
                reverse=True,
            )
            intents.clear()
            intents.update(ordered[:MAX_STORED_INTENTS])
            return dict(intents)
