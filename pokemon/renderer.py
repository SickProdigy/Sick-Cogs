"""Bounded Pokémon encounter and retro battle rendering."""

import asyncio
import io
import textwrap
from pathlib import Path
from urllib.parse import urlparse

import aiohttp
from PIL import Image, ImageDraw, ImageFont

from .data import MOVES, SPECIES, sprite
from .models import OwnedPokemon,pokemon_max_hp

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

    async def encounter(self,species_id:int,level:int=5,gender:str="unknown",backdrop:int=0,expired:bool=False):
        data=await self.get_sprite(species_id)
        try:
            return await self._render(self._encounter_sync,species_id,data,level,gender,backdrop,expired)
        except (OSError, ValueError) as exc:
            raise RenderError("Encounter rendering failed.") from exc

    async def starter(self,pokemon,trainer_name):
        data=await self.get_sprite(pokemon.species_id,shiny=pokemon.shiny)
        try:
            return await self._render(self._starter_sync,pokemon,data,trainer_name)
        except (OSError,ValueError) as exc:
            raise RenderError("Starter reveal rendering failed.") from exc

    async def starter_choice(self,species_id):
        data=await self.get_sprite(species_id)
        try:
            return await self._render(self._starter_sync,None,data,"",species_id)
        except (OSError,ValueError) as exc:
            raise RenderError("Starter selection rendering failed.") from exc

    async def party_card(self,pokemon,trainer_name):
        data=await asyncio.gather(*(self.get_sprite(item.species_id,shiny=item.shiny) for item in pokemon))
        try:return await self._render(self._party_card_sync,pokemon,data,trainer_name)
        except (OSError,ValueError) as exc:raise RenderError("Party card rendering failed.") from exc

    async def collection_card(self,pokemon,page,pages,total,trainer_name):
        data=await asyncio.gather(*(self.get_sprite(item.species_id,shiny=item.shiny) for item in pokemon))
        try:return await self._render(self._collection_card_sync,pokemon,data,page,pages,total,trainer_name)
        except (OSError,ValueError) as exc:raise RenderError("Collection card rendering failed.") from exc

    async def pokedex_registration(self,pokemon,trainer_name):
        data=await self.get_sprite(pokemon.species_id,shiny=pokemon.shiny)
        try:return await self._render(self._pokedex_registration_sync,pokemon,data,trainer_name)
        except (OSError,ValueError) as exc:raise RenderError("Pokédex registration rendering failed.") from exc

    async def trainer_card(self,user_name,conf,style="retro"):
        owned={item["instance_id"]:item for item in conf.get("collection",[])}
        lead=owned.get(conf.get("party",[None])[0]) if conf.get("party") else None
        pokemon=OwnedPokemon.from_raw(lead) if lead else None
        data=await self.get_sprite(pokemon.species_id,shiny=pokemon.shiny) if pokemon else None
        try:return await self._render(self._trainer_card_sync,user_name,conf,pokemon,data,style)
        except (OSError,ValueError) as exc:raise RenderError("Trainer card rendering failed.") from exc

    async def progression(self,pokemon,evolved_from=None,move_key=None,pending=False):
        current=await self.get_sprite(pokemon.species_id,shiny=pokemon.shiny)
        previous=await self.get_sprite(evolved_from,shiny=pokemon.shiny) if evolved_from else None
        try:return await self._render(self._progression_sync,pokemon,current,previous,evolved_from,move_key,pending)
        except (OSError,ValueError) as exc:raise RenderError("Progression rendering failed.") from exc

    async def battle(self, battle):
        if battle.state!="active":
            species_id=battle.player.species_id if battle.state=="won" else battle.wild_species_id
            result_sprite=await self.get_sprite(species_id,shiny=battle.player.shiny if battle.state=="won" else False)
            try:return await self._render(self._battle_result_sync,battle,result_sprite)
            except (OSError,ValueError) as exc:raise RenderError("Battle result rendering failed.") from exc
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
    def _dialogue(draw,text,origin,width=88,max_lines=2,size=16):
        lines=textwrap.wrap(" ".join(str(text).split()),width=width,break_long_words=False,break_on_hyphens=False) or [""]
        font=ImageFont.load_default(size=size);x,y=origin
        for index,line in enumerate(lines[:max_lines]):draw.text((x,y+index*(size+3)),line,fill=RETRO[0],font=font)

    @staticmethod
    def _gender_mark(draw,origin,gender,fill=RETRO[0]):
        x,y=origin
        if gender=="male":
            draw.ellipse((x,y+5,x+7,y+12),outline=fill,width=1);draw.line((x+6,y+6,x+12,y),fill=fill,width=1)
            draw.line((x+9,y,x+12,y,x+12,y+3),fill=fill,width=1)
        elif gender=="female":
            draw.ellipse((x,y+3,x+7,y+10),outline=fill,width=1);draw.line((x+3,y+10,x+3,y+17),fill=fill,width=1);draw.line((x,y+14,x+7,y+14),fill=fill,width=1)
        else:draw.line((x,y+9,x+9,y+9),fill=fill,width=1)

    @staticmethod
    def _pokeball(draw,center,radius):
        cx,cy=center;box=(cx-radius,cy-radius,cx+radius,cy+radius)
        draw.ellipse(box,fill=RETRO[7],outline=RETRO[0],width=5)
        draw.pieslice(box,180,360,fill=(205,63,58),outline=RETRO[0],width=4)
        draw.rectangle((cx-radius+3,cy-5,cx+radius-3,cy+6),fill=RETRO[0])
        button=max(7,radius//3)
        draw.ellipse((cx-button,cy-button,cx+button,cy+button),fill=RETRO[7],outline=RETRO[0],width=4)
        inner=max(3,button//2)
        draw.ellipse((cx-inner,cy-inner,cx+inner,cy+inner),fill=RETRO[5],outline=RETRO[1],width=2)

    def _starter_sync(self,pokemon,data,trainer_name,choice_species_id=None):
        canvas=Image.new("RGB",(800,450),(224,227,222));draw=ImageDraw.Draw(canvas)
        # Original retro research lab inspired by the early games: stocked shelves and woodwork.
        draw.rectangle((0,0,800,245),fill=(218,222,216))
        for y in range(24,246,28):draw.line((0,y,800,y),fill=(178,185,181),width=2)
        # Computer workstation on the left.
        draw.rectangle((24,145,245,226),fill=(119,124,126),outline=RETRO[0],width=5)
        draw.rectangle((42,35,222,145),fill=(92,98,101),outline=RETRO[0],width=5)
        draw.rectangle((57,49,207,125),fill=(126,183,177),outline=RETRO[0],width=4)
        draw.rectangle((76,162,207,186),fill=(179,184,181),outline=RETRO[0],width=3)
        for x in range(85,199,18):draw.line((x,168,x+9,168),fill=RETRO[1],width=3)
        draw.rectangle((48,193,221,215),fill=(78,84,87),outline=RETRO[0],width=3)
        for x,color in ((61,(151,157,154)),(91,(124,143,145)),(121,(167,153,117)),(151,(151,157,154))):draw.ellipse((x,197,x+13,210),fill=color,outline=RETRO[0],width=2)
        # Stocked research shelving on the right.
        left,right=555,775
        draw.rectangle((left,20,right,235),fill=(112,118,122),outline=RETRO[0],width=5)
        for shelf_y in (65,112,159,206):draw.rectangle((left+7,shelf_y,right-7,shelf_y+8),fill=(75,82,86),outline=RETRO[0],width=2)
        for row_y in (32,79,126,173):
            for column,color in enumerate(((165,168,163),(125,151,158),(172,157,117),(145,148,145))):
                x=left+14+column*47;draw.rectangle((x,row_y,x+29,row_y+29),fill=color,outline=RETRO[0],width=2)
        # Muted region survey and evolution research displays.
        panel=(213,222,216);ink=(74,105,108);accent=(111,145,140)
        draw.rectangle((275,40,390,165),fill=panel,outline=RETRO[0],width=4)
        draw.text((287,48),"REGION SURVEY",fill=ink,font=ImageFont.load_default(size=11))
        draw.polygon(((297,83),(315,69),(335,77),(349,67),(371,82),(361,98),(371,111),(351,126),(333,119),(317,137),(295,124),(304,105),(290,96)),fill=(157,178,164),outline=ink)
        draw.line((301,111,321,101,337,106,354,90,367,93),fill=(224,227,218),width=3)
        for x,y in ((304,91),(322,119),(347,82),(356,111)):draw.ellipse((x-3,y-3,x+3,y+3),fill=(173,151,99),outline=ink)
        draw.rectangle((405,40,520,165),fill=panel,outline=RETRO[0],width=4)
        draw.text((417,48),"EVOLUTION",fill=ink,font=ImageFont.load_default(size=11))
        draw.line((425,105,497,105),fill=accent,width=3)
        for x in (449,481):draw.polygon(((x-5,101),(x,105),(x-5,109)),fill=accent)
        draw.ellipse((416,94,434,112),fill=(139,162,158),outline=ink,width=2)
        draw.ellipse((443,89,466,113),fill=(126,153,151),outline=ink,width=2)
        draw.polygon(((447,91),(451,80),(456,90),(462,81),(464,94)),fill=(126,153,151),outline=ink)
        draw.ellipse((476,84,505,113),fill=(112,142,145),outline=ink,width=2)
        draw.polygon(((480,87),(484,73),(491,84),(499,74),(502,90)),fill=(112,142,145),outline=ink)
        draw.line((420,137,506,137),fill=(139,151,148),width=2)
        draw.line((432,145,493,145),fill=(139,151,148),width=2)
        draw.rectangle((0,235,800,350),fill=(222,226,223))
        for x in range(-160,961,80):draw.line((400,235,x,350),fill=(184,191,188),width=2)
        for y in (270,310):draw.line((0,y,800,y),fill=(184,191,188),width=2)
        # A broad wooden presentation table holds the two unchosen starter balls.
        draw.polygon(((165,258),(635,258),(700,350),(100,350)),fill=(132,82,48),outline=RETRO[0])
        draw.polygon(((180,245),(620,245),(650,280),(150,280)),fill=(189,125,67),outline=RETRO[0])
        draw.line((180,258,620,258),fill=(231,170,94),width=5)
        self._pokeball(draw,(255,276),34)
        self._pokeball(draw,(545,276),34)
        image=self._open(data,(250,220),trim=True,upscale=True)
        canvas.paste(image,(400-image.width//2,310-image.height),image)
        species=SPECIES[choice_species_id or pokemon.species_id]
        draw.rounded_rectangle((20,350,780,440),12,fill=RETRO[5],outline=RETRO[0],width=5)
        if choice_species_id is not None:
            draw.text((45,370),f"Would you like {species.name}?",fill=RETRO[0],font=ImageFont.load_default(size=25))
            draw.text((45,407),"Use Previous and Next, then choose your partner.",fill=RETRO[1],font=ImageFont.load_default(size=18))
        else:
            trainer=" ".join(str(trainer_name).split())[:24] or "Trainer"
            draw.text((45,370),f"{trainer} received {species.name}!",fill=RETRO[0],font=ImageFont.load_default(size=26))
            draw.text((45,407),f"Lv.{pokemon.level}",fill=RETRO[1],font=ImageFont.load_default(size=18))
            self._gender_mark(draw,(84,410),pokemon.gender,RETRO[1])
            draw.text((104,407),"Your journey begins.",fill=RETRO[1],font=ImageFont.load_default(size=18))
        return self._save(canvas)

    def _party_card_sync(self,pokemon,data,trainer_name):
        canvas=Image.new("RGB",(1200,360),(236,205,105));draw=ImageDraw.Draw(canvas)
        for y in range(360):
            ratio=y/359;draw.line((0,y,1200,y),fill=(int(246-66*ratio),int(220-74*ratio),int(132-63*ratio)))
        draw.rounded_rectangle((20,18,1180,342),20,fill=(255,244,194),outline=(92,66,25),width=5)
        trainer=" ".join(str(trainer_name).split())[:24] or "Trainer"
        draw.text((46,34),f"{trainer}'s Party",fill=(92,66,25),font=ImageFont.load_default(size=28))
        self._pokeball(draw,(1138,56),26)
        slots=list(zip(pokemon,data))
        for index in range(6):
            left=38+188*index;cx=left+86;top=78
            draw.rounded_rectangle((left,top,left+172,top+238),16,fill=(242,224,157),outline=(126,91,34),width=3)
            draw.ellipse((cx-66,top+137,cx+66,top+181),fill=(190,151,62),outline=(104,75,28),width=3)
            if index>=len(slots):
                draw.text((cx-32,top+99),"EMPTY",fill=(143,118,67),font=ImageFont.load_default(size=16));continue
            item,raw=slots[index];image=self._open(raw,(142,132),trim=True,upscale=True)
            canvas.paste(image,(cx-image.width//2,top+166-image.height),image)
            species=SPECIES[item.species_id];maximum=pokemon_max_hp(item);current=maximum if item.current_hp is None else item.current_hp
            name=item.nickname or species.name;draw.text((left+10,top+10),f"{index+1}. {name}",fill=(61,48,25),font=ImageFont.load_default(size=16))
            if item.shiny:draw.text((left+10,top+34),"SHINY",fill=(126,91,34),font=ImageFont.load_default(size=12))
            draw.text((left+10,top+190),f"Lv.{item.level}",fill=(82,62,29),font=ImageFont.load_default(size=15))
            draw.text((left+10,top+211),f"HP {current}/{maximum}",fill=(82,62,29),font=ImageFont.load_default(size=14))
            self._gender_mark(draw,(left+145,top+207),item.gender,(82,62,29))
        return self._save(canvas)

    def _collection_card_sync(self,pokemon,data,page,pages,total,trainer_name):
        canvas=Image.new("RGB",(900,720),(225,217,177));draw=ImageDraw.Draw(canvas)
        for y in range(720):
            ratio=y/719;draw.line((0,y,900,y),fill=(int(240-36*ratio),int(230-40*ratio),int(181-34*ratio)))
        draw.rounded_rectangle((20,18,880,702),18,fill=(245,239,207),outline=(54,83,70),width=5)
        trainer=" ".join(str(trainer_name).split())[:24] or "Trainer"
        draw.text((44,34),f"{trainer}'s Collection · {total} POKEMON",fill=(42,70,58),font=ImageFont.load_default(size=28))
        draw.text((735,43),f"PAGE {page}/{pages}",fill=(65,91,78),font=ImageFont.load_default(size=16))
        for index,(item,raw) in enumerate(zip(pokemon,data)):
            col=index%3;row=index//3;left=43+280*col;top=82+198*row;cx=left+127
            draw.rounded_rectangle((left,top,left+254,top+178),14,fill=(221,229,200),outline=(78,105,88),width=3)
            draw.ellipse((cx-69,top+102,cx+69,top+145),fill=(169,188,132),outline=(76,104,76),width=2)
            image=self._open(raw,(142,120),trim=True,upscale=True);canvas.paste(image,(cx-image.width//2,top+126-image.height),image)
            species=SPECIES[item.species_id];name=item.nickname or species.name;number=(page-1)*9+index+1
            shiny=" · SHINY" if item.shiny else ""
            draw.text((left+12,top+149),f"{number}. {name}",fill=(42,70,58),font=ImageFont.load_default(size=17))
            level_text=f"Lv.{item.level}";level_font=ImageFont.load_default(size=14);level_x=left+180
            draw.text((level_x,top+151),level_text,fill=(65,91,78),font=level_font)
            mark_x=min(left+239,level_x+int(draw.textlength(level_text,font=level_font))+6)
            self._gender_mark(draw,(mark_x,top+150),item.gender,(65,91,78))
            if shiny:draw.text((left+190,top+12),"SHINY",fill=(126,91,34),font=ImageFont.load_default(size=11))
        return self._save(canvas)

    @staticmethod
    def battle_result_text(battle):
        wild=SPECIES[battle.wild_species_id];player=SPECIES[battle.player.species_id]
        trainer=" ".join(str(battle.trainer_name or "Trainer").split())[:24] or "Trainer"
        if battle.state=="caught":return f"Gotcha! {wild.name} was caught by {trainer}!"
        if battle.state=="won":return f"{trainer} defeated {wild.name}!"
        if battle.state=="lost":return f"{wild.name} escaped from {trainer}! Your party has no conscious Pokemon. Go to a Pokemon Center to heal."
        return f"{wild.name} escaped from {trainer}!"

    def _battle_result_sync(self,battle,data):
        wild=SPECIES[battle.wild_species_id];message=self.battle_result_text(battle)
        if battle.state in {"lost","ran"}:
            canvas=Image.new("RGB",(800,450),RETRO[5]);draw=ImageDraw.Draw(canvas)
            for y in range(0,360,12):draw.line((0,y,800,y),fill=RETRO[4])
            draw.ellipse((465,190,750,255),fill=RETRO[2],outline=RETRO[0],width=4)
            draw.ellipse((55,300,390,390),fill=RETRO[2],outline=RETRO[0],width=4)
            image=self._retro(self._open(data,(210,185),trim=True,upscale=True))
            canvas.paste(image,(595-image.width//2,220-image.height),image)
            self._status_box(draw,(40,35),wild.name,battle.wild_level,battle.wild_hp,battle.wild_max_hp,battle.wild_status,battle.wild_gender)
            if battle.state=="lost":
                draw.rounded_rectangle((430,275,750,345),12,fill=RETRO[7],outline=RETRO[0],width=4)
                draw.text((474,298),"ESCAPED",fill=RETRO[0],font=ImageFont.load_default(size=24))
            draw.rectangle((0,390,800,450),fill=RETRO[5],outline=RETRO[0],width=5)
            self._dialogue(draw,message,(20,400))
            return self._save(canvas)

        canvas=Image.new("RGB",(800,450),(229,214,145));draw=ImageDraw.Draw(canvas)
        for y in range(450):
            ratio=y/449;draw.line((0,y,800,y),fill=(int(244-49*ratio),int(232-58*ratio),int(174-69*ratio)))
        draw.rounded_rectangle((24,20,776,430),22,fill=(250,243,205),outline=RETRO[0],width=5)
        if battle.state=="caught":
            draw.ellipse((245,205,555,310),fill=(182,168,89),outline=RETRO[0],width=4)
            image=self._open(data,(230,190),trim=True,upscale=True);canvas.paste(image,(400-image.width//2,240-image.height),image)
            self._pokeball(draw,(400,290),43)
        else:
            heading="Victory!"
            draw.text((400-int(draw.textlength(heading,font=ImageFont.load_default(size=34)))//2,42),heading,fill=RETRO[0],font=ImageFont.load_default(size=34))
            draw.ellipse((235,225,565,340),fill=(182,168,89),outline=RETRO[0],width=4)
            image=self._open(data,(250,215),trim=True,upscale=True);canvas.paste(image,(400-image.width//2,300-image.height),image)
        draw.rounded_rectangle((48,350,752,410),12,fill=RETRO[5],outline=RETRO[0],width=4)
        self._dialogue(draw,message,(70,360),width=76,size=16)
        if battle.state=="caught":
            font=ImageFont.load_default(size=14);level=f"Lv. {battle.wild_level}";draw.text((70,386),level,fill=RETRO[1],font=font)
            mark_x=70+int(draw.textlength(level,font=font))+8;self._gender_mark(draw,(mark_x,384),battle.wild_gender,RETRO[1])
        return self._save(canvas)

    def _encounter_sync(self,species_id,data,level=5,gender="unknown",backdrop=0,expired=False):
        canvas=Image.new("RGB",(800,450),RETRO[4]);draw=ImageDraw.Draw(canvas)
        self._encounter_backdrop(draw,backdrop)
        pokemon=self._open(data,(250,220),trim=True,upscale=True)
        canvas.paste(pokemon,(440-pokemon.width//2,300-pokemon.height),pokemon)
        maximum=((2*SPECIES[species_id].hp)*level)//100+level+10
        self._status_box(draw,(30,28),SPECIES[species_id].name,level,maximum,maximum,"",gender)
        if expired:
            canvas=Image.blend(canvas,Image.new("RGB",canvas.size,(105,105,105)),0.42);draw=ImageDraw.Draw(canvas)
        draw.rounded_rectangle((20,360,780,440),12,fill=RETRO[5],outline=RETRO[0],width=5)
        message=f"The wild {SPECIES[species_id].name} got away!" if expired else f"A wild {SPECIES[species_id].name} appeared!"
        draw.text((45,385),message,fill=RETRO[0],font=ImageFont.load_default(size=24))
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

    def _pokedex_registration_sync(self,pokemon,data,trainer_name):
        species=SPECIES[pokemon.species_id];trainer=" ".join(str(trainer_name or "Trainer").split())[:24] or "Trainer";canvas=Image.new("RGB",(800,450),RETRO[5]);draw=ImageDraw.Draw(canvas)
        draw.rectangle((28,25,772,340),fill=RETRO[7],outline=RETRO[0],width=6)
        draw.rectangle((48,48,330,315),fill=RETRO[4],outline=RETRO[1],width=4)
        image=self._retro(self._open(data,(245,225),trim=True,upscale=True));canvas.paste(image,(189-image.width//2,285-image.height),image)
        draw.text((370,62),"POKEDEX REGISTRATION",fill=RETRO[0],font=ImageFont.load_default(size=27))
        draw.text((370,120),f"No. {species.id:03d}  {species.name}",fill=RETRO[1],font=ImageFont.load_default(size=25))
        draw.text((370,175),"New Pokemon data",fill=RETRO[0],font=ImageFont.load_default(size=22))
        draw.text((370,205),"was added to the Pokedex!",fill=RETRO[0],font=ImageFont.load_default(size=22))
        draw.rectangle((0,350,800,450),fill=RETRO[5],outline=RETRO[0],width=5);self._dialogue(draw,f"{species.name} was registered in {trainer}'s Pokedex.",(25,382),width=84,size=19)
        return self._save(canvas)

    def _trainer_card_sync(self,user_name,conf,pokemon,data,style):
        gold=style=="gold";paper=(247,225,153) if gold else RETRO[4];panel=(255,241,190) if gold else RETRO[5];ink=(91,60,27) if gold else RETRO[0]
        canvas=Image.new("RGB",(800,450),paper);draw=ImageDraw.Draw(canvas)
        draw.rounded_rectangle((24,24,776,426),22,fill=panel,outline=ink,width=7)
        draw.text((55,48),"TRAINER CARD",fill=ink,font=ImageFont.load_default(size=32));draw.text((55,100),user_name[:28],fill=ink,font=ImageFont.load_default(size=27))
        draw.text((55,158),f"BADGES  {len(conf.get('badges',[]))}/8",fill=ink,font=ImageFont.load_default(size=20))
        badge_keys=("boulder","cascade","thunder","rainbow","soul","marsh","volcano","earth")
        for index,key in enumerate(badge_keys):
            x=62+index*65;color=(214,165,52) if key in conf.get("badges",[]) else (170,174,151)
            draw.ellipse((x,195,x+40,235),fill=color,outline=ink,width=3)
        draw.text((55,270),f"POKEDEX  {len(conf.get('pokedex_caught',[]))} caught / {len(conf.get('pokedex_seen',[]))} seen",fill=ink,font=ImageFont.load_default(size=19))
        draw.text((55,308),f"COLLECTION  {len(conf.get('collection',[]))}    PARTY  {len(conf.get('party',[]))}/6",fill=ink,font=ImageFont.load_default(size=19))
        if pokemon and data:
            image=self._retro(self._open(data,(230,220),trim=True,upscale=True));canvas.paste(image,(650-image.width//2,330-image.height),image)
            draw.text((530,350),f"PARTNER  {SPECIES[pokemon.species_id].name}",fill=ink,font=ImageFont.load_default(size=18));draw.text((530,380),f"Lv. {pokemon.level}",fill=ink,font=ImageFont.load_default(size=18))
        else:draw.text((530,350),"NO PARTNER",fill=ink,font=ImageFont.load_default(size=18))
        return self._save(canvas)

    def _progression_sync(self,pokemon,current_data,previous_data,evolved_from,move_key,pending):
        canvas=Image.new("RGB",(800,450),RETRO[4]);draw=ImageDraw.Draw(canvas)
        for y in range(0,360,12):draw.line((0,y,800,y),fill=RETRO[5])
        if evolved_from:
            old=SPECIES[evolved_from];new=SPECIES[pokemon.species_id]
            draw.text((400-int(draw.textlength("EVOLUTION!",font=ImageFont.load_default(size=34)))//2,28),"EVOLUTION!",fill=RETRO[0],font=ImageFont.load_default(size=34))
            left=self._retro(self._open(previous_data,(230,210),trim=True,upscale=True));right=self._retro(self._open(current_data,(250,225),trim=True,upscale=True))
            canvas.paste(left,(205-left.width//2,290-left.height),left);canvas.paste(right,(595-right.width//2,290-right.height),right)
            draw.line((340,205,455,205),fill=RETRO[0],width=7);draw.polygon(((455,205),(430,188),(430,222)),fill=RETRO[0])
            message=f"What? {old.name} evolved into {new.name}!"
        else:
            species=SPECIES[pokemon.species_id];image=self._retro(self._open(current_data,(245,220),trim=True,upscale=True));canvas.paste(image,(205-image.width//2,300-image.height),image)
            draw.rounded_rectangle((355,40,755,315),14,fill=RETRO[7],outline=RETRO[0],width=5)
            draw.text((380,60),f"{species.name}'s moves",fill=RETRO[0],font=ImageFont.load_default(size=24))
            for index,key in enumerate(pokemon.moves):draw.text((390,110+38*index),f"{index+1}. {MOVES[key].name}  PP {pokemon.move_pp.get(key,MOVES[key].pp)}",fill=RETRO[1],font=ImageFont.load_default(size=19))
            move=MOVES[move_key].name if move_key else "a new move"
            message=f"{species.name} is trying to learn {move}!" if pending else f"{species.name} learned {move}!"
        draw.rectangle((0,360,800,450),fill=RETRO[5],outline=RETRO[0],width=5);self._dialogue(draw,message,(25,382),width=84,size=19)
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
        self._status_box(draw,(40,35),wild.name,battle.wild_level,battle.wild_hp,battle.wild_max_hp,battle.wild_status,battle.wild_gender)
        self._status_box(draw,(430,270),player.name,battle.player.level,battle.player_hp,battle.max_hp(battle.player),battle.player_status,battle.player.gender)
        draw.rectangle((0, 390, 800, 450), fill=RETRO[5], outline=RETRO[0], width=5)
        text = battle.result or battle.last_action or f"What will {player.name} do?"
        self._dialogue(draw,text,(20,400))
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
        font=ImageFont.load_default(size=18);label_x=x+14;label_y=y+10
        draw.text((label_x,label_y),name,fill=RETRO[0],font=font)
        mark_x=label_x+int(draw.textlength(name,font=font))+7
        BattleRenderer._gender_mark(draw,(mark_x,y+13),gender,RETRO[0])
        draw.text((mark_x+20,label_y),f"Lv.{level}",fill=RETRO[0],font=font)
        draw.rectangle((x + 70, y + 45, x + 295, y + 62), outline=RETRO[0], width=2)
        width = int(221 * max(0, hp) / max(1, maximum))
        draw.rectangle((x + 72, y + 47, x + 72 + width, y + 60), fill=RETRO[2])
        label = f"HP {hp}/{maximum}"
        if status:
            label += f" · {status.upper()}"
        draw.text((x + 14, y + 67), label, fill=RETRO[0], font=ImageFont.load_default(size=14))
