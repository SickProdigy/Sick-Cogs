import asyncio
import io
import os
from pathlib import Path
from urllib.parse import urlparse

from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError

API_ROOT = "https://api.scryfall.com/cards"
USER_AGENT = "Sick-Cogs-MTG/0.2 (+https://gitea.rcs1.top/sickprodigy/Sick-Cogs)"
ACCEPT = "application/json;q=0.9,*/*;q=0.8"
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_CACHE_BYTES = 128 * 1024 * 1024
MAX_CACHE_FILES = 192
HAND_PAGE_SIZE = 8


class ArtError(RuntimeError):
    pass


class ScryfallArtCache:
    def __init__(self, root: Path, session):
        self.root = Path(root)
        self.session = session
        self._locks = {}

    def _lock(self, printing_id):
        return self._locks.setdefault(printing_id, asyncio.Lock())

    async def get(self, card):
        if not card.scryfall_id:
            return None
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / f"{card.scryfall_id}.jpg"
        if path.is_file():
            await asyncio.to_thread(os.utime, path, None)
            return path
        async with self._lock(card.scryfall_id):
            if path.is_file():
                return path
            image_url = await self._image_url(card.scryfall_id)
            payload = await self._download(image_url)
            await asyncio.to_thread(self._validate_and_write, path, payload)
            await asyncio.to_thread(self._trim)
            return path

    async def _image_url(self, printing_id):
        headers = {"User-Agent": USER_AGENT, "Accept": ACCEPT}
        async with self.session.get(f"{API_ROOT}/{printing_id}", headers=headers) as response:
            if response.status != 200:
                raise ArtError(f"Scryfall metadata returned HTTP {response.status}.")
            data = await response.json(content_type=None)
        image_uris = data.get("image_uris") or {}
        image_url = image_uris.get("normal") or image_uris.get("large")
        parsed = urlparse(image_url or "")
        if parsed.scheme != "https" or not parsed.hostname or not parsed.hostname.endswith(".scryfall.io"):
            raise ArtError("Scryfall returned an untrusted image URL.")
        return image_url

    async def _download(self, image_url):
        headers = {"User-Agent": USER_AGENT, "Accept": "image/*"}
        async with self.session.get(image_url, headers=headers) as response:
            if response.status != 200:
                raise ArtError(f"Card image returned HTTP {response.status}.")
            if not (response.headers.get("Content-Type") or "").lower().startswith("image/"):
                raise ArtError("Card image response was not an image.")
            payload = bytearray()
            async for chunk in response.content.iter_chunked(65536):
                payload.extend(chunk)
                if len(payload) > MAX_IMAGE_BYTES:
                    raise ArtError("Card image exceeded the cache size limit.")
        return bytes(payload)

    @staticmethod
    def _validate_and_write(path, payload):
        try:
            with Image.open(io.BytesIO(payload)) as image:
                image.verify()
                if image.width > 4096 or image.height > 4096:
                    raise ArtError("Card image dimensions were too large.")
                if image.format != "JPEG":
                    raise ArtError("Scryfall card image was not JPEG.")
        except (UnidentifiedImageError, OSError) as exc:
            raise ArtError("Card image could not be decoded.") from exc
        temporary = path.with_suffix(".tmp")
        temporary.write_bytes(payload)
        os.replace(temporary, path)

    def _trim(self):
        files = sorted(
            (path for path in self.root.glob("*.jpg") if path.is_file()),
            key=lambda path: path.stat().st_mtime,
        )
        total = sum(path.stat().st_size for path in files)
        while files and (len(files) > MAX_CACHE_FILES or total > MAX_CACHE_BYTES):
            oldest = files.pop(0)
            total -= oldest.stat().st_size
            oldest.unlink(missing_ok=True)


def _font(size):
    for candidate in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
    ):
        if Path(candidate).is_file():
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default()


def render_hand(cards, paths, page=0):
    start = page * HAND_PAGE_SIZE
    visible = cards[start : start + HAND_PAGE_SIZE]
    visible_paths = paths[start : start + HAND_PAGE_SIZE]
    if not visible:
        raise ArtError("That hand page is empty.")
    card_width, card_height = 220, 307
    columns = min(4, len(visible))
    rows = (len(visible) + 3) // 4
    canvas = Image.new("RGB", (columns * 236 + 20, rows * 327 + 54), (22, 27, 25))
    draw = ImageDraw.Draw(canvas)
    title_font, number_font = _font(24), _font(28)
    draw.text((16, 12), f"Private hand - page {page + 1}", fill=(240, 235, 218), font=title_font)
    for offset, (card, path) in enumerate(zip(visible, visible_paths)):
        column, row = offset % 4, offset // 4
        x, y = 16 + column * 236, 48 + row * 327
        panel = Image.new("RGB", (card_width, card_height), (49, 56, 52))
        if path:
            try:
                with Image.open(path) as source:
                    source = source.convert("RGB")
                    source.thumbnail((card_width, card_height), Image.Resampling.LANCZOS)
                    panel.paste(source, ((card_width - source.width) // 2, (card_height - source.height) // 2))
            except (OSError, UnidentifiedImageError):
                path = None
        if not path:
            fallback = ImageDraw.Draw(panel)
            fallback.multiline_text((14, 50), f"{card.name}\n\n{card.kind}\nCost {card.cost}\n\n{card.text}", fill=(240, 235, 218), font=_font(18), spacing=5)
        canvas.paste(panel, (x, y))
        draw.ellipse((x + 6, y + 6, x + 48, y + 48), fill=(15, 18, 17), outline=(222, 185, 82), width=3)
        label = str(start + offset + 1)
        draw.text((x + 27, y + 27), label, fill=(255, 244, 207), font=number_font, anchor="mm")
    output = io.BytesIO()
    canvas.save(output, format="PNG", optimize=True)
    output.seek(0)
    return output
