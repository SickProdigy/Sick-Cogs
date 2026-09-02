from collections import defaultdict
from collections.abc import Awaitable, Callable, Collection, Mapping
from string import Template
from typing import Any, Optional

import discord
import re
from redbot.core.utils.chat_formatting import bold, pagify

class TemplateValidationError(ValueError):
    """Raised when an RSS message template cannot be safely configured."""

def template_fields(template: str) -> set[str]:
    """Return placeholder names and reject malformed dollar expressions."""
    fields: set[str] = set()
    for match in Template.pattern.finditer(template):
        name = match.group("named") or match.group("braced")
        if name:
            fields.add(name)
        elif match.group("invalid") is not None:
            raise TemplateValidationError(
                "The template contains an invalid `$` placeholder. Use `$$` for a literal dollar sign."
            )
    return fields

def validate_template(template: str, available_fields: Collection[str]) -> None:
    """Reject placeholders that are not supplied by this feed."""
    unknown = template_fields(template) - set(available_fields) - {"name"}
    if unknown:
        formatted = ", ".join(f"`${field}`" for field in sorted(unknown))
        raise TemplateValidationError(f"Unknown template tag(s): {formatted}.")

def render_template(template: str, values: Mapping[str, Any]) -> str:
    """Render a saved template, treating fields absent from an entry as empty."""
    return Template(template).substitute(defaultdict(str, values))

VALID_IMAGES = ["png", "webp", "gif", "jpeg", "jpg"]

class FeedRenderer:
    """Render feed entries without performing Discord delivery."""

    @staticmethod
    def render_message(
        feed_name: str,
        template: str,
        entry: Mapping[str, Any],
        character_limit: int = 0,
    ) -> Optional[str]:
        message = render_template(template, {"name": bold(feed_name), **entry})
        if not message or not message.strip():
            return None

        if character_limit > 0:
            pages = list(
                pagify(
                    message,
                    delims=["\n", " "],
                    priority=True,
                    page_length=character_limit + 8,
                )
            )
            return pages[0] if pages else None
        return message

    @staticmethod
    async def build_embeds(
        message: str,
        feed: Mapping[str, Any],
        entry: Mapping[str, Any],
        validate_image: Callable[[str], Awaitable[Optional[str]]],
    ) -> list[discord.Embed]:
        embeds = [discord.Embed(description=page) for page in pagify(message, delims=["\n"])]
        if not embeds:
            return []

        color = feed.get("embed_color")
        if color:
            parsed_color = discord.Color(int(color, 16))
            for embed in embeds:
                embed.color = parsed_color

        for time_tag in ("updated_parsed_datetime", "published_parsed_datetime"):
            published_time = entry.get(time_tag)
            if published_time:
                embeds[-1].timestamp = published_time
                break

        image_tag = feed.get("embed_image")
        image_url = entry.get(image_tag) if image_tag else None
        if image_url and await validate_image(image_url) in VALID_IMAGES:
            embeds[-1].set_image(url=image_url)

        thumbnail_tag = feed.get("embed_thumbnail")
        thumbnail_url = entry.get(thumbnail_tag) if thumbnail_tag else None
        if thumbnail_url and await validate_image(thumbnail_url) in VALID_IMAGES:
            embeds[0].set_thumbnail(url=thumbnail_url)

        return embeds

    @staticmethod
    def render_announcement(
        feed_name: str,
        announcement: Optional[str],
        entry: Mapping[str, Any],
    ) -> Optional[str]:
        """Render optional normal-message content sent with a feed entry."""
        if not announcement:
            return None
        rendered = render_template(
            announcement, {"name": bold(feed_name), **entry}
        ).strip()
        return rendered or None

    @staticmethod
    def announcement_role_ids(announcement: Optional[str]) -> set[int]:
        """Return only role IDs explicitly written into the saved template."""
        if not announcement:
            return set()
        return {
            int(role_id)
            for role_id in re.findall(r"<@&([0-9]{1,20})>", announcement)
        }
