import time

from redbot.core import Config

from .models import IntentStatus
from .networks import DEFAULT_NETWORK

CONFIG_IDENTIFIER = 9365048217
MAX_STORED_INTENTS = 25


def create_config(cog) -> Config:
    """Create and register the cog's backward-compatible configuration."""
    config = Config.get_conf(cog, identifier=CONFIG_IDENTIFIER, force_registration=True)
    config.register_global(
        deployment_id=None,
        approval_base_url=None,
        provider="unconfigured",
        default_network=DEFAULT_NETWORK,
        companion_enabled=False,
        companion_host="127.0.0.1",
        companion_port=8787,
    )
    config.register_user(profile=None, intents={}, approval_sessions={})
    return config


class WalletConfigMixin:
    """Stored-data helpers shared by the command and companion layers."""

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
