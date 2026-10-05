import asyncio
import random
from datetime import datetime,timezone
import discord
from redbot.core import Config,commands
from .data import MOVES,SPECIES,SPAWN_IDS,sprite
from .models import Battle,BattleError,OwnedPokemon
from .views import BattleView,EncounterView

CONFIG_IDENTIFIER=813604927242
GUILD={"enabled":False,"channels":[],"activity":0,"threshold":20,"active_encounter":None}
USER={"collection":[],"party":[],"balls":10,"starter_chosen":False}
GLOBAL={"schema":1,"next_encounter":1,"encounters":{}}

class Pokemon(commands.Cog):
    """Catch globally owned Pokémon in opt-in guild channels."""
    __version__="0.1.0";__author__="SickProdigy"
    def __init__(self,bot):
        self.bot=bot;self.config=Config.get_conf(self,identifier=CONFIG_IDENTIFIER,force_registration=True)
        self.config.register_guild(**GUILD);self.config.register_user(**USER);self.config.register_global(**GLOBAL)
        self.battles={};self.locks={}
    async def cog_load(self):
        for key,raw in (await self.config.encounters()).items():
            if raw.get("battle"):
                battle=Battle.from_raw(raw["battle"]);self.battles[battle.encounter_id]=battle
                if battle.state=="active":self.bot.add_view(BattleView(self,battle.encounter_id),message_id=battle.message_id)
            elif raw.get("state")=="open":self.bot.add_view(EncounterView(self,int(key)),message_id=raw.get("message_id"))
    def lock(self,key):return self.locks.setdefault(key,asyncio.Lock())
    @commands.Cog.listener()
    async def on_message_without_command(self,message):
        if not message.guild or message.author.bot or len((message.content or "").strip())<3:return
        conf=await self.config.guild(message.guild).all()
        if not conf["enabled"] or message.channel.id not in conf["channels"] or conf["active_encounter"]:return
        conf["activity"]+=1
        if conf["activity"]<conf["threshold"]:
            await self.config.guild(message.guild).activity.set(conf["activity"]);return
        async with self.lock(("spawn",message.guild.id)):
            current=await self.config.guild(message.guild).active_encounter()
            if current:return
            await self.spawn(message.channel)
    async def spawn(self,channel):
        eid=await self.config.next_encounter();await self.config.next_encounter.set(eid+1)
        sid=random.SystemRandom().choice(SPAWN_IDS);level=random.SystemRandom().randrange(3,9)
        embed=discord.Embed(title=f"A wild {SPECIES[sid].name} appeared!",description="Press **Encounter** to battle it.",color=discord.Color.green())
        embed.set_image(url=sprite(sid));msg=await channel.send(embed=embed,view=EncounterView(self,eid))
        raw={"state":"open","species_id":sid,"level":level,"guild_id":channel.guild.id,"channel_id":channel.id,"message_id":msg.id,"created_at":datetime.now(timezone.utc).isoformat()}
        encounters=await self.config.encounters();encounters[str(eid)]=raw;await self.config.encounters.set(encounters)
        await self.config.guild(channel.guild).active_encounter.set(eid);await self.config.guild(channel.guild).activity.set(0)
        await self.config.guild(channel.guild).threshold.set(random.SystemRandom().randrange(12,26))
    async def claim(self,i,eid):
        async with self.lock(("user",i.user.id)), self.lock(("encounter",eid)):
            if any(b.user_id==i.user.id and b.state=="active" for b in self.battles.values()):
                await i.response.send_message("Finish your active encounter first.",ephemeral=True);return
            encounters=await self.config.encounters();raw=encounters.get(str(eid))
            if not raw or raw.get("state")!="open":await i.response.send_message("This encounter was already claimed.",ephemeral=True);return
            user=await self.config.user(i.user).all()
            if not user["party"]:await i.response.send_message("Choose a starter first with the Pokémon starter command.",ephemeral=True);return
            owned=OwnedPokemon.from_raw(user["collection"][user["party"][0]])
            wild=SPECIES[raw["species_id"]];battle=Battle(eid,i.user.id,raw["guild_id"],raw["channel_id"],raw["message_id"],owned,raw["species_id"],raw["level"],SPECIES[owned.species_id].hp+owned.level*2,wild.hp+raw["level"]*2,seed=random.SystemRandom().randrange(1,2**31))
            raw["state"]="battle";raw["battle"]=battle.raw();encounters[str(eid)]=raw;await self.config.encounters.set(encounters);self.battles[eid]=battle
            await i.response.edit_message(embed=self.battle_embed(battle),view=BattleView(self,eid))
    def battle_embed(self,b):
        player=SPECIES[b.player.species_id];wild=SPECIES[b.wild_species_id]
        e=discord.Embed(title=f"Wild {wild.name} · Lv. {b.wild_level}",description=b.result or f"Turn {b.turn}",color=discord.Color.blurple())
        e.set_thumbnail(url=sprite(wild.id));e.set_image(url=sprite(player.id,back=True))
        e.add_field(name=f"{wild.name} HP",value=f"{b.wild_hp}/{b.wild_max_hp}",inline=True)
        e.add_field(name=f"{player.name} HP",value=f"{b.player_hp}/{b.max_hp(b.player)}",inline=True)
        e.add_field(name="Moves",value=" · ".join(f"{n+1}. {MOVES[k].name}" for n,k in enumerate(player.moves)),inline=False)
        return e
    async def battle_action(self,i,eid,action):
        async with self.lock(("battle",eid)):
            battle=self.battles.get(eid)
            try:action(battle)
            except (BattleError,IndexError,AttributeError) as e:await i.response.send_message(str(e),ephemeral=True);return
            await self.save_battle(battle)
            done=battle.state!="active"
            if done:await self.clear_guild(battle.guild_id,eid)
            await i.response.edit_message(embed=self.battle_embed(battle),view=None if done else BattleView(self,eid))
    async def throw_ball(self,i,eid):
        async with self.lock(("battle",eid)):
            battle=self.battles.get(eid);conf=await self.config.user(i.user).all()
            if conf["balls"]<1:await i.response.send_message("You have no Poké Balls.",ephemeral=True);return
            conf["balls"]-=1
            try:caught=battle.throw_ball()
            except BattleError as e:await i.response.send_message(str(e),ephemeral=True);return
            if caught:
                pokemon=battle.caught();conf["collection"].append(pokemon.raw())
                if len(conf["party"])<6:conf["party"].append(len(conf["collection"])-1)
            await self.config.user(i.user).set(conf);await self.save_battle(battle)
            if battle.state!="active":await self.clear_guild(battle.guild_id,eid)
            await i.response.edit_message(embed=self.battle_embed(battle),view=None if battle.state!="active" else BattleView(self,eid))
    async def save_battle(self,b):
        encounters=await self.config.encounters();raw=encounters[str(b.encounter_id)];raw["battle"]=b.raw();raw["state"]="battle";encounters[str(b.encounter_id)]=raw;await self.config.encounters.set(encounters)
    async def clear_guild(self,guild_id,eid):
        guild=self.bot.get_guild(guild_id)
        if guild and await self.config.guild(guild).active_encounter()==eid:await self.config.guild(guild).active_encounter.set(None)
    @commands.group(name="pokemon",aliases=["pkmn"],invoke_without_command=True)
    async def pokemon(self,ctx):await ctx.send_help()
    @pokemon.command(name="starter")
    async def starter(self,ctx,choice:str):
        choices={"bulbasaur":1,"charmander":4,"squirtle":7};sid=choices.get(choice.casefold())
        if not sid:await ctx.send("Choose Bulbasaur, Charmander, or Squirtle.");return
        conf=await self.config.user(ctx.author).all()
        if conf["starter_chosen"]:await ctx.send("You already chose a starter.");return
        pokemon=OwnedPokemon(__import__("uuid").uuid4().hex,sid);conf["collection"]=[pokemon.raw()];conf["party"]=[0];conf["starter_chosen"]=True
        await self.config.user(ctx.author).set(conf);await ctx.send(f"{SPECIES[sid].name} joined your global party!")
    @pokemon.command(name="collection")
    async def collection(self,ctx):
        conf=await self.config.user(ctx.author).all()
        if not conf["collection"]:await ctx.send("Choose a starter first.");return
        lines=[f"{n+1}. {SPECIES[p['species_id']].name} · Lv. {p['level']}" for n,p in enumerate(conf["collection"])]
        await ctx.send("\n".join(lines[:50]))
    @pokemon.group(name="set",invoke_without_command=True)
    @commands.admin_or_permissions(manage_guild=True)
    async def pokemon_set(self,ctx):await ctx.send_help()
    @pokemon_set.command(name="channel")
    async def set_channel(self,ctx,channel:discord.TextChannel):
        channels=await self.config.guild(ctx.guild).channels()
        if channel.id not in channels:channels.append(channel.id)
        await self.config.guild(ctx.guild).channels.set(channels);await self.config.guild(ctx.guild).enabled.set(True)
        await ctx.send(f"Wild encounters enabled in {channel.mention}.")
    @pokemon_set.command(name="clear")
    async def clear_encounter(self,ctx):
        eid=await self.config.guild(ctx.guild).active_encounter()
        if eid:
            encounters=await self.config.encounters();encounters.pop(str(eid),None);await self.config.encounters.set(encounters);self.battles.pop(eid,None)
        await self.config.guild(ctx.guild).active_encounter.set(None);await ctx.send("Active encounter cleared.")
    @pokemon_set.command(name="disable")
    async def disable(self,ctx):
        await self.config.guild(ctx.guild).enabled.set(False);await ctx.send("Wild encounters disabled.")
    async def red_delete_data_for_user(self,*,requester,user_id):
        await self.config.user_from_id(user_id).clear()
        encounters=await self.config.encounters()
        for key in list(encounters):
            if encounters[key].get("battle",{}).get("user_id")==user_id:encounters.pop(key);self.battles.pop(int(key),None)
        await self.config.encounters.set(encounters)
