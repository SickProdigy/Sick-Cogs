import asyncio
import logging
import secrets
from typing import Dict
import discord
from redbot.core import Config, commands
from .engine import Game, GameError
from .views import ChallengeView, GameView

log=logging.getLogger("red.sick-cogs.MTG")
CONFIG_IDENTIFIER=813604927115
DEFAULTS={"schema":1,"next_game_id":1,"games":{}}
MATCH_TIMEOUT_SECONDS=7*24*60*60

class MTG(commands.Cog):
    """Play a deliberately bounded two-player Magic rules prototype."""
    __author__="SickProdigy"
    __version__="0.1.3"
    def __init__(self,bot):
        self.bot=bot; self.config=Config.get_conf(self,identifier=CONFIG_IDENTIFIER,force_registration=True)
        self.config.register_global(**DEFAULTS); self.games:Dict[int,Game]={}; self.locks={}; self.channels={}
        self.storage_lock=asyncio.Lock(); self.cleanup_task=None
    async def cog_load(self):
        raw=await self.config.games()
        for key,value in raw.items():
            try:
                game=Game.from_raw(value); game.message_id=int(value.get("message_id",0))
                self.games[game.game_id]=game; self.channels[game.game_id]=int(value.get("channel_id",0))
            except Exception: log.exception("Could not restore MTG game %s",key)
        await self.cleanup_expired()
        for game in self.games.values():
            if not game.finished: self.bot.add_view(GameView(self,game.game_id),message_id=game.message_id or None)
        self.cleanup_task=asyncio.create_task(self._cleanup_loop())
    def cog_unload(self):
        if self.cleanup_task: self.cleanup_task.cancel()
    def lock(self,gid): return self.locks.setdefault(gid,asyncio.Lock())
    async def create_game(self,a,b,channel):
        async with self.storage_lock:
            for game in self.games.values():
                if not game.finished and (a in game.order or b in game.order): raise GameError("One player already has an active game.")
            gid=await self.config.next_game_id(); await self.config.next_game_id.set(gid+1)
            users=[a,b]; secrets.SystemRandom().shuffle(users)
            game=Game(gid,users); game.message_id=0; self.games[gid]=game; self.channels[gid]=channel
            await self._save_unlocked(game)
        return game
    async def _save_unlocked(self,game):
        raw=game.to_raw(); raw["channel_id"]=self.channels.get(game.game_id,0); raw["message_id"]=getattr(game,"message_id",0)
        games=await self.config.games(); games[str(game.game_id)]=raw; await self.config.games.set(games)
    async def save(self,game):
        async with self.storage_lock: await self._save_unlocked(game)
    async def cleanup_expired(self):
        expired=[]; now=int(__import__("time").time())
        for game in list(self.games.values()):
            async with self.lock(game.game_id):
                if game.is_expired(now,MATCH_TIMEOUT_SECONDS):
                    game.expire(); await self.save(game); expired.append(game)
        for game in expired: await self.refresh_message(game)
        return len(expired)
    async def _cleanup_loop(self):
        try:
            while True:
                await asyncio.sleep(3600); await self.cleanup_expired()
        except asyncio.CancelledError: return
    def find(self,user):
        found=[g for g in self.games.values() if user in g.order and not g.finished]
        if not found: raise GameError("You do not have an active MTG game.")
        return found[0]
    def game_embed(self,g):
        names={u:(self.bot.get_user(u).display_name if self.bot.get_user(u) else str(u)) for u in g.order}
        e=discord.Embed(title=f"MTG prototype · Game {g.game_id}",color=discord.Color.dark_green())
        if g.phase=="opening":
            e.description=f"Opening hands - **{names[g.active_user]}** will play first."
        else:
            e.description=f"Turn **{g.turn}** - **{g.phase.replace('_',' ').title()}**\nActive: **{names[g.active_user]}**"
            if g.priority_user: e.description+=f"\nPriority: **{names[g.priority_user]}**"
            elif g.phase=="attackers": e.description+=f"\nWaiting for **{names[g.active_user]}** to declare attackers."
            elif g.phase=="blockers": e.description+=f"\nWaiting for **{names[g.opponent(g.active_user)]}** to declare blockers."
        for user in g.order:
            p=g.players[user]; field=[]
            for n,x in enumerate(p.battlefield,1):
                c=g.card(x.uid); state=" ↷" if x.tapped else ""
                stats=f" {c.power+x.bonus}/{c.toughness+x.bonus}" if c.creature else ""
                field.append(f"{n}. {c.name}{stats}{state}")
            e.add_field(name=f"{names[user]} · {p.life} life · {len(p.hand)} cards",value="\n".join(field) or "No permanents",inline=False)
        if g.stack: e.add_field(name="Stack",value=" → ".join(g.card(x.uid).name for x in reversed(g.stack)),inline=False)
        if g.finished: e.description=f"Winner: **{names[g.winner]}** - {g.finished_reason}." if g.winner else f"Match ended - {g.finished_reason}."
        e.set_footer(text="Experimental supported-card subset · hands are private")
        return e
    async def refresh_message(self,game):
        try:
            channel=self.bot.get_channel(self.channels.get(game.game_id)); message=await channel.fetch_message(game.message_id)
            await message.edit(embed=self.game_embed(game),view=None if game.finished else GameView(self,game.game_id))
        except (discord.HTTPException,AttributeError): pass
    async def act(self,i,game_id,action,label):
        game=self.games.get(game_id)
        if not game:
            await i.response.send_message("This match is unavailable.",ephemeral=True); return
        async with self.lock(game.game_id):
            try: action(game); game.record(i.user.id,label); await self.save(game)
            except (GameError,IndexError,ValueError) as e: await i.response.send_message(str(e),ephemeral=True); return
            await i.response.edit_message(embed=self.game_embed(game),view=None if game.finished else GameView(self,game.game_id))
    async def mutate_ctx(self,ctx,action,label):
        game=self.find(ctx.author.id)
        async with self.lock(game.game_id):
            try: action(game); game.record(ctx.author.id,label); await self.save(game)
            except (GameError,IndexError,ValueError) as e: await ctx.send(str(e)); return
        await self.refresh_message(game)
        await ctx.message.add_reaction("✅")
    @commands.group(name="mtg",invoke_without_command=True)
    async def mtg(self,ctx):
        """Play the MTG rules prototype."""
        await ctx.send_help()
    @mtg.command(name="challenge")
    @commands.guild_only()
    async def challenge(self,ctx,member:discord.Member):
        """Challenge another member."""
        if member.bot or member.id==ctx.author.id: await ctx.send("Challenge another human member."); return
        await ctx.send(f"{member.mention}, {ctx.author.mention} challenged you to a two-player MTG prototype game.",view=ChallengeView(self,ctx.author.id,member.id),allowed_mentions=discord.AllowedMentions(users=True))
    @mtg.command(name="status")
    async def status(self,ctx):
        try: game=self.find(ctx.author.id)
        except GameError as e: await ctx.send(str(e)); return
        await ctx.send(embed=self.game_embed(game))
    @mtg.command(name="play")
    async def play(self,ctx,position:int,target:str=None):
        """Play/cast a hand position. Target: USER_ID or USER_ID:FIELD_POSITION."""
        await self.mutate_ctx(ctx,lambda g:g.play(ctx.author.id,position,target),"play")
    @mtg.command(name="attack")
    async def attack(self,ctx,*positions:int):
        """Declare battlefield positions as attackers; no positions skips combat."""
        await self.mutate_ctx(ctx,lambda g:g.declare_attackers(ctx.author.id,positions),"attack")
    @mtg.command(name="block")
    async def block(self,ctx,*assignments:str):
        """Block as ATTACKER_POSITION:BLOCKER_POSITION; no values means no blocks."""
        def run(g):
            pairs={}
            for item in assignments:
                a,b=item.split(":",1); pairs[int(a)]=int(b)
            g.declare_blockers(ctx.author.id,pairs)
        await self.mutate_ctx(ctx,run,"block")
    @mtg.command(name="pass")
    async def pass_(self,ctx): await self.mutate_ctx(ctx,lambda g:g.pass_priority(ctx.author.id),"pass")
    @mtg.command(name="concede")
    async def concede(self,ctx): await self.mutate_ctx(ctx,lambda g:g.concede(ctx.author.id),"concede")
    async def red_delete_data_for_user(self,*,requester,user_id):
        async with self.storage_lock:
            changed=False; games=await self.config.games()
            for key in list(games):
                if user_id in [int(x) for x in games[key].get("order",[])]:
                    games.pop(key); gid=int(key); self.games.pop(gid,None); self.channels.pop(gid,None); self.locks.pop(gid,None); changed=True
            if changed: await self.config.games.set(games)
