"""Small async client for the SickProdigy MyBB API v1 plugin."""

import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit

import aiohttp


class MyBBAPIError(RuntimeError):
    def __init__(self, message, *, code="api_error", status=0, retry_after=None):
        super().__init__(message)
        self.code = code
        self.status = status
        self.retry_after = retry_after


def normalize_base_url(value):
    base = value.strip().rstrip("/")
    parsed = urlsplit(base)
    if parsed.scheme != "https":
        raise ValueError("The MyBB URL must use HTTPS.")
    if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Enter a board URL without credentials, query parameters, or fragments.")
    if parsed.hostname.casefold() == "localhost":
        raise ValueError("Local and private MyBB addresses are not allowed.")
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise ValueError("Local and private MyBB addresses are not allowed.")
    if base.endswith("/api/v1"):
        base = base[:-7]
    return base


async def reject_private_destination(base_url):
    """Reject connector destinations resolving to loopback/private/link-local networks."""
    parsed = urlsplit(base_url)
    port = parsed.port or 443
    try:
        results = await asyncio.get_running_loop().getaddrinfo(
            parsed.hostname, port, type=socket.SOCK_STREAM
        )
    except OSError as exc:
        raise MyBBAPIError("The MyBB hostname could not be resolved.", code="connection_failed") from exc
    addresses = {item[4][0] for item in results}
    if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise MyBBAPIError(
            "The MyBB hostname resolves to a local or private address.", code="unsafe_destination"
        )


class MyBBClient:
    def __init__(self, base_url, token=None):
        self.base_url = normalize_base_url(base_url)
        self.api_url = self.base_url + "/api/v1"
        self.token = token.strip() if token else None

    async def request(self, method, path, *, params=None, payload=None, idempotency_key=None):
        await reject_private_destination(self.base_url)
        headers = {"Accept": "application/json", "User-Agent": "SickCogs-MyBB/0.1"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if payload is not None:
            headers["Content-Type"] = "application/json"
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        timeout = aiohttp.ClientTimeout(total=20)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.request(
                    method,
                    self.api_url + path,
                    params=params,
                    json=payload,
                    headers=headers,
                    allow_redirects=False,
                ) as response:
                    if 300 <= response.status < 400:
                        raise MyBBAPIError(
                            "MyBB returned an unexpected redirect.",
                            code="unexpected_redirect",
                            status=response.status,
                        )
                    try:
                        body = await response.json(content_type=None)
                    except (aiohttp.ContentTypeError, ValueError):
                        body = {}
                    if response.status >= 400:
                        error = body.get("error", {}) if isinstance(body, dict) else {}
                        raise MyBBAPIError(
                            error.get("message") or f"MyBB API returned HTTP {response.status}.",
                            code=error.get("code", "http_error"),
                            status=response.status,
                            retry_after=response.headers.get("Retry-After"),
                        )
        except MyBBAPIError:
            raise
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise MyBBAPIError("Could not connect to the MyBB API.", code="connection_failed") from exc
        if not isinstance(body, dict):
            raise MyBBAPIError("MyBB returned an invalid response.", code="invalid_response")
        return body

    async def health(self):
        return await self.request("GET", "/health")

    async def forums(self):
        return await self.request("GET", "/forums")

    async def threads(self, forum_id, page=1, per_page=20):
        return await self.request(
            "GET",
            f"/forums/{int(forum_id)}/threads",
            params={"page": max(1, page), "per_page": min(max(1, per_page), 100)},
        )

    async def thread(self, thread_id):
        return await self.request("GET", f"/threads/{int(thread_id)}")

    async def publish_thread(self, payload, idempotency_key):
        return await self.request(
            "POST", "/threads", payload=payload, idempotency_key=idempotency_key
        )
