from __future__ import annotations

import asyncio
import io
import textwrap
from pathlib import Path
from typing import Dict, Optional, Tuple

import discord
from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError
from redbot.core import commands
from redbot.core.utils.chat_formatting import box

MAX_INPUT_BYTES = 8 * 1024 * 1024
MAX_PIXELS = 16_000_000
CAPTION_LIMIT = 300
TEMPLATE_DIR = Path(__file__).parent / "templates"
TEMPLATES: Dict[str, Tuple[str, str]] = {
    "better-choice": ("Better Choice", "better-choice.png"),
    "paperwork-escalation": ("Paperwork Escalation", "paperwork-escalation.png"),
}


class Meme(commands.Cog):
    """Create static memes locally from original templates or Discord avatars."""

    __author__ = ["SickProdigy"]
    __version__ = "1.0.0"

    def __init__(self, bot):
        self.bot = bot

    async def red_delete_data_for_user(self, **kwargs):
        """Nothing to delete."""

    def format_help_for_context(self, ctx: commands.Context) -> str:
        result = super().format_help_for_context(ctx)
        return f"{result}\n\nAuthor: {self.__author__[0]}\nCog Version: {self.__version__}"

    @staticmethod
    def _captions(text: str) -> Tuple[str, str]:
        parts = [part.strip() for part in text.split("|", maxsplit=1)]
        top, bottom = parts[0], parts[1] if len(parts) == 2 else ""
        if not top and not bottom:
            raise commands.BadArgument("Provide caption text.")
        if len(top) > CAPTION_LIMIT or len(bottom) > CAPTION_LIMIT:
            raise commands.BadArgument(f"Each caption must be {CAPTION_LIMIT} characters or fewer.")
        return top, bottom

    @staticmethod
    def _template(query: str) -> str:
        query = query.casefold().strip().replace("_", "-").replace(" ", "-")
        if query in TEMPLATES:
            return query
        matches = [key for key, (name, _) in TEMPLATES.items()
                   if query in key or query in name.casefold().replace(" ", "-")]
        if len(matches) == 1:
            return matches[0]
        if not matches:
            raise commands.BadArgument(f"No bundled template matches `{query}`.")
        raise commands.BadArgument("That template name is ambiguous.")

    @staticmethod
    def _font(size: int):
        try:
            return ImageFont.truetype("DejaVuSans-Bold.ttf", size=size)
        except OSError:
            return ImageFont.load_default()

    @classmethod
    def _draw_caption(cls, image, text: str, region) -> None:
        if not text:
            return
        draw = ImageDraw.Draw(image)
        left, top, right, bottom = region
        pad = max(12, image.width // 60)
        width, height = right - left - 2 * pad, bottom - top - 2 * pad
        chosen = None
        for size in range(min(72, max(24, width // 12)), 17, -2):
            font = cls._font(size)
            wrapped = textwrap.fill(text.upper(), width=max(8, int(width / (size * 0.58))))
            bounds = draw.multiline_textbbox((0, 0), wrapped, font=font, spacing=5, stroke_width=3)
            if bounds[2] <= width and bounds[3] <= height:
                chosen = font, wrapped, bounds
                break
        if chosen is None:
            font = cls._font(18)
            wrapped = textwrap.fill(text.upper(), width=max(8, width // 11))
            bounds = draw.multiline_textbbox((0, 0), wrapped, font=font, spacing=4, stroke_width=2)
        else:
            font, wrapped, bounds = chosen
        x = left + ((right - left) - (bounds[2] - bounds[0])) / 2
        y = top + ((bottom - top) - (bounds[3] - bounds[1])) / 2
        draw.multiline_text((x, y), wrapped, font=font, fill="white", align="center",
                            spacing=5, stroke_width=3, stroke_fill="black")

    @classmethod
    def _render(cls, source: bytes, top: str, bottom: str, add_bands: bool) -> bytes:
        if len(source) > MAX_INPUT_BYTES:
            raise ValueError("The source image is too large.")
        try:
            with Image.open(io.BytesIO(source)) as opened:
                if opened.width * opened.height > MAX_PIXELS:
                    raise ValueError("The source image has too many pixels.")
                image = opened.convert("RGB")
        except (Image.DecompressionBombError, UnidentifiedImageError, OSError) as exc:
            raise ValueError("The source image could not be read.") from exc
        image.thumbnail((1200, 800), Image.Resampling.LANCZOS)
        if add_bands:
            band = max(100, image.height // 5)
            canvas = Image.new("RGB", (image.width, image.height + 2 * band), "black")
            canvas.paste(image, (0, band))
            image = canvas
        else:
            band = max(120, image.height // 5)
        cls._draw_caption(image, top, (0, 0, image.width, band))
        cls._draw_caption(image, bottom, (0, image.height - band, image.width, image.height))
        output = io.BytesIO()
        image.save(output, format="PNG", optimize=True)
        return output.getvalue()

    async def _send_render(self, ctx, source, top, bottom, filename, add_bands=False):
        loop = asyncio.get_running_loop()
        try:
            result = await loop.run_in_executor(None, self._render, source, top, bottom, add_bands)
        except ValueError as exc:
            await ctx.send(str(exc))
            return
        except Exception:
            await ctx.send("I couldn't render that meme. Please try again.")
            return
        await ctx.send(file=discord.File(io.BytesIO(result), filename=filename))

    @commands.group(invoke_without_command=True)
    @commands.bot_has_permissions(attach_files=True)
    async def meme(self, ctx, template: Optional[str] = None, *, text: str = ""):
        """Create a meme: `meme <template> <top> | <bottom>`."""
        if template is None:
            await ctx.send_help()
            return
        key = self._template(template)
        top, bottom = self._captions(text)
        source = (TEMPLATE_DIR / TEMPLATES[key][1]).read_bytes()
        async with ctx.typing():
            await self._send_render(ctx, source, top, bottom, f"meme-{key}.png")

    @meme.command(name="list", aliases=["templates"])
    async def meme_list(self, ctx, *, search: str = ""):
        """List bundled meme templates."""
        search = search.casefold().strip()
        matches = [(key, name) for key, (name, _) in TEMPLATES.items()
                   if not search or search in key or search in name.casefold()]
        if not matches:
            await ctx.send("No bundled templates matched that search.")
            return
        await ctx.send("**Bundled meme templates**\n" +
                       "\n".join(f"- `{key}` — {name}" for key, name in matches))

    @meme.command(name="avatar")
    @commands.bot_has_permissions(attach_files=True)
    async def meme_avatar(self, ctx, member: discord.Member, *, text: str):
        """Create a meme using a member's displayed avatar."""
        top, bottom = self._captions(text)
        try:
            source = await member.display_avatar.with_size(1024).read()
        except discord.HTTPException:
            await ctx.send("I couldn't download that avatar. Please try again.")
            return
        async with ctx.typing():
            await self._send_render(
                ctx, source, top, bottom, f"meme-avatar-{member.id}.png", add_bands=True
            )

    @commands.command()
    async def memeversion(self, ctx):
        """Show the installed Meme cog version."""
        await ctx.send(box(
            f"Meme cog version: {self.__version__}\nAuthors: {', '.join(self.__author__)}",
            lang="py",
        ))
