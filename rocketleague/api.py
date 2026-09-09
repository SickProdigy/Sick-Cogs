from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import aiohttp


STARTGG_API_URL = "https://api.start.gg/gql/alpha"
STARTGG_WEB_URL = "https://www.start.gg"
ROCKET_LEAGUE_GAME_NAME = "Rocket League"


class StartGGError(RuntimeError):
    """A safe, user-facing start.gg provider error."""


@dataclass(frozen=True)
class RLCSEvent:
    id: int
    name: str
    start_at: Optional[int]
    entrants: Optional[int]


@dataclass(frozen=True)
class RLCSTournament:
    id: int
    name: str
    slug: str
    start_at: Optional[int]
    end_at: Optional[int]
    is_online: bool
    city: Optional[str]
    state: Optional[str]
    country: Optional[str]
    events: tuple[RLCSEvent, ...]

    @property
    def url(self) -> str:
        return f"{STARTGG_WEB_URL}/{self.slug.lstrip('/')}"


class StartGGClient:
    """Small provider client limited to the public start.gg GraphQL API."""

    def __init__(self, session: aiohttp.ClientSession, token: str):
        self.session = session
        self.token = token
        self._rocket_league_game_id: Optional[int] = None

    async def _query(self, query: str, variables: Dict[str, Any]) -> Dict[str, Any]:
        headers = {"Authorization": f"Bearer {self.token}"}
        try:
            async with self.session.post(
                STARTGG_API_URL,
                headers=headers,
                json={"query": query, "variables": variables},
            ) as response:
                if response.status in {401, 403}:
                    raise StartGGError("start.gg rejected the configured API token.")
                if response.status == 429:
                    raise StartGGError("start.gg is rate limiting requests. Try again shortly.")
                if response.status >= 500:
                    raise StartGGError("start.gg is temporarily unavailable.")
                if response.status != 200:
                    raise StartGGError("start.gg could not complete that request.")
                payload = await response.json(content_type=None)
        except StartGGError:
            raise
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise StartGGError("Could not reach start.gg. Try again shortly.") from exc

        if payload.get("errors"):
            raise StartGGError("start.gg could not complete that request.")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise StartGGError("start.gg returned an unexpected response.")
        return data

    async def rocket_league_game_id(self) -> int:
        if self._rocket_league_game_id is not None:
            return self._rocket_league_game_id

        data = await self._query(
            """
            query RocketLeagueGame {
              videogames(query: {filter: {name: "Rocket League"}, perPage: 10}) {
                nodes { id name }
              }
            }
            """,
            {},
        )
        nodes = ((data.get("videogames") or {}).get("nodes") or [])
        for node in nodes:
            if str(node.get("name", "")).casefold() == ROCKET_LEAGUE_GAME_NAME.casefold():
                self._rocket_league_game_id = int(node["id"])
                return self._rocket_league_game_id
        raise StartGGError("Rocket League is not available in the start.gg game catalog.")

    async def upcoming_rlcs(self, limit: int = 5) -> List[RLCSTournament]:
        game_id = await self.rocket_league_game_id()
        data = await self._query(
            """
            query UpcomingRocketLeague($gameId: ID!, $perPage: Int!) {
              tournaments(query: {
                page: 1
                perPage: $perPage
                sortBy: "startAt asc"
                filter: {upcoming: true, videogameIds: [$gameId]}
              }) {
                nodes {
                  id name slug startAt endAt isOnline city addrState countryCode
                  events(filter: {videogameId: [$gameId]}) {
                    id name startAt numEntrants
                  }
                }
              }
            }
            """,
            {"gameId": game_id, "perPage": 50},
        )
        nodes = ((data.get("tournaments") or {}).get("nodes") or [])
        tournaments = [self._parse_tournament(node) for node in nodes if self._is_rlcs(node)]
        return tournaments[:limit]

    async def tournament(self, slug: str) -> Optional[RLCSTournament]:
        game_id = await self.rocket_league_game_id()
        data = await self._query(
            """
            query RocketLeagueTournament($slug: String!, $gameId: [ID]!) {
              tournament(slug: $slug) {
                id name slug startAt endAt isOnline city addrState countryCode
                events(filter: {videogameId: $gameId}) {
                  id name startAt numEntrants
                }
              }
            }
            """,
            {"slug": normalize_tournament_slug(slug), "gameId": [game_id]},
        )
        node = data.get("tournament")
        if not isinstance(node, dict):
            return None
        events = node.get("events") or []
        if not events:
            return None
        return self._parse_tournament(node)

    @staticmethod
    def _is_rlcs(node: Dict[str, Any]) -> bool:
        name = str(node.get("name") or "").casefold()
        slug = str(node.get("slug") or "").casefold()
        return "rlcs" in name or "rlcs" in slug

    @staticmethod
    def _parse_tournament(node: Dict[str, Any]) -> RLCSTournament:
        events = tuple(
            RLCSEvent(
                id=int(event["id"]),
                name=str(event.get("name") or "Rocket League"),
                start_at=_optional_int(event.get("startAt")),
                entrants=_optional_int(event.get("numEntrants")),
            )
            for event in (node.get("events") or [])
            if event.get("id") is not None
        )
        return RLCSTournament(
            id=int(node["id"]),
            name=str(node.get("name") or "Rocket League tournament"),
            slug=str(node.get("slug") or ""),
            start_at=_optional_int(node.get("startAt")),
            end_at=_optional_int(node.get("endAt")),
            is_online=bool(node.get("isOnline")),
            city=_optional_str(node.get("city")),
            state=_optional_str(node.get("addrState")),
            country=_optional_str(node.get("countryCode")),
            events=events,
        )


def normalize_tournament_slug(value: str) -> str:
    value = value.strip().strip("<>")
    for prefix in ("https://www.start.gg/", "https://start.gg/", "http://www.start.gg/", "http://start.gg/"):
        if value.casefold().startswith(prefix.casefold()):
            value = value[len(prefix) :]
            break
    value = value.split("?", 1)[0].split("#", 1)[0].strip("/")
    if not value.startswith("tournament/"):
        value = f"tournament/{value}"
    parts = value.split("/")
    return "/".join(parts[:2])


def _optional_int(value: Any) -> Optional[int]:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _optional_str(value: Any) -> Optional[str]:
    text = str(value).strip() if value is not None else ""
    return text or None
