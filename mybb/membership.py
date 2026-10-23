"""Client for External Sign-In's account-linking service."""

import asyncio
from urllib.parse import urlsplit

import aiohttp

from .client import normalize_base_url, reject_private_destination
from .roles import RoleSyncError, entitlement_name


class MembershipError(RuntimeError):
    def __init__(self, message, *, code="provider_error", status=0, retry_after=None):
        super().__init__(message)
        self.code, self.status, self.retry_after = code, status, retry_after


class ExternalSignInClient:
    def __init__(self, board_url, client_id, client_secret):
        self.board_url = normalize_base_url(board_url)
        self.issuer = self.board_url if self.board_url.endswith("/external-signin") else self.board_url + "/external-signin"
        self.client_id = str(client_id).strip()
        self.client_secret = str(client_secret).strip()
        if not self.client_id or not self.client_secret:
            raise MembershipError("The account-linking client ID and secret are required.", code="not_configured")

    async def request(self, path, payload, *, allow_empty_success=False):
        await reject_private_destination(self.board_url)
        timeout = aiohttp.ClientTimeout(total=20)
        headers = {"Accept": "application/json", "User-Agent": "SickCogs-MyBB/0.3"}
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    self.issuer + path,
                    data=payload,
                    headers=headers,
                    auth=aiohttp.BasicAuth(self.client_id, self.client_secret),
                    allow_redirects=False,
                ) as response:
                    try:
                        body = await response.json(content_type=None)
                    except (aiohttp.ContentTypeError, ValueError) as exc:
                        if allow_empty_success and 200 <= response.status < 300:
                            raw = await response.text()
                            if not raw.strip():
                                return {}
                        raise MembershipError("External Sign-In returned an invalid response.", code="invalid_response", status=response.status) from exc
                    if not isinstance(body, dict):
                        raise MembershipError("External Sign-In returned an invalid response.", code="invalid_response", status=response.status)
                    if response.status >= 400:
                        raise MembershipError(
                            body.get("error_description") or body.get("message") or body.get("error") or f"External Sign-In returned HTTP {response.status}.",
                            code=str(body.get("error") or "http_error"), status=response.status,
                            retry_after=response.headers.get("Retry-After"),
                        )
        except MembershipError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise MembershipError("Could not connect to External Sign-In.", code="connection_failed") from exc
        return body

    async def start_link(self, discord_user_id, discord_username, context):
        body = await self.request("/device/authorize", {
            "discord_user_id": str(int(discord_user_id)),
            "discord_username": str(discord_username),
            "context": context,
        })
        required = ("device_code", "user_code", "verification_uri_complete", "expires_in", "interval")
        if any(not body.get(key) for key in required):
            raise MembershipError("External Sign-In returned an incomplete linking response.", code="invalid_response")
        parsed = urlsplit(str(body["verification_uri_complete"]))
        issuer_url = urlsplit(self.issuer)
        if (
            parsed.scheme != "https" or parsed.netloc != issuer_url.netloc
            or not parsed.path.startswith(issuer_url.path + "/device")
        ):
            raise MembershipError("External Sign-In returned an unsafe verification URL.", code="invalid_response")
        body["expires_in"] = min(max(int(body["expires_in"]), 1), 600)
        body["interval"] = min(max(int(body["interval"]), 5), 60)
        return body

    async def poll_link(self, device_code):
        return await self.request("/device/poll", {"device_code": device_code})

    async def membership(self, link_token, discord_username):
        body = await self.request("/membership", {
            "link_token": link_token,
            "discord_username": str(discord_username),
        })
        status = body.get("status")
        if status not in {"active", "inactive", "unlinked"}:
            raise MembershipError("External Sign-In returned an unknown membership state.", code="invalid_response")
        entitlements = body.get("entitlements")
        if not isinstance(entitlements, list) or any(not isinstance(item, str) for item in entitlements):
            raise MembershipError("External Sign-In returned invalid entitlements.", code="invalid_response")
        try:
            body["entitlements"] = {entitlement_name(item) for item in entitlements}
        except RoleSyncError as exc:
            raise MembershipError("External Sign-In returned an invalid entitlement name.", code="invalid_response") from exc
        if status != "active" and body["entitlements"]:
            raise MembershipError("External Sign-In returned entitlements for an inactive link.", code="invalid_response")
        return body

    async def unlink(self, link_token):
        await self.request(
            "/unlink",
            {"link_token": link_token},
            allow_empty_success=True,
        )
