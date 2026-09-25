from __future__ import annotations

import html as html_module
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import quote, urlparse

import aiohttp


BLAST_TOURNAMENTS_URL = "https://blast.tv/rl/tournaments"
BLAST_HOST = "blast.tv"
MAX_BLAST_RESPONSE_BYTES = 2_000_000
BLAST_REQUEST_HEADERS = {
    "Accept": "*/*",
    "User-Agent": "curl/7.88.1",
}


class BlastError(RuntimeError):
    """A safe, user-facing BLAST ingestion error."""


@dataclass(frozen=True)
class BlastTournament:
    slug: str
    name: str
    start_at: int
    end_at: int
    location: Optional[str]
    prize_pool: Optional[str] = None
    team_count: Optional[int] = None
    description: Optional[str] = None

    @property
    def url(self) -> str:
        return f"https://blast.tv/rl/tournaments/{self.slug}"

    @property
    def identity(self) -> str:
        return f"blast:{self.slug}"

    @property
    def image_url(self) -> str:
        slug = quote(self.slug, safe="-")
        return (
            f"https://assets.blast.tv/images/tournament/{slug}"
            "?height=416&width=640&quality=100&format=webp"
        )

    @property
    def fingerprint(self) -> str:
        return f"{self.name}|{self.start_at}|{self.end_at}|{self.location or ''}"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Dict[str, Any]) -> "BlastTournament":
        return cls(
            slug=str(value["slug"]),
            name=str(value["name"]),
            start_at=int(value["start_at"]),
            end_at=int(value["end_at"]),
            location=_optional_text(value.get("location")),
            prize_pool=_optional_text(value.get("prize_pool")),
            team_count=_optional_int(value.get("team_count")),
            description=_optional_text(value.get("description")),
        )


@dataclass(frozen=True)
class BlastResult:
    slug: str
    tournament_name: str
    champion: str
    runner_up: str
    champion_score: int
    runner_up_score: int
    semifinalists: Tuple[str, ...] = ()
    matches: Tuple["BlastMatch", ...] = ()
    top_players: Tuple["BlastPlayerStat", ...] = ()
    power_rankings: Tuple["BlastPowerRanking", ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Dict[str, Any]) -> "BlastResult":
        return cls(
            slug=str(value["slug"]), tournament_name=str(value["tournament_name"]),
            champion=str(value["champion"]), runner_up=str(value["runner_up"]),
            champion_score=int(value["champion_score"]), runner_up_score=int(value["runner_up_score"]),
            semifinalists=tuple(str(item) for item in value.get("semifinalists") or ()),
            matches=tuple(
                BlastMatch.from_dict(item)
                for item in value.get("matches") or ()
                if isinstance(item, dict)
            ),
            top_players=tuple(
                BlastPlayerStat.from_dict(item)
                for item in value.get("top_players") or ()
                if isinstance(item, dict)
            ),
            power_rankings=tuple(
                BlastPowerRanking.from_dict(item)
                for item in value.get("power_rankings") or ()
                if isinstance(item, dict)
            ),
        )


@dataclass(frozen=True)
class BlastMatch:
    round_name: str
    team_a: str
    team_a_score: int
    team_b: str
    team_b_score: int

    @classmethod
    def from_dict(cls, value: Dict[str, Any]) -> "BlastMatch":
        return cls(
            round_name=str(value["round_name"]),
            team_a=str(value["team_a"]),
            team_a_score=int(value["team_a_score"]),
            team_b=str(value["team_b"]),
            team_b_score=int(value["team_b_score"]),
        )


@dataclass(frozen=True)
class BlastPlayerStat:
    player_name: str
    games_played: int
    rating: float

    @classmethod
    def from_dict(cls, value: Dict[str, Any]) -> "BlastPlayerStat":
        return cls(str(value["player_name"]), int(value["games_played"]), float(value["rating"]))


@dataclass(frozen=True)
class BlastPowerRanking:
    team_name: str
    points: float

    @classmethod
    def from_dict(cls, value: Dict[str, Any]) -> "BlastPowerRanking":
        return cls(str(value["team_name"]), float(value["points"]))


class _StructuredDataParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._capture = False
        self._parts: List[str] = []
        self.scripts: List[str] = []

    def handle_starttag(self, tag: str, attrs: List[tuple[str, Optional[str]]]) -> None:
        attributes = {key.casefold(): value for key, value in attrs}
        self._capture = tag.casefold() == "script" and attributes.get("type") == "application/ld+json"
        self._parts = [] if self._capture else self._parts

    def handle_data(self, data: str) -> None:
        if self._capture:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "script" and self._capture:
            self.scripts.append("".join(self._parts))
            self._capture = False
            self._parts = []


class BlastClient:
    """One-request parser for BLAST's server-rendered Rocket League catalog."""

    def __init__(self, session: aiohttp.ClientSession):
        self.session = session

    async def tournaments(self) -> List[BlastTournament]:
        page = await self._page(BLAST_TOURNAMENTS_URL, "schedule")
        tournaments = parse_blast_tournaments(page)
        if not tournaments:
            details = _blast_page_diagnostics(page)
            raise BlastError(
                "BLAST returned no dated RLCS tournaments; the cached schedule was kept. "
                f"Diagnostic: {details}."
            )
        return tournaments

    async def tournament_result(self, tournament: BlastTournament) -> Optional[BlastResult]:
        page = await self._page(f"{tournament.url}/series?view=stats", "tournament details")
        return parse_blast_result(page, tournament.slug)

    async def _page(self, url: str, label: str) -> str:
        try:
            async with self.session.get(
                url,
                allow_redirects=False,
                headers=BLAST_REQUEST_HEADERS,
            ) as response:
                if response.status == 429:
                    raise BlastError(f"BLAST is rate limiting the weekly {label} request.")
                if response.status != 200:
                    raise BlastError(f"BLAST could not provide the RLCS {label}.")
                declared_size = response.content_length
                if declared_size is not None and declared_size > MAX_BLAST_RESPONSE_BYTES:
                    raise BlastError(f"BLAST returned a {label} page larger than the safety limit.")
                chunks = []
                received = 0
                async for chunk in response.content.iter_chunked(64 * 1024):
                    received += len(chunk)
                    if received > MAX_BLAST_RESPONSE_BYTES:
                        raise BlastError(
                            f"BLAST returned a {label} page larger than the safety limit."
                        )
                    chunks.append(chunk)
                payload = b"".join(chunks)
        except BlastError:
            raise
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise BlastError(f"The weekly BLAST {label} request could not be reached.") from exc
        try:
            page = payload.decode(response.charset or "utf-8")
        except (LookupError, UnicodeDecodeError) as exc:
            raise BlastError(f"BLAST returned an unreadable {label} page.") from exc
        return page


def parse_blast_result(page: str, slug: str) -> Optional[BlastResult]:
    roots = []
    stream_root = _stream_hydration_root(page)
    if stream_root is not None:
        roots.append(stream_root)
    for values in _hydration_payloads(page):
        try:
            roots.append(_unflatten_devalue(values))
        except (KeyError, TypeError, ValueError, RecursionError):
            continue
    for root in roots:
        loader_data = root.get("loaderData") if isinstance(root, dict) else None
        route = loader_data.get("routes/$gameId.tournaments") if isinstance(loader_data, dict) else None
        timeline = route.get("tournamentTimelineData") if isinstance(route, dict) else None
        if not isinstance(timeline, dict):
            continue
        records = []
        for group in timeline.values():
            if isinstance(group, list):
                records.extend(item for item in group if isinstance(item, dict))
        record = next((item for item in records if item.get("id") == slug), None)
        if not record:
            continue
        matches = [item for item in record.get("keyMatches") or [] if isinstance(item, dict)]
        final = next((item for item in matches if "grand final" in str(item.get("name") or "").casefold()), None)
        if not final:
            continue
        team_a, team_b = _team_name(final.get("teamA")), _team_name(final.get("teamB"))
        score_a, score_b = _optional_int(final.get("teamAScore")), _optional_int(final.get("teamBScore"))
        if not team_a or not team_b or score_a is None or score_b is None or score_a == score_b:
            continue
        champion, runner_up = (team_a, team_b) if score_a > score_b else (team_b, team_a)
        champion_score, runner_up_score = (score_a, score_b) if score_a > score_b else (score_b, score_a)
        semifinalists = []
        completed_matches = []
        for match in matches:
            round_name = str(match.get("name") or "")
            if not any(label in round_name.casefold() for label in ("semi final", "grand final")):
                continue
            first_name, second_name = _team_name(match.get("teamA")), _team_name(match.get("teamB"))
            first_score, second_score = _optional_int(match.get("teamAScore")), _optional_int(match.get("teamBScore"))
            if first_name and second_name and first_score is not None and second_score is not None and first_score != second_score:
                completed_matches.append(
                    BlastMatch(round_name, first_name, first_score, second_name, second_score)
                )
                if "semi final" in round_name.casefold():
                    semifinalists.append(first_name if first_score < second_score else second_name)
        stats_route = loader_data.get("routes/$gameId.tournaments.$tournamentId_.$view")
        player_stats = stats_route.get("playerStatsPromise") if isinstance(stats_route, dict) else None
        rankings = stats_route.get("powerRankingsPromise") if isinstance(stats_route, dict) else None
        top_players = []
        if isinstance(player_stats, list):
            for item in player_stats[:3]:
                if not isinstance(item, dict):
                    continue
                name = _optional_text(item.get("playerName"))
                games = _optional_int(item.get("gamesPlayed"))
                try:
                    rating = float(item.get("rating"))
                except (TypeError, ValueError):
                    continue
                if name and games is not None:
                    top_players.append(BlastPlayerStat(name, games, rating))
        ranking_items = rankings.get("teams") if isinstance(rankings, dict) else None
        power_rankings = []
        if isinstance(ranking_items, list):
            for item in ranking_items[:5]:
                if not isinstance(item, dict):
                    continue
                name = _optional_text(item.get("teamName"))
                try:
                    points = float(item.get("rating"))
                except (TypeError, ValueError):
                    continue
                if name:
                    power_rankings.append(BlastPowerRanking(name, points))
        return BlastResult(
            slug=slug, tournament_name=str(record.get("name") or slug),
            champion=champion, runner_up=runner_up,
            champion_score=champion_score, runner_up_score=runner_up_score,
            semifinalists=tuple(semifinalists[:2]),
            matches=tuple(completed_matches),
            top_players=tuple(top_players),
            power_rankings=tuple(power_rankings),
        )
    return None


def _team_name(value: Any) -> Optional[str]:
    return _optional_text(value.get("name")) if isinstance(value, dict) else None


def _hydration_payloads(page: str) -> List[Any]:
    payloads = []
    pattern = re.compile(
        r'window\.__reactRouterContext\.streamController\.enqueue\('
        r'(?P<value>"(?:\\.|[^"\\])*")\);'
    )
    for match in pattern.finditer(page):
        try:
            chunk = json.loads(match.group("value"))
            value = json.loads(html_module.unescape(chunk).replace('\\"', '"'))
        except (TypeError, ValueError):
            continue
        payloads.append(value)
    return payloads


def _stream_hydration_root(page: str) -> Optional[Any]:
    chunks = []
    pattern = re.compile(
        r'window\.__reactRouterContext\.streamController\.enqueue\('
        r'(?P<value>"(?:\\.|[^"\\])*")\);'
    )
    for match in pattern.finditer(page):
        try:
            chunks.append(json.loads(match.group("value")))
        except (TypeError, ValueError):
            continue
    if not chunks:
        return None
    try:
        values = json.loads(html_module.unescape(chunks[0]).replace('\\"', '"').strip())
    except (TypeError, ValueError):
        return None
    if not isinstance(values, list):
        return None
    promise_starts = {}
    for chunk in chunks[1:]:
        match = re.fullmatch(r"P(\d+):(.*)\s*", chunk, flags=re.DOTALL)
        if not match:
            continue
        try:
            deferred = json.loads(match.group(2))
        except (TypeError, ValueError):
            continue
        if not isinstance(deferred, list):
            continue
        promise_starts[int(match.group(1))] = len(values)
        values.extend(deferred)

    memo: Dict[int, Any] = {}

    def load(reference: Any) -> Any:
        if not isinstance(reference, int):
            return reference
        if reference < 0 or reference >= len(values):
            return None
        if reference in memo:
            return memo[reference]
        raw = values[reference]
        if isinstance(raw, list) and len(raw) == 2 and raw[0] == "P":
            return load(promise_starts.get(raw[1], -1))
        if isinstance(raw, dict):
            hydrated: Dict[str, Any] = {}
            memo[reference] = hydrated
            for key_reference, value_reference in raw.items():
                key = load(int(key_reference[1:])) if key_reference.startswith("_") else key_reference
                hydrated[str(key)] = load(value_reference)
            return hydrated
        if isinstance(raw, list):
            hydrated_list: List[Any] = []
            memo[reference] = hydrated_list
            hydrated_list.extend(load(item) for item in raw)
            return hydrated_list
        memo[reference] = raw
        return raw

    try:
        return load(0)
    except (KeyError, TypeError, ValueError, RecursionError):
        return None


def parse_blast_tournaments(page: str) -> List[BlastTournament]:
    parser = _StructuredDataParser()
    parser.feed(page)
    catalog: Dict[str, Dict[str, Optional[str]]] = {}
    for script in parser.scripts:
        try:
            value = json.loads(script)
        except (TypeError, ValueError):
            continue
        if not isinstance(value, dict) or value.get("@type") != "ItemList":
            continue
        for entry in value.get("itemListElement") or []:
            item = entry.get("item") if isinstance(entry, dict) else None
            if not isinstance(item, dict) or item.get("@type") != "SportsEvent":
                continue
            name = _optional_text(item.get("name"))
            url = _safe_blast_url(item.get("url"))
            if not name or not url or "rlcs" not in name.casefold():
                continue
            slug = urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]
            location_value = item.get("location")
            location = _optional_text(location_value.get("name")) if isinstance(location_value, dict) else None
            catalog[slug] = {"name": name, "location": location}

    rich_records = _hydration_tournaments(page)
    normalized = _decode_hydration(page)
    dated: Dict[str, tuple[int, int]] = {}
    iso_pattern = r'(20\d{2}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z)'
    for slug in catalog:
        match = re.search(
            rf'"{re.escape(slug)}".{{0,800}}?{iso_pattern}'
            rf'(?:.{{0,160}}?{iso_pattern})?',
            normalized,
            flags=re.DOTALL,
        )
        if not match:
            continue
        start_at = _iso_timestamp(match.group(1))
        end_at = _iso_timestamp(match.group(2)) if match.group(2) else start_at
        if start_at is not None and end_at is not None:
            dated[slug] = (start_at, end_at)

    tournaments = [
        BlastTournament(
            slug=slug,
            name=str(details["name"]),
            start_at=dated[slug][0],
            end_at=max(dated[slug][0], dated[slug][1]),
            location=(
                _optional_text(rich_records.get(slug, {}).get("location"))
                or _optional_text(details.get("location"))
            ),
            prize_pool=_optional_text(rich_records.get(slug, {}).get("prizePool")),
            team_count=_optional_int(rich_records.get(slug, {}).get("numberOfTeams")),
            description=_optional_text(rich_records.get(slug, {}).get("description")),
        )
        for slug, details in catalog.items()
        if slug in dated
    ]
    return sorted(tournaments, key=lambda item: (item.start_at, item.name.casefold()))


def upcoming_tournaments(
    tournaments: Iterable[BlastTournament], *, now: Optional[int] = None
) -> List[BlastTournament]:
    current = int(datetime.now(timezone.utc).timestamp()) if now is None else int(now)
    return sorted(
        (item for item in tournaments if item.end_at >= current),
        key=lambda item: (item.start_at, item.name.casefold()),
    )


def _hydration_tournaments(page: str) -> Dict[str, Dict[str, Any]]:
    try:
        values = json.loads(_decode_hydration(page))
        root = _unflatten_devalue(values)
    except (KeyError, TypeError, ValueError, RecursionError):
        return {}
    if not isinstance(root, dict):
        return {}
    loader_data = root.get("loaderData")
    if not isinstance(loader_data, dict):
        return {}
    route = loader_data.get("routes/$gameId.tournaments._index")
    if not isinstance(route, dict):
        return {}
    grouped = route.get("groupedUpcomingTournaments")
    if not isinstance(grouped, dict):
        return {}
    records: Dict[str, Dict[str, Any]] = {}
    for group in grouped.values():
        if not isinstance(group, list):
            continue
        for record in group:
            if not isinstance(record, dict):
                continue
            slug = _optional_text(record.get("id"))
            if slug:
                records[slug] = record
    return records


def _unflatten_devalue(values: Any) -> Any:
    """Decode the reference-array subset emitted by BLAST's server renderer."""
    if not isinstance(values, list):
        raise TypeError("devalue payload is not an array")
    memo: Dict[int, Any] = {}

    def load(reference: Any) -> Any:
        if not isinstance(reference, int):
            return reference
        if reference < 0:
            return None
        if reference >= len(values):
            raise ValueError("devalue reference is out of range")
        if reference in memo:
            return memo[reference]
        raw = values[reference]
        if isinstance(raw, dict):
            hydrated: Dict[str, Any] = {}
            memo[reference] = hydrated
            for key_reference, value_reference in raw.items():
                if not key_reference.startswith("_"):
                    raise ValueError("invalid devalue object key")
                key = load(int(key_reference[1:]))
                hydrated[str(key)] = load(value_reference)
            return hydrated
        if isinstance(raw, list):
            hydrated_list: List[Any] = []
            memo[reference] = hydrated_list
            hydrated_list.extend(load(item) for item in raw)
            return hydrated_list
        memo[reference] = raw
        return raw

    return load(0)


def _blast_page_diagnostics(page: str) -> str:
    """Return structural counts only; never include response content."""
    parser = _StructuredDataParser()
    parser.feed(page)
    sports_events = 0
    rlcs_events = 0
    for script in parser.scripts:
        try:
            value = json.loads(script)
        except (TypeError, ValueError):
            continue
        if not isinstance(value, dict) or value.get("@type") != "ItemList":
            continue
        for entry in value.get("itemListElement") or []:
            item = entry.get("item") if isinstance(entry, dict) else None
            if not isinstance(item, dict) or item.get("@type") != "SportsEvent":
                continue
            sports_events += 1
            if "rlcs" in str(item.get("name") or "").casefold():
                rlcs_events += 1
    normalized = _decode_hydration(page)
    dates = len(re.findall(r"20\d{2}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", normalized))
    world = "rlcs-world-championship-2026" in normalized
    world_state = "yes" if world else "no"
    byte_count = len(page.encode("utf-8"))
    return (
        f"bytes={byte_count}, jsonld={len(parser.scripts)}, "
        f"events={sports_events}, rlcs={rlcs_events}, hydration={len(normalized)}, "
        f"dates={dates}, world={world_state}"
    )


def _safe_blast_url(value: Any) -> Optional[str]:
    text = _optional_text(value)
    if not text:
        return None
    parsed = urlparse(text)
    if parsed.scheme != "https" or parsed.hostname != BLAST_HOST:
        return None
    if not parsed.path.startswith("/rl/tournaments/"):
        return None
    return text


def _decode_hydration(page: str) -> str:
    chunks = []
    pattern = re.compile(
        r'window\.__reactRouterContext\.streamController\.enqueue\('
        r'(?P<value>"(?:\\.|[^"\\])*")\);'
    )
    for match in pattern.finditer(page):
        try:
            value = json.loads(match.group("value"))
        except (TypeError, ValueError):
            continue
        if isinstance(value, str):
            chunks.append(value)
    source = "\n".join(chunks) if chunks else page
    return html_module.unescape(source).replace('\\"', '"')


def _iso_timestamp(value: str) -> Optional[int]:
    try:
        return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())
    except (TypeError, ValueError):
        return None


def _optional_int(value: Any) -> Optional[int]:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _optional_text(value: Any) -> Optional[str]:
    text = str(value).strip() if value is not None else ""
    return text or None
