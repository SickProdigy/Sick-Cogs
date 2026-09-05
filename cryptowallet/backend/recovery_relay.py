import hashlib
import hmac
import json
import secrets
import time
from urllib.parse import urlparse

import aiohttp


RECOVERY_RELAY_TOKEN_NAMESPACE = "cryptowallet_relay"
RECOVERY_RELAY_PATH = "/api/recovery-handoff.php"
RECOVERY_RELAY_TIMEOUT_SECONDS = 15
RECOVERY_RELAY_MAX_RESPONSE_BYTES = 16 * 1024


def _relay_signature(secret: str, timestamp: int, nonce: str, body: bytes) -> str:
    canonical = "\n".join((
        "v1", str(timestamp), nonce, "POST", RECOVERY_RELAY_PATH,
        hashlib.sha256(body).hexdigest(),
    ))
    return hmac.new(
        secret.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256
    ).hexdigest()


class RecoveryRelayMixin:
    """Register one-time recovery handoffs through the public HTTPS relay."""

    async def recovery_relay_status(self) -> dict:
        tokens = await self.bot.get_shared_api_tokens(RECOVERY_RELAY_TOKEN_NAMESPACE)
        secret = str(tokens.get("secret") or "").strip()
        approval_base_url = str(await self.config.approval_base_url() or "").rstrip("/")
        parsed = urlparse(approval_base_url)
        configured = (
            parsed.scheme == "https"
            and bool(parsed.netloc)
            and len(secret) >= 32
            and len(secret) <= 512
        )
        return {"configured": configured, "approval_base_url": approval_base_url}

    async def register_recovery_handoff(
        self, jwt_token: str, expires_at: int
    ) -> str:
        status = await self.recovery_relay_status()
        if not status["configured"]:
            raise RuntimeError("The one-time recovery relay is not configured")
        now = int(time.time())
        if expires_at <= now or expires_at > now + 5 * 60:
            raise RuntimeError("The recovery handoff expiry is invalid")
        if not jwt_token or len(jwt_token) > 16 * 1024:
            raise RuntimeError("The recovery handoff token is invalid")
        tokens = await self.bot.get_shared_api_tokens(RECOVERY_RELAY_TOKEN_NAMESPACE)
        secret = str(tokens.get("secret") or "").strip()
        handle = secrets.token_urlsafe(32)
        payload = {
            "operation": "register",
            "handoff_digest": hashlib.sha256(handle.encode("utf-8")).hexdigest(),
            "jwt": jwt_token,
            "expires_at": expires_at,
        }
        body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        timestamp = int(time.time())
        nonce = secrets.token_urlsafe(24)
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-SickWallet-Timestamp": str(timestamp),
            "X-SickWallet-Nonce": nonce,
            "X-SickWallet-Signature": _relay_signature(
                secret, timestamp, nonce, body
            ),
        }
        url = f"{status['approval_base_url']}{RECOVERY_RELAY_PATH}"
        timeout = aiohttp.ClientTimeout(total=RECOVERY_RELAY_TIMEOUT_SECONDS)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, data=body, headers=headers) as response:
                    raw = await response.content.read(
                        RECOVERY_RELAY_MAX_RESPONSE_BYTES + 1
                    )
                    if len(raw) > RECOVERY_RELAY_MAX_RESPONSE_BYTES:
                        raise RuntimeError("The recovery relay returned too much data")
                    try:
                        result = json.loads(raw.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                        raise RuntimeError(
                            "The recovery relay returned an invalid response"
                        ) from exc
                    if response.status != 201 or result.get("status") != "registered":
                        raise RuntimeError("The recovery relay rejected the handoff")
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise RuntimeError("The recovery relay could not be reached") from exc
        return handle
