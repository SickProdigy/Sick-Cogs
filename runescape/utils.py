import csv
import io
import re
from urllib.parse import quote

OSRS_SKILLS = (
    "Overall", "Attack", "Defence", "Strength", "Hitpoints", "Ranged", "Prayer", "Magic",
    "Cooking", "Woodcutting", "Fletching", "Fishing", "Firemaking", "Crafting", "Smithing",
    "Mining", "Herblore", "Agility", "Thieving", "Slayer", "Farming", "Runecraft", "Hunter",
    "Construction",
)
RS3_SKILLS = (
    "Overall", "Attack", "Defence", "Strength", "Constitution", "Ranged", "Prayer", "Magic",
    "Cooking", "Woodcutting", "Fletching", "Fishing", "Firemaking", "Crafting", "Smithing",
    "Mining", "Herblore", "Agility", "Thieving", "Slayer", "Farming", "Runecrafting", "Hunter",
    "Construction", "Summoning", "Dungeoneering", "Divination", "Invention", "Archaeology",
    "Necromancy",
)


def parse_hiscores(text: str, skills: tuple[str, ...]) -> list[dict]:
    """Parse the skill section of a Jagex lite hiscores response."""
    rows = []
    for name, row in zip(skills, csv.reader(io.StringIO(text))):
        if len(row) < 3:
            continue
        try:
            rank, level, experience = (int(value) for value in row[:3])
        except ValueError:
            continue
        rows.append({"name": name, "rank": rank, "level": level, "experience": experience})
    return rows


def clean_extract(value: str, limit: int = 900) -> str:
    """Make a MediaWiki extract safe and compact enough for an embed."""
    value = re.sub(r"\s+", " ", value or "").strip()
    if not value:
        return "No summary is available for this page."
    if len(value) <= limit:
        return value
    shortened = value[: limit - 1].rsplit(" ", 1)[0]
    return f"{shortened}…"


def wiki_page_url(base_url: str, title: str) -> str:
    return f"{base_url}/w/{quote(title.replace(' ', '_'), safe='()_-')}"
