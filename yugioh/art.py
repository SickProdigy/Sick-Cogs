import asyncio,io,os
from pathlib import Path
from urllib.parse import urlparse
from PIL import Image,ImageDraw,ImageFont,UnidentifiedImageError
MAX_IMAGE_BYTES=5*1024*1024;MAX_CACHE_BYTES=128*1024*1024;MAX_CACHE_FILES=192;HAND_PAGE_SIZE=6
ASSETS=Path(__file__).parent/"assets"
class ArtError(RuntimeError):pass
class CardArtCache:
    def __init__(self,root,session):self.root=Path(root);self.session=session;self._locks={}
    async def get(self,card):
        self.root.mkdir(parents=True,exist_ok=True);path=self.root/f"{card.passcode}.jpg"
        if path.is_file():await asyncio.to_thread(os.utime,path,None);return path
        lock=self._locks.setdefault(card.passcode,asyncio.Lock())
        async with lock:
            if path.is_file():return path
            url=f"https://images.ygoprodeck.com/images/cards_small/{card.passcode}.jpg";parsed=urlparse(url)
            if parsed.scheme!="https" or parsed.hostname!="images.ygoprodeck.com":raise ArtError("Untrusted card image URL.")
            async with self.session.get(url,headers={"User-Agent":"Sick-Cogs-YuGiOh/0.1","Accept":"image/*"}) as response:
                if response.status!=200:raise ArtError(f"Card art returned HTTP {response.status}.")
                payload=await response.read()
            await asyncio.to_thread(self._write,path,payload);await asyncio.to_thread(self._trim);return path
    @staticmethod
    def _write(path,payload):
        if len(payload)>MAX_IMAGE_BYTES:raise ArtError("Card image exceeded its size limit.")
        try:
            with Image.open(io.BytesIO(payload)) as image:
                image.verify()
                if image.width>4096 or image.height>4096 or image.format not in {"JPEG","PNG","WEBP"}:raise ArtError("Unsupported card image.")
        except (OSError,UnidentifiedImageError) as exc:raise ArtError("Card image could not be decoded.") from exc
        temp=path.with_suffix(".tmp");temp.write_bytes(payload);os.replace(temp,path)
    def _trim(self):
        files=sorted(self.root.glob("*.jpg"),key=lambda p:p.stat().st_mtime);total=sum(p.stat().st_size for p in files)
        while files and(len(files)>MAX_CACHE_FILES or total>MAX_CACHE_BYTES):old=files.pop(0);total-=old.stat().st_size;old.unlink(missing_ok=True)
def font(size):
    p=Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
    return ImageFont.truetype(p,size) if p.is_file() else ImageFont.load_default()
def render_hand(cards,paths,page=0):
    visible=cards[page*HAND_PAGE_SIZE:(page+1)*HAND_PAGE_SIZE];visible_paths=paths[page*HAND_PAGE_SIZE:(page+1)*HAND_PAGE_SIZE]
    if not visible:raise ArtError("That hand page is empty.")
    with Image.open(ASSETS/"hand_tray.png") as source:canvas=source.convert("RGB").resize((1200,800))
    draw=ImageDraw.Draw(canvas);w,h=190,278;step=165;start=(1200-(step*(len(visible)-1)+w))//2
    for i,(card,path) in enumerate(zip(visible,visible_paths)):
        x=start+i*step;y=265-abs((len(visible)-1)/2-i)*18;panel=Image.new("RGB",(w,h),(35,38,48))
        if path:
            try:
                with Image.open(path) as im:im=im.convert("RGB");im.thumbnail((w,h));panel.paste(im,((w-im.width)//2,(h-im.height)//2))
            except OSError:path=None
        if not path:ImageDraw.Draw(panel).multiline_text((10,45),f"{card.name}\n\n{card.kind}\nATK {card.attack}\nDEF {card.defense}",font=font(15),fill="white",spacing=5)
        canvas.paste(panel,(x,int(y)));draw.ellipse((x+5,int(y)+5,x+43,int(y)+43),fill=(15,17,24),outline=(230,180,80),width=3);draw.text((x+24,int(y)+24),str(page*HAND_PAGE_SIZE+i+1),font=font(19),fill="white",anchor="mm")
    out=io.BytesIO();canvas.save(out,"PNG",optimize=True);out.seek(0);return out
def render_field(game):
    with Image.open(ASSETS/"duel_table.png") as source:canvas=source.convert("RGB").resize((1200,675))
    draw=ImageDraw.Draw(canvas);back=Image.open(ASSETS/"card_back.png").convert("RGB").resize((58,84))
    for side,user in enumerate(game.order):
        p=game.players[user];base_y=118 if side==0 else 474
        for i,m in enumerate(p.monsters):
            x=365+i*96
            if not m:continue
            if m.face_up:
                c=game.card(m.uid);draw.rounded_rectangle((x,base_y,x+70,base_y+94),6,fill=(42,45,53),outline=(215,175,85),width=2);draw.multiline_text((x+5,base_y+7),f"{c.name[:10]}\n{c.attack}/{c.defense}",font=font(11),fill="white")
            else:canvas.paste(back.resize((70,94)),(x,base_y))
        spell_y=base_y+101 if side==0 else base_y-91
        for i,s in enumerate(p.spells):
            if s:canvas.paste(back.resize((58,84)),(371+i*96,spell_y))
        draw.text((20,20 if side==0 else 625),f"P{side+1}  LP {p.life}  Hand {len(p.hand)}  Deck {len(p.deck)}  GY {len(p.graveyard)}",font=font(20),fill="white")
    draw.text((600,337),f"Turn {game.turn} - {game.phase.upper()}",font=font(22),fill=(255,225,150),anchor="mm")
    out=io.BytesIO();canvas.save(out,"PNG",optimize=True);out.seek(0);back.close();return out
