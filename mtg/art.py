import asyncio
import io
import os
from pathlib import Path
from urllib.parse import urlparse

from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError

API_ROOT = "https://api.scryfall.com/cards"
USER_AGENT = "Sick-Cogs-MTG/0.7 (+https://gitea.rcs1.top/sickprodigy/Sick-Cogs)"
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
        self._api_lock = asyncio.Lock()
        self._last_api_request = 0.0

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
        async with self._api_lock:
            loop=asyncio.get_running_loop()
            delay=0.11-(loop.time()-self._last_api_request)
            if delay>0: await asyncio.sleep(delay)
            async with self.session.get(f"{API_ROOT}/{printing_id}", headers=headers) as response:
                self._last_api_request=loop.time()
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


def render_battlefield(game, names, paths, background_path):
    with Image.open(background_path) as source:
        canvas = source.convert("RGB").resize((1280, 853), Image.Resampling.LANCZOS)
    draw = ImageDraw.Draw(canvas, "RGBA")
    title_font, label_font, small_font = _font(26), _font(18), _font(14)
    draw.rounded_rectangle((390, 362, 890, 491), 18, fill=(8, 12, 11, 215), outline=(222, 185, 82, 230), width=3)
    phase = game.phase.replace("_", " ").title()
    draw.text((640, 384), f"Turn {game.turn} - {phase}", fill=(250, 240, 215), font=title_font, anchor="mm")
    priority = names.get(game.priority_user, "Declaration step") if game.priority_user else "Declaration step"
    draw.text((640, 425), f"Priority: {priority}", fill=(218, 210, 188), font=label_font, anchor="mm")
    if game.stack:
        stack_names = " -> ".join(game.card(spell.uid).name for spell in reversed(game.stack))
        draw.text((640, 462), f"Stack: {stack_names}", fill=(255, 215, 132), font=small_font, anchor="mm")

    def draw_player(user, top):
        player = game.players[user]
        y0 = 34 if top else 626
        y_cards = 92 if top else 666
        draw.rounded_rectangle((194, y0, 1086, y0 + 190), 16, fill=(7, 10, 9, 150), outline=(200, 174, 112, 180), width=2)
        summary = (
            f"{names[user]}  |  Life {player.life}  |  Hand {len(player.hand)}  |  "
            f"Library {len(player.library)}  |  Graveyard {len(player.graveyard)}  |  Exile 0"
        )
        draw.text((214, y0 + 12), summary, fill=(249, 241, 220), font=label_font)
        permanents = player.battlefield[:8]
        for index, permanent in enumerate(permanents, 1):
            x = 214 + (index - 1) * 108
            card = game.card(permanent.uid)
            panel = Image.new("RGB", (96, 134), (52, 58, 54))
            path = paths.get(card.key)
            if path:
                try:
                    with Image.open(path) as image:
                        image = image.convert("RGB")
                        image.thumbnail((96, 134), Image.Resampling.LANCZOS)
                        panel.paste(image, ((96 - image.width) // 2, (134 - image.height) // 2))
                except (OSError, UnidentifiedImageError):
                    path = None
            if not path:
                fallback = ImageDraw.Draw(panel)
                fallback.multiline_text((7, 28), f"{card.name}\n{card.power}/{card.toughness}" if card.creature else card.name, fill=(240, 235, 218), font=small_font, spacing=4)
            if permanent.tapped:
                overlay = Image.new("RGBA", panel.size, (45, 18, 18, 115))
                panel = Image.alpha_composite(panel.convert("RGBA"), overlay).convert("RGB")
            canvas.paste(panel, (x, y_cards))
            draw.ellipse((x + 4, y_cards + 4, x + 32, y_cards + 32), fill=(10, 13, 12, 235), outline=(222, 185, 82, 255), width=2)
            draw.text((x + 18, y_cards + 18), str(index), fill=(255, 244, 207), font=small_font, anchor="mm")
            if card.keyword_text:
                short={"Flying":"Fly","Vigilance":"Vig","Defender":"Def","Reach":"Reach"}
                abilities="/".join(short.get(word,word[:3]) for word in card.keyword_text.split(", "))
                draw.rounded_rectangle((x+34,y_cards+4,x+92,y_cards+23),5,fill=(8,12,11,220))
                draw.text((x+63,y_cards+13),abilities,fill=(255,244,207),font=_font(10),anchor="mm")
            if permanent.tapped:
                draw.text((x + 48, y_cards + 116), "TAPPED", fill=(255, 220, 205), font=small_font, anchor="mm")
        if len(player.battlefield) > 8:
            draw.text((1068, y_cards + 60), f"+{len(player.battlefield)-8}", fill=(255, 244, 207), font=label_font, anchor="e")

    draw_player(game.order[1], True)
    draw_player(game.order[0], False)
    output = io.BytesIO()
    canvas.save(output, format="PNG", optimize=True)
    output.seek(0)
    return output
