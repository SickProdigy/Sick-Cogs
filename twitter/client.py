import asyncio
from typing import Any, Dict, List, Optional

import aiohttp


API_ROOT = "https://api.x.com/2"
USER_AGENT = "Sick-Cogs-Twitter/0.1.1 (+https://github.com/SickProdigy/Sick-Cogs)"
MAX_TIMELINE_PAGES = 10


class XAPIError(RuntimeError):
    def __init__(self, message: str, *, status: Optional[int] = None, retry_after: Optional[int] = None):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


def normalize_username(value: str) -> str:
    username = str(value).strip().lstrip("@").casefold()
    if not username or len(username) > 15 or not username.isascii() or not all(
        character.isalnum() or character == "_" for character in username
    ):
        raise ValueError("Enter a valid X username using up to 15 letters, numbers, or underscores.")
    return username


def new_posts(posts: List[Dict[str, Any]], since_id: Optional[str]) -> List[Dict[str, Any]]:
    floor = int(since_id or 0)
    return sorted(
        (post for post in posts if str(post.get("id", "")).isdigit() and int(post["id"]) > floor),
        key=lambda post: int(post["id"]),
    )


class XClient:
    def __init__(self, session: aiohttp.ClientSession, bearer_token: str):
        self.session = session
        self.bearer_token = str(bearer_token).strip()
        if not self.bearer_token:
            raise ValueError("The X API bearer token is missing.")

    async def request(self, path: str, *, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        headers = {"Authorization": f"Bearer {self.bearer_token}"}
        try:
            async with self.session.get(f"{API_ROOT}{path}", headers=headers, params=params) as response:
                retry_after = response.headers.get("retry-after")
                try:
                    payload = await response.json(content_type=None)
                except (aiohttp.ContentTypeError, ValueError):
                    payload = {}
                if response.status >= 400:
                    detail = payload.get("detail") or payload.get("title")
                    if response.status == 401:
                        detail = "X rejected the configured bearer token."
                    elif response.status == 402:
                        detail = "The X API account has insufficient credits."
                    elif response.status == 429:
                        detail = "The X API rate limit was reached."
                    raise XAPIError(
                        detail or f"X API returned HTTP {response.status}.",
                        status=response.status,
                        retry_after=int(retry_after) if retry_after and retry_after.isdigit() else None,
                    )
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise XAPIError("Could not reach the X API.") from exc
        if not isinstance(payload, dict):
            raise XAPIError("X API returned an invalid response.")
        return payload

    async def user_by_username(self, username: str) -> Dict[str, Any]:
        payload = await self.request(
            f"/users/by/username/{normalize_username(username)}",
            params={"user.fields": "id,name,username,profile_image_url,most_recent_tweet_id"},
        )
        data = payload.get("data")
        if not isinstance(data, dict) or not data.get("id"):
            raise XAPIError("That X account was not found.", status=404)
        return data

    async def user_posts(self, user_id: str, since_id: str) -> List[Dict[str, Any]]:
        user_id, since_id = str(user_id), str(since_id)
        if not user_id.isdigit() or not since_id.isdigit():
            raise XAPIError("The saved X account cursor is invalid.")
        params = {
            "since_id": since_id,
            "max_results": 100,
            "exclude": "replies,retweets",
            "tweet.fields": "created_at",
        }
        posts = {}
        for _ in range(MAX_TIMELINE_PAGES):
            payload = await self.request(f"/users/{user_id}/tweets", params=params)
            data = payload.get("data", [])
            if isinstance(data, list):
                posts.update(
                    (str(post["id"]), post)
                    for post in data
                    if isinstance(post, dict) and str(post.get("id", "")).isdigit()
                )
            meta = payload.get("meta", {})
            next_token = meta.get("next_token") if isinstance(meta, dict) else None
            if not next_token:
                return list(posts.values())
            params["pagination_token"] = str(next_token)
        raise XAPIError(
            "The X timeline exceeded the bounded pagination limit; saved cursors were not advanced."
        )
