from __future__ import annotations

import html as html_module
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any, Dict, Iterable, List, Optional
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
        try:
            async with self.session.get(
                BLAST_TOURNAMENTS_URL,
                allow_redirects=False,
                headers=BLAST_REQUEST_HEADERS,
            ) as response:
                if response.status == 429:
                    raise BlastError("BLAST is rate limiting the weekly schedule request.")
                if response.status != 200:
                    raise BlastError("BLAST could not provide the RLCS schedule.")
                declared_size = response.content_length
                if declared_size is not None and declared_size > MAX_BLAST_RESPONSE_BYTES:
                    raise BlastError("BLAST returned a schedule page larger than the safety limit.")
                chunks = []
                received = 0
                async for chunk in response.content.iter_chunked(64 * 1024):
                    received += len(chunk)
                    if received > MAX_BLAST_RESPONSE_BYTES:
                        raise BlastError(
                            "BLAST returned a schedule page larger than the safety limit."
                        )
                    chunks.append(chunk)
                payload = b"".join(chunks)
        except BlastError:
            raise
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise BlastError("The weekly BLAST schedule request could not be reached.") from exc
        try:
            page = payload.decode(response.charset or "utf-8")
        except (LookupError, UnicodeDecodeError) as exc:
            raise BlastError("BLAST returned an unreadable schedule page.") from exc
        tournaments = parse_blast_tournaments(page)
        if not tournaments:
            details = _blast_page_diagnostics(page)
            raise BlastError(
                "BLAST returned no dated RLCS tournaments; the cached schedule was kept. "
                f"Diagnostic: {details}."
            )
        return tournaments


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
