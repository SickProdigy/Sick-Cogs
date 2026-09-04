import hashlib
import hmac
import secrets
import time


PAIRING_LIFETIME_SECONDS = 10 * 60
PAIRING_TOKEN_NAMESPACE = "cryptowallet_companion"
REQUEST_WINDOW_SECONDS = 5 * 60
MAX_STORED_NONCES = 500


def companion_signature(
    credential: str, timestamp: str, nonce: str, method: str, path: str, body: bytes
) -> str:
    """Return the v1 website-server request signature."""
    body_digest = hashlib.sha256(body).hexdigest()
    canonical = "\n".join(("v1", timestamp, nonce, method.upper(), path, body_digest))
    return hmac.new(
        credential.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256
    ).hexdigest()


class CompanionPairingMixin:
    """Manage one-time website-server pairing and durable credentials."""

    @staticmethod
    def _pairing_digest(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    async def begin_companion_pairing(self) -> tuple[str, int]:
        code = secrets.token_urlsafe(24)
        expires_at = int(time.time()) + PAIRING_LIFETIME_SECONDS
        await self.config.pairing_code_digest.set(self._pairing_digest(code))
        await self.config.pairing_expires_at.set(expires_at)
        return code, expires_at

    async def cancel_companion_pairing(self) -> None:
        await self.config.pairing_code_digest.set(None)
        await self.config.pairing_expires_at.set(0)

    async def complete_companion_pairing(self, code: str) -> dict | None:
        async with self.pairing_lock:
            expected = await self.config.pairing_code_digest()
            expires_at = await self.config.pairing_expires_at()
            if (
                not expected
                or expires_at <= int(time.time())
                or not hmac.compare_digest(expected, self._pairing_digest(code))
            ):
                return None
            installation_id = secrets.token_urlsafe(18)
            credential = secrets.token_urlsafe(32)
            await self.bot.set_shared_api_tokens(
                PAIRING_TOKEN_NAMESPACE,
                installation_id=installation_id,
                credential=credential,
            )
            await self.config.paired_at.set(int(time.time()))
            await self.cancel_companion_pairing()
        return {
            "installation_id": installation_id,
            "credential": credential,
            "deployment_id": await self.config.deployment_id(),
            "discord_application_id": self.discord_application_id(),
        }

    async def companion_pairing_status(self) -> dict:
        tokens = await self.bot.get_shared_api_tokens(PAIRING_TOKEN_NAMESPACE)
        return {
            "paired": bool(tokens.get("installation_id") and tokens.get("credential")),
            "installation_id": tokens.get("installation_id"),
            "paired_at": await self.config.paired_at(),
            "pairing_expires_at": await self.config.pairing_expires_at(),
        }

    async def unpair_companion(self) -> None:
        await self.bot.set_shared_api_tokens(
            PAIRING_TOKEN_NAMESPACE, installation_id="", credential=""
        )
        await self.config.paired_at.set(0)
        await self.config.companion_nonces.set({})
        await self.cancel_companion_pairing()

    async def verify_companion_request(self, request) -> tuple[bool, str]:
        """Authenticate one website-server request and atomically consume its nonce."""
        installation_id = request.headers.get("X-SickWallet-Installation", "")
        timestamp = request.headers.get("X-SickWallet-Timestamp", "")
        nonce = request.headers.get("X-SickWallet-Nonce", "")
        signature = request.headers.get("X-SickWallet-Signature", "")
        if not timestamp.isdigit() or not 16 <= len(nonce) <= 128 or len(signature) != 64:
            return False, "invalid_authentication"
        now = int(time.time())
        request_time = int(timestamp)
        if abs(now - request_time) > REQUEST_WINDOW_SECONDS:
            return False, "request_expired"
        if request.query_string:
            return False, "unsigned_query"
        tokens = await self.bot.get_shared_api_tokens(PAIRING_TOKEN_NAMESPACE)
        expected_installation = tokens.get("installation_id", "")
        credential = tokens.get("credential", "")
        if not expected_installation or not credential:
            return False, "not_paired"
        if not hmac.compare_digest(expected_installation, installation_id):
            return False, "invalid_authentication"
        body = await request.read()
        expected = companion_signature(
            credential, timestamp, nonce, request.method, request.path, body
        )
        if not hmac.compare_digest(expected, signature.casefold()):
            return False, "invalid_authentication"
        async with self.pairing_lock:
            async with self.config.companion_nonces() as nonces:
                for stored_nonce, seen_at in list(nonces.items()):
                    if int(seen_at) < now - REQUEST_WINDOW_SECONDS:
                        del nonces[stored_nonce]
                if nonce in nonces:
                    return False, "request_replayed"
                nonces[nonce] = now
                if len(nonces) > MAX_STORED_NONCES:
                    oldest = sorted(nonces, key=nonces.get)[: len(nonces) - MAX_STORED_NONCES]
                    for stored_nonce in oldest:
                        del nonces[stored_nonce]
        return True, "ok"
