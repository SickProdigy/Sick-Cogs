"""Minimal Google Calendar REST client using service-account authentication."""

import asyncio
import base64
import json
import time
from urllib.parse import quote

import aiohttp
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

SCOPE = "https://www.googleapis.com/auth/calendar"
TOKEN_URL = "https://oauth2.googleapis.com/token"
API_ROOT = "https://www.googleapis.com/calendar/v3"


def _b64(value):
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def service_assertion(client_email, private_key, now=None):
    issued = int(time.time() if now is None else now)
    header = _b64(json.dumps({"alg": "RS256", "typ": "JWT"}, separators=(",", ":")).encode())
    claims = _b64(json.dumps({
        "iss": client_email, "scope": SCOPE, "aud": TOKEN_URL,
        "iat": issued, "exp": issued + 3600,
    }, separators=(",", ":")).encode())
    unsigned = f"{header}.{claims}".encode("ascii")
    key = serialization.load_pem_private_key(private_key.replace("\\n", "\n").encode(), password=None)
    signature = key.sign(unsigned, padding.PKCS1v15(), hashes.SHA256())
    return f"{header}.{claims}.{_b64(signature)}"


class GoogleCalendarClient:
    def __init__(self, client_email, private_key):
        self.client_email = client_email
        self.private_key = private_key
        self._access_token = None
        self._expires_at = 0

    @staticmethod
    def calendar_path(calendar_id, suffix=""):
        return f"/calendars/{quote(calendar_id, safe='')}{suffix}"

    async def _token(self):
        if self._access_token and self._expires_at > time.time() + 60:
            return self._access_token
        assertion = await asyncio.to_thread(service_assertion, self.client_email, self.private_key)
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
            async with session.post(TOKEN_URL, data={
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion,
            }) as response:
                data = await response.json(content_type=None)
                if response.status >= 400:
                    raise RuntimeError(data.get("error_description") or data.get("error") or f"OAuth HTTP {response.status}")
        self._access_token = data["access_token"]
        self._expires_at = time.time() + int(data.get("expires_in", 3600))
        return self._access_token

    async def request(self, method, path, *, params=None, payload=None):
        token = await self._token()
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
            async with session.request(method, API_ROOT + path, params=params, json=payload,
                                       headers={"Authorization": f"Bearer {token}"}) as response:
                data = {} if response.status == 204 else await response.json(content_type=None)
                message = data.get("error", {}).get("message") if isinstance(data, dict) else None
                if response.status == 404:
                    raise LookupError(message or "Calendar or event not found.")
                if response.status == 403:
                    raise PermissionError(message or "Calendar access denied.")
                if response.status >= 400:
                    raise RuntimeError(message or f"Google Calendar HTTP {response.status}")
                return data
