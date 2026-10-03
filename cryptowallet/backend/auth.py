import base64
import hashlib
import json
import re
import secrets
import time
from decimal import Decimal, InvalidOperation

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from ..core.validation import normalize_evm_address, normalize_solana_address
from .terms import CRYPTOWALLET_MAINNET_TERMS_VERSION, CRYPTOWALLET_TERMS_PRODUCT


JWT_TOKEN_NAMESPACE = "cryptowallet_jwt"
JWT_LIFETIME_SECONDS = 5 * 60
CLAIM_HANDOFF_LIFETIME_SECONDS = 3 * 60
TOTP_ENROLLMENT_LIFETIME_SECONDS = 10 * 60
WALLET_TERMS_LIFETIME_SECONDS = 10 * 60
POLYMARKET_ONBOARDING_LIFETIME_SECONDS = 5 * 60


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
        self, discord_user_id: int, profile: dict, *, delegation_days: int | None = None,
        terms: dict | None = None,
    ) -> tuple[str, int]:
        """Create a short-lived CDP custom-auth token for wallet authorization."""
        return await self._create_wallet_handoff(
            discord_user_id, profile, purpose="authorize",
            delegation_default_days=delegation_days,
            terms=terms,
        )

    async def create_recovery_handoff(
        self, discord_user_id: int, profile: dict
    ) -> tuple[str, int]:
        """Create a short-lived token for protected recovery-method enrollment."""
        return await self._create_wallet_handoff(
            discord_user_id, profile, purpose="recovery"
        )

    async def create_external_companion_handoff(
        self, discord_user_id: int, purpose: str, payload: dict
    ) -> tuple[str, int]:
        """Sign a short-lived product payload for the shared static companion."""

        claim_names = {
            "tokenfactory_external": "sickwallet_tokenfactory",
            "totp_enroll": "sickwallet_totp",
            "wallet_terms": "sickwallet_terms",
            "polymarket_terms": "sickwallet_polymarket_terms",
            "polymarket_connect": "sickwallet_polymarket",
            "polymarket_eligibility": "sickwallet_polymarket_eligibility",
        }
        claim_name = claim_names.get(purpose)
        if claim_name is None or not isinstance(payload, dict):
            raise ValueError("Unsupported external companion handoff")
        if purpose == "wallet_terms":
            expected = {"product", "version", "result_handle"}
            if (
                set(payload) != expected
                or payload.get("product") != CRYPTOWALLET_TERMS_PRODUCT
                or payload.get("version") != CRYPTOWALLET_MAINNET_TERMS_VERSION
                or not isinstance(payload.get("result_handle"), str)
                or not 32 <= len(payload["result_handle"]) <= 128
            ):
                raise ValueError("The CryptoWallet terms handoff binding is invalid")
        if purpose == "polymarket_terms":
            expected = {"product", "version", "result_handle"}
            if (
                set(payload) != expected
                or payload.get("product") != "polymarket"
                or payload.get("version") != "2026-10-02.1"
                or not isinstance(payload.get("result_handle"), str)
                or re.fullmatch(r"[A-Za-z0-9_-]{32,128}", payload["result_handle"]) is None
            ):
                raise ValueError("The Polymarket terms handoff binding is invalid")
        if purpose == "polymarket_eligibility":
            base_expected = {
                "request_id", "result_handle", "discord_user_id", "action",
                "signer_address", "account_wallet_address", "created_at",
                "expires_at", "chain_id", "purpose",
            }
            action = payload.get("action")
            action_expected = (
                {
                    "session_address", "market_path", "outcome",
                    "max_spend_pusd", "max_price",
                }
                if action == "buy"
                else {
                    "session_address", "market_path", "outcome",
                    "shares", "min_price",
                }
                if action == "sell"
                else {"market_path", "outcome"}
                if action == "claim"
                else set()
            )
            try:
                signer = normalize_evm_address(payload.get("signer_address", ""))
                wallet = normalize_evm_address(payload.get("account_wallet_address", ""))
                session_address = (
                    normalize_evm_address(payload.get("session_address", ""))
                    if action in {"buy", "sell"} else None
                )
                numeric_fields = (
                    ("max_spend_pusd", "max_price")
                    if action == "buy" else ("shares", "min_price")
                    if action == "sell" else ()
                )
                numbers = {
                    field: (
                        None if payload.get(field) is None
                        else Decimal(str(payload.get(field)))
                    )
                    for field in numeric_fields
                }
            except (
                AttributeError, InvalidOperation, TypeError, ValueError,
            ) as exc:
                raise ValueError(
                    "The Polymarket eligibility binding is invalid"
                ) from exc
            action_invalid = bool(action_expected) and (
                action in {"buy", "sell"} and session_address in {signer, wallet}
                or not isinstance(payload.get("market_path"), str)
                or re.fullmatch(
                    r"/markets/(?:[0-9]+|slug/[A-Za-z0-9_-]+)",
                    payload["market_path"],
                ) is None
                or not isinstance(payload.get("outcome"), str)
                or re.fullmatch(r"[^\s/]{1,128}", payload["outcome"]) is None
                or any(
                    value is not None and (
                        not value.is_finite() or value <= 0
                        or value.as_tuple().exponent < -6
                    )
                    for value in numbers.values()
                )
                or action == "buy" and (
                    numbers["max_price"] is not None
                    and numbers["max_price"] >= 1
                )
                or action == "sell" and numbers["min_price"] >= 1
            )
            if (
                set(payload) != base_expected | action_expected
                or payload.get("purpose") != "polymarket_eligibility"
                or payload.get("chain_id") != 137
                or payload.get("discord_user_id") != discord_user_id
                or action not in {
                    "deploy", "provision", "rotate", "deposit", "buy", "sell", "claim",
                }
                or not isinstance(payload.get("request_id"), str)
                or re.fullmatch(r"[A-Za-z0-9_-]{32,128}", payload["request_id"]) is None
                or not isinstance(payload.get("result_handle"), str)
                or re.fullmatch(r"[A-Za-z0-9_-]{32,128}", payload["result_handle"]) is None
                or not isinstance(payload.get("created_at"), int)
                or not isinstance(payload.get("expires_at"), int)
                or payload["expires_at"] != payload["created_at"] + POLYMARKET_ONBOARDING_LIFETIME_SECONDS
                or signer.casefold() == wallet.casefold()
                or action_invalid
            ):
                raise ValueError("The Polymarket eligibility binding is invalid")
        if purpose == "polymarket_connect":
            expected = {
                "connection_id", "result_handle", "discord_user_id",
                "signer_address", "account_wallet_address", "wallet_type",
                "challenge", "created_at", "expires_at", "chain_id", "purpose",
            }
            wallet_types = {"EOA", "POLY_PROXY", "GNOSIS_SAFE", "DEPOSIT_WALLET"}
            try:
                signer = normalize_evm_address(payload.get("signer_address", ""))
                wallet = normalize_evm_address(payload.get("account_wallet_address", ""))
            except (AttributeError, ValueError) as exc:
                raise ValueError("The Polymarket onboarding binding is invalid") from exc
            if (
                set(payload) != expected
                or payload.get("purpose") != "polymarket_connect"
                or payload.get("chain_id") != 137
                or payload.get("discord_user_id") != discord_user_id
                or payload.get("wallet_type") not in wallet_types
                or not isinstance(payload.get("connection_id"), str)
                or re.fullmatch(r"[A-Za-z0-9_-]{1,128}", payload["connection_id"]) is None
                or not isinstance(payload.get("result_handle"), str)
                or re.fullmatch(r"[A-Za-z0-9_-]{32,128}", payload["result_handle"]) is None
                or not isinstance(payload.get("challenge"), str)
                or re.fullmatch(r"[A-Za-z0-9_-]{32,128}", payload["challenge"]) is None
                or not isinstance(payload.get("created_at"), int)
                or not isinstance(payload.get("expires_at"), int)
                or payload["expires_at"] != payload["created_at"] + POLYMARKET_ONBOARDING_LIFETIME_SECONDS
                or (payload["wallet_type"] == "EOA" and signer.casefold() != wallet.casefold())
                or (payload["wallet_type"] != "EOA" and signer.casefold() == wallet.casefold())
            ):
                raise ValueError("The Polymarket onboarding binding is invalid")
        configuration = await self.jwt_configuration()
        if configuration is None:
            raise RuntimeError("The protected companion signing key is not configured")
        deployment_id = str(await self.config.deployment_id() or "")
        application_id = getattr(self.bot.user, "id", None)
        if not deployment_id or application_id is None:
            raise RuntimeError("The protected companion identity is incomplete")
        now = int(time.time())
        if purpose in {"polymarket_connect", "polymarket_eligibility"} and (
            payload["created_at"] < now - 5 or payload["created_at"] > now + 5
        ):
            raise ValueError("The Polymarket binding is stale")
        lifetime = (
            POLYMARKET_ONBOARDING_LIFETIME_SECONDS
            if purpose in {"polymarket_connect", "polymarket_eligibility"}
            else
            WALLET_TERMS_LIFETIME_SECONDS
            if purpose in {"wallet_terms", "polymarket_terms"}
            else TOTP_ENROLLMENT_LIFETIME_SECONDS
            if purpose == "totp_enroll"
            else CLAIM_HANDOFF_LIFETIME_SECONDS
        )
        expires_at = now + lifetime
        signed_payload = dict(payload)
        if purpose in {"polymarket_connect", "polymarket_eligibility"}:
            signed_payload["discord_user_id"] = str(discord_user_id)
        claims = {
            "iss": configuration["issuer"],
            "aud": configuration["audience"],
            "sub": str(discord_user_id),
            "iat": now,
            "nbf": now,
            "exp": expires_at,
            "jti": secrets.token_urlsafe(18),
            "sickwallet_purpose": purpose,
            "sickwallet_deployment": deployment_id,
            "sickwallet_application": str(application_id),
            "sickwallet_discord_user": str(discord_user_id),
            claim_name: signed_payload,
        }
        token = jwt.encode(
            claims,
            configuration["private_key"],
            algorithm="ES256",
            headers={"kid": configuration["kid"], "typ": "JWT"},
        )
        return token, expires_at

    async def create_clanker_external_handoff(
        self, discord_user_id: int, handoff: dict
    ) -> tuple[str, int]:
        """Sign a short-lived Clanker-owned external operation for the companion."""
        configuration = await self.jwt_configuration()
        if configuration is None:
            raise RuntimeError("The protected companion signing key is not configured")
        deployment_id = str(await self.config.deployment_id() or "")
        application_id = getattr(self.bot.user, "id", None)
        if not deployment_id or application_id is None:
            raise RuntimeError("The protected companion identity is incomplete")
        expected = {"version", "kind", "requester_id", "expires_at", "intent", "operation", "verification_command"}
        if (not isinstance(handoff, dict) or set(handoff) != expected
                or handoff.get("kind") not in {"clanker-v4-external-handoff", "clanker-v4-external-template", "clanker-v4-reward-collection"}
                or str(handoff.get("requester_id")) != str(discord_user_id)):
            raise ValueError("The Clanker external handoff binding is invalid")
        now = int(time.time())
        expires_at = min(int(handoff.get("expires_at", 0)), now + CLAIM_HANDOFF_LIFETIME_SECONDS)
        if expires_at <= now:
            raise ValueError("The Clanker external handoff has expired")
        claims = {
            "iss": configuration["issuer"], "aud": configuration["audience"],
            "sub": str(discord_user_id), "iat": now, "nbf": now, "exp": expires_at,
            "jti": secrets.token_urlsafe(18),
            "sickwallet_purpose": "clanker_external",
            "sickwallet_deployment": deployment_id,
            "sickwallet_application": str(application_id),
            "sickwallet_discord_user": str(discord_user_id),
            "sickwallet_clanker": handoff,
        }
        token = jwt.encode(
            claims, configuration["private_key"], algorithm="ES256",
            headers={"kid": configuration["kid"], "typ": "JWT"},
        )
        return token, expires_at

    async def _create_wallet_handoff(
        self, discord_user_id: int, profile: dict, *, purpose: str,
        delegation_default_days: int | None = None,
        terms: dict | None = None,
    ) -> tuple[str, int]:
        if purpose not in {"authorize", "recovery"}:
            raise ValueError("Unsupported wallet handoff purpose")
        configuration = await self.jwt_configuration()
        if configuration is None:
            raise RuntimeError("CryptoWallet custom authentication is not configured")
        profile_id = str(profile.get("profile_id") or "")
        provider_user_id = str(profile.get("provider_user_id") or "")
        stored_discord_user_id = int(profile.get("discord_user_id", 0) or 0)
        base_address = next(
            (
                str(account.get("address") or "")
                for account in profile.get("accounts") or []
                if account.get("network") == "base-sepolia"
            ),
            "",
        )
        solana_address = next(
            (
                str(account.get("address") or "")
                for account in profile.get("accounts") or []
                if account.get("network") == "solana-devnet"
            ),
            "",
        )
        try:
            base_address = normalize_evm_address(base_address)
            expected_accounts = [{"family": "evm", "address": base_address}]
            if solana_address:
                expected_accounts.append({
                    "family": "solana",
                    "address": normalize_solana_address(solana_address),
                })
        except ValueError as exc:
            raise RuntimeError("A provisioned wallet address is invalid") from exc
        deployment_id = str(await self.config.deployment_id() or "")
        application_id = getattr(self.bot.user, "id", None)
        if (
            not profile_id
            or provider_user_id != profile_id
            or stored_discord_user_id != discord_user_id
            or not base_address
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
            "sickwallet_address": base_address,
            "sickwallet_accounts": expected_accounts,
            "sickwallet_purpose": purpose,
        }
        if terms is not None:
            expected_terms = {"product", "version", "result_handle"}
            if (
                purpose != "authorize" or not isinstance(terms, dict)
                or set(terms) != expected_terms
                or terms.get("product") != CRYPTOWALLET_TERMS_PRODUCT
                or terms.get("version") != CRYPTOWALLET_MAINNET_TERMS_VERSION
                or not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", str(terms.get("result_handle") or ""))
            ):
                raise ValueError("The wallet terms setup binding is invalid")
            claims["sickwallet_terms"] = terms
        if purpose == "authorize":
            configured_days = int(await self.config.delegation_duration_days() or 0)
            delegation_days = (
                configured_days
                if delegation_default_days is None
                else int(delegation_default_days)
            )
            delegation_max_days = int(
                await self.config.delegation_max_duration_days() or 0
            )
            if (
                not 1 <= delegation_days <= 365
                or not delegation_days <= delegation_max_days <= 365
            ):
                raise RuntimeError("The wallet delegation policy is invalid")
            claims["sickwallet_delegation_default_days"] = delegation_days
            claims["sickwallet_delegation_max_days"] = delegation_max_days
            claims["sickwallet_delegation_expires_at"] = (
                now + delegation_days * 24 * 60 * 60
            )
        token = jwt.encode(
            claims,
            configuration["private_key"],
            algorithm="ES256",
            headers={"kid": configuration["kid"], "typ": "JWT"},
        )
        return token, expires_at
