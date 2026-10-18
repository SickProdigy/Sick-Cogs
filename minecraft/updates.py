"""Official Minecraft release-source parsing helpers."""

import datetime
import html
import re
from html.parser import HTMLParser


JAVA_PRERELEASE_URL = (
    "https://feedback.minecraft.net/api/v2/help_center/en-us/sections/"
    "360002267532/articles.json?sort_by=created_at&sort_order=desc&per_page=100"
)
CHANGELOG_URL = (
    "https://feedback.minecraft.net/api/v2/help_center/en-us/sections/"
    "360001186971/articles.json?sort_by=created_at&sort_order=desc&per_page=100"
)
PREVIEW_URL = (
    "https://feedback.minecraft.net/api/v2/help_center/en-us/sections/"
    "360001185332/articles.json?sort_by=created_at&sort_order=desc&per_page=100"
)
MAX_SEEN_UPDATE_IDS = 500


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.ignored = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.ignored += 1
        elif not self.ignored and tag in {"p", "li", "br", "h1", "h2", "h3"}:
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in {"script", "style"} and self.ignored:
            self.ignored -= 1

    def handle_data(self, data):
        if not self.ignored:
            self.parts.append(data)


def body_summary(value, limit=420):
    parser = _TextExtractor()
    try:
        parser.feed(str(value or ""))
    except (TypeError, ValueError):
        return "Official Minecraft release notes are available at the source link."
    text = re.sub(r"\s+", " ", html.unescape("".join(parser.parts))).strip()
    if not text:
        return "Official Minecraft release notes are available at the source link."
    if len(text) <= limit:
        return text
    clipped = text[: limit + 1].rsplit(" ", 1)[0].rstrip(".,;: ")
    return f"{clipped}…"


def _timestamp(value):
    try:
        parsed = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed.astimezone(datetime.timezone.utc).isoformat()


def _version_from_title(title):
    matches = re.findall(r"(?<!\d)(\d+(?:\.\d+)+(?:[-.][0-9A-Za-z]+)*)", title)
    return matches[-1] if matches else title


def parse_feedback_articles(payload, *, preview=False):
    if not isinstance(payload, dict) or not isinstance(payload.get("articles"), list):
        raise ValueError("Minecraft Feedback returned an invalid article list.")
    items = []
    for article in payload["articles"]:
        if not isinstance(article, dict):
            continue
        title = str(article.get("title") or "").strip()
        article_id = article.get("id")
        url = str(article.get("html_url") or "").strip()
        published_at = _timestamp(article.get("created_at"))
        lowered = title.casefold()
        if not title or not article_id or not url or not published_at:
            continue
        if "java" in lowered:
            edition = "java"
            if "release candidate" in lowered or re.search(r"(?:^|\W)rc(?:\W|\d)", lowered):
                channel = "release candidate"
            elif "pre-release" in lowered or "prerelease" in lowered:
                channel = "prerelease"
            elif "snapshot" in lowered:
                channel = "snapshot"
            else:
                channel = "release"
        elif preview:
            edition, channel = "bedrock", "preview"
        elif "bedrock" in lowered:
            edition, channel = "bedrock", "release"
        else:
            continue
        items.append(
            {
                "id": f"feedback:{article_id}",
                "edition": edition,
                "channel": channel,
                "version": _version_from_title(title),
                "title": title,
                "published_at": published_at,
                "summary": body_summary(article.get("body")),
                "url": url,
                "source": "Minecraft Feedback",
            }
        )
    return items


def sort_updates(items, *, reverse=False):
    return sorted(items, key=lambda item: (item["published_at"], item["id"]), reverse=reverse)


def filter_updates(items, settings):
    result = []
    for item in items:
        if item["edition"] == "java" and not settings.get("updates_java", True):
            continue
        if item["edition"] == "bedrock" and not settings.get("updates_bedrock", True):
            continue
        if item["edition"] == "java" and item["channel"] != "release":
            if not settings.get("updates_java_prerelease", False):
                continue
        if item["edition"] == "bedrock" and item["channel"] == "preview":
            if not settings.get("updates_bedrock_preview", False):
                continue
        result.append(item)
    return result
