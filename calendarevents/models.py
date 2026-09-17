"""Portable calendar event formatting without external calendar access."""

from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlencode


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


@dataclass(frozen=True)
class CalendarEvent:
    event_id: str
    guild_id: int
    creator_id: int
    title: str
    starts_at: datetime
    ends_at: datetime
    description: str = ""
    location: str = ""
    source_url: str = ""

    def __post_init__(self):
        if not self.event_id or self.guild_id <= 0 or self.creator_id <= 0 or not self.title.strip():
            raise ValueError("Calendar event identity and title are required.")
        if self.starts_at.tzinfo is None or self.ends_at.tzinfo is None or self.ends_at <= self.starts_at:
            raise ValueError("Calendar events require ordered timezone-aware dates.")

    @classmethod
    def from_raw(cls, raw):
        if not isinstance(raw, dict):
            raise ValueError("Calendar event data must be an object.")
        try:
            return cls(
                str(raw["event_id"]), int(raw["guild_id"]), int(raw["creator_id"]), str(raw["title"]),
                datetime.fromisoformat(str(raw["starts_at"])), datetime.fromisoformat(str(raw["ends_at"])),
                str(raw.get("description", "")), str(raw.get("location", "")), str(raw.get("source_url", "")),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Calendar event data is invalid.") from exc

    def to_raw(self):
        return {
            "event_id": self.event_id, "guild_id": self.guild_id, "creator_id": self.creator_id,
            "title": self.title, "starts_at": self.starts_at.isoformat(), "ends_at": self.ends_at.isoformat(),
            "description": self.description, "location": self.location, "source_url": self.source_url,
        }

    @staticmethod
    def _utc(value):
        return value.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    def google_url(self):
        details = self.description + ("\n\nSource: " + self.source_url if self.source_url else "")
        return "https://calendar.google.com/calendar/render?" + urlencode({
            "action": "TEMPLATE", "text": self.title, "dates": f"{self._utc(self.starts_at)}/{self._utc(self.ends_at)}",
            "details": details, "location": self.location,
        })

    def ics(self):
        details = self.description + ("\nSource: " + self.source_url if self.source_url else "")
        now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//SickGaming//Calendar//EN", "BEGIN:VEVENT",
                 f"UID:{_escape(self.event_id)}@sickgaming", f"DTSTAMP:{now}", f"DTSTART:{self._utc(self.starts_at)}",
                 f"DTEND:{self._utc(self.ends_at)}", f"SUMMARY:{_escape(self.title)}", f"DESCRIPTION:{_escape(details)}"]
        if self.location:
            lines.append(f"LOCATION:{_escape(self.location)}")
        lines.extend(["END:VEVENT", "END:VCALENDAR", ""])
        return "\r\n".join(lines)
