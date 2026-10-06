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
ENCOUNTER_BACKDROPS = (
    ((116,190,232),(210,238,220),(116,170,104),(72,142,76),"hills"),
    ((118,201,224),(225,240,207),(103,166,91),(63,131,68),"forest"),
    ((244,185,112),(255,226,174),(180,139,82),(120,111,66),"sunset"),
    ((107,128,190),(194,196,224),(98,111,139),(61,83,91),"mountains"),
    ((184,221,241),(238,243,220),(157,191,116),(95,151,77),"meadow"),
    ((89,157,190),(187,222,218),(80,137,124),(52,107,91),"water"),
    ((204,174,224),(239,222,231),(143,119,162),(89,83,126),"mist"),
    ((236,213,146),(249,237,190),(190,159,91),(137,116,68),"plains"),
    ((119,183,160),(213,232,192),(82,137,92),(49,104,70),"grove"),
    ((145,194,227),(226,238,244),(137,160,171),(81,116,131),"coast"),
    ((221,156,126),(247,213,176),(159,112,82),(105,83,64),"canyon"),
    ((103,104,162),(190,176,210),(80,91,126),(48,67,86),"night"),
)


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

    async def encounter(self,species_id:int,level:int=5,gender:str="unknown",backdrop:int=0):
        data=await self.get_sprite(species_id)
        try:
            return await self._render(self._encounter_sync,species_id,data,level,gender,backdrop)
        except (OSError, ValueError) as exc:
            raise RenderError("Encounter rendering failed.") from exc

    async def starter(self,pokemon,trainer_name):
        data=await self.get_sprite(pokemon.species_id,shiny=pokemon.shiny)
        try:
            return await self._render(self._starter_sync,pokemon,data,trainer_name)
        except (OSError,ValueError) as exc:
            raise RenderError("Starter reveal rendering failed.") from exc

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

    @staticmethod
    def _gender_mark(draw,origin,gender,fill=RETRO[0]):
        x,y=origin
        if gender=="male":
            draw.ellipse((x,y+3,x+14,y+17),outline=fill,width=3);draw.line((x+12,y+5,x+24,y-7),fill=fill,width=3)
            draw.line((x+17,y-7,x+24,y-7,x+24,y),fill=fill,width=3)
        elif gender=="female":
            draw.ellipse((x,y,x+14,y+14),outline=fill,width=3);draw.line((x+7,y+14,x+7,y+27),fill=fill,width=3);draw.line((x+1,y+21,x+13,y+21),fill=fill,width=3)
        else:draw.line((x,y+10,x+18,y+10),fill=fill,width=3)

    def _starter_sync(self,pokemon,data,trainer_name):
        canvas=Image.new("RGB",(800,450),RETRO[5]);draw=ImageDraw.Draw(canvas)
        # Original retro laboratory: paneled walls, equipment, tiled floor, and center table.
        for y in range(235):
            ratio=y/234;color=tuple(round(a+(b-a)*ratio) for a,b in zip((177,211,218),(225,232,211)))
            draw.line((0,y,800,y),fill=color)
        draw.rectangle((0,205,800,235),fill=(83,112,103),outline=RETRO[0],width=4)
        for left in (35,565):
            draw.rectangle((left,35,left+200,165),fill=(111,174,197),outline=RETRO[0],width=5)
            draw.line((left+100,38,left+100,162),fill=RETRO[5],width=4);draw.line((left+3,100,left+197,100),fill=RETRO[5],width=4)
        draw.rectangle((270,40,530,180),fill=(205,218,195),outline=RETRO[0],width=5)
        for y in (75,115,155):draw.line((275,y,525,y),fill=RETRO[1],width=3)
        for x,color in ((292,(205,63,58)),(330,(91,158,202)),(368,RETRO[2]),(406,(205,63,58)),(444,(91,158,202)),(482,RETRO[2])):
            draw.rectangle((x,50,x+22,73),fill=color,outline=RETRO[0],width=2)
        draw.rectangle((0,235,800,350),fill=(190,199,181))
        for x in range(-100,901,100):draw.line((400,235,x,350),fill=(137,151,137),width=2)
        for y in (270,310):draw.line((0,y,800,y),fill=(137,151,137),width=2)
        draw.polygon(((260,270),(620,270),(680,350),(200,350)),fill=(139,166,158),outline=RETRO[0])
        draw.line((270,285,610,285),fill=RETRO[5],width=5)
        # A complete ball sits on the lab table beneath the emerging partner.
        for end in ((385,165),(440,145),(495,165),(530,205),(350,205)):draw.line((440,235,*end),fill=RETRO[3],width=5)
        ball=(385,240,495,350)
        draw.ellipse(ball,fill=RETRO[7],outline=RETRO[0],width=6)
        draw.pieslice(ball,180,360,fill=(205,63,58),outline=RETRO[0],width=5)
        draw.rectangle((388,288,492,302),fill=RETRO[0])
        draw.ellipse((421,272,459,310),fill=RETRO[7],outline=RETRO[0],width=6)
        draw.ellipse((432,283,448,299),fill=RETRO[5],outline=RETRO[1],width=2)
        image=self._open(data,(250,220),trim=True,upscale=True)
        canvas.paste(image,(440-image.width//2,255-image.height),image)
        species=SPECIES[pokemon.species_id]
        trainer=" ".join(str(trainer_name).split())[:24] or "Trainer"
        draw.rounded_rectangle((20,350,780,440),12,fill=RETRO[5],outline=RETRO[0],width=5)
        draw.text((45,370),f"@{trainer} received {species.name}!",fill=RETRO[0],font=ImageFont.load_default(size=26))
        draw.text((45,407),f"Lv.{pokemon.level}",fill=RETRO[1],font=ImageFont.load_default(size=18))
        self._gender_mark(draw,(105,404),pokemon.gender,RETRO[1])
        draw.text((145,407),"Your journey begins.",fill=RETRO[1],font=ImageFont.load_default(size=18))
        return self._save(canvas)

    def _encounter_sync(self,species_id,data,level=5,gender="unknown",backdrop=0):
        canvas=Image.new("RGB",(800,450),RETRO[4]);draw=ImageDraw.Draw(canvas)
        self._encounter_backdrop(draw,backdrop)
        pokemon=self._open(data,(250,220),trim=True,upscale=True)
        canvas.paste(pokemon,(440-pokemon.width//2,300-pokemon.height),pokemon)
        maximum=((2*SPECIES[species_id].hp)*level)//100+level+10
        self._status_box(draw,(30,28),SPECIES[species_id].name,level,maximum,maximum,"",gender)
        draw.rounded_rectangle((20,360,780,440),12,fill=RETRO[5],outline=RETRO[0],width=5)
        draw.text((45,385),f"A wild {SPECIES[species_id].name} appeared!",fill=RETRO[0],font=ImageFont.load_default(size=24))
        return self._save(canvas)

    @staticmethod
    def _encounter_backdrop(draw,index):
        sky,low,far,near,kind=ENCOUNTER_BACKDROPS[int(index)%len(ENCOUNTER_BACKDROPS)]
        for y in range(330):
            ratio=y/329
            color=tuple(round(a+(b-a)*ratio) for a,b in zip(sky,low))
            draw.line((0,y,800,y),fill=color)
        if kind in {"mountains","canyon","night"}:
            draw.polygon(((0,275),(120,150),(230,260),(345,125),(500,270),(650,165),(800,260),(800,330),(0,330)),fill=far)
        elif kind in {"forest","grove"}:
            for x in range(-30,850,70):draw.ellipse((x,150,x+105,335),fill=far)
        elif kind in {"water","coast"}:
            draw.rectangle((0,245,800,330),fill=far)
            for y in range(260,325,18):draw.line((0,y,800,y),fill=low,width=3)
        else:
            draw.ellipse((-180,205,470,430),fill=far);draw.ellipse((300,190,980,430),fill=far)
        draw.rectangle((0,300,800,360),fill=near)
        draw.ellipse((255,267,630,352),fill=RETRO[2],outline=RETRO[0],width=4)
        draw.ellipse((275,280,612,341),fill=(183,205,112),outline=RETRO[1],width=2)
        for x in range(280,615,28):draw.line((x,289,x+7,275),fill=RETRO[0],width=3)

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
        self._status_box(draw,(40,35),wild.name,battle.wild_level,battle.wild_hp,battle.wild_max_hp,battle.wild_status,battle.wild_gender)
        self._status_box(draw,(430,270),player.name,battle.player.level,battle.player_hp,battle.max_hp(battle.player),battle.player_status,battle.player.gender)
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
    def _status_box(draw,origin,name,level,hp,maximum,status,gender="unknown"):
        x, y = origin
        draw.rounded_rectangle((x, y, x + 320, y + 88), 12, fill=RETRO[7], outline=RETRO[0], width=4)
        symbol={"female":"♀","male":"♂","genderless":"—"}.get(gender,"?")
        draw.text((x+14,y+10),f"{name}  {symbol}  Lv.{level}",fill=RETRO[0],font=ImageFont.load_default(size=18))
        draw.rectangle((x + 70, y + 45, x + 295, y + 62), outline=RETRO[0], width=2)
        width = int(221 * max(0, hp) / max(1, maximum))
        draw.rectangle((x + 72, y + 47, x + 72 + width, y + 60), fill=RETRO[2])
        label = f"HP {hp}/{maximum}"
        if status:
            label += f" · {status.upper()}"
        draw.text((x + 14, y + 67), label, fill=RETRO[0], font=ImageFont.load_default(size=14))
