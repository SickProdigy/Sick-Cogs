import asyncio
import html
import io
import re
from typing import Any, Dict, Iterable, List, Optional

import aiohttp
from PIL import Image, UnidentifiedImageError


APP_ID = 2399830
API_URL = "https://api.steampowered.com/ISteamNews/GetNewsForApp/v2/"
ANNOUNCEMENTS_URL = f"https://steamcommunity.com/app/{APP_ID}/announcements/"
USER_AGENT = "Sick-Cogs-Ark/1.1.3 (+https://github.com/SickProdigy/Sick-Cogs)"
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
_YOUTUBE_RE = re.compile(
    r"\[previewyoutube=([^;\]\s]+)(?:;[^\]]*)?\].*?\[/previewyoutube\]",
    re.IGNORECASE | re.DOTALL,
)
_FULL_RESOLUTION_LINE_RE = re.compile(
    r"^[ \t]*download(?:\s+all\s+screenshots)?\s+in\s+full\s+resolution[ \t]*$",
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
    for pattern in (_BB_IMAGE_RE, _HTML_IMAGE_RE):
        for match in pattern.finditer(source):
            image = normalize_image_url(match.group(1))
            if image:
                return image
    return None


def extract_images(contents: str, *, limit: int = 4) -> List[str]:
    """Return unique announcement images in Steam's original order."""
    if limit <= 0:
        return []
    source = contents or ""
    candidates = []
    featured = extract_image(source)
    if featured:
        candidates.append(featured)
    for pattern in (_BB_IMAGE_RE, _HTML_IMAGE_RE):
        for match in pattern.finditer(source):
            image = normalize_image_url(match.group(1))
            if image and image not in candidates:
                candidates.append(image)
    return candidates[:limit]


def extract_youtube_urls(contents: str, *, limit: int = 1) -> List[str]:
    """Convert Steam previewyoutube markup into native YouTube links for Discord."""
    if limit <= 0:
        return []
    urls = []
    for match in _YOUTUBE_RE.finditer(contents or ""):
        url = f"https://youtu.be/{match.group(1)}"
        if url not in urls:
            urls.append(url)
        if len(urls) >= limit:
            break
    return urls


def image_dimensions(payload: bytes):
    """Read PNG or JPEG dimensions without adding an image-library dependency."""
    if len(payload) >= 24 and payload[:8] == b"\x89PNG\r\n\x1a\n":
        return int.from_bytes(payload[16:20], "big"), int.from_bytes(payload[20:24], "big")
    if payload[:2] != b"\xff\xd8":
        return None
    position = 2
    start_of_frame = {
        0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
        0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF,
    }
    while position + 9 <= len(payload):
        if payload[position] != 0xFF:
            position += 1
            continue
        marker = payload[position + 1]
        position += 2
        if marker in {0xD8, 0xD9}:
            continue
        if position + 2 > len(payload):
            break
        segment_length = int.from_bytes(payload[position : position + 2], "big")
        if marker in start_of_frame and position + 7 <= len(payload):
            height = int.from_bytes(payload[position + 3 : position + 5], "big")
            width = int.from_bytes(payload[position + 5 : position + 7], "big")
            return width, height
        position += max(segment_length, 2)
    return None


def optimize_gallery_image(payload: bytes, *, max_edge: int = 1600, quality: int = 82) -> bytes:
    """Resize a gallery image and encode it as a compact progressive JPEG."""
    try:
        with Image.open(io.BytesIO(payload)) as source:
            source.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
            image = source.convert("RGB")
            output = io.BytesIO()
            image.save(output, format="JPEG", quality=quality, optimize=True, progressive=True)
            return output.getvalue()
    except (OSError, UnidentifiedImageError, ValueError) as exc:
        raise ValueError("Unsupported announcement image.") from exc


def plain_text(contents: str, *, limit: int = 900, youtube_links: bool = False) -> str:
    text = str(contents or "").replace("{STEAM_CLAN_IMAGE}", "")
    text = _BB_IMAGE_RE.sub("", text)
    if youtube_links:
        text = _YOUTUBE_RE.sub(
            lambda match: f"Watch trailer on YouTube: https://youtu.be/{match.group(1)}",
            text,
        )
    else:
        text = _YOUTUBE_RE.sub("", text)
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
