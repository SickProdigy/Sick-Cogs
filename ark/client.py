import asyncio
import html
import re
from typing import Any, Dict, Iterable, List, Optional

import aiohttp


APP_ID = 2399830
API_URL = "https://api.steampowered.com/ISteamNews/GetNewsForApp/v2/"
ANNOUNCEMENTS_URL = f"https://steamcommunity.com/app/{APP_ID}/announcements/"
USER_AGENT = "Sick-Cogs-ArkAnnouncements/1.0.2 (+https://github.com/SickProdigy/Sick-Cogs)"
STEAM_CLAN_IMAGE_ROOT = "https://clan.steamstatic.com/images"

CATEGORIES = ("updates", "hotfixes", "community", "events", "wipes", "releases", "promotions")
CATEGORY_LABELS = {
    "updates": "Update or Patch Notes",
    "hotfixes": "Explicitly Named Hotfix",
    "community": "Community Crunch",
    "events": "In-Game Event",
    "wipes": "Wipe / Transfer",
    "releases": "Content Release",
    "promotions": "Promotion",
    "official": "Official Announcement",
}

_BB_IMAGE_RE = re.compile(r"\[img\](.+?)\[/img\]", re.IGNORECASE | re.DOTALL)
_HTML_IMAGE_RE = re.compile(r'<img[^>]+src=["\']([^"\']+)', re.IGNORECASE)
_FULL_RESOLUTION_RE = re.compile(r"download\s+in\s+full\s+resolution", re.IGNORECASE)
_FULL_RESOLUTION_LINE_RE = re.compile(
    r"^[ \t]*download\s+in\s+full\s+resolution[ \t]*$",
    re.IGNORECASE | re.MULTILINE,
)
_BB_TAG_RE = re.compile(r"\[/?[a-z*][^\]]*\]", re.IGNORECASE)
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"[ \t]+")
_BLANK_RE = re.compile(r"\n{3,}")


class SteamNewsError(RuntimeError):
    pass


def normalize_image_url(value: str) -> Optional[str]:
    url = html.unescape(str(value or "").strip())
    url = url.replace("{STEAM_CLAN_IMAGE}", STEAM_CLAN_IMAGE_ROOT)
    url = url.replace(
        "https://clan.cloudflare.steamstatic.com/images",
        STEAM_CLAN_IMAGE_ROOT,
    )
    if url.startswith("//"):
        url = f"https:{url}"
    if not url.startswith(("https://", "http://")):
        return None
    return url


def extract_image(contents: str) -> Optional[str]:
    source = contents or ""
    full_resolution = _FULL_RESOLUTION_RE.search(source)
    if full_resolution:
        preceding_images = [
            match for match in _BB_IMAGE_RE.finditer(source, 0, full_resolution.start())
        ]
        for match in reversed(preceding_images):
            image = normalize_image_url(match.group(1))
            if image:
                return image

    for pattern in (_BB_IMAGE_RE, _HTML_IMAGE_RE):
        for match in pattern.finditer(source):
            image = normalize_image_url(match.group(1))
            if image:
                return image
    return None


def plain_text(contents: str, *, limit: int = 900) -> str:
    text = str(contents or "").replace("{STEAM_CLAN_IMAGE}", "")
    text = _BB_IMAGE_RE.sub("", text)
    text = re.sub(r"\[url=([^\]]+)\](.*?)\[/url\]", r"\2", text, flags=re.IGNORECASE | re.DOTALL)
    text = text.replace("[*]", "• ")
    text = _BB_TAG_RE.sub("", text)
    text = _HTML_TAG_RE.sub("", text)
    text = html.unescape(text).replace("\r\n", "\n").replace("\r", "\n")
    text = _FULL_RESOLUTION_LINE_RE.sub("", text)
    text = "\n".join(_SPACE_RE.sub(" ", line).strip() for line in text.splitlines())
    text = _BLANK_RE.sub("\n\n", text).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def classify_news(item: Dict[str, Any]) -> str:
    title = str(item.get("title") or "").casefold()
    sample = plain_text(str(item.get("contents") or ""), limit=1500).casefold()
    combined = f"{title}\n{sample}"

    if "community crunch" in title:
        return "community"
    if "hotfix" in combined:
        return "hotfixes"
    if any(word in combined for word in ("wipe", "wiped", "server transfer", "transfers open", "transfers on")):
        return "wipes"
    if any(word in title for word in ("event", "bonus rates", "evolution event")):
        return "events"
    if any(word in title for word in ("sale", "discount", "free weekend")):
        return "promotions"
    if any(word in title for word in ("patch", "update", "upgrade", "client version", "server version")):
        return "updates"
    if any(word in title for word in ("out now", "now live", "launch", "released", "available now")):
        return "releases"
    return "official"


def item_id(item: Dict[str, Any]) -> str:
    return str(item.get("gid") or item.get("id") or "").strip()


def new_items(items: Iterable[Dict[str, Any]], posted_ids: Iterable[str]) -> List[Dict[str, Any]]:
    seen = {str(value) for value in posted_ids}
    pending = [item for item in items if item_id(item) and item_id(item) not in seen]
    return sorted(pending, key=lambda item: (int(item.get("date") or 0), item_id(item)))


def recent_items(items: Iterable[Dict[str, Any]], count: int) -> List[Dict[str, Any]]:
    ordered = sorted(
        items, key=lambda item: (int(item.get("date") or 0), item_id(item)), reverse=True
    )[:count]
    return list(reversed(ordered))


class SteamNewsClient:
    def __init__(self, session: aiohttp.ClientSession):
        self.session = session

    async def fetch(self, *, count: int = 50) -> List[Dict[str, Any]]:
        params = {"appid": APP_ID, "count": max(1, min(int(count), 100)), "maxlength": 0, "format": "json"}
        try:
            async with self.session.get(API_URL, params=params) as response:
                if response.status >= 400:
                    raise SteamNewsError(f"Steam News returned HTTP {response.status}.")
                payload = await response.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
            raise SteamNewsError("Could not retrieve ARK announcements from Steam.") from exc

        items = payload.get("appnews", {}).get("newsitems", []) if isinstance(payload, dict) else []
        if not isinstance(items, list):
            raise SteamNewsError("Steam News returned an invalid response.")
        return [item for item in items if isinstance(item, dict) and item_id(item)]
