from __future__ import annotations

import asyncio
import json
import re
import time
from datetime import datetime, timedelta, timezone
from html import unescape
from html.parser import HTMLParser
from typing import Optional
from urllib.parse import parse_qs, urlparse

import aiohttp


MEDAL_ROCKET_LEAGUE_CATEGORY_ID = "adufon9HW"
MEDAL_ROCKET_LEAGUE_URL = "https://medal.tv/games/rocket-league"
TWITCH_API_URL = "https://api.twitch.tv/helix"
TWITCH_TOKEN_URL = "https://id.twitch.tv/oauth2/token"
YOUTUBE_API_URL = "https://www.googleapis.com/youtube/v3"
HYDRATION_RE = re.compile(r"var hydrationData=(\{.*?\})</script>", re.DOTALL)
ISO_DURATION_RE = re.compile(
    r"^P(?:(?P<days>\d+)D)?(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>[\d.]+)S)?)?$"
)


class ClipSourceError(RuntimeError):
    """A safe, user-facing clip provider error."""


class _MetaParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.values = {}

    def handle_starttag(self, tag, attrs):
        if tag != "meta":
            return
        attributes = dict(attrs)
        key = str(attributes.get("property") or attributes.get("name") or "").casefold()
        content = str(attributes.get("content") or "")
        if key and content and key not in self.values:
            self.values[key] = content


def _medal_clip_links(html: str) -> list[str]:
    paths = re.findall(r"/games/rocket-league/clips/[A-Za-z0-9_-]+", html)
    return [f"https://medal.tv{path}" for path in dict.fromkeys(paths)]


def _medal_clip_from_html(url: str, html: str) -> Optional[dict]:
    parser = _MetaParser()
    parser.feed(html)
    title = parser.values.get("og:title") or parser.values.get("twitter:title")
    description = parser.values.get("og:description") or parser.values.get("description") or ""
    video_url = parser.values.get("og:video") or ""
    duration_value = (parse_qs(urlparse(video_url).query).get("v") or [0])[0]
    promotional = re.compile(
        r"(?:discord\.gg|discord(?:app)?\.com/invite|discord\s+server|server\s+link|join\s+my\s+(?:discord|server))",
        re.IGNORECASE,
    )
    try:
        duration = float(duration_value)
    except (TypeError, ValueError):
        duration = 0
    clip_id = urlparse(url).path.rstrip("/").split("/")[-1]
    creator_match = re.search(r" by (.+?) and millions of other", description, re.IGNORECASE)
    if not title or not clip_id or duration <= 0 or promotional.search(f"{title} {description}"):
        return None
    title = re.sub(r" - Clipped Rocket League with Medal\.tv$", "", title).strip()
    return {
        "provider": "medal",
        "clip_id": clip_id,
        "title": title[:200],
        "creator": (creator_match.group(1) if creator_match else "Medal creator")[:100],
        "url": url,
        "video_url": video_url,
        "duration": duration,
        "published_at": 0,
        "thumbnail": parser.values.get("og:image") or parser.values.get("twitter:image") or "",
        "views": 0,
    }


def _clean_url(value: str) -> str:
    return value.strip().strip("<>")


def _medal_hydration(html: str) -> dict:
    match = HYDRATION_RE.search(html)
    if not match:
        raise ClipSourceError("Medal did not return public profile or clip details.")
    try:
        payload = json.loads(unescape(match.group(1)))
    except (json.JSONDecodeError, TypeError) as exc:
        raise ClipSourceError("Medal returned an unexpected public page.") from exc
    if not isinstance(payload, dict):
        raise ClipSourceError("Medal returned an unexpected public page.")
    return payload


def _first_mapping_value(value) -> Optional[dict]:
    if not isinstance(value, dict):
        return None
    return next((item for item in value.values() if isinstance(item, dict)), None)


def _iso_duration_seconds(value: str) -> Optional[float]:
    match = ISO_DURATION_RE.fullmatch(str(value or ""))
    if not match:
        return None
    parts = {key: float(item or 0) for key, item in match.groupdict().items()}
    return (
        parts["days"] * 86400
        + parts["hours"] * 3600
        + parts["minutes"] * 60
        + parts["seconds"]
    )


def detect_clip_source(value: str, provider: Optional[str] = None) -> tuple[str, str]:
    """Return a provider and normalized input without contacting it."""
    value = _clean_url(value)
    provider = str(provider or "").strip().casefold()
    aliases = {"yt": "youtube", "youtube": "youtube", "medal": "medal", "twitch": "twitch"}
    if provider:
        provider = aliases.get(provider, provider)
        if provider not in {"medal", "twitch", "youtube"}:
            raise ClipSourceError("Use Medal, Twitch, or YouTube as the clip provider.")
        if not value:
            raise ClipSourceError("Provide a creator name, channel, playlist, or supported URL.")
        return provider, value
    parsed = urlparse(value)
    host = (parsed.hostname or "").casefold()
    if host == "medal.tv" or host.endswith(".medal.tv"):
        return "medal", value
    if host in {"twitch.tv", "www.twitch.tv", "clips.twitch.tv"}:
        return "twitch", value
    if host in {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}:
        return "youtube", value
    raise ClipSourceError(
        "Use a recognized Medal, Twitch, or YouTube URL, or specify a provider before the name."
    )


def clip_identity(clip: dict) -> str:
    return f"{clip.get('provider')}:{clip.get('clip_id')}"


class ClipProviders:
    """Resolve and fetch normalized short-form Rocket League clip sources."""

    def __init__(self, bot, session: aiohttp.ClientSession):
        self.bot = bot
        self.session = session
        self._twitch_token: Optional[str] = None
        self._twitch_token_expires_at = 0.0
        self._twitch_game_id: Optional[str] = None

    async def resolve(self, provider: str, value: str) -> tuple[dict, list[dict]]:
        if provider == "medal":
            source = await self._resolve_medal(value)
        elif provider == "twitch":
            source = await self._resolve_twitch(value)
        elif provider == "youtube":
            source = await self._resolve_youtube(value)
        else:
            raise ClipSourceError("That clip provider is not supported.")
        return source, await self.fetch(source)

    async def fetch(self, source: dict) -> list[dict]:
        provider = str(source.get("provider") or "")
        if provider == "medal":
            return await self._fetch_medal(source)
        if provider == "twitch":
            return await self._fetch_twitch(source)
        if provider == "youtube":
            return await self._fetch_youtube(source)
        raise ClipSourceError("That clip provider is not supported.")

    async def _html(self, url: str) -> str:
        try:
            async with self.session.get(url, headers={"Accept": "text/html"}) as response:
                if response.status == 404:
                    raise ClipSourceError("That public page was not found.")
                if response.status == 429:
                    raise ClipSourceError("The clip provider is rate limiting requests.")
                if response.status >= 500:
                    raise ClipSourceError("The clip provider is temporarily unavailable.")
                if response.status != 200:
                    raise ClipSourceError(
                        f"The clip provider rejected that page (HTTP {response.status})."
                    )
                return await response.text()
        except ClipSourceError:
            raise
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise ClipSourceError("Could not reach the clip provider. Try again shortly.") from exc

    async def _resolve_medal(self, value: str) -> dict:
        value = _clean_url(value)
        parsed = urlparse(value)
        if not parsed.scheme:
            value = f"https://medal.tv/u/{value.lstrip('@')}"
            parsed = urlparse(value)
        host = (parsed.hostname or "").casefold()
        if host != "medal.tv" and not host.endswith(".medal.tv"):
            raise ClipSourceError("Use a Medal username, profile URL, clip URL, or game URL.")
        path = parsed.path.rstrip("/")
        if path.casefold() == "/games/rocket-league":
            return {
                "provider": "medal",
                "kind": "game",
                "key": MEDAL_ROCKET_LEAGUE_CATEGORY_ID,
                "name": "Medal Rocket League",
                "url": MEDAL_ROCKET_LEAGUE_URL,
            }
        html = await self._html(value)
        if "/clips/" in path.casefold():
            clip = _medal_clip_from_html(value, html)
            if clip is None or clip.get("creator") == "Medal creator":
                raise ClipSourceError("That Medal clip does not identify a public creator.")
            username = str(clip.get("creator"))
            profile_url = f"https://medal.tv/u/{username}"
            await self._html(profile_url)
            return {
                "provider": "medal",
                "kind": "profile",
                "key": username.casefold(),
                "name": username,
                "url": profile_url,
            }
        if not path.casefold().startswith("/u/"):
            raise ClipSourceError("Use a Medal username, profile URL, clip URL, or Rocket League game URL.")
        username = path.split("/", 2)[-1]
        try:
            hydration = _medal_hydration(html)
            profile = _first_mapping_value(hydration.get("profiles"))
            if isinstance(profile, dict):
                username = str(profile.get("userName") or profile.get("displayName") or username)
        except ClipSourceError:
            pass
        return {
            "provider": "medal",
            "kind": "profile",
            "key": username.casefold(),
            "name": username,
            "url": f"https://medal.tv/u/{username}",
        }

    async def _fetch_medal(self, source: dict) -> list[dict]:
        html = await self._html(str(source.get("url") or ""))
        links = _medal_clip_links(html)[:25]
        if not links:
            raise ClipSourceError("Medal did not expose any public Rocket League clips on that page.")
        semaphore = asyncio.Semaphore(4)

        async def fetch_one(url: str):
            async with semaphore:
                try:
                    return _medal_clip_from_html(url, await self._html(url))
                except ClipSourceError:
                    return None

        clips = await asyncio.gather(*(fetch_one(url) for url in links))
        return [clip for clip in clips if clip is not None]

    async def _twitch_access_token(self) -> tuple[str, str]:
        credentials = await self.bot.get_shared_api_tokens("twitch")
        client_id = str(credentials.get("client_id") or "").strip()
        client_secret = str(credentials.get("client_secret") or "").strip()
        if not client_id or not client_secret:
            raise ClipSourceError(
                "Configure Twitch with `set api twitch client_id,YOUR_ID client_secret,YOUR_SECRET`."
            )
        if self._twitch_token and time.monotonic() < self._twitch_token_expires_at:
            return client_id, self._twitch_token
        try:
            async with self.session.post(
                TWITCH_TOKEN_URL,
                params={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "grant_type": "client_credentials",
                },
            ) as response:
                payload = await response.json(content_type=None)
                if response.status != 200:
                    raise ClipSourceError(
                        f"Twitch rejected the shared API credentials (HTTP {response.status})."
                    )
        except ClipSourceError:
            raise
        except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
            raise ClipSourceError("Could not authenticate with Twitch.") from exc
        token = str(payload.get("access_token") or "")
        if not token:
            raise ClipSourceError("Twitch returned an unexpected authentication response.")
        self._twitch_token = token
        self._twitch_token_expires_at = time.monotonic() + max(
            60, int(payload.get("expires_in") or 3600) - 60
        )
        return client_id, token

    async def _twitch_get(self, path: str, params: dict) -> dict:
        client_id, token = await self._twitch_access_token()
        headers = {"Client-Id": client_id, "Authorization": f"Bearer {token}"}
        try:
            async with self.session.get(
                f"{TWITCH_API_URL}{path}", params=params, headers=headers
            ) as response:
                payload = await response.json(content_type=None)
                if response.status == 401:
                    self._twitch_token = None
                    self._twitch_token_expires_at = 0.0
                    raise ClipSourceError("Twitch expired the application token. Retry the request.")
                if response.status == 429:
                    raise ClipSourceError("Twitch is rate limiting requests.")
                if response.status != 200:
                    raise ClipSourceError(
                        f"Twitch could not complete that request (HTTP {response.status})."
                    )
                return payload
        except ClipSourceError:
            raise
        except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
            raise ClipSourceError("Could not reach Twitch. Try again shortly.") from exc

    @staticmethod
    def _twitch_name(value: str) -> tuple[Optional[str], Optional[str]]:
        value = _clean_url(value)
        parsed = urlparse(value)
        if not parsed.scheme:
            return value.lstrip("@"), None
        host = (parsed.hostname or "").casefold()
        parts = [part for part in parsed.path.split("/") if part]
        if host == "clips.twitch.tv" and parts:
            return None, parts[0]
        if host in {"twitch.tv", "www.twitch.tv"} and parts:
            if parts[0].casefold() == "clip" and len(parts) > 1:
                return None, parts[1]
            return parts[0], None
        raise ClipSourceError("Use a Twitch broadcaster name, channel URL, or clip URL.")

    async def _resolve_twitch(self, value: str) -> dict:
        login, clip_id = self._twitch_name(value)
        if clip_id:
            payload = await self._twitch_get("/clips", {"id": clip_id})
            clips = payload.get("data") or []
            if not clips:
                raise ClipSourceError("That Twitch clip was not found.")
            login = str(clips[0].get("broadcaster_name") or "")
            broadcaster_id = str(clips[0].get("broadcaster_id") or "")
            display_name = login
        else:
            payload = await self._twitch_get("/users", {"login": login})
            users = payload.get("data") or []
            if not users:
                raise ClipSourceError("That Twitch broadcaster was not found.")
            broadcaster_id = str(users[0].get("id") or "")
            login = str(users[0].get("login") or login)
            display_name = str(users[0].get("display_name") or login)
        return {
            "provider": "twitch",
            "kind": "profile",
            "key": broadcaster_id,
            "name": display_name,
            "url": f"https://www.twitch.tv/{login}",
        }

    async def _twitch_rocket_league_id(self) -> str:
        if self._twitch_game_id:
            return self._twitch_game_id
        payload = await self._twitch_get("/games", {"name": "Rocket League"})
        games = payload.get("data") or []
        if not games:
            raise ClipSourceError("Twitch did not return the Rocket League category.")
        self._twitch_game_id = str(games[0].get("id") or "")
        return self._twitch_game_id

    async def _fetch_twitch(self, source: dict) -> list[dict]:
        now = datetime.now(timezone.utc)
        payload = await self._twitch_get(
            "/clips",
            {
                "broadcaster_id": source.get("key"),
                "first": 100,
                "started_at": (now - timedelta(days=90)).isoformat().replace("+00:00", "Z"),
                "ended_at": now.isoformat().replace("+00:00", "Z"),
            },
        )
        game_id = await self._twitch_rocket_league_id()
        clips = []
        for item in payload.get("data") or []:
            if str(item.get("game_id") or "") != game_id:
                continue
            clip_id = str(item.get("id") or "").strip()
            if not clip_id:
                continue
            created_at = str(item.get("created_at") or "")
            try:
                published_at = int(datetime.fromisoformat(created_at.replace("Z", "+00:00")).timestamp())
            except ValueError:
                published_at = 0
            clips.append(
                {
                    "provider": "twitch",
                    "clip_id": clip_id,
                    "title": str(item.get("title") or "Rocket League clip")[:200],
                    "creator": str(item.get("broadcaster_name") or source.get("name") or "Twitch creator")[:100],
                    "url": str(item.get("url") or f"https://clips.twitch.tv/{clip_id}"),
                    "duration": float(item.get("duration") or 0),
                    "published_at": published_at,
                    "thumbnail": str(item.get("thumbnail_url") or ""),
                    "views": int(item.get("view_count") or 0),
                }
            )
        return clips

    async def _youtube_key(self) -> str:
        tokens = await self.bot.get_shared_api_tokens("youtube")
        key = str(tokens.get("api_key") or "").strip()
        if not key:
            raise ClipSourceError(
                "Configure YouTube with `set api youtube api_key,YOUR_API_KEY`."
            )
        return key

    async def _youtube_get(self, resource: str, params: dict) -> dict:
        params = {**params, "key": await self._youtube_key()}
        try:
            async with self.session.get(f"{YOUTUBE_API_URL}/{resource}", params=params) as response:
                payload = await response.json(content_type=None)
                error = payload.get("error") if isinstance(payload, dict) else None
                message = str((error or {}).get("message") or "").strip()
                if response.status == 403:
                    if message:
                        raise ClipSourceError(f"YouTube rejected this request: {message}")
                    raise ClipSourceError("YouTube rejected the API key or its quota is exhausted.")
                if response.status != 200:
                    if message:
                        raise ClipSourceError(f"YouTube rejected this request: {message}")
                    raise ClipSourceError(
                        f"YouTube could not complete that request (HTTP {response.status})."
                    )
                return payload
        except ClipSourceError:
            raise
        except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
            raise ClipSourceError("Could not reach YouTube. Try again shortly.") from exc

    async def _resolve_youtube(self, value: str) -> dict:
        value = _clean_url(value)
        parsed = urlparse(value)
        playlist_id = None
        handle = None
        channel_id = None
        if not parsed.scheme:
            handle = value if value.startswith("@") else f"@{value}"
        else:
            host = (parsed.hostname or "").casefold()
            if host not in {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}:
                raise ClipSourceError("Use a YouTube handle, channel URL, playlist URL, or video URL.")
            query = parse_qs(parsed.query)
            playlist_id = (query.get("list") or [None])[0]
            parts = [part for part in parsed.path.split("/") if part]
            if not playlist_id and parts:
                if parts[0].startswith("@"):
                    handle = parts[0]
                elif parts[0] == "channel" and len(parts) > 1:
                    channel_id = parts[1]
                elif parts[0] in {"shorts", "watch"} or host == "youtu.be":
                    video_id = parts[-1] if host == "youtu.be" or parts[0] == "shorts" else (query.get("v") or [None])[0]
                    if video_id:
                        video_payload = await self._youtube_get(
                            "videos", {"part": "snippet", "id": video_id}
                        )
                        videos = video_payload.get("items") or []
                        if videos:
                            channel_id = videos[0].get("snippet", {}).get("channelId")
        if playlist_id:
            payload = await self._youtube_get(
                "playlists", {"part": "snippet", "id": playlist_id}
            )
            items = payload.get("items") or []
            if not items:
                raise ClipSourceError("That public YouTube playlist was not found.")
            snippet = items[0].get("snippet") or {}
            return {
                "provider": "youtube",
                "kind": "playlist",
                "key": playlist_id,
                "name": str(snippet.get("title") or "YouTube playlist"),
                "url": f"https://www.youtube.com/playlist?list={playlist_id}",
            }
        channel_params = {"part": "snippet,contentDetails"}
        if channel_id:
            channel_params["id"] = channel_id
        elif handle:
            channel_params["forHandle"] = handle
        else:
            raise ClipSourceError("That YouTube URL does not identify a channel or playlist.")
        payload = await self._youtube_get("channels", channel_params)
        items = payload.get("items") or []
        if not items:
            raise ClipSourceError("That public YouTube channel was not found.")
        channel = items[0]
        snippet = channel.get("snippet") or {}
        uploads = channel.get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads")
        if not uploads:
            raise ClipSourceError("YouTube did not expose that channel's uploads playlist.")
        return {
            "provider": "youtube",
            "kind": "channel",
            "key": str(uploads),
            "channel_id": str(channel.get("id") or ""),
            "name": str(snippet.get("title") or handle or "YouTube channel"),
            "url": f"https://www.youtube.com/channel/{channel.get('id')}",
        }

    async def _fetch_youtube(self, source: dict) -> list[dict]:
        playlist = await self._youtube_get(
            "playlistItems",
            {
                "part": "snippet,contentDetails",
                "playlistId": source.get("key"),
                "maxResults": 50,
            },
        )
        items = playlist.get("items") or []
        video_ids = [
            str(item.get("contentDetails", {}).get("videoId") or "")
            for item in items
            if item.get("contentDetails", {}).get("videoId")
        ]
        if not video_ids:
            return []
        details = await self._youtube_get(
            "videos",
            {"part": "snippet,contentDetails,status,statistics", "id": ",".join(video_ids)},
        )
        clips = []
        for item in details.get("items") or []:
            status = item.get("status") or {}
            if status.get("privacyStatus") != "public" or status.get("embeddable") is False:
                continue
            snippet = item.get("snippet") or {}
            clip_id = str(item.get("id") or "")
            duration = _iso_duration_seconds(item.get("contentDetails", {}).get("duration"))
            if not clip_id or duration is None:
                continue
            published = str(snippet.get("publishedAt") or "")
            try:
                published_at = int(datetime.fromisoformat(published.replace("Z", "+00:00")).timestamp())
            except ValueError:
                published_at = 0
            thumbnails = snippet.get("thumbnails") or {}
            thumbnail = next(
                (str((thumbnails.get(size) or {}).get("url") or "") for size in ("maxres", "standard", "high", "medium", "default") if thumbnails.get(size)),
                "",
            )
            clips.append(
                {
                    "provider": "youtube",
                    "clip_id": clip_id,
                    "title": str(snippet.get("title") or "YouTube clip")[:200],
                    "creator": str(snippet.get("channelTitle") or source.get("name") or "YouTube creator")[:100],
                    "url": f"https://www.youtube.com/shorts/{clip_id}",
                    "duration": float(duration),
                    "published_at": published_at,
                    "thumbnail": thumbnail,
                    "views": int((item.get("statistics") or {}).get("viewCount") or 0),
                }
            )
        return clips
