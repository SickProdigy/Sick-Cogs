import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Mapping, Optional
from urllib.parse import quote, urlsplit

import aiohttp


TOKEN_NAMESPACE = "azerothcore_api"
RACE_NAMES = {
    1: "Human", 2: "Orc", 3: "Dwarf", 4: "Night Elf", 5: "Undead",
    6: "Tauren", 7: "Gnome", 8: "Troll", 10: "Blood Elf", 11: "Draenei",
}
CLASS_NAMES = {
    1: "Warrior", 2: "Paladin", 3: "Hunter", 4: "Rogue", 5: "Priest",
    6: "Death Knight", 7: "Shaman", 8: "Mage", 9: "Warlock", 11: "Druid",
}


class DatabaseConfigurationError(RuntimeError):
    pass


class DatabaseUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class ApiSettings:
    base_url: str
    community_token: str
    account_token: Optional[str]
    timeout: int

    @classmethod
    def from_tokens(cls, tokens: Mapping[str, str]) -> "ApiSettings":
        base_url = str(tokens.get("base_url", "")).strip().rstrip("/")
        community_token = str(tokens.get("community_token", "")).strip()
        account_token = str(tokens.get("account_token", "")).strip() or None
        missing = []
        if not base_url:
            missing.append("base_url")
        if not community_token:
            missing.append("community_token")
        if missing:
            raise DatabaseConfigurationError(
                "AzerothCore API is missing required shared-token fields: "
                + ", ".join(missing) + "."
            )
        parsed = urlsplit(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
            raise DatabaseConfigurationError(
                "AzerothCore API base_url must be an HTTP(S) URL without embedded credentials."
            )
        try:
            timeout = int(tokens.get("timeout", 10))
        except (TypeError, ValueError) as exc:
            raise DatabaseConfigurationError("AzerothCore API timeout must be an integer.") from exc
        if not 3 <= timeout <= 60:
            raise DatabaseConfigurationError("AzerothCore API timeout must be between 3 and 60 seconds.")
        return cls(base_url, community_token, account_token, timeout)


@dataclass(frozen=True)
class CharacterRecord:
    guid: int
    name: str
    race: int
    character_class: int
    gender: int
    level: int
    online: bool
    total_time: int
    money: int
    total_kills: int
    quests: int
    account_name: str = ""
    account_type: Optional[int] = None
    player_type: str = "human"

    @property
    def race_name(self) -> str:
        return RACE_NAMES.get(self.race, f"Race {self.race}")

    @property
    def class_name(self) -> str:
        return CLASS_NAMES.get(self.character_class, f"Class {self.character_class}")


class AzerothDatabase:
    """Compatibility-named client for the private ac-api-v1 service."""

    def __init__(
        self,
        token_getter: Callable[[], Awaitable[Mapping[str, str]]],
        prefix_getter: Optional[Callable[[], Awaitable[List[str]]]] = None,
    ):
        self._token_getter = token_getter
        self._session: Optional[aiohttp.ClientSession] = None
        self._lock = asyncio.Lock()

    async def is_configured(self) -> bool:
        tokens = await self._token_getter()
        return bool(str(tokens.get("base_url", "")).strip() and str(tokens.get("community_token", "")).strip())

    async def has_account_access(self) -> bool:
        if not await self.is_configured():
            return False
        return bool((await self.settings()).account_token)

    async def settings(self) -> ApiSettings:
        return ApiSettings.from_tokens(await self._token_getter())

    async def close(self) -> None:
        async with self._lock:
            if self._session is not None and not self._session.closed:
                await self._session.close()
            self._session = None

    async def _get_session(self) -> aiohttp.ClientSession:
        async with self._lock:
            if self._session is None or self._session.closed:
                self._session = aiohttp.ClientSession()
            return self._session

    async def _request(
        self,
        method: str,
        path: str,
        *,
        scope: str = "community",
        params: Optional[Dict[str, Any]] = None,
        json: Optional[Dict[str, Any]] = None,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        settings = await self.settings()
        token = settings.account_token if scope == "account" else settings.community_token
        if not token:
            raise DatabaseConfigurationError("AzerothCore API account_token is not configured.")
        headers = {"Authorization": f"Bearer {token}"}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        try:
            session = await self._get_session()
            async with session.request(
                method,
                settings.base_url + path,
                headers=headers,
                params=params,
                json=json,
                timeout=aiohttp.ClientTimeout(total=settings.timeout),
            ) as response:
                try:
                    payload = await response.json(content_type=None)
                except (aiohttp.ContentTypeError, ValueError) as exc:
                    raise DatabaseUnavailable(
                        "The AzerothCore API returned an invalid response."
                    ) from exc
                if response.status >= 400:
                    error = payload.get("error", {}) if isinstance(payload, dict) else {}
                    code = str(error.get("code") or "api_error")
                    message = str(error.get("message") or "The AzerothCore API rejected the request.")
                    if code in {"character_not_found", "account_not_found"}:
                        return {"_not_found": True}
                    raise DatabaseUnavailable(f"AzerothCore API error ({code}): {message}")
                if not isinstance(payload, dict):
                    raise DatabaseUnavailable("The AzerothCore API returned an invalid response.")
                return payload
        except asyncio.TimeoutError as exc:
            raise DatabaseUnavailable("The AzerothCore API request timed out.") from exc
        except aiohttp.ClientError as exc:
            raise DatabaseUnavailable(
                "Could not reach the private AzerothCore API. Check its URL, network path, and listener."
            ) from exc

    @staticmethod
    def _record(raw: Mapping[str, Any]) -> CharacterRecord:
        return CharacterRecord(
            guid=int(raw.get("character_id") or 0),
            name=str(raw.get("name") or "Unknown"),
            race=int(raw.get("race") or 0),
            character_class=int(raw.get("class") or 0),
            gender=int(raw.get("gender") or 0),
            level=int(raw.get("level") or 0),
            online=bool(raw.get("online")),
            total_time=int(raw.get("playtime_seconds") or 0),
            money=int(raw.get("money_copper") or 0),
            total_kills=int(raw.get("kills") or 0),
            quests=int(raw.get("quests") or 0),
            player_type=str(raw.get("player_type") or "human"),
        )

    async def check(self) -> Dict[str, Any]:
        capabilities = await self._request("GET", "/v1/capabilities")
        realm = await self._request("GET", "/v1/realm/status")
        return {
            "api_version": str(capabilities.get("api_version") or "unknown"),
            "characters": int(realm.get("characters") or 0),
            "online": int(realm.get("online_characters") or 0),
        }

    async def character(self, name: str) -> Optional[CharacterRecord]:
        payload = await self._request("GET", "/v1/characters/by-name/" + quote(name, safe=""))
        if payload.get("_not_found"):
            return None
        return self._record(payload)

    async def online(self, audience: str, limit: int = 100) -> List[CharacterRecord]:
        if audience not in {"humans", "ai"}:
            raise ValueError("Audience must be humans or ai.")
        payload = await self._request(
            "GET",
            "/v1/players/online",
            params={"audience": audience, "limit": max(1, min(int(limit), 100))},
        )
        return [self._record(item) for item in payload.get("entries", []) if isinstance(item, dict)]

    async def leaderboard(
        self, stat: str, audience: str = "humans", limit: int = 10
    ) -> List[CharacterRecord]:
        if stat not in {"level", "gold", "kills", "quests"}:
            raise ValueError("Unknown leaderboard statistic.")
        if audience not in {"humans", "ai"}:
            raise ValueError("Audience must be humans or ai.")
        payload = await self._request(
            "GET",
            "/v1/leaderboards/players",
            params={
                "metric": stat,
                "audience": audience,
                "limit": max(1, min(int(limit), 25)),
                "offset": 0,
            },
        )
        return [self._record(item) for item in payload.get("entries", []) if isinstance(item, dict)]

    async def create_account(
        self, username: str, password: str, idempotency_key: str
    ) -> Dict[str, Any]:
        return await self._request(
            "POST",
            "/v1/accounts",
            scope="account",
            json={"username": username, "password": password},
            idempotency_key=idempotency_key,
        )
