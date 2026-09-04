import base64
import hashlib
import json
import secrets
import time

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec


JWT_TOKEN_NAMESPACE = "cryptowallet_jwt"
JWT_LIFETIME_SECONDS = 5 * 60
CLAIM_HANDOFF_LIFETIME_SECONDS = 3 * 60


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _public_jwk(private_key, kid: str) -> dict:
    numbers = private_key.public_key().public_numbers()
    size = (private_key.curve.key_size + 7) // 8
    return {
        "kty": "EC",
        "use": "sig",
        "alg": "ES256",
        "kid": kid,
        "crv": "P-256",
        "x": _base64url(numbers.x.to_bytes(size, "big")),
        "y": _base64url(numbers.y.to_bytes(size, "big")),
    }


def _key_id(private_key) -> str:
    public = _public_jwk(private_key, "")
    thumbprint = json.dumps(
        {key: public[key] for key in ("crv", "kty", "x", "y")},
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return _base64url(hashlib.sha256(thumbprint).digest())


class JwtAuthMixin:
    """Generate and use the deployment's server-only custom-auth signing key."""

    @staticmethod
    def _load_private_key(pem: str):
        key = serialization.load_pem_private_key(pem.encode("ascii"), password=None)
        if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(
            key.curve, ec.SECP256R1
        ):
            raise ValueError("CryptoWallet JWT key must be a P-256 EC private key")
        return key

    async def initialize_jwt_auth(self) -> None:
        tokens = await self.bot.get_shared_api_tokens(JWT_TOKEN_NAMESPACE)
        pem = str(tokens.get("private_key_pem") or "")
        kid = str(tokens.get("kid") or "")
        if pem or kid:
            if not pem or not kid:
                raise RuntimeError("CryptoWallet JWT key storage is incomplete")
            key = self._load_private_key(pem)
            if not secrets.compare_digest(_key_id(key), kid):
                raise RuntimeError("CryptoWallet JWT key ID does not match its private key")
            return

        key = ec.generate_private_key(ec.SECP256R1())
        pem = key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode("ascii")
        await self.bot.set_shared_api_tokens(
            JWT_TOKEN_NAMESPACE,
            private_key_pem=pem,
            kid=_key_id(key),
        )

    async def jwt_configuration(self) -> dict | None:
        approval_base_url = await self.config.approval_base_url()
        cdp = await self.bot.get_shared_api_tokens("cryptowallet_cdp")
        project_id = str(cdp.get("project_id") or "").strip()
        tokens = await self.bot.get_shared_api_tokens(JWT_TOKEN_NAMESPACE)
        pem = str(tokens.get("private_key_pem") or "")
        kid = str(tokens.get("kid") or "")
        if not approval_base_url or not project_id or not pem or not kid:
            return None
        try:
            key = self._load_private_key(pem)
        except (TypeError, ValueError):
            return None
        if not secrets.compare_digest(_key_id(key), kid):
            return None
        return {
            "issuer": approval_base_url,
            "audience": project_id,
            "jwks_url": f"{approval_base_url}/api/jwks.php",
            "kid": kid,
            "private_key": key,
        }

    async def jwt_public_status(self) -> dict:
        configuration = await self.jwt_configuration()
        if configuration is None:
            return {"configured": False}
        return {
            "configured": True,
            "issuer": configuration["issuer"],
            "audience": configuration["audience"],
            "jwks_url": configuration["jwks_url"],
            "kid": configuration["kid"],
        }

    async def jwt_jwks(self) -> dict | None:
        configuration = await self.jwt_configuration()
        if configuration is None:
            return None
        return {"keys": [_public_jwk(configuration["private_key"], configuration["kid"])]}

    async def create_cdp_auth_token(self, session, profile: dict) -> tuple[str, int]:
        configuration = await self.jwt_configuration()
        if configuration is None:
            raise RuntimeError("CryptoWallet custom authentication is not configured")
        profile_id = str(profile.get("profile_id") or "")
        if not profile_id or int(profile.get("discord_user_id", 0) or 0) != session.discord_user_id:
            raise RuntimeError("Wallet profile identity does not match the verified session")
        now = int(time.time())
        expires_at = min(session.expires_at, now + JWT_LIFETIME_SECONDS)
        if expires_at <= now:
            raise RuntimeError("The verified browser session has expired")
        claims = {
            "iss": configuration["issuer"],
            "aud": configuration["audience"],
            "sub": profile_id,
            "iat": now,
            "nbf": now,
            "exp": expires_at,
            "jti": secrets.token_urlsafe(18),
            "sickwallet_deployment": session.deployment_id,
            "sickwallet_application": str(session.discord_application_id),
            "sickwallet_purpose": session.purpose.value,
        }
        token = jwt.encode(
            claims,
            configuration["private_key"],
            algorithm="ES256",
            headers={"kid": configuration["kid"], "typ": "JWT"},
        )
        return token, expires_at

    async def create_authorization_handoff(
        self, discord_user_id: int, profile: dict
    ) -> tuple[str, int]:
        """Create a short-lived CDP custom-auth token for wallet authorization."""
        return await self._create_wallet_handoff(
            discord_user_id, profile, purpose="authorize"
        )

    async def create_recovery_handoff(
        self, discord_user_id: int, profile: dict
    ) -> tuple[str, int]:
        """Create a short-lived token for protected recovery-method enrollment."""
        return await self._create_wallet_handoff(
            discord_user_id, profile, purpose="recovery"
        )

    async def _create_wallet_handoff(
        self, discord_user_id: int, profile: dict, *, purpose: str
    ) -> tuple[str, int]:
        if purpose not in {"authorize", "recovery"}:
            raise ValueError("Unsupported wallet handoff purpose")
        configuration = await self.jwt_configuration()
        if configuration is None:
            raise RuntimeError("CryptoWallet custom authentication is not configured")
        profile_id = str(profile.get("profile_id") or "")
        provider_user_id = str(profile.get("provider_user_id") or "")
        stored_discord_user_id = int(profile.get("discord_user_id", 0) or 0)
        address = next(
            (
                str(account.get("address") or "")
                for account in profile.get("accounts") or []
                if account.get("network") == "base-sepolia"
            ),
            "",
        )
        deployment_id = str(await self.config.deployment_id() or "")
        application_id = getattr(self.bot.user, "id", None)
        if (
            not profile_id
            or provider_user_id != profile_id
            or stored_discord_user_id != discord_user_id
            or not address
            or not deployment_id
            or application_id is None
        ):
            raise RuntimeError("The provisioned wallet identity is incomplete or mismatched")
        now = int(time.time())
        expires_at = now + CLAIM_HANDOFF_LIFETIME_SECONDS
        claims = {
            "iss": configuration["issuer"],
            "aud": configuration["audience"],
            "sub": profile_id,
            "iat": now,
            "nbf": now,
            "exp": expires_at,
            "jti": secrets.token_urlsafe(18),
            "sickwallet_deployment": deployment_id,
            "sickwallet_application": str(application_id),
            "sickwallet_discord_user": str(discord_user_id),
            "sickwallet_address": address,
            "sickwallet_purpose": purpose,
        }
        token = jwt.encode(
            claims,
            configuration["private_key"],
            algorithm="ES256",
            headers={"kid": configuration["kid"], "typ": "JWT"},
        )
        return token, expires_at
