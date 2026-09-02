import hashlib
import hmac
import secrets
import time


PAIRING_LIFETIME_SECONDS = 10 * 60
PAIRING_TOKEN_NAMESPACE = "cryptowallet_companion"


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
        await self.cancel_companion_pairing()
