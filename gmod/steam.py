"""Official Garry's Mod Steam News access and safe rendering helpers."""

import asyncio
import html
import re
from typing import Any, Dict, Iterable, List, Optional

import aiohttp


APP_ID = 4000
API_URL = "https://api.steampowered.com/ISteamNews/GetNewsForApp/v2/"
ANNOUNCEMENTS_URL = f"https://steamcommunity.com/app/{APP_ID}/announcements/"
USER_AGENT = "Sick-Cogs-GMod/1.0.1 (+https://github.com/SickProdigy/Sick-Cogs)"
OFFICIAL_FEED_NAME = "steam_community_announcements"

_BB_IMAGE_SRC_RE = re.compile(
    r"\[img[^\]]*\bsrc=[\"\x27]([^\"\x27]+)[\"\x27][^\]]*\](?:\[/img\])?",
    re.IGNORECASE | re.DOTALL,
)
_BB_IMAGE_RE = re.compile(r"\[img\](.+?)\[/img\]", re.IGNORECASE | re.DOTALL)
_HTML_IMAGE_RE = re.compile(r'<img[^>]+src=["\']([^"\']+)', re.IGNORECASE)
_BB_TAG_RE = re.compile(r"\[/?[a-z*][^\]]*\]", re.IGNORECASE)
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"[ \t]+")
_BLANK_RE = re.compile(r"\n{3,}")


class SteamNewsError(RuntimeError):
    pass


def is_official_news(item: Dict[str, Any]) -> bool:
    return str(item.get("feedname") or "").casefold() == OFFICIAL_FEED_NAME


def item_id(item: Dict[str, Any]) -> str:
    return str(item.get("gid") or item.get("id") or "").strip()


def new_items(items: Iterable[Dict[str, Any]], posted_ids: Iterable[str]) -> List[Dict[str, Any]]:
    seen = {str(value) for value in posted_ids}
    pending = [item for item in items if item_id(item) and item_id(item) not in seen]
    return sorted(pending, key=lambda item: (int(item.get("date") or 0), item_id(item)))


def recent_items(items: Iterable[Dict[str, Any]], count: int) -> List[Dict[str, Any]]:
    selected = sorted(
        items,
        key=lambda item: (int(item.get("date") or 0), item_id(item)),
        reverse=True,
    )[:count]
    return list(reversed(selected))


def plain_text(contents: str, *, limit: int = 900) -> str:
    text = str(contents or "").replace("{STEAM_CLAN_IMAGE}", "")
    text = _BB_IMAGE_RE.sub("", text)
    text = re.sub(
        r"\[url=([^\]]+)\](.*?)\[/url\]",
        r"\2",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    text = text.replace("[*]", "• ")
    text = _BB_TAG_RE.sub("", text)
    text = _HTML_TAG_RE.sub("", text)
    text = html.unescape(text).replace("\r\n", "\n").replace("\r", "\n")
    text = "\n".join(_SPACE_RE.sub(" ", line).strip() for line in text.splitlines())
    text = _BLANK_RE.sub("\n\n", text).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def image_url(contents: str) -> Optional[str]:
    source = str(contents or "")
    for pattern in (_BB_IMAGE_SRC_RE, _BB_IMAGE_RE, _HTML_IMAGE_RE):
        for match in pattern.finditer(source):
            value = html.unescape(match.group(1).strip()).replace(
                "{STEAM_CLAN_IMAGE}",
                "https://clan.steamstatic.com/images",
            )
            value = value.replace(
                "https://clan.cloudflare.steamstatic.com/images",
                "https://clan.steamstatic.com/images",
            ).replace(
                "https://clan.fastly.steamstatic.com/images",
                "https://clan.steamstatic.com/images",
            )
            if value.startswith("//"):
                value = f"https:{value}"
            if value.startswith(("https://", "http://")):
                return value
    return None


class SteamNewsClient:
    def __init__(self, session: aiohttp.ClientSession):
        self.session = session

    async def fetch(self, *, count: int = 50) -> List[Dict[str, Any]]:
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
            raise SteamNewsError("Could not retrieve Garry's Mod announcements from Steam.") from exc
        items = payload.get("appnews", {}).get("newsitems", []) if isinstance(payload, dict) else []
        if not isinstance(items, list):
            raise SteamNewsError("Steam News returned an invalid response.")
        return [
            item
            for item in items
            if isinstance(item, dict) and item_id(item) and is_official_news(item)
        ]
