import asyncio
import logging
from typing import Dict
import discord
from redbot.core import Config, commands
from .engine import Game, GameError
from .views import ChallengeView, GameView

log=logging.getLogger("red.sick-cogs.MTG")
CONFIG_IDENTIFIER=813604927115
DEFAULTS={"schema":1,"next_game_id":1,"games":{}}

class MTG(commands.Cog):
    """Play a deliberately bounded two-player Magic rules prototype."""
    __author__="SickProdigy"
    __version__="0.1.0"
    def __init__(self,bot):
        self.bot=bot; self.config=Config.get_conf(self,identifier=CONFIG_IDENTIFIER,force_registration=True)
        self.config.register_global(**DEFAULTS); self.games:Dict[int,Game]={}; self.locks={}; self.channels={}
    async def cog_load(self):
        raw=await self.config.games()
        for key,value in raw.items():
            try:
                game=Game.from_raw(value); self.games[game.game_id]=game; self.channels[game.game_id]=int(value.get("channel_id",0))
                if not game.winner: self.bot.add_view(GameView(self,game.game_id),message_id=value.get("message_id"))
            except Exception: log.exception("Could not restore MTG game %s",key)
    def cog_unload(self):
        pass
    def lock(self,gid): return self.locks.setdefault(gid,asyncio.Lock())
    async def create_game(self,a,b,channel):
        for game in self.games.values():
            if not game.winner and (a in game.order or b in game.order): raise GameError("One player already has an active game.")
        gid=await self.config.next_game_id(); await self.config.next_game_id.set(gid+1)
        game=Game(gid,[a,b]); game.message_id=0; self.games[gid]=game; self.channels[gid]=channel; await self.save(game); return game
    async def save(self,game):
        raw=game.to_raw(); raw["channel_id"]=self.channels.get(game.game_id,0); raw["message_id"]=getattr(game,"message_id",0)
        games=await self.config.games(); games[str(game.game_id)]=raw; await self.config.games.set(games)
    def find(self,user):
        found=[g for g in self.games.values() if user in g.order and not g.winner]
        if not found: raise GameError("You do not have an active MTG game.")
        return found[0]
    def game_embed(self,g):
        names={u:(self.bot.get_user(u).display_name if self.bot.get_user(u) else str(u)) for u in g.order}
        e=discord.Embed(title=f"MTG prototype · Game {g.game_id}",color=discord.Color.dark_green())
        e.description="Opening hands" if g.phase=="opening" else f"Turn **{g.turn}** · **{g.phase.replace('_',' ').title()}**\nActive: **{names[g.active_user]}**"
        for user in g.order:
            p=g.players[user]; field=[]
            for n,x in enumerate(p.battlefield,1):
                c=g.card(x.uid); state=" ↷" if x.tapped else ""
                stats=f" {c.power+x.bonus}/{c.toughness+x.bonus}" if c.creature else ""
                field.append(f"{n}. {c.name}{stats}{state}")
            e.add_field(name=f"{names[user]} · {p.life} life · {len(p.hand)} cards",value="\n".join(field) or "No permanents",inline=False)
        if g.stack: e.add_field(name="Stack",value=" → ".join(g.card(x.uid).name for x in reversed(g.stack)),inline=False)
        if g.winner: e.description=f"🏆 **{names[g.winner]} wins** — {g.finished_reason}."
        e.set_footer(text="Experimental supported-card subset · hands are private")
        return e
    async def act(self,i,game_id,action):
        game=self.games.get(game_id)
        if not game:
            await i.response.send_message("This match is unavailable.",ephemeral=True); return
        async with self.lock(game.game_id):
            try: action(game); await self.save(game)
            except (GameError,IndexError,ValueError) as e: await i.response.send_message(str(e),ephemeral=True); return
            await i.response.edit_message(embed=self.game_embed(game),view=None if game.winner else GameView(self,game.game_id))
    async def mutate_ctx(self,ctx,action):
        game=self.find(ctx.author.id)
        async with self.lock(game.game_id):
            try: action(game); await self.save(game)
            except (GameError,IndexError,ValueError) as e: await ctx.send(str(e)); return
        try:
            channel=self.bot.get_channel(self.channels.get(game.game_id)); message=await channel.fetch_message(game.message_id)
            await message.edit(embed=self.game_embed(game),view=None if game.winner else GameView(self,game.game_id))
        except (discord.HTTPException,AttributeError): pass
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
        await self.mutate_ctx(ctx,lambda g:g.play(ctx.author.id,position,target))
    @mtg.command(name="attack")
    async def attack(self,ctx,*positions:int):
        """Declare battlefield positions as attackers; no positions skips combat."""
        await self.mutate_ctx(ctx,lambda g:g.declare_attackers(ctx.author.id,positions))
    @mtg.command(name="block")
    async def block(self,ctx,*assignments:str):
        """Block as ATTACKER_POSITION:BLOCKER_POSITION; no values means no blocks."""
        def run(g):
            pairs={}
            for item in assignments:
                a,b=item.split(":",1); pairs[int(a)]=int(b)
            g.declare_blockers(ctx.author.id,pairs)
        await self.mutate_ctx(ctx,run)
    @mtg.command(name="pass")
    async def pass_(self,ctx): await self.mutate_ctx(ctx,lambda g:g.pass_priority(ctx.author.id))
    @mtg.command(name="concede")
    async def concede(self,ctx): await self.mutate_ctx(ctx,lambda g:g.concede(ctx.author.id))
    async def red_delete_data_for_user(self,*,requester,user_id):
        changed=False; games=await self.config.games()
        for key in list(games):
            if user_id in [int(x) for x in games[key].get("order",[])]:
                games.pop(key); self.games.pop(int(key),None); changed=True
        if changed: await self.config.games.set(games)
