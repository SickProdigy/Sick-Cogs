from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Union

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
                timeout=aiohttp.ClientTimeout(total=60),
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
            operation = path.strip("/").split("?", 1)[0] or "API"
            raise LidarrError(
                f"Could not reach the configured Lidarr server while requesting `{operation}`."
            ) from exc

    async def discover_configuration(
        self, *, root_folder_path: Optional[str] = None,
        quality_profile_id: Optional[Union[int, str]] = None,
        metadata_profile_id: Optional[Union[int, str]] = None,
    ) -> Dict[str, Any]:
        status = await self.request("GET", "system/status")
        roots = await self.request("GET", "rootfolder")
        qualities = await self.request("GET", "qualityprofile")
        metadata = await self.request("GET", "metadataprofile")
        if not isinstance(status, dict) or not status.get("version"):
            raise LidarrError("Lidarr did not return valid system status.")
        usable_roots = [item for item in roots or [] if item.get("accessible", True)]
        root = next(
            (item for item in usable_roots if str(item.get("path")) == root_folder_path),
            None,
        ) if root_folder_path else (usable_roots[0] if usable_roots else None)
        if root is None:
            raise LidarrError(
                "The configured Lidarr root folder does not exist." if root_folder_path
                else "Lidarr has no accessible root folder."
            )
        def choose_profile(items, requested, default_id, label):
            if not items:
                raise LidarrError(f"Lidarr has no {label} profiles.")
            if requested is None:
                selected = next(
                    (item for item in items if int(item.get("id", 0)) == int(default_id or 0)),
                    items[0],
                )
            else:
                value = str(requested).strip()
                selected = (
                    next((item for item in items if int(item.get("id", 0)) == int(value)), None)
                    if value.isdigit() else
                    next((item for item in items if str(item.get("name", "")).casefold() == value.casefold()), None)
                )
                if selected is None:
                    available = ", ".join(str(item.get("name") or item.get("id")) for item in items)
                    raise LidarrError(f"Unknown Lidarr {label} profile. Available: {available}.")
            return int(selected["id"]), str(selected.get("name") or selected["id"])

        quality_id, quality_name = choose_profile(
            qualities or [], quality_profile_id, root.get("defaultQualityProfileId"), "quality"
        )
        metadata_id, metadata_name = choose_profile(
            metadata or [], metadata_profile_id, root.get("defaultMetadataProfileId"), "metadata"
        )
        return {
            "status": status, "root_folder_path": str(root["path"]),
            "quality_profile_id": quality_id, "quality_profile_name": quality_name,
            "metadata_profile_id": metadata_id, "metadata_profile_name": metadata_name,
        }

    async def validate_configuration(
        self, *, root_folder_path: str, quality_profile_id: int, metadata_profile_id: int
    ) -> Dict[str, Any]:
        discovered = await self.discover_configuration(
            root_folder_path=root_folder_path, quality_profile_id=quality_profile_id,
            metadata_profile_id=metadata_profile_id,
        )
        return discovered["status"]

    async def lookup(self, media_type: str, term: str) -> List[Dict[str, Any]]:
        if media_type not in {"artist", "album"}:
            raise LidarrError("Lidarr requests support artists and albums, not individual tracks.")
        result = await self.request("GET", f"{media_type}/lookup", params={"term": term})
        if not isinstance(result, list):
            raise LidarrError("Lidarr returned an invalid lookup response.")
        return result

    async def artists(self, *, mb_id: Optional[str] = None) -> List[Dict[str, Any]]:
        params = {"mbId": mb_id} if mb_id else None
        result = await self.request("GET", "artist", params=params)
        return result if isinstance(result, list) else []

    async def albums(self, *, foreign_album_id: str) -> List[Dict[str, Any]]:
        result = await self.request("GET", "album", params={"foreignAlbumId": foreign_album_id})
        return result if isinstance(result, list) else []

    async def search_album(self, album_id: int) -> Dict[str, Any]:
        result = await self.request(
            "POST", "command", payload={"name": "AlbumSearch", "albumIds": [int(album_id)]}
        )
        if not isinstance(result, dict) or not result.get("id"):
            raise LidarrError("Lidarr did not confirm the album search command.")
        return result

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

    async def update_artist_tags(self, artist: Dict[str, Any], tag_ids: List[int]):
        payload = dict(artist)
        payload["tags"] = sorted(set(
            int(value) for value in list(payload.get("tags") or []) + list(tag_ids)
        ))
        return await self.request("PUT", f"artist/{int(payload['id'])}", payload=payload)

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
