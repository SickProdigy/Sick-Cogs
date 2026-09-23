from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import aiohttp

from .client import (
    MAX_JSON_BYTES,
    NavidromeError,
    _read_limited,
    _response_peer_is_public,
    validate_base_url,
    validate_public_base_url,
)


class LidarrError(RuntimeError):
    """A safe, user-facing Lidarr error."""


class LidarrClient:
    """Small provider boundary for the Lidarr v1 API."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        base_url: str,
        api_key: str,
        *,
        public_only: bool = False,
        allow_http: bool = False,
    ):
        try:
            self.base_url = validate_base_url(base_url, allow_http=allow_http)
        except ValueError as exc:
            raise LidarrError(str(exc).replace("Navidrome", "Lidarr")) from exc
        self.session = session
        self.api_key = api_key
        self.public_only = public_only

    async def _validate_target(self) -> None:
        if self.public_only:
            try:
                await validate_public_base_url(self.base_url)
            except ValueError as exc:
                raise LidarrError(str(exc).replace("Navidrome", "Lidarr")) from exc

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Any:
        await self._validate_target()
        try:
            async with self.session.request(
                method,
                f"{self.base_url}/api/v1/{path.lstrip('/')}",
                params=params,
                json=payload,
                headers={"X-Api-Key": self.api_key},
                allow_redirects=False,
            ) as response:
                if self.public_only and not _response_peer_is_public(response):
                    raise LidarrError("The guild-managed Lidarr connection reached an unsafe destination.")
                if 300 <= response.status < 400:
                    raise LidarrError("Lidarr returned an unexpected redirect.")
                if response.status in {401, 403}:
                    raise LidarrError("Lidarr rejected the configured API key.")
                if response.status == 404:
                    raise LidarrError("The requested Lidarr resource was not found.")
                if response.status >= 400:
                    raise LidarrError(f"Lidarr returned HTTP {response.status}.")
                if response.status == 204:
                    return None
                data = await _read_limited(response, MAX_JSON_BYTES)
                if not data.strip():
                    return None
                try:
                    return json.loads(data.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise LidarrError("Lidarr returned an invalid response.") from exc
        except LidarrError:
            raise
        except NavidromeError as exc:
            raise LidarrError(str(exc).replace("Navidrome", "Lidarr")) from exc
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise LidarrError("Could not reach the configured Lidarr server.") from exc

    async def validate_configuration(
        self, *, root_folder_path: str, quality_profile_id: int, metadata_profile_id: int
    ) -> Dict[str, Any]:
        status = await self.request("GET", "system/status")
        roots = await self.request("GET", "rootfolder")
        qualities = await self.request("GET", "qualityprofile")
        metadata = await self.request("GET", "metadataprofile")
        if not isinstance(status, dict) or not status.get("version"):
            raise LidarrError("Lidarr did not return valid system status.")
        if not any(str(item.get("path")) == root_folder_path for item in roots or []):
            raise LidarrError("The configured Lidarr root folder does not exist.")
        if not any(int(item.get("id", 0)) == quality_profile_id for item in qualities or []):
            raise LidarrError("The configured Lidarr quality profile does not exist.")
        if not any(int(item.get("id", 0)) == metadata_profile_id for item in metadata or []):
            raise LidarrError("The configured Lidarr metadata profile does not exist.")
        return status

    async def lookup(self, media_type: str, term: str) -> List[Dict[str, Any]]:
        if media_type not in {"artist", "album"}:
            raise LidarrError("Lidarr requests support artists and albums, not individual tracks.")
        result = await self.request("GET", f"{media_type}/lookup", params={"term": term})
        if not isinstance(result, list):
            raise LidarrError("Lidarr returned an invalid lookup response.")
        return result

    async def artists(self) -> List[Dict[str, Any]]:
        result = await self.request("GET", "artist")
        return result if isinstance(result, list) else []

    async def albums(self, *, foreign_album_id: str) -> List[Dict[str, Any]]:
        result = await self.request("GET", "album", params={"foreignAlbumId": foreign_album_id})
        return result if isinstance(result, list) else []

    async def tags(self) -> List[Dict[str, Any]]:
        result = await self.request("GET", "tag")
        return result if isinstance(result, list) else []

    async def ensure_tag(self, label: str) -> int:
        for tag in await self.tags():
            if str(tag.get("label") or "").casefold() == label.casefold():
                return int(tag["id"])
        created = await self.request("POST", "tag", payload={"label": label})
        if not isinstance(created, dict) or not created.get("id"):
            raise LidarrError("Lidarr did not return the created tag.")
        return int(created["id"])

    async def add_artist(self, candidate: Dict[str, Any], settings: Dict[str, Any], tag_ids: List[int]):
        payload = dict(candidate)
        payload.update({
            "qualityProfileId": int(settings["quality_profile_id"]),
            "metadataProfileId": int(settings["metadata_profile_id"]),
            "rootFolderPath": settings["root_folder_path"],
            "monitored": True,
            "monitorNewItems": "all",
            "tags": sorted(set(int(value) for value in tag_ids)),
            "addOptions": {"monitor": settings.get("monitor", "all"), "searchForMissingAlbums": True},
        })
        return await self.request("POST", "artist", payload=payload)

    async def add_album(self, candidate: Dict[str, Any], settings: Dict[str, Any], tag_ids: List[int]):
        payload = dict(candidate)
        artist = dict(payload.get("artist") or {})
        artist.update({
            "qualityProfileId": int(settings["quality_profile_id"]),
            "metadataProfileId": int(settings["metadata_profile_id"]),
            "rootFolderPath": settings["root_folder_path"],
            "monitored": True,
            "tags": sorted(set(int(value) for value in tag_ids)),
        })
        payload.update({"artist": artist, "monitored": True, "addOptions": {"searchForNewAlbum": True}})
        return await self.request("POST", "album", payload=payload)
