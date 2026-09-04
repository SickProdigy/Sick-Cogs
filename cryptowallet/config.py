import time

from redbot.core import Config

from .models import IntentStatus
from .networks import DEFAULT_NETWORK, NETWORKS

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
        pairing_code_digest=None,
        pairing_expires_at=0,
        paired_at=0,
        companion_nonces={},
        provider_paused=False,
        provider_usage={},
    )
    config.register_user(
        profile=None,
        claimed_at=0,
        intents={},
        approval_sessions={},
        notifications_enabled=True,
    )
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

    async def companion_session_payload(self, session) -> dict:
        """Build the public API representation from authoritative stored state."""
        payload = {
            "version": 1,
            "purpose": session.purpose.value,
            "expires_at": session.expires_at,
            "identity_verified": True,
            "transaction": None,
        }
        if not session.intent_id:
            return payload
        data = await self.config.user_from_id(session.discord_user_id).intents.get_raw(
            session.intent_id, default=None
        )
        if data is None:
            return payload
        network = NETWORKS.get(str(data.get("network") or ""))
        if network is None:
            return payload
        payload["transaction"] = {
            "intent_id": str(data.get("intent_id") or ""),
            "network": network.key,
            "network_name": network.name,
            "chain_id": network.chain_id,
            "native_symbol": network.native_symbol,
            "from_address": str(data.get("from_address") or ""),
            "to_address": str(data.get("to_address") or ""),
            "value_wei": str(data.get("value_wei") or "0"),
            "status": str(data.get("status") or "unknown"),
            "expires_at": int(data.get("expires_at", 0) or 0),
        }
        return payload
