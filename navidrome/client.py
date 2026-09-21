import asyncio
import hashlib
import secrets
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urlsplit

import aiohttp


class NavidromeError(RuntimeError):
    """A safe, user-facing Navidrome error."""


def validate_base_url(value: str, *, allow_http: bool = False) -> str:
    value = value.strip().rstrip("/")
    parsed = urlsplit(value)
    if parsed.scheme not in ({"https", "http"} if allow_http else {"https"}):
        raise ValueError("The Navidrome URL must use HTTPS.")
    if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Enter a base URL without credentials, query parameters, or fragments.")
    return value


class NavidromeClient:
    API_VERSION = "1.16.1"
    CLIENT_NAME = "SickCogs"

    def __init__(self, session: aiohttp.ClientSession, base_url: str, username: str, password: str):
        self.session = session
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self._native_token: Optional[str] = None
        self._native_login_lock = asyncio.Lock()

    def _auth_params(self) -> Dict[str, str]:
        salt = secrets.token_hex(8)
        token = hashlib.md5((self.password + salt).encode("utf-8")).hexdigest()
        return {
            "u": self.username,
            "t": token,
            "s": salt,
            "v": self.API_VERSION,
            "c": self.CLIENT_NAME,
            "f": "json",
        }

    async def request(self, endpoint: str, **params: Any) -> Dict[str, Any]:
        query = self._auth_params()
        query.update({key: str(value) for key, value in params.items() if value is not None})
        url = f"{self.base_url}/rest/{endpoint}.view"
        try:
            async with self.session.get(url, params=query, allow_redirects=False) as response:
                if 300 <= response.status < 400:
                    raise NavidromeError("Navidrome returned an unexpected redirect.")
                if response.status in {401, 403}:
                    raise NavidromeError("Navidrome rejected the configured credentials.")
                if response.status >= 400:
                    raise NavidromeError(f"Navidrome returned HTTP {response.status}.")
                payload = await response.json(content_type=None)
        except NavidromeError:
            raise
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise NavidromeError("Could not reach the configured Navidrome server.") from exc
        except (TypeError, ValueError) as exc:
            raise NavidromeError("Navidrome returned an invalid response.") from exc

        root = payload.get("subsonic-response", {})
        if root.get("status") != "ok":
            error = root.get("error", {})
            code = error.get("code")
            suffix = f" (code {code})" if code is not None else ""
            raise NavidromeError(f"Navidrome rejected the request{suffix}.")
        return root

    async def ping(self) -> Dict[str, Any]:
        return await self.request("ping")

    async def newest_albums(self, size: int = 10) -> List[Dict[str, Any]]:
        root = await self.request("getAlbumList2", type="newest", size=max(1, min(size, 50)))
        return list(root.get("albumList2", {}).get("album", []) or [])

    async def library_summary(self) -> Dict[str, Any]:
        ping = await self.ping()
        folders = await self.request("getMusicFolders")
        artists = await self.request("getArtists")
        indexes = artists.get("artists", {}).get("index", []) or []
        artist_count = sum(len(index.get("artist", []) or []) for index in indexes)
        folder_items = folders.get("musicFolders", {}).get("musicFolder", []) or []
        return {
            "type": ping.get("type") or "Navidrome",
            "server_version": ping.get("serverVersion") or "Unknown",
            "api_version": ping.get("version") or "Unknown",
            "open_subsonic": bool(ping.get("openSubsonic")),
            "music_folders": len(folder_items),
            "artists": artist_count,
        }

    async def cover_art(self, cover_art_id: Optional[str]) -> Optional[bytes]:
        if not cover_art_id:
            return None
        params = self._auth_params()
        params.update({"id": str(cover_art_id), "size": "500"})
        try:
            async with self.session.get(
                f"{self.base_url}/rest/getCoverArt.view",
                params=params,
                allow_redirects=False,
            ) as response:
                if response.status != 200:
                    return None
                if int(response.headers.get("Content-Length", "0") or 0) > 8_000_000:
                    return None
                data = await response.read()
        except (aiohttp.ClientError, TimeoutError, ValueError):
            return None
        return data if 0 < len(data) <= 8_000_000 else None

    async def _native_login(self) -> str:
        if self._native_token:
            return self._native_token
        async with self._native_login_lock:
            if self._native_token:
                return self._native_token
            try:
                async with self.session.post(
                    f"{self.base_url}/auth/login",
                    json={"username": self.username, "password": self.password},
                    allow_redirects=False,
                ) as response:
                    if 300 <= response.status < 400:
                        raise NavidromeError("Navidrome returned an unexpected redirect.")
                    if response.status in {401, 403}:
                        raise NavidromeError("Navidrome rejected the configured admin credentials.")
                    if response.status >= 400:
                        raise NavidromeError(
                            f"Navidrome native login returned HTTP {response.status}."
                        )
                    payload = await response.json(content_type=None)
            except NavidromeError:
                raise
            except (aiohttp.ClientError, TimeoutError, TypeError, ValueError) as exc:
                raise NavidromeError("Could not authenticate with Navidrome's native API.") from exc
            token = str(payload.get("token") or "")
            if not token:
                raise NavidromeError("Navidrome native login did not return an access token.")
            if not payload.get("isAdmin"):
                raise NavidromeError(
                    "The configured Navidrome account must be an administrator for user management."
                )
            self._native_token = token
            return token

    async def native_request(
        self, method: str, path: str, *, payload: Optional[Dict[str, Any]] = None
    ) -> Any:
        token = await self._native_login()
        try:
            async with self.session.request(
                method,
                f"{self.base_url}/api/{path.lstrip('/')}",
                json=payload,
                headers={"X-ND-Authorization": f"Bearer {token}"},
                allow_redirects=False,
            ) as response:
                if 300 <= response.status < 400:
                    raise NavidromeError("Navidrome returned an unexpected redirect.")
                if response.status in {401, 403}:
                    self._native_token = None
                    raise NavidromeError(
                        "Navidrome denied user management; verify the configured account is admin."
                    )
                if response.status == 404:
                    raise NavidromeError(
                        "This Navidrome version does not expose the required native user API."
                    )
                if response.status >= 400:
                    raise NavidromeError(
                        f"Navidrome user management returned HTTP {response.status}."
                    )
                if response.status == 204:
                    return None
                text = await response.text()
                if not text.strip():
                    return None
                return await response.json(content_type=None)
        except NavidromeError:
            raise
        except (aiohttp.ClientError, TimeoutError, TypeError, ValueError) as exc:
            raise NavidromeError("Navidrome returned an invalid native API response.") from exc

    async def users(self) -> List[Dict[str, Any]]:
        result = await self.native_request("GET", "user/")
        if isinstance(result, list):
            return result
        if isinstance(result, dict):
            data = result.get("data", result.get("users", []))
            if isinstance(data, list):
                return data
        raise NavidromeError("Navidrome returned an invalid user list.")

    async def user_by_username(self, username: str) -> Optional[Dict[str, Any]]:
        wanted = username.casefold()
        for user in await self.users():
            if str(user.get("userName") or "").casefold() == wanted:
                return user
        return None

    async def create_user(
        self, username: str, password: str, *, name: str = "", email: str = ""
    ) -> Dict[str, Any]:
        payload = {
            "userName": username,
            "name": name or username,
            "email": email,
            "isAdmin": False,
            "password": password,
        }
        result = await self.native_request("POST", "user/", payload=payload)
        if isinstance(result, dict) and result.get("id"):
            user_id = str(result["id"])
            if result.get("userName"):
                return result
            fetched = await self.native_request("GET", f"user/{quote(user_id, safe='')}")
            if isinstance(fetched, dict):
                return fetched
        created = await self.user_by_username(username)
        if created:
            return created
        raise NavidromeError("Navidrome created the user but did not return its record.")

    async def update_user(self, user: Dict[str, Any], **changes: Any) -> Dict[str, Any]:
        user_id = str(user.get("id") or "")
        if not user_id:
            raise NavidromeError("The mapped Navidrome user has no ID.")
        payload = {
            "id": user_id,
            "userName": str(user.get("userName") or ""),
            "name": str(user.get("name") or ""),
            "email": str(user.get("email") or ""),
            "isAdmin": bool(user.get("isAdmin")),
        }
        payload.update(changes)
        await self.native_request(
            "PUT", f"user/{quote(user_id, safe='')}", payload=payload
        )
        result = await self.native_request("GET", f"user/{quote(user_id, safe='')}")
        if not isinstance(result, dict):
            raise NavidromeError("Navidrome returned an invalid updated user record.")
        return result

    async def delete_user(self, user_id: str) -> None:
        if not user_id:
            raise NavidromeError("The mapped Navidrome user has no ID.")
        await self.native_request("DELETE", f"user/{quote(user_id, safe='')}")
