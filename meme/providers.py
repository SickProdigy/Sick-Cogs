from __future__ import annotations

import base64
import random
import re
import time
from typing import List, Optional
from urllib.parse import quote

import aiohttp

from .models import MemeResult

REDDIT_MEDIA_RE = re.compile(r"\.(?:jpe?g|png|gif|webp)(?:\?.*)?$", re.I)


class ProviderError(RuntimeError):
    pass


class MemeApiProvider:
    API_URL = "https://meme-api.com/gimme"

    def __init__(self, session: aiohttp.ClientSession):
        self.session = session

    async def fetch(self, source: str = "memes", limit: int = 25) -> List[MemeResult]:
        source = source.strip().removeprefix("r/") or "memes"
        if not re.fullmatch(r"[A-Za-z0-9_]{2,100}", source):
            raise ProviderError("That community name is invalid.")
        limit = min(max(limit, 1), 50)
        async with self.session.get(f"{self.API_URL}/{quote(source)}/{limit}") as response:
            if response.status != 200:
                raise ProviderError(f"Meme API request failed ({response.status}).")
            payload = await response.json()
        if payload.get("code"):
            raise ProviderError(payload.get("message") or "Meme API returned an error.")
        items = payload.get("memes", [payload])
        results = []
        for item in items:
            previews = item.get("preview") or []
            media_url = item.get("url") or (previews[-1] if previews else "")
            if not media_url:
                continue
            clean_url = media_url.lower().split("?", 1)[0]
            results.append(MemeResult(
                title=item.get("title") or "Online meme",
                description=(
                    f"r/{item.get('subreddit', source)} · {item.get('ups', 0)} points"
                ),
                media_url=media_url,
                source_url=item.get("postLink") or media_url,
                provider="Meme API / Reddit",
                post_id=f"memeapi:{item.get('postLink', media_url)}",
                is_gif=clean_url.endswith(".gif"),
                nsfw=bool(item.get("nsfw") or item.get("spoiler")),
                author=item.get("author"),
            ))
        return results


class ImgurProvider:
    API_URL = "https://api.imgur.com/3"

    def __init__(self, session: aiohttp.ClientSession):
        self.session = session

    async def fetch(self, client_id: str, query: str = "", gifs_only: bool = False):
        headers = {"Authorization": f"Client-ID {client_id}"}
        if query:
            url = f"{self.API_URL}/gallery/search/top/week/0"
            params = {"q": query}
            if gifs_only:
                params["q_type"] = "anigif"
        else:
            url = f"{self.API_URL}/gallery/hot/viral/day/0"
            params = {"showViral": "true"}
        async with self.session.get(url, headers=headers, params=params) as response:
            if response.status != 200:
                raise ProviderError(f"Imgur request failed ({response.status}).")
            payload = await response.json()
        results = []
        for entry in payload.get("data", []):
            images = entry.get("images") or [entry]
            for image in images[:1]:
                media_url = image.get("link")
                if not media_url:
                    continue
                animated = bool(image.get("animated"))
                if gifs_only and not animated:
                    continue
                results.append(MemeResult(
                    title=entry.get("title") or image.get("title") or "Imgur post",
                    description=entry.get("description") or "",
                    media_url=media_url,
                    source_url=entry.get("link") or media_url,
                    provider="Imgur",
                    post_id=f"imgur:{entry.get('id', image.get('id', media_url))}",
                    is_gif=animated,
                    nsfw=bool(entry.get("nsfw") or image.get("nsfw")),
                    author=entry.get("account_url"),
                ))
        return results


class RedditProvider:
    TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
    API_URL = "https://oauth.reddit.com"

    def __init__(self, session: aiohttp.ClientSession, user_agent: str):
        self.session = session
        self.user_agent = user_agent
        self._token: Optional[str] = None
        self._expires_at = 0.0

    async def _access_token(self, client_id: str, client_secret: str) -> str:
        now = time.monotonic()
        if self._token and now < self._expires_at:
            return self._token
        basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
        headers = {"Authorization": f"Basic {basic}", "User-Agent": self.user_agent}
        data = {"grant_type": "client_credentials"}
        async with self.session.post(self.TOKEN_URL, headers=headers, data=data) as response:
            if response.status != 200:
                raise ProviderError(f"Reddit authentication failed ({response.status}).")
            payload = await response.json()
        self._token = payload.get("access_token")
        if not self._token:
            raise ProviderError("Reddit did not return an access token.")
        self._expires_at = now + max(60, int(payload.get("expires_in", 3600)) - 60)
        return self._token

    async def fetch(
        self,
        client_id: str,
        client_secret: str,
        subreddit: str = "memes",
        query: str = "",
        sort: str = "hot",
        limit: int = 50,
    ) -> List[MemeResult]:
        subreddit = subreddit.strip().removeprefix("r/")
        if not re.fullmatch(r"[A-Za-z0-9_+]{2,100}", subreddit):
            raise ProviderError("That subreddit name is invalid.")
        token = await self._access_token(client_id, client_secret)
        headers = {"Authorization": f"Bearer {token}", "User-Agent": self.user_agent}
        if query:
            url = f"{self.API_URL}/r/{quote(subreddit)}/search"
            params = {
                "q": query, "restrict_sr": "on", "sort": "relevance",
                "limit": limit, "raw_json": 1,
            }
        else:
            sort = sort if sort in {"hot", "new", "top", "rising"} else "hot"
            url = f"{self.API_URL}/r/{quote(subreddit)}/{sort}"
            params = {"limit": limit, "raw_json": 1}
        async with self.session.get(url, headers=headers, params=params) as response:
            if response.status != 200:
                raise ProviderError(f"Reddit request failed ({response.status}).")
            payload = await response.json()

        results = []
        for child in payload.get("data", {}).get("children", []):
            post = child.get("data", {})
            media_url = post.get("url_overridden_by_dest") or post.get("url") or ""
            if not REDDIT_MEDIA_RE.search(media_url):
                continue
            lower_url = media_url.lower().split("?", 1)[0]
            results.append(
                MemeResult(
                    title=post.get("title") or "Reddit meme",
                    description=(
                        f"r/{post.get('subreddit', subreddit)} · "
                        f"{post.get('score', 0)} points"
                    ),
                    media_url=media_url,
                    source_url=f"https://www.reddit.com{post.get('permalink', '')}",
                    provider="Reddit",
                    post_id=f"reddit:{post.get('id', media_url)}",
                    is_gif=lower_url.endswith(".gif"),
                    nsfw=bool(post.get("over_18")),
                    author=post.get("author"),
                )
            )
        return results


def choose_result(results: List[MemeResult], seen: Optional[set] = None) -> MemeResult:
    available = [result for result in results if not seen or result.post_id not in seen]
    if not available:
        raise ProviderError("No new supported media was found for that source.")
    return random.SystemRandom().choice(available)
