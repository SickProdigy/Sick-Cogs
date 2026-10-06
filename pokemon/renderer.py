"""Bounded Pokémon encounter and retro battle rendering."""

import asyncio
import io
from pathlib import Path
from urllib.parse import urlparse

import aiohttp
from PIL import Image, ImageDraw, ImageFont

from .data import SPECIES, sprite

SPRITE_HOST = "raw.githubusercontent.com"
MAX_SPRITE_BYTES = 2 * 1024 * 1024
MAX_RENDER_BYTES = 8 * 1024 * 1024
RETRO = [
    (15, 56, 15),
    (48, 98, 48),
    (139, 172, 15),
    (155, 188, 15),
    (202, 220, 159),
    (224, 238, 207),
    (68, 72, 56),
    (255, 255, 255),
]


class RenderError(RuntimeError):
    pass


class BattleRenderer:
    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir
        self.session = None
        self.lock = asyncio.Lock()
        self.render_slots = asyncio.Semaphore(2)

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()

    async def _session(self):
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=20),
                headers={"User-Agent": "Sick-Cogs-Pokemon/0.2"},
            )
        return self.session

    async def get_sprite(self, species_id: int, *, back=False, shiny=False):
        variant = f"{species_id}-{'back' if back else 'front'}-{'shiny' if shiny else 'normal'}.png"
        path = self.cache_dir / variant
        if path.exists() and 0 < path.stat().st_size <= MAX_SPRITE_BYTES:
            return path.read_bytes()
        url = sprite(species_id, back=back, shiny=shiny)
        if (urlparse(url).hostname or "").casefold() != SPRITE_HOST:
            raise RenderError("Sprite provider host is not allowed.")
        session = await self._session()
        try:
            async with session.get(url) as response:
                if response.status != 200:
                    raise RenderError(f"Sprite provider returned HTTP {response.status}.")
                if (response.url.host or "").casefold() != SPRITE_HOST:
                    raise RenderError("Sprite provider redirected to an untrusted host.")
                data = await response.read()
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise RenderError("Sprite download failed.") from exc
        if not 0 < len(data) <= MAX_SPRITE_BYTES:
            raise RenderError("Sprite file size is invalid.")
        try:
            with Image.open(io.BytesIO(data)) as image:
                image.verify()
        except Exception as exc:
            raise RenderError("Sprite image is invalid.") from exc
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_bytes(data)
        temporary.replace(path)
        await self._prune()
        return data

    async def _prune(self):
        files = sorted(
            self.cache_dir.glob("*.png"),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
        for path in files[500:]:
            try:
                path.unlink()
            except OSError:
                pass

    async def _render(self, callback, *args):
        async with self.render_slots:
            return await asyncio.to_thread(callback, *args)

    async def encounter(self, species_id: int):
        data = await self.get_sprite(species_id)
        try:
            return await self._render(self._encounter_sync, species_id, data)
        except (OSError, ValueError) as exc:
            raise RenderError("Encounter rendering failed.") from exc

    async def battle(self, battle):
        front = await self.get_sprite(battle.wild_species_id)
        try:
            back = await self.get_sprite(
                battle.player.species_id, back=True, shiny=battle.player.shiny
            )
        except RenderError:
            back = await self.get_sprite(
                battle.player.species_id, shiny=battle.player.shiny
            )
        try:
            return await self._render(self._battle_sync, battle, front, back)
        except (OSError, ValueError) as exc:
            raise RenderError("Battle rendering failed.") from exc

    @staticmethod
    def _open(data, size, *, trim=False, upscale=False):
        image = Image.open(io.BytesIO(data)).convert("RGBA")
        if trim:
            bounds=image.getchannel("A").getbbox()
            if bounds:image=image.crop(bounds)
        maximum_width,maximum_height=size
        scale=min(maximum_width/max(1,image.width),maximum_height/max(1,image.height))
        if not upscale:scale=min(1.0,scale)
        else:scale=min(3.5,scale)
        target=(max(1,round(image.width*scale)),max(1,round(image.height*scale)))
        if target!=image.size:image=image.resize(target,Image.Resampling.NEAREST)
        return image

    @staticmethod
    def _save(image):
        output = io.BytesIO()
        image.save(output, "PNG", optimize=True)
        if output.tell() > MAX_RENDER_BYTES:
            raise RenderError("Rendered image exceeds Discord's upload limit.")
        output.seek(0)
        return output

    def _encounter_sync(self, species_id, data):
        canvas = Image.new("RGB", (800, 450), (105, 174, 93))
        draw = ImageDraw.Draw(canvas)
        for y in range(0, 330):
            ratio = y / 330
            draw.line((0, y, 800, y), fill=(90 + int(80 * ratio), 165 + int(45 * ratio), 220))
        draw.ellipse((90, 310, 710, 470), fill=(72, 139, 74))
        draw.ellipse((400, 285, 730, 375), fill=(198, 222, 165))
        pokemon = self._open(data,(240,210),trim=True,upscale=True)
        canvas.paste(pokemon,(565-pokemon.width//2,300-pokemon.height),pokemon)
        return self._save(canvas)

    def _battle_sync(self, battle, front_data, back_data):
        canvas = Image.new("RGB", (800, 450), RETRO[4])
        draw = ImageDraw.Draw(canvas)
        draw.rectangle((0, 0, 800, 315), fill=RETRO[5])
        for y in range(0, 315, 12):
            draw.line((0, y, 800, y), fill=RETRO[4])
        draw.ellipse((465, 190, 750, 255), fill=RETRO[2], outline=RETRO[0], width=4)
        draw.ellipse((55, 300, 390, 390), fill=RETRO[2], outline=RETRO[0], width=4)
        front = self._retro(self._open(front_data,(210,185),trim=True,upscale=True))
        back = self._retro(self._open(back_data,(250,220),trim=True,upscale=True))
        canvas.paste(front, (595 - front.width // 2, 220 - front.height), front)
        canvas.paste(back, (205 - back.width // 2, 350 - back.height), back)
        wild = SPECIES[battle.wild_species_id]
        player = SPECIES[battle.player.species_id]
        self._status_box(draw, (40, 35), wild.name, battle.wild_level, battle.wild_hp, battle.wild_max_hp, battle.wild_status)
        self._status_box(draw, (430, 270), player.name, battle.player.level, battle.player_hp, battle.max_hp(battle.player), battle.player_status)
        draw.rectangle((0, 390, 800, 450), fill=RETRO[5], outline=RETRO[0], width=5)
        text = battle.result or battle.last_action or f"What will {player.name} do?"
        draw.text((20, 410), text[:105], fill=RETRO[0], font=ImageFont.load_default(size=18))
        return self._save(canvas)

    @staticmethod
    def _retro(image):
        palette = Image.new("P", (1, 1))
        flat = []
        for color in RETRO:
            flat.extend(color)
        flat.extend([0] * (768 - len(flat)))
        palette.putpalette(flat)
        alpha = image.getchannel("A")
        rgb = image.convert("RGB").quantize(palette=palette, dither=Image.Dither.FLOYDSTEINBERG).convert("RGBA")
        rgb.putalpha(alpha)
        return rgb

    @staticmethod
    def _status_box(draw, origin, name, level, hp, maximum, status):
        x, y = origin
        draw.rounded_rectangle((x, y, x + 320, y + 88), 12, fill=RETRO[7], outline=RETRO[0], width=4)
        draw.text((x + 14, y + 10), f"{name}  Lv.{level}", fill=RETRO[0], font=ImageFont.load_default(size=18))
        draw.rectangle((x + 70, y + 45, x + 295, y + 62), outline=RETRO[0], width=2)
        width = int(221 * max(0, hp) / max(1, maximum))
        draw.rectangle((x + 72, y + 47, x + 72 + width, y + 60), fill=RETRO[2])
        label = f"HP {hp}/{maximum}"
        if status:
            label += f" · {status.upper()}"
        draw.text((x + 14, y + 67), label, fill=RETRO[0], font=ImageFont.load_default(size=14))
