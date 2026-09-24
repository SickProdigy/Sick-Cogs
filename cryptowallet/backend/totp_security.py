import base64
import secrets
import time

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from ..core.validation import totp_code, verify_totp_code


TOTP_TOKEN_NAMESPACE = "cryptowallet_totp"
TOTP_STATE_VERSION = 1
TOTP_ENROLLMENT_LABEL = b"cryptowallet-totp-enrollment-v1"


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.b64decode(value + padding, altchars=b"-_", validate=True)


def _load_encryption_key(encoded: str) -> bytes:
    try:
        key = _decode(encoded)
    except (TypeError, ValueError, base64.binascii.Error) as exc:
        raise RuntimeError("CryptoWallet TOTP encryption key is invalid") from exc
    if len(key) != 32:
        raise RuntimeError("CryptoWallet TOTP encryption key must contain 256 bits")
    return key


def _associated_data(deployment_id: str, user_id: int, profile_id: str) -> bytes:
    if not deployment_id or int(user_id) <= 0 or not profile_id:
        raise ValueError("TOTP encryption identity is incomplete")
    return (
        f"cryptowallet-totp:v1:{deployment_id}:{int(user_id)}:{profile_id}"
    ).encode("utf-8")


def protect_totp_secret(
    key: bytes,
    secret: str,
    *,
    deployment_id: str,
    user_id: int,
    profile_id: str,
    enrolled_at: int,
) -> dict:
    """Encrypt one canonical TOTP secret and bind it to a wallet identity."""

    canonical = secret.strip().upper()
    totp_code(canonical, 0)
    nonce = secrets.token_bytes(12)
    ciphertext = AESGCM(key).encrypt(
        nonce,
        canonical.encode("ascii"),
        _associated_data(deployment_id, user_id, profile_id),
    )
    return {
        "version": TOTP_STATE_VERSION,
        "enabled": False,
        "profile_id": profile_id,
        "nonce": _encode(nonce),
        "ciphertext": _encode(ciphertext),
        "enrolled_at": int(enrolled_at),
        "last_counter": None,
    }


def reveal_totp_secret(
    key: bytes,
    state: dict,
    *,
    deployment_id: str,
    user_id: int,
    profile_id: str,
) -> str:
    """Decrypt a TOTP secret only for its bound deployment, user, and profile."""

    if (
        not isinstance(state, dict)
        or state.get("version") != TOTP_STATE_VERSION
        or not secrets.compare_digest(str(state.get("profile_id") or ""), profile_id)
    ):
        raise ValueError("TOTP state does not match this wallet profile")
    try:
        nonce = _decode(str(state["nonce"]))
        ciphertext = _decode(str(state["ciphertext"]))
        plaintext = AESGCM(key).decrypt(
            nonce,
            ciphertext,
            _associated_data(deployment_id, user_id, profile_id),
        )
        secret = plaintext.decode("ascii")
        totp_code(secret, 0)
        return secret
    except (KeyError, TypeError, ValueError, UnicodeDecodeError, InvalidTag) as exc:
        raise ValueError("TOTP state could not be authenticated") from exc


def _load_enrollment_private_key(pem: str):
    try:
        key = serialization.load_pem_private_key(
            pem.encode("ascii"), password=None
        )
    except (TypeError, ValueError) as exc:
        raise RuntimeError("CryptoWallet TOTP enrollment key is invalid") from exc
    if not isinstance(key, rsa.RSAPrivateKey) or key.key_size < 2048:
        raise RuntimeError("CryptoWallet TOTP enrollment key must be RSA-2048 or stronger")
    return key


def _integer_b64(value: int) -> str:
    return _encode(value.to_bytes((value.bit_length() + 7) // 8, "big"))


class TotpSecurityMixin:
    """Manage encrypted opt-in TOTP state without exposing authenticator secrets."""

    async def initialize_totp_security(self) -> None:
        tokens = await self.bot.get_shared_api_tokens(TOTP_TOKEN_NAMESPACE)
        encoded = str(tokens.get("encryption_key") or "")
        private_pem = str(tokens.get("enrollment_private_key_pem") or "")
        updates = {}
        if encoded:
            _load_encryption_key(encoded)
        else:
            updates["encryption_key"] = _encode(
                AESGCM.generate_key(bit_length=256)
            )
        if private_pem:
            _load_enrollment_private_key(private_pem)
        else:
            enrollment_key = rsa.generate_private_key(
                public_exponent=65537, key_size=2048
            )
            updates["enrollment_private_key_pem"] = enrollment_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            ).decode("ascii")
        if updates:
            await self.bot.set_shared_api_tokens(TOTP_TOKEN_NAMESPACE, **updates)

    async def totp_enrollment_public_jwk(self) -> dict:
        """Return only the public browser-enrollment key as a JWK."""

        tokens = await self.bot.get_shared_api_tokens(TOTP_TOKEN_NAMESPACE)
        key = _load_enrollment_private_key(
            str(tokens.get("enrollment_private_key_pem") or "")
        )
        numbers = key.public_key().public_numbers()
        return {
            "kty": "RSA",
            "alg": "RSA-OAEP-256",
            "use": "enc",
            "n": _integer_b64(numbers.n),
            "e": _integer_b64(numbers.e),
        }

    async def decrypt_totp_enrollment(self, ciphertext: str) -> str:
        """Decrypt and validate a browser-generated TOTP seed."""

        if not isinstance(ciphertext, str) or len(ciphertext) > 1024:
            raise ValueError("The encrypted enrollment is invalid")
        tokens = await self.bot.get_shared_api_tokens(TOTP_TOKEN_NAMESPACE)
        key = _load_enrollment_private_key(
            str(tokens.get("enrollment_private_key_pem") or "")
        )
        try:
            plaintext = key.decrypt(
                _decode(ciphertext),
                padding.OAEP(
                    mgf=padding.MGF1(algorithm=hashes.SHA256()),
                    algorithm=hashes.SHA256(),
                    label=TOTP_ENROLLMENT_LABEL,
                ),
            )
            secret = plaintext.decode("ascii")
            totp_code(secret, 0)
        except (TypeError, ValueError, UnicodeDecodeError) as exc:
            raise ValueError("The encrypted enrollment could not be authenticated") from exc
        return secret

    async def _totp_encryption_key(self) -> bytes:
        tokens = await self.bot.get_shared_api_tokens(TOTP_TOKEN_NAMESPACE)
        encoded = str(tokens.get("encryption_key") or "")
        if not encoded:
            raise RuntimeError("CryptoWallet TOTP encryption is not initialized")
        return _load_encryption_key(encoded)

    async def activate_encrypted_totp_enrollment(
        self,
        user_id: int,
        ciphertext: str,
        code: str,
        *,
        now: int | None = None,
    ) -> bool:
        """Decrypt, prove, and persist one new enrollment without plaintext storage."""

        secret = await self.decrypt_totp_enrollment(ciphertext)
        timestamp = int(time.time() if now is None else now)
        counter = verify_totp_code(secret, code, timestamp)
        if counter is None:
            return False
        profile = await self.config.user_from_id(user_id).profile()
        profile_id = str((profile or {}).get("profile_id") or "")
        if not profile_id:
            return False
        existing = await self.config.user_from_id(user_id).totp_security()
        if existing is not None:
            return False
        key = await self._totp_encryption_key()
        deployment_id = str(await self.config.deployment_id() or "")
        state = protect_totp_secret(
            key,
            secret,
            deployment_id=deployment_id,
            user_id=user_id,
            profile_id=profile_id,
            enrolled_at=timestamp,
        )
        state["enabled"] = True
        state["last_counter"] = counter
        await self.config.user_from_id(user_id).totp_security.set(state)
        return True

    async def stage_totp_enrollment(
        self, user_id: int, profile_id: str, secret: str, *, now: int | None = None
    ) -> None:
        """Store an encrypted pending enrollment; a valid code must activate it."""

        key = await self._totp_encryption_key()
        deployment_id = str(await self.config.deployment_id() or "")
        state = protect_totp_secret(
            key,
            secret,
            deployment_id=deployment_id,
            user_id=user_id,
            profile_id=profile_id,
            enrolled_at=int(time.time() if now is None else now),
        )
        await self.config.user_from_id(user_id).totp_security.set(state)

    async def activate_totp_enrollment(
        self, user_id: int, code: str, *, now: int | None = None
    ) -> bool:
        """Enable a pending enrollment only after proving possession of its secret."""

        return await self._verify_user_totp(
            user_id, code, now=now, require_enabled=False, activate=True
        )

    async def verify_user_totp(
        self, user_id: int, code: str, *, now: int | None = None
    ) -> bool:
        """Verify an enabled user code and atomically consume its time counter."""

        return await self._verify_user_totp(
            user_id, code, now=now, require_enabled=True, activate=False
        )

    async def _verify_user_totp(
        self,
        user_id: int,
        code: str,
        *,
        now: int | None,
        require_enabled: bool,
        activate: bool,
    ) -> bool:
        key = await self._totp_encryption_key()
        deployment_id = str(await self.config.deployment_id() or "")
        profile = await self.config.user_from_id(user_id).profile()
        profile_id = str((profile or {}).get("profile_id") or "")
        if not profile_id:
            return False
        timestamp = int(time.time() if now is None else now)
        async with self.config.user_from_id(user_id).totp_security() as state:
            if not isinstance(state, dict) or bool(state.get("enabled")) != require_enabled:
                return False
            try:
                secret = reveal_totp_secret(
                    key,
                    state,
                    deployment_id=deployment_id,
                    user_id=user_id,
                    profile_id=profile_id,
                )
                last_counter = state.get("last_counter")
                if last_counter is not None:
                    last_counter = int(last_counter)
                counter = verify_totp_code(
                    secret, code, timestamp, last_counter=last_counter
                )
            except (TypeError, ValueError):
                return False
            if counter is None:
                return False
            state["last_counter"] = counter
            if activate:
                state["enabled"] = True
            return True

    async def user_totp_enabled(self, user_id: int) -> bool:
        """Return whether valid-looking state requires step-up verification."""

        state = await self.config.user_from_id(user_id).totp_security()
        return bool(isinstance(state, dict) and state.get("enabled") is True)

    async def disable_user_totp(self, user_id: int) -> None:
        """Remove encrypted TOTP material and replay state."""

        await self.config.user_from_id(user_id).totp_security.clear()
