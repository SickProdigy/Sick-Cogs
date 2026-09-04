import base64
import hashlib
import json
import secrets
import time
import uuid
from dataclasses import dataclass
from urllib.parse import quote, urlencode

import aiohttp
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519


CDP_API_BASE_URL = "https://api.cdp.coinbase.com/platform"
CDP_API_HOST = "api.cdp.coinbase.com"
REQUEST_TIMEOUT_SECONDS = 15
MAX_RESPONSE_BYTES = 1024 * 1024


class CdpApiError(RuntimeError):
    """Raised when the direct CDP API client cannot complete a request."""

    def __init__(self, message: str, *, status: int | None = None, error_type: str = ""):
        super().__init__(message)
        self.status = status
        self.error_type = error_type


@dataclass(frozen=True, slots=True)
class CdpApiCredentials:
    api_key_id: str
    api_key_secret: str
    wallet_secret: str


def _load_api_private_key(secret: str):
    normalized = secret.replace("\\n", "\n")
    try:
        key = serialization.load_pem_private_key(normalized.encode("utf-8"), password=None)
        if isinstance(key, ec.EllipticCurvePrivateKey):
            return key, "ES256"
    except (TypeError, ValueError):
        pass
    try:
        decoded = base64.b64decode(normalized, validate=True)
        if len(decoded) == 64:
            return ed25519.Ed25519PrivateKey.from_private_bytes(decoded[:32]), "EdDSA"
    except (TypeError, ValueError):
        pass
    raise CdpApiError("The CDP API key secret is not a supported EC or Ed25519 key.")


def _load_wallet_private_key(secret: str):
    try:
        key = serialization.load_der_private_key(
            base64.b64decode(secret, validate=True), password=None
        )
    except (TypeError, ValueError) as exc:
        raise CdpApiError("The CDP wallet secret is not a valid DER private key.") from exc
    if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(
        key.curve, ec.SECP256R1
    ):
        raise CdpApiError("The CDP wallet secret must contain a P-256 EC private key.")
    return key


def _request_path(path: str) -> str:
    if not path.startswith("/") or "?" in path:
        raise CdpApiError("CDP request paths must be absolute and omit query strings.")
    return f"/platform{path}"


def _api_jwt(credentials: CdpApiCredentials, method: str, path: str) -> str:
    private_key, algorithm = _load_api_private_key(credentials.api_key_secret)
    now = int(time.time())
    full_path = _request_path(path)
    claims = {
        "sub": credentials.api_key_id,
        "iss": "cdp",
        "aud": ["cdp_service"],
        "nbf": now,
        "exp": now + 120,
        "uri": f"{method} {CDP_API_HOST}{full_path}",
    }
    headers = {
        "alg": algorithm,
        "kid": credentials.api_key_id,
        "typ": "JWT",
        "nonce": "".join(str(secrets.randbelow(10)) for _ in range(16)),
    }
    try:
        return jwt.encode(claims, private_key, algorithm=algorithm, headers=headers)
    except Exception as exc:
        raise CdpApiError("The CDP API authentication token could not be signed.") from exc


def _wallet_jwt(credentials: CdpApiCredentials, method: str, path: str, body: dict) -> str:
    now = int(time.time())
    claims = {
        "uris": [f"{method} {CDP_API_HOST}{_request_path(path)}"],
        "iat": now,
        "nbf": now,
        "jti": str(uuid.uuid4()),
    }
    if body:
        encoded = json.dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8")
        claims["reqHash"] = hashlib.sha256(encoded).hexdigest()
    try:
        return jwt.encode(
            claims,
            _load_wallet_private_key(credentials.wallet_secret),
            algorithm="ES256",
            headers={"typ": "JWT"},
        )
    except CdpApiError:
        raise
    except Exception as exc:
        raise CdpApiError("The CDP wallet authentication token could not be signed.") from exc


class CdpApiClient:
    """Minimal CDP v2 client compatible with Red's existing aiohttp version."""

    def __init__(
        self,
        credentials: CdpApiCredentials,
        *,
        base_url: str = CDP_API_BASE_URL,
        session: aiohttp.ClientSession | None = None,
        request_limiter=None,
        request_observer=None,
    ):
        self.credentials = credentials
        self.base_url = base_url.rstrip("/")
        self.session = session
        self.request_limiter = request_limiter
        self.request_observer = request_observer

    def validate_key_material(self) -> None:
        """Parse both server-side signing keys without exposing their values."""
        _load_api_private_key(self.credentials.api_key_secret)
        _load_wallet_private_key(self.credentials.wallet_secret)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        body: dict | None = None,
        query: dict[str, str | int] | None = None,
        wallet_auth: bool = False,
        developer_auth: bool = False,
        idempotency_key: str | None = None,
        allow_empty_response: bool = False,
    ) -> dict:
        method = method.upper()
        if self.request_limiter is not None:
            await self.request_limiter(method, path)
        request_body = body or {}
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {_api_jwt(self.credentials, method, path)}",
            "User-Agent": "Sick-Cogs-CryptoWallet/0.19",
        }
        if wallet_auth:
            headers["X-Wallet-Auth"] = _wallet_jwt(
                self.credentials, method, path, request_body
            )
        if developer_auth:
            headers["X-Developer-Auth"] = _wallet_jwt(
                self.credentials, method, path, request_body
            )
        if idempotency_key:
            headers["X-Idempotency-Key"] = idempotency_key
        url = f"{self.base_url}{path}"
        if query:
            url = f"{url}?{urlencode(query)}"
        timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS)
        owned_session = self.session is None
        session = self.session or aiohttp.ClientSession(timeout=timeout)
        try:
            async with session.request(
                method,
                url,
                headers=headers,
                json=request_body if body is not None else None,
            ) as response:
                if self.request_observer is not None:
                    try:
                        retry_after = 0.0
                        if response.status == 429:
                            try:
                                retry_after = min(
                                    300.0,
                                    float(response.headers.get("Retry-After", 1)),
                                )
                            except (TypeError, ValueError):
                                retry_after = 1.0
                        await self.request_observer(
                            method, path, response.status, retry_after
                        )
                    except Exception:
                        pass
                raw = bytearray()
                async for chunk in response.content.iter_chunked(64 * 1024):
                    raw.extend(chunk)
                    if len(raw) > MAX_RESPONSE_BYTES:
                        raise CdpApiError("CDP returned an oversized response.")
                if (
                    200 <= response.status < 300
                    and not raw
                    and allow_empty_response
                ):
                    return {}
                try:
                    payload = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise CdpApiError("CDP returned an invalid JSON response.") from exc
                if response.status < 200 or response.status >= 300:
                    error_type = ""
                    correlation_id = ""
                    if isinstance(payload, dict):
                        error_type = str(payload.get("errorType") or "").strip()
                        correlation_id = str(payload.get("correlationId") or "").strip()
                    details = [f"HTTP {response.status}"]
                    if error_type:
                        details.append(error_type)
                    if correlation_id:
                        details.append(f"correlation {correlation_id}")
                    raise CdpApiError(
                        f"CDP returned {'; '.join(details)}.",
                        status=response.status,
                        error_type=error_type,
                    )
                if not isinstance(payload, dict):
                    raise CdpApiError("CDP returned an unexpected response shape.")
                return payload
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise CdpApiError("CDP could not be reached.") from exc
        finally:
            if owned_session:
                await session.close()

    async def check_connection(self) -> None:
        """Validate local key material and perform one read-only project request."""
        self.validate_key_material()
        payload = await self._request("GET", "/v2/end-users", query={"pageSize": 1})
        end_users = payload.get("endUsers")
        if not isinstance(end_users, list):
            raise CdpApiError("CDP returned an invalid end-user list.")

    async def create_end_user(
        self, profile_id: str, jwt_kid: str, idempotency_key: str
    ) -> dict:
        body = {
            "userId": profile_id,
            "authenticationMethods": [{"type": "jwt", "kid": jwt_kid, "sub": profile_id}],
            "evmAccount": {
                "createSmartAccount": True,
                "enableSpendPermissions": False,
            },
            "solanaAccount": {},
        }
        return await self._request(
            "POST",
            "/v2/end-users",
            body=body,
            wallet_auth=True,
            idempotency_key=idempotency_key,
        )

    async def add_solana_account(
        self, user_id: str, idempotency_key: str
    ) -> dict:
        """Idempotently add one Solana account to an existing end user."""
        path = f"/v2/end-users/{quote(user_id, safe='')}/solana"
        return await self._request(
            "POST",
            path,
            body={},
            wallet_auth=True,
            idempotency_key=idempotency_key,
        )

    async def validate_access_token(self, access_token: str) -> dict:
        return await self._request(
            "POST",
            "/v2/end-users/auth/validate-token",
            body={"accessToken": access_token},
        )

    async def get_account_delegation(
        self, user_id: str, address: str, project_id: str
    ) -> dict | None:
        """Return an active account-scoped delegation, or None when absent."""
        path = (
            f"/v2/embedded-wallet-api/end-users/{quote(user_id, safe='')}"
            f"/address/{quote(address, safe='')}/delegation"
        )
        try:
            return await self._request("GET", path, query={"projectID": project_id})
        except CdpApiError as exc:
            if exc.status == 404 and exc.error_type == "not_found":
                return None
            raise

    async def get_user_delegation(self, user_id: str, project_id: str) -> dict | None:
        """Return the user-scoped delegation shared by all accounts, if present."""
        path = f"/v2/embedded-wallet-api/end-users/{quote(user_id)}/delegation"
        try:
            return await self._request("GET", path, query={"projectID": project_id})
        except CdpApiError as exc:
            if exc.status == 404 and exc.error_type == "not_found":
                return None
            raise

    async def revoke_user_delegation(self, user_id: str, project_id: str) -> None:
        """Revoke the delegation shared by every account owned by an end user."""
        path = f"/v2/embedded-wallet-api/end-users/{quote(user_id)}/delegation"
        try:
            await self._request(
                "DELETE", path, body={}, query={"projectID": project_id},
                developer_auth=True, allow_empty_response=True,
            )
        except CdpApiError as exc:
            if exc.status == 404 and exc.error_type == "not_found":
                return
            raise

    async def revoke_account_delegation(
        self, user_id: str, address: str, project_id: str
    ) -> None:
        """Revoke only the active delegation for one end-user account."""
        path = (
            f"/v2/embedded-wallet-api/end-users/{quote(user_id, safe='')}"
            f"/address/{quote(address, safe='')}/delegation"
        )
        try:
            await self._request(
                "DELETE",
                path,
                body={},
                query={"projectID": project_id},
                developer_auth=True,
                allow_empty_response=True,
            )
        except CdpApiError as exc:
            if exc.status == 404 and exc.error_type == "not_found":
                return
            raise

    async def send_smart_account_user_operation(
        self,
        user_id: str,
        address: str,
        project_id: str,
        network: str,
        to_address: str,
        value_wei: int,
        idempotency_key: str,
    ) -> dict:
        """Prepare, sign, and send one sponsored smart-account user operation."""
        path = (
            f"/v2/embedded-wallet-api/end-users/{quote(user_id, safe='')}"
            f"/evm/smart-accounts/{quote(address, safe='')}/send"
        )
        body = {
            "network": network,
            "calls": [{"to": to_address, "value": str(value_wei), "data": "0x"}],
            "useCdpPaymaster": True,
        }
        return await self._request(
            "POST",
            path,
            body=body,
            query={"projectID": project_id},
            wallet_auth=True,
            idempotency_key=idempotency_key,
        )

    async def get_smart_account_user_operation(
        self, user_id: str, address: str, user_operation_hash: str, project_id: str
    ) -> dict:
        """Retrieve current public state for one submitted user operation."""
        path = (
            f"/v2/embedded-wallet-api/end-users/{quote(user_id, safe='')}"
            f"/evm/smart-accounts/{quote(address, safe='')}"
            f"/user-operations/{quote(user_operation_hash, safe='')}"
        )
        return await self._request("GET", path, query={"projectID": project_id})

    async def list_token_balances(
        self,
        address: str,
        network: str,
        *,
        page_size: int,
        page_token: str | None = None,
    ) -> dict:
        path = f"/v2/evm/token-balances/{quote(network, safe='')}/{quote(address, safe='')}"
        query: dict[str, str | int] = {"pageSize": page_size}
        if page_token:
            query["pageToken"] = page_token
        return await self._request("GET", path, query=query)

    async def list_address_transactions(
        self,
        address: str,
        network: str,
        *,
        limit: int,
        page_token: str | None = None,
    ) -> dict:
        """Return indexed native, ERC-20, and ERC-721 address activity."""
        path = (
            f"/v1/networks/{quote(network, safe='')}"
            f"/addresses/{quote(address.lower(), safe='')}/transactions"
        )
        query: dict[str, str | int] = {"limit": limit}
        if page_token:
            query["page"] = page_token
        return await self._request("GET", path, query=query)
