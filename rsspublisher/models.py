import hashlib
import json
import time
from copy import deepcopy
from enum import Enum
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
