import hashlib
import hmac
import json
import re
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


def _validated_mainnet_approval_result(result: dict) -> dict:
    """Validate the relay result before it can become approval evidence."""
    if not isinstance(result, dict) or result.get("status") != "approved":
        raise RuntimeError("The mainnet approval relay returned an invalid result")
    fingerprint = str(result.get("fingerprint") or "")
    intent_id = str(result.get("intent_id") or "")
    try:
        requester_id = int(result.get("requester_id", 0))
        approved_at = int(result.get("approved_at", 0))
    except (TypeError, ValueError) as exc:
        raise RuntimeError("The mainnet approval relay returned an invalid result") from exc
    if (
        not re.fullmatch(r"[a-f0-9]{64}", fingerprint)
        or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", intent_id)
        or requester_id <= 0
        or approved_at <= 0
    ):
        raise RuntimeError("The mainnet approval relay returned an invalid result")
    return {
        "fingerprint": fingerprint,
        "intent_id": intent_id,
        "requester_id": requester_id,
        "approved_at": approved_at,
    }


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

    async def poll_tokenfactory_result(self, handle: str) -> dict | None:
        """Poll one external TokenFactory handoff result through authenticated HTTPS."""

        status = await self.recovery_relay_status()
        if not status["configured"]:
            raise RuntimeError("The one-time relay is not configured")
        if not handle or len(handle) > 128:
            raise RuntimeError("The TokenFactory result handle is invalid")
        tokens = await self.bot.get_shared_api_tokens(RECOVERY_RELAY_TOKEN_NAMESPACE)
        secret = str(tokens.get("secret") or "").strip()
        payload = {
            "operation": "poll",
            "handoff_digest": hashlib.sha256(handle.encode("utf-8")).hexdigest(),
        }
        body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        timestamp = int(time.time())
        nonce = secrets.token_urlsafe(24)
        path = "/api/tokenfactory-result.php"
        canonical = "\n".join((
            "v1", str(timestamp), nonce, "POST", path, hashlib.sha256(body).hexdigest(),
        ))
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-SickWallet-Timestamp": str(timestamp),
            "X-SickWallet-Nonce": nonce,
            "X-SickWallet-Signature": hmac.new(
                secret.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256
            ).hexdigest(),
        }
        timeout = aiohttp.ClientTimeout(total=RECOVERY_RELAY_TIMEOUT_SECONDS)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    f"{status['approval_base_url']}{path}", data=body, headers=headers
                ) as response:
                    raw = await response.content.read(RECOVERY_RELAY_MAX_RESPONSE_BYTES + 1)
                    if len(raw) > RECOVERY_RELAY_MAX_RESPONSE_BYTES:
                        raise RuntimeError("The TokenFactory relay returned too much data")
                    if response.status == 204:
                        return None
                    result = json.loads(raw.decode("utf-8"))
                    if response.status != 200 or result.get("status") != "submitted":
                        raise RuntimeError("The TokenFactory relay rejected the poll")
                    return {
                        "transaction_hash": str(result["transaction_hash"]),
                        "recipient": str(result["recipient"]),
                    }
        except (aiohttp.ClientError, TimeoutError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("The TokenFactory relay could not be reached") from exc

    async def poll_mainnet_approval_result(self, handle: str) -> dict | None:
        """Consume one authenticated protected-mainnet approval result."""
        status = await self.recovery_relay_status()
        if not status["configured"]:
            raise RuntimeError("The one-time relay is not configured")
        if not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", str(handle or "")):
            raise RuntimeError("The mainnet approval result handle is invalid")
        tokens = await self.bot.get_shared_api_tokens(RECOVERY_RELAY_TOKEN_NAMESPACE)
        secret = str(tokens.get("secret") or "").strip()
        payload = {
            "operation": "poll",
            "handoff_digest": hashlib.sha256(handle.encode("utf-8")).hexdigest(),
        }
        body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        timestamp = int(time.time())
        nonce = secrets.token_urlsafe(24)
        path = "/api/mainnet-approval-result.php"
        canonical = "\n".join((
            "v1", str(timestamp), nonce, "POST", path, hashlib.sha256(body).hexdigest(),
        ))
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-SickWallet-Timestamp": str(timestamp),
            "X-SickWallet-Nonce": nonce,
            "X-SickWallet-Signature": hmac.new(
                secret.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256
            ).hexdigest(),
        }
        timeout = aiohttp.ClientTimeout(total=RECOVERY_RELAY_TIMEOUT_SECONDS)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    f"{status['approval_base_url']}{path}", data=body, headers=headers
                ) as response:
                    raw = await response.content.read(
                        RECOVERY_RELAY_MAX_RESPONSE_BYTES + 1
                    )
                    if len(raw) > RECOVERY_RELAY_MAX_RESPONSE_BYTES:
                        raise RuntimeError(
                            "The mainnet approval relay returned too much data"
                        )
                    if response.status == 204:
                        return None
                    result = json.loads(raw.decode("utf-8"))
                    if response.status != 200:
                        raise RuntimeError(
                            "The mainnet approval relay rejected the poll"
                        )
                    return _validated_mainnet_approval_result(result)
        except (
            aiohttp.ClientError, TimeoutError, UnicodeDecodeError, json.JSONDecodeError
        ) as exc:
            raise RuntimeError(
                "The mainnet approval relay could not be reached"
            ) from exc
