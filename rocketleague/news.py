from __future__ import annotations

import asyncio
import html
import re
from typing import Any, Iterable, Optional

import aiohttp

APP_ID = 252950
API_URL = "https://api.steampowered.com/ISteamNews/GetNewsForApp/v2/"
ANNOUNCEMENTS_URL = f"https://steamcommunity.com/app/{APP_ID}/announcements/"
STEAM_CLAN_IMAGE_ROOT = "https://clan.steamstatic.com/images"
_BB_IMAGE_SRC_RE = re.compile(
    r"\[img[^\]]*\bsrc=[\"\x27]([^\"\x27]+)[\"\x27][^\]]*\](?:\[/img\])?",
    re.IGNORECASE | re.DOTALL,
)
_BB_IMAGE_RE = re.compile(r"\[img\](.+?)\[/img\]", re.IGNORECASE | re.DOTALL)
_HTML_IMAGE_RE = re.compile(r"<img[^>]+src=[\"\x27]([^\"\x27]+)", re.IGNORECASE)
_BLOCK_END_RE = re.compile(
    r"\[\/(?:p|h[1-6]|list|quote|table|tr)[^\]]*\]", re.IGNORECASE
)
_BLOCK_START_RE = re.compile(r"\[(?:p|h[1-6]|list|quote|table|tr)[^\]]*\]", re.IGNORECASE)
_LIST_ITEM_START_RE = re.compile(r"\[\*\]\s*\[p[^\]]*\]", re.IGNORECASE)
_LIST_ITEM_END_RE = re.compile(r"\[\/p\]\s*\[\/\*\]", re.IGNORECASE)
_LIST_END_RE = re.compile(r"\[\/\*\]", re.IGNORECASE)
_BREAK_RE = re.compile(r"\[(?:br|hr)\s*\/?\]", re.IGNORECASE)
_BB_TAG_RE = re.compile(r"\[\/?[a-z*][^\]]*\]", re.IGNORECASE)
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"[ \t]+")
_BLANK_RE = re.compile(r"\n{2,}")


class SteamNewsError(RuntimeError):
    pass


def normalize_image_url(value: str) -> Optional[str]:
    url = html.unescape(str(value or "").strip())
    url = url.replace("{STEAM_CLAN_IMAGE}", STEAM_CLAN_IMAGE_ROOT)
    url = url.replace("https://clan.cloudflare.steamstatic.com/images", STEAM_CLAN_IMAGE_ROOT)
    url = url.replace("https://clan.fastly.steamstatic.com/images", STEAM_CLAN_IMAGE_ROOT)
    if url.startswith("//"):
        url = f"https:{url}"
    return url if url.startswith(("https://", "http://")) else None


def extract_image(contents: str) -> Optional[str]:
    for pattern in (_BB_IMAGE_SRC_RE, _BB_IMAGE_RE, _HTML_IMAGE_RE):
        for match in pattern.finditer(contents or ""):
            image = normalize_image_url(match.group(1))
            if image:
                return image
    return None


def plain_text(contents: str, *, limit: int = 1400) -> str:
    text = str(contents or "").replace("{STEAM_CLAN_IMAGE}", "")
    text = _BB_IMAGE_RE.sub("", text)
    text = re.sub(
        r"\[url=([^\]]+)\](.*?)\[/url\]",
        r"\2",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    text = _LIST_ITEM_START_RE.sub("• ", text)
    text = _LIST_ITEM_END_RE.sub("\n", text)
    text = _BLOCK_END_RE.sub("\n", text)
    text = _BLOCK_START_RE.sub("\n", text)
    text = _LIST_END_RE.sub("\n", text)
    text = _BREAK_RE.sub("\n", text)
    text = text.replace("[*]", "• ")
    text = _BB_TAG_RE.sub("", text)
    text = _HTML_TAG_RE.sub("", text)
    text = html.unescape(text).replace("\r\n", "\n").replace("\r", "\n")
    text = "\n".join(_SPACE_RE.sub(" ", line).strip() for line in text.splitlines())
    text = _BLANK_RE.sub("\n", text).strip()
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."


def item_id(item: dict[str, Any]) -> str:
    return str(item.get("gid") or item.get("id") or "").strip()


def official_url(item: dict[str, Any]) -> str:
    identifier = item_id(item)
    if identifier.isdigit():
        return f"https://steamcommunity.com/games/{APP_ID}/announcements/detail/{identifier}"
    fallback = str(item.get("url") or "").strip()
    return fallback if fallback.startswith(("https://", "http://")) else ANNOUNCEMENTS_URL


def recent_items(
    items: Iterable[dict[str, Any]], count: int
) -> list[dict[str, Any]]:
    ordered = sorted(
        items, key=lambda item: (int(item.get("date") or 0), item_id(item)), reverse=True
    )[:count]
    return list(reversed(ordered))


def new_items(
    items: Iterable[dict[str, Any]], posted_ids: Iterable[str]
) -> list[dict[str, Any]]:
    seen = {str(value) for value in posted_ids}
    pending = [item for item in items if item_id(item) and item_id(item) not in seen]
    return sorted(pending, key=lambda item: (int(item.get("date") or 0), item_id(item)))


class SteamNewsClient:
    def __init__(self, session: aiohttp.ClientSession):
        self.session = session

    async def fetch(self, *, count: int = 50) -> list[dict[str, Any]]:
        params = {
            "appid": APP_ID,
            "count": max(1, min(int(count), 100)),
            "maxlength": 0,
            "format": "json",
        }
        try:
            async with self.session.get(API_URL, params=params) as response:
                if response.status >= 400:
                    raise SteamNewsError(f"Steam News returned HTTP {response.status}.")
                payload = await response.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
            raise SteamNewsError(
                "Could not retrieve Rocket League announcements from Steam."
            ) from exc
        items = (
            payload.get("appnews", {}).get("newsitems", [])
            if isinstance(payload, dict)
            else []
        )
        if not isinstance(items, list):
            raise SteamNewsError("Steam News returned an invalid response.")
        return [item for item in items if isinstance(item, dict) and item_id(item)]
