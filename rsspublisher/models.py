import hashlib
import json
import time
from copy import deepcopy
from enum import Enum
from html import unescape
from html.parser import HTMLParser
from typing import Any, Mapping, Optional

class FeedMode(str, Enum):
    LATEST = "latest"
    CATCHUP = "catchup"

FEED_DEFAULTS = {
    "last_title": None,
    "last_link": None,
    "last_time": None,
    "last_entry_id": None,
    "template": "$title\n$link",
    "announcement": None,
    "template_tags": [],
    "is_special": [],
    "embed": True,
    "embed_color": None,
    "embed_image": None,
    "embed_thumbnail": None,
    "allowed_tags": [],
    "title_prefix": None,
    "allowed_authors": [],
    "limit": 0,
    "mode": FeedMode.LATEST.value,
    "paused": False,
    "last_checked_at": None,
    "last_success_at": None,
    "last_error": None,
    "consecutive_failures": 0,
}

def normalize_mode(value: Any) -> str:
    try:
        return FeedMode(str(value).lower()).value
    except ValueError:
        return FeedMode.LATEST.value

def migrate_feed_data(data: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
    """Return a current feed dictionary and whether persisted data changed."""
    migrated = dict(data)
    for key, value in FEED_DEFAULTS.items():
        if key not in migrated:
            migrated[key] = deepcopy(value)

    migrated["mode"] = normalize_mode(migrated.get("mode"))
    migrated["embed"] = bool(migrated.get("embed", True))
    migrated["paused"] = bool(migrated.get("paused", False))
    title_prefix = migrated.get("title_prefix")
    migrated["title_prefix"] = str(title_prefix).strip() if title_prefix else None
    allowed_authors = migrated.get("allowed_authors", [])
    migrated["allowed_authors"] = [
        str(author).strip() for author in allowed_authors
        if str(author).strip()
    ] if isinstance(allowed_authors, (list, tuple)) else []
    try:
        failure_count = int(migrated.get("consecutive_failures", 0) or 0)
    except (TypeError, ValueError):
        failure_count = 0
    migrated["consecutive_failures"] = max(0, failure_count)
    return migrated, migrated != dict(data)

def entry_identity(entry: Mapping[str, Any]) -> str:
    """Build a stable identity from a feed entry without trusting timestamps alone."""
    for key in ("id", "guid"):
        value = entry.get(key)
        if value:
            return f"id:{value}"

    link = entry.get("link")
    if link:
        return f"link:{link}"

    timestamp = entry.get("updated_parsed") or entry.get("published_parsed")
    if isinstance(timestamp, time.struct_time):
        timestamp = tuple(timestamp)
    fallback = json.dumps(
        {
            "title": entry.get("title", ""),
            "timestamp": timestamp,
            "summary": entry.get("summary", ""),
        },
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    return f"hash:{hashlib.sha256(fallback.encode('utf-8')).hexdigest()}"


class _PlaintextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data: str):
        if data.strip():
            self.parts.append(data.strip())


def normalize_author(value: Any) -> str:
    """Normalize a feed author value to safe plaintext for exact matching."""
    if isinstance(value, Mapping):
        value = value.get("name") or value.get("author") or ""
    parser = _PlaintextExtractor()
    parser.feed(unescape(str(value or "")))
    parser.close()
    return " ".join(parser.parts).strip()


def entry_authors(entry: Mapping[str, Any]) -> tuple[str, ...]:
    values = []
    authors = entry.get("authors")
    if isinstance(authors, (list, tuple)):
        values.extend(authors)
    for key in ("author_detail", "author"):
        if entry.get(key):
            values.append(entry[key])
    normalized = []
    for value in values:
        author = normalize_author(value)
        if author and author.casefold() not in {item.casefold() for item in normalized}:
            normalized.append(author)
    return tuple(normalized)


def feed_filter_failures(entry: Mapping[str, Any], feed: Mapping[str, Any]) -> tuple[str, ...]:
    """Return configured filter families that reject an entry."""
    migrated, _ = migrate_feed_data(feed)
    failures = []
    prefix = migrated.get("title_prefix")
    title = str(entry.get("title") or "").strip()
    if prefix and not title.casefold().startswith(str(prefix).casefold()):
        failures.append(f"title does not start with {prefix!r}")
    allowed_authors = {author.casefold() for author in migrated.get("allowed_authors", [])}
    authors = {author.casefold() for author in entry_authors(entry)}
    if allowed_authors and not authors.intersection(allowed_authors):
        failures.append("author is not in the allowlist")
    allowed_tags = {str(tag).casefold() for tag in migrated.get("allowed_tags", [])}
    entry_tags = {str(tag).casefold() for tag in entry.get("tags_list", [])}
    if allowed_tags and not entry_tags.intersection(allowed_tags):
        failures.append("entry tags do not match the allowlist")
    return tuple(failures)


def feed_filter_summary(feed: Mapping[str, Any]) -> str:
    migrated, _ = migrate_feed_data(feed)
    parts = []
    if migrated.get("title_prefix"):
        parts.append(f"title starts with {migrated['title_prefix']!r}")
    if migrated.get("allowed_authors"):
        parts.append("authors: " + ", ".join(migrated["allowed_authors"]))
    if migrated.get("allowed_tags"):
        parts.append("tags: " + ", ".join(migrated["allowed_tags"]))
    return " · ".join(parts) or "None"


def feed_delivery_style(feed: Mapping[str, Any]) -> str:
    """Describe the effective Discord delivery format for a saved feed."""
    migrated, _ = migrate_feed_data(feed)
    if migrated["embed"]:
        return "RSS embed"
    normalized_template = "".join(str(migrated["template"]).split())
    if normalized_template in {"$link", "${link}"}:
        return "Native link preview"
    return "Plain text"


def feed_summary(name: str, feed: Mapping[str, Any]) -> str:
    """Build a safe compact feed summary for list commands."""
    migrated, _ = migrate_feed_data(feed)
    state = "Paused" if migrated["paused"] else "Active"
    announcement = "Configured" if migrated.get("announcement") else "None"
    source = migrated.get("url") or "Unknown"
    mode = migrated["mode"]
    return (
        f"{name}\n"
        f"  Source: {source}\n"
        f"  Delivery: {feed_delivery_style(migrated)}\n"
        f"  State: {state} · {mode}\n"
        f"  Announcement: {announcement}"
        + (f"\n  Filters: {feed_filter_summary(migrated)}" if feed_filter_summary(migrated) != "None" else "")
    )


class RssFeed:
    """Serializable RSS feed configuration and delivery state."""

    def __init__(self, **kwargs):
        migrated, _ = migrate_feed_data(kwargs)
        self.name: Optional[str] = migrated.get("name")
        self.url: Optional[str] = migrated.get("url")
        for key in FEED_DEFAULTS:
            setattr(self, key, migrated[key])

    def to_json(self) -> dict[str, Any]:
        data = {"name": self.name, "url": self.url}
        data.update({key: deepcopy(getattr(self, key)) for key in FEED_DEFAULTS})
        return data

    @classmethod
    def from_json(cls, data: Mapping[str, Any]):
        return cls(**data)

INTERNAL_TAGS = ["is_special", "template_tags", "embed", "embed_color", "embed_image", "embed_thumbnail"]

class TagType(Enum):
    PLAINTEXT = 1
    HTML = 2
    DICT = 3
    LIST = 4
