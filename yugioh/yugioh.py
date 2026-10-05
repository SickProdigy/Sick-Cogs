import asyncio,logging,math,time
import aiohttp,discord
from redbot.core import Config,commands
from redbot.core.data_manager import cog_data_path
from .art import ArtError,CardArtCache,HAND_PAGE_SIZE,render_field,render_hand
from .cards import CARDS,SUPPORTED_MECHANICS
from .engine import Game,GameError
from .views import ChallengeView,GameView,HandPaginationView
log=logging.getLogger("red.sick-cogs.YuGiOh")
CONFIG_IDENTIFIER=813604927359
DEFAULTS={"schema":1,"next_game_id":1,"games":{}}
DUEL_TIMEOUT_SECONDS=604800
class YuGiOh(commands.Cog):
    """Persistent two-player Yu-Gi-Oh rules prototype."""
    __author__="SickProdigy";__version__="0.1.0"
    def __init__(self,bot):
        self.bot=bot;self.config=Config.get_conf(self,identifier=CONFIG_IDENTIFIER,force_registration=True);self.config.register_global(**DEFAULTS)
        self.games={};self.channels={};self.locks={};self.storage_lock=asyncio.Lock();self.session=None;self.art_cache=None;self.cleanup_task=None
    async def cog_load(self):
        self.session=aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20));self.art_cache=CardArtCache(cog_data_path(self)/"art_cache",self.session)
        for key,value in (await self.config.games()).items():
            try:g=Game.from_raw(value);g.message_id=int(value.get("message_id",0));self.games[g.game_id]=g;self.channels[g.game_id]=int(value.get("channel_id",0))
            except Exception:log.exception("Could not restore YuGiOh duel %s",key)
        await self.cleanup_expired()
        for g in self.games.values():
            if not g.finished:self.bot.add_view(GameView(self,g.game_id,g.state_version),message_id=g.message_id or None)
        self.cleanup_task=asyncio.create_task(self._cleanup_loop())
    def cog_unload(self):
        if self.cleanup_task:self.cleanup_task.cancel()
        if self.session and not self.session.closed:asyncio.create_task(self.session.close())
    def lock(self,gid):return self.locks.setdefault(gid,asyncio.Lock())
    async def create_game(self,a,b,channel):
        async with self.storage_lock:
            if any(not g.finished and(a in g.order or b in g.order) for g in self.games.values()):raise GameError("One duelist already has an active duel.")
            gid=await self.config.next_game_id();await self.config.next_game_id.set(gid+1);g=Game(gid,[a,b]);g.message_id=0;self.games[gid]=g;self.channels[gid]=channel;await self._save(g);return g
    async def _save(self,g):
        raw=g.to_raw();raw["channel_id"]=self.channels.get(g.game_id,0);raw["message_id"]=getattr(g,"message_id",0);games=await self.config.games();games[str(g.game_id)]=raw;await self.config.games.set(games)
    async def save(self,g):
        async with self.storage_lock:await self._save(g)
    def find(self,user):
        found=[g for g in self.games.values() if user in g.order and not g.finished]
        if not found:raise GameError("You do not have an active Yu-Gi-Oh duel.")
        return found[0]
    def game_embed(self,g):
        names={u:(self.bot.get_user(u).display_name if self.bot.get_user(u) else str(u)) for u in g.order};e=discord.Embed(title=f"Yu-Gi-Oh prototype - Duel {g.game_id}",color=discord.Color.dark_purple())
        e.description=(f"Winner: {names.get(g.winner,g.winner)} - {g.finished_reason}." if g.finished and g.winner else f"Duel ended - {g.finished_reason}." if g.finished else f"Turn {g.turn} - {g.phase.upper()} - Active: {names[g.active_user]}")
        for user in g.order:
            p=g.players[user];mon=[]
            for i,m in enumerate(p.monsters,1):
                if not m:continue
                mon.append(f"M{i}: face-down DEF" if not m.face_up else f"M{i}: {g.card(m.uid).name} {g.card(m.uid).attack}/{g.card(m.uid).defense} {m.position[:3].upper()}")
            e.add_field(name=f"{names[user]} - {p.life} LP",value=f"Hand {len(p.hand)} | Deck {len(p.deck)} | GY {len(p.graveyard)} | Banished {len(p.banished)}\n"+("\n".join(mon) or "No monsters"),inline=False)
        if g.pending_attack:e.add_field(name="Response window",value="The defending duelist may activate a supported Trap or resolve the attack.",inline=False)
        e.set_footer(text=f"State {g.state_version} - fixed legacy-style subset - private hands");return e
    async def refresh_message(self,g):
        try:
            channel=self.bot.get_channel(self.channels.get(g.game_id));message=await channel.fetch_message(g.message_id);image=await asyncio.to_thread(render_field,g);file=discord.File(image,filename="yugioh-field.png");embed=self.game_embed(g);embed.set_image(url="attachment://yugioh-field.png");await message.edit(embed=embed,attachments=[file],view=None if g.finished else GameView(self,g.game_id,g.state_version))
        except (discord.HTTPException,AttributeError,OSError):log.warning("Could not refresh duel %s",g.game_id,exc_info=True)
    async def send_hand(self,i,gid,page,editing=False):
        g=self.games.get(gid)
        if not g or i.user.id not in g.order:await i.followup.send("This private hand is unavailable.",ephemeral=True);return
        cards=g.hand(i.user.id);paths=[]
        for card in cards:
            try:paths.append(await self.art_cache.get(card))
            except (ArtError,aiohttp.ClientError,asyncio.TimeoutError,OSError):paths.append(None)
        pages=max(1,math.ceil(len(cards)/HAND_PAGE_SIZE));page=max(0,min(page,pages-1));visible=cards[page*HAND_PAGE_SIZE:(page+1)*HAND_PAGE_SIZE];start=page*HAND_PAGE_SIZE;view=HandPaginationView(self,gid,i.user.id,page,pages) if pages>1 else None;text="\n".join(f"{start+n}. {c.name} - {c.kind}"+(f", Level {c.level}, {c.attack}/{c.defense}" if c.monster else f": {c.text}") for n,c in enumerate(visible,1)) or "Your hand is empty."
        try:
            image=await asyncio.to_thread(render_hand,cards,paths,page);file=discord.File(image,filename=f"yugioh-hand-{gid}-{page+1}.png")
            if editing:await i.message.edit(content=text,attachments=[file],view=view)
            else:await i.followup.send(text,file=file,view=view,ephemeral=True)
        except (ArtError,OSError):
            if editing:await i.message.edit(content=text,attachments=[],view=view)
            else:await i.followup.send(text,view=view,ephemeral=True)
    async def act(self,i,gid,action,label):
        g=self.games.get(gid)
        if not g:await i.response.send_message("This duel is unavailable.",ephemeral=True);return
        async with self.lock(gid):
            try:action(g);await self.save(g)
            except (GameError,IndexError,ValueError) as e:await i.response.send_message(str(e),ephemeral=True);return
        await i.response.defer();await self.refresh_message(g)
    async def mutate(self,ctx,action):
        try:g=self.find(ctx.author.id)
        except GameError as e:await ctx.send(str(e));return
        async with self.lock(g.game_id):
            try:action(g);await self.save(g)
            except (GameError,IndexError,ValueError) as e:await ctx.send(str(e));return
        await self.refresh_message(g);await ctx.tick()
    @commands.group(name="yugioh",aliases=["ygo"],invoke_without_command=True)
    async def yugioh(self,ctx):await ctx.send_help()
    @yugioh.command(name="challenge")
    @commands.guild_only()
    async def challenge(self,ctx,member:discord.Member):
        if member.bot or member.id==ctx.author.id:await ctx.send("Challenge another human member.");return
        await ctx.send(f"{member.mention}, {ctx.author.mention} challenged you to a Yu-Gi-Oh prototype duel.",view=ChallengeView(self,ctx.author.id,member.id),allowed_mentions=discord.AllowedMentions(users=True))
    @yugioh.command(name="status")
    async def status(self,ctx):
        try:g=self.find(ctx.author.id)
        except GameError as e:await ctx.send(str(e));return
        await ctx.send(embed=self.game_embed(g))
    @yugioh.command(name="hand")
    async def hand(self,ctx):await ctx.send("Use View hand on the duel message so the hand stays private.")
    @yugioh.command(name="next")
    async def next_(self,ctx):await self.mutate(ctx,lambda g:g.advance(ctx.author.id))
    @yugioh.command(name="summon")
    async def summon(self,ctx,hand_position:int,zone:int,position:str="attack",*tributes:int):await self.mutate(ctx,lambda g:g.summon(ctx.author.id,hand_position,zone,position.casefold(),False,tributes))
    @yugioh.command(name="setmonster")
    async def setmonster(self,ctx,hand_position:int,zone:int,*tributes:int):await self.mutate(ctx,lambda g:g.summon(ctx.author.id,hand_position,zone,"defense",True,tributes))
    @yugioh.command(name="set")
    async def set_(self,ctx,hand_position:int,zone:int):await self.mutate(ctx,lambda g:g.set_spell(ctx.author.id,hand_position,zone))
    @yugioh.command(name="activate")
    async def activate(self,ctx,hand_position:int):await self.mutate(ctx,lambda g:g.activate(ctx.author.id,hand_position))
    @yugioh.command(name="flip")
    async def flip(self,ctx,zone:int):await self.mutate(ctx,lambda g:g.flip_summon(ctx.author.id,zone))
    @yugioh.command(name="position")
    async def position(self,ctx,zone:int):await self.mutate(ctx,lambda g:g.change_position(ctx.author.id,zone))
    @yugioh.command(name="attack")
    async def attack(self,ctx,attacker_zone:int,target_zone:int=None):await self.mutate(ctx,lambda g:g.attack(ctx.author.id,attacker_zone,target_zone))
    @yugioh.command(name="trap")
    async def trap(self,ctx,spell_zone:int):await self.mutate(ctx,lambda g:g.respond_attack(ctx.author.id,spell_zone))
    @yugioh.command(name="resolve")
    async def resolve(self,ctx):await self.mutate(ctx,lambda g:g.respond_attack(ctx.author.id))
    @yugioh.command(name="catalog")
    async def catalog(self,ctx):await ctx.send("\n".join(f"{c.key} - {c.name} ({c.kind})" for c in CARDS.values()))
    @yugioh.command(name="rules")
    async def rules(self,ctx):await ctx.send("\n".join(f"- {x}" for x in SUPPORTED_MECHANICS))
    @yugioh.command(name="concede")
    async def concede(self,ctx):await self.mutate(ctx,lambda g:g.concede(ctx.author.id))
    async def cleanup_expired(self):
        count=0
        for g in list(self.games.values()):
            async with self.lock(g.game_id):
                if g.is_expired(int(time.time()),DUEL_TIMEOUT_SECONDS):g.expire();await self.save(g);count+=1
        return count
    async def _cleanup_loop(self):
        try:
            while True:await asyncio.sleep(3600);await self.cleanup_expired()
        except asyncio.CancelledError:return
    async def red_delete_data_for_user(self,*,requester,user_id):
        async with self.storage_lock:
            games=await self.config.games()
            for key in list(games):
                if user_id in [int(x) for x in games[key].get("order",[])]:games.pop(key);self.games.pop(int(key),None);self.channels.pop(int(key),None)
            await self.config.games.set(games)
