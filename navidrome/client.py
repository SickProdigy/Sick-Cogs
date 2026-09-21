import hashlib
import secrets
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

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
