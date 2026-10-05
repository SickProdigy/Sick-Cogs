import asyncio
import logging
import random
import time
from datetime import datetime,timedelta,timezone
from pathlib import Path
import discord
from discord.ext import tasks
from redbot.core import Config,commands
from redbot.core.data_manager import cog_data_path
from .catalog import CatalogError,PokemonCatalog
from .data import MOVES,SPECIES,generation_for,sprite
from .models import Battle,BattleError,OwnedPokemon
from .renderer import BattleRenderer,RenderError
from .views import BattleView,EncounterView

log=logging.getLogger("red.sick-cogs.Pokemon")
CONFIG_IDENTIFIER=813604927242
GUILD={"enabled":False,"channels":[],"activity":0,"threshold":20,"threshold_min":12,"threshold_max":25,"active_encounter":None,"encounter_timeout":900,"battle_timeout":1800,"spawn_cooldown":300,"last_spawn_at":None,"generations":[1]}
USER={"collection":[],"party":[],"balls":10,"starter_chosen":False,"transactions":{}}
GLOBAL={"schema":2,"next_encounter":1,"encounters":{}}

def activity_weight(active_users):
    return 1+min(2,max(0,active_users-1))

def available_species(generations):
    return [item for item in SPECIES.values() if item.id not in {1,4,7} and generation_for(item.id) in generations]

def encounter_is_expired(raw,now):
    try:due=datetime.fromisoformat(raw.get("expires_at",""))
    except (TypeError,ValueError):return False
    return raw.get("state") in {"open","battle"} and due<=now

class Pokemon(commands.Cog):
    """Catch globally owned Pokémon in opt-in guild channels."""
    __version__="0.3.0";__author__="SickProdigy"
    def __init__(self,bot):
        self.bot=bot;self.config=Config.get_conf(self,identifier=CONFIG_IDENTIFIER,force_registration=True)
        self.config.register_guild(**GUILD);self.config.register_user(**USER);self.config.register_global(**GLOBAL)
        self.battles={};self.locks={};self.activity={};self.recent_users={};self.recent_content={};self.catalog=PokemonCatalog(cog_data_path(self)/"catalog.json",Path(__file__).with_name("gen1.json"));self.renderer=BattleRenderer(cog_data_path(self)/"sprites");self.cleanup_loop.start()
    async def cog_load(self):
        await self._migrate()
        try:self.catalog.load()
        except CatalogError:log.exception("Pokémon catalog cache could not be loaded")
        for key,raw in (await self.config.encounters()).items():
            if raw.get("battle"):
                battle=Battle.from_raw(raw["battle"]);self.battles[battle.encounter_id]=battle
                if battle.state=="active":self.bot.add_view(BattleView(self,battle.encounter_id),message_id=battle.message_id)
            elif raw.get("state")=="open":self.bot.add_view(EncounterView(self,int(key)),message_id=raw.get("message_id"))
    def cog_unload(self):
        self.cleanup_loop.cancel();self.bot.loop.create_task(self.renderer.close())
    @tasks.loop(seconds=60)
    async def cleanup_loop(self):
        now=datetime.now(timezone.utc);expired=[]
        async with self.lock("encounters"):
            encounters=await self.config.encounters()
            for key,raw in encounters.items():
                if encounter_is_expired(raw,now):
                    raw["state"]="expired";expired.append((int(key),dict(raw)))
            if expired:await self.config.encounters.set(encounters)
        for eid,raw in expired:
            self.battles.pop(eid,None);await self.clear_guild(int(raw["guild_id"]),eid)
            channel=self.bot.get_channel(int(raw["channel_id"]))
            if channel:
                try:
                    message=await channel.fetch_message(int(raw["message_id"]));await message.edit(content="This wild encounter expired.",view=None)
                except (discord.Forbidden,discord.NotFound,discord.HTTPException):pass
    @cleanup_loop.before_loop
    async def before_cleanup(self):await self.bot.wait_until_ready()
    async def _migrate(self):
        if await self.config.schema()>=2:return
        for user_id,data in (await self.config.all_users()).items():
            collection=data.get("collection",[])
            party=data.get("party",[])
            if party and isinstance(party[0],int):data["party"]=[collection[x]["instance_id"] for x in party if 0<=x<len(collection)]
            data.setdefault("transactions",{})
            await self.config.user_from_id(int(user_id)).set(data)
        await self.config.schema.set(2)
    def lock(self,key):return self.locks.setdefault(key,asyncio.Lock())
    async def put_encounter(self,eid,raw):
        async with self.lock("encounters"):
            encounters=await self.config.encounters();encounters[str(eid)]=raw;await self.config.encounters.set(encounters)
    @commands.Cog.listener()
    async def on_message_without_command(self,message):
        if not message.guild or message.author.bot or len((message.content or "").strip())<3:return
        conf=await self.config.guild(message.guild).all()
        if not conf["enabled"] or message.channel.id not in conf["channels"] or conf["active_encounter"]:return
        now=time.monotonic();content=" ".join((message.content or "").casefold().split())
        recent_key=(message.guild.id,message.author.id)
        previous=self.recent_content.get(recent_key)
        if previous and previous[0]==content and now-previous[1]<30:return
        self.recent_content[recent_key]=(content,now)
        users=self.recent_users.setdefault(message.guild.id,{})
        users[message.author.id]=now
        for user_id,last_seen in list(users.items()):
            if now-last_seen>300:users.pop(user_id,None)
        key=message.guild.id;weight=activity_weight(len(users));count=self.activity.get(key,conf["activity"])+weight;self.activity[key]=count
        if conf["last_spawn_at"]:
            try:last=datetime.fromisoformat(conf["last_spawn_at"])
            except (TypeError,ValueError):last=None
            if last and datetime.now(timezone.utc)<last+timedelta(seconds=conf["spawn_cooldown"]):return
        if count<conf["threshold"]:return
        async with self.lock(("spawn",message.guild.id)):
            current=await self.config.guild(message.guild).active_encounter()
            if current:return
            await self.spawn(message.channel)
    async def spawn(self,channel):
        async with self.lock("encounters"):
            eid=await self.config.next_encounter();await self.config.next_encounter.set(eid+1)
        conf=await self.config.guild(channel.guild).all()
        pool=available_species(conf["generations"])
        if not pool:raise RuntimeError("No Pokémon are available for the configured generations.")
        chosen=random.SystemRandom().choices(pool,weights=[max(1,item.catch_rate) for item in pool],k=1)[0]
        sid=chosen.id;level=random.SystemRandom().randrange(3,9)
        embed=discord.Embed(title=f"A wild {SPECIES[sid].name} appeared!",description="Press **Encounter** to battle it.",color=discord.Color.green())
        try:
            image=await self.renderer.encounter(sid);file=discord.File(image,filename="encounter.png");embed.set_image(url="attachment://encounter.png")
            msg=await channel.send(embed=embed,file=file,view=EncounterView(self,eid))
        except RenderError:
            log.exception("Encounter rendering failed");embed.set_image(url=sprite(sid));msg=await channel.send(embed=embed,view=EncounterView(self,eid))
        raw={"state":"open","species_id":sid,"level":level,"guild_id":channel.guild.id,"channel_id":channel.id,"message_id":msg.id,"created_at":datetime.now(timezone.utc).isoformat(),"expires_at":(datetime.now(timezone.utc)+timedelta(seconds=conf["encounter_timeout"])).isoformat()}
        await self.put_encounter(eid,raw)
        self.activity[channel.guild.id]=0
        await self.config.guild(channel.guild).active_encounter.set(eid);await self.config.guild(channel.guild).activity.set(0)
        await self.config.guild(channel.guild).threshold.set(random.SystemRandom().randrange(conf["threshold_min"],conf["threshold_max"]+1))
        await self.config.guild(channel.guild).last_spawn_at.set(datetime.now(timezone.utc).isoformat())
    async def claim(self,i,eid):
        async with self.lock(("user",i.user.id)), self.lock(("encounter",eid)), self.lock("encounters"):
            if any(b.user_id==i.user.id and b.state=="active" for b in self.battles.values()):
                await i.response.send_message("Finish your active encounter first.",ephemeral=True);return
            encounters=await self.config.encounters();raw=encounters.get(str(eid))
            if not raw or raw.get("state")!="open":await i.response.send_message("This encounter was already claimed.",ephemeral=True);return
            user=await self.config.user(i.user).all()
            if not user["party"]:await i.response.send_message("Choose a starter first with the Pokémon starter command.",ephemeral=True);return
            owned_raw=next((p for p in user["collection"] if p["instance_id"]==user["party"][0]),None)
            if not owned_raw:
                await i.response.send_message("Your active party needs repair.",ephemeral=True);return
            owned=OwnedPokemon.from_raw(owned_raw)
            collection={item["instance_id"]:item for item in user["collection"]}
            party=[OwnedPokemon.from_raw(collection[identity]) for identity in user["party"] if identity in collection]
            wild=SPECIES[raw["species_id"]];battle=Battle(eid,i.user.id,raw["guild_id"],raw["channel_id"],raw["message_id"],owned,raw["species_id"],raw["level"],Battle.stat(owned,"hp"),wild.hp+raw["level"]*2,seed=random.SystemRandom().randrange(1,2**31));battle.initialize_party(party);battle.wild_hp=battle.wild_max_hp
            battle_seconds=await self.config.guild_from_id(int(raw["guild_id"])).battle_timeout()
            raw["state"]="battle";raw["expires_at"]=(datetime.now(timezone.utc)+timedelta(seconds=battle_seconds)).isoformat();raw["battle"]=battle.raw();encounters[str(eid)]=raw;await self.config.encounters.set(encounters);self.battles[eid]=battle
            embed,files=await self.rendered_battle(battle)
            await i.response.edit_message(embed=embed,attachments=files,view=BattleView(self,eid))
    async def rendered_battle(self,battle):
        embed=self.battle_embed(battle)
        try:
            image=await self.renderer.battle(battle);embed.set_image(url="attachment://battle.png")
            return embed,[discord.File(image,filename="battle.png")]
        except RenderError:
            log.exception("Battle rendering failed")
            return embed,[]
    @staticmethod
    def move_label(key):return MOVES[key].name[:80]
    def battle_embed(self,b):
        player=SPECIES[b.player.species_id];wild=SPECIES[b.wild_species_id]
        e=discord.Embed(title=f"Wild {wild.name} · Lv. {b.wild_level}",description=b.result or b.last_action or f"Turn {b.turn}",color=discord.Color.blurple())
        e.set_thumbnail(url=sprite(wild.id))
        e.add_field(name=f"{wild.name} HP",value=f"{b.wild_hp}/{b.wild_max_hp}",inline=True)
        e.add_field(name=f"{player.name} HP",value=f"{b.player_hp}/{b.max_hp(b.player)}",inline=True)
        e.add_field(name="Moves",value=" · ".join(f"{n+1}. {MOVES[k].name} ({b.player.move_pp.get(k,MOVES[k].pp)} PP)" for n,k in enumerate(b.player.moves)),inline=False)
        return e
    async def battle_action(self,i,eid,action):
        battle=self.battles.get(eid)
        if not battle:
            await i.response.send_message("This battle is unavailable.",ephemeral=True);return
        async with self.lock(("battle",eid)), self.lock(("user",battle.user_id)):
            battle=self.battles.get(eid)
            if not battle:
                await i.response.send_message("This battle is unavailable.",ephemeral=True);return
            try:action(battle)
            except (BattleError,IndexError,AttributeError) as e:await i.response.send_message(str(e),ephemeral=True);return
            if battle.state=="won":await self.sync_battle_player(battle)
            await self.save_battle(battle)
            done=battle.state!="active"
            if done:await self.clear_guild(battle.guild_id,eid)
            embed,files=await self.rendered_battle(battle)
            await i.response.edit_message(embed=embed,attachments=files,view=None if done else BattleView(self,eid))
    async def sync_battle_player(self,battle):
        conf=await self.config.user_from_id(battle.user_id).all()
        updates={item.instance_id:item.raw() for item in battle.party}
        updates[battle.player.instance_id]=battle.player.raw()
        conf["collection"]=[
            updates.get(raw["instance_id"],raw) for raw in conf["collection"]
        ]
        await self.config.user_from_id(battle.user_id).set(conf)
    async def throw_ball(self,i,eid):
        battle=self.battles.get(eid)
        if not battle:
            await i.response.send_message("This battle is unavailable.",ephemeral=True);return
        async with self.lock(("battle",eid)), self.lock(("user",battle.user_id)):
            battle=self.battles.get(eid)
            if not battle:
                await i.response.send_message("This battle is unavailable.",ephemeral=True);return
            if battle.state!="active" or battle.needs_switch:
                await i.response.send_message("Switch Pokémon first." if battle.needs_switch else "This encounter is over.",ephemeral=True);return
            conf=await self.config.user(i.user).all();tx_key=f"{eid}:{battle.rolls}";tx=conf["transactions"].get(tx_key,{})
            if conf["balls"]<1 and not tx.get("ball_charged"):await i.response.send_message("You have no Poké Balls.",ephemeral=True);return
            if not tx.get("ball_charged"):
                conf["balls"]-=1;tx["ball_charged"]=True;conf["transactions"][tx_key]=tx;await self.config.user(i.user).set(conf)
            try:caught=battle.throw_ball()
            except BattleError as e:await i.response.send_message(str(e),ephemeral=True);return
            if caught:
                identity=f"catch-{battle.user_id}-{eid}";pokemon=battle.caught(identity)
                if not any(p["instance_id"]==identity for p in conf["collection"]):conf["collection"].append(pokemon.raw())
                if len(conf["party"])<6 and identity not in conf["party"]:conf["party"].append(identity)
                tx["settled"]=True;tx["caught_id"]=identity;conf["transactions"][tx_key]=tx
            while len(conf["transactions"])>100:conf["transactions"].pop(next(iter(conf["transactions"])))
            await self.config.user(i.user).set(conf);await self.save_battle(battle)
            if battle.state!="active":await self.clear_guild(battle.guild_id,eid)
            embed,files=await self.rendered_battle(battle)
            await i.response.edit_message(embed=embed,attachments=files,view=None if battle.state!="active" else BattleView(self,eid))
    async def save_battle(self,b):
        seconds=await self.config.guild_from_id(b.guild_id).battle_timeout()
        async with self.lock("encounters"):
            encounters=await self.config.encounters()
            raw=encounters[str(b.encounter_id)]
            raw["battle"]=b.raw()
            raw["state"]=b.state
            raw["expires_at"]=(datetime.now(timezone.utc)+timedelta(seconds=seconds)).isoformat()
            encounters[str(b.encounter_id)]=raw
            await self.config.encounters.set(encounters)
    async def clear_guild(self,guild_id,eid):
        conf=self.config.guild_from_id(guild_id)
        if await conf.active_encounter()==eid:await conf.active_encounter.set(None)
    @commands.group(name="pokemon",aliases=["pkmn"],invoke_without_command=True)
    async def pokemon(self,ctx):await ctx.send_help()
    @pokemon.command(name="starter")
    async def starter(self,ctx,choice:str):
        choices={"bulbasaur":1,"charmander":4,"squirtle":7};sid=choices.get(choice.casefold())
        if not sid:await ctx.send("Choose Bulbasaur, Charmander, or Squirtle.");return
        async with self.lock(("user",ctx.author.id)):
            conf=await self.config.user(ctx.author).all()
            if conf["starter_chosen"]:await ctx.send("You already chose a starter.");return
            pokemon=OwnedPokemon.create(__import__("uuid").uuid4().hex,sid,seed=random.SystemRandom().randrange(1,2**31));conf["collection"]=[pokemon.raw()];conf["party"]=[pokemon.instance_id];conf["starter_chosen"]=True
            await self.config.user(ctx.author).set(conf)
        await ctx.send(f"{SPECIES[sid].name} joined your global party!")
    @pokemon.command(name="collection",aliases=["box"])
    async def collection(self,ctx,page:int=1):
        conf=await self.config.user(ctx.author).all()
        if not conf["collection"]:await ctx.send("Choose a starter first.");return
        pages=max(1,(len(conf["collection"])+9)//10);page=max(1,min(page,pages));start=(page-1)*10
        lines=[]
        for n,pokemon in enumerate(conf["collection"][start:start+10],start+1):
            species=SPECIES[pokemon["species_id"]];marker="⭐ " if pokemon.get("shiny") else ""
            lines.append(f"{n}. {marker}{pokemon.get('nickname') or species.name} · Lv. {pokemon['level']} · {pokemon['instance_id'][:8]}")
        await ctx.send(f"**Global collection · {page}/{pages}**\n"+"\n".join(lines))
    @pokemon.group(name="party",invoke_without_command=True)
    async def party(self,ctx):
        conf=await self.config.user(ctx.author).all();owned={p["instance_id"]:p for p in conf["collection"]}
        lines=[]
        for slot,identity in enumerate(conf["party"],1):
            pokemon=owned.get(identity)
            if pokemon:lines.append(f"{slot}. {SPECIES[pokemon['species_id']].name} · Lv. {pokemon['level']} · {identity[:8]}")
        await ctx.send("**Party**\n"+("\n".join(lines) or "Empty"))
    @party.command(name="add")
    async def party_add(self,ctx,identifier:str,slot:int=None):
        async with self.lock(("user",ctx.author.id)):
            conf=await self.config.user(ctx.author).all()
            matches=[p["instance_id"] for p in conf["collection"] if p["instance_id"].startswith(identifier)]
            if len(matches)!=1:await ctx.send("Use a unique collection ID prefix.");return
            identity=matches[0];party=[value for value in conf["party"] if value!=identity]
            if slot is None:party.append(identity)
            elif 1<=slot<=6:
                party.insert(min(slot-1,len(party)),identity)
            else:await ctx.send("Slot must be 1–6.");return
            conf["party"]=party[:6];await self.config.user(ctx.author).set(conf)
        await ctx.send("Party updated.")
    @party.command(name="remove")
    async def party_remove(self,ctx,slot:int):
        async with self.lock(("user",ctx.author.id)):
            conf=await self.config.user(ctx.author).all()
            if not 1<=slot<=len(conf["party"]):await ctx.send("That party slot is empty.");return
            conf["party"].pop(slot-1);await self.config.user(ctx.author).set(conf)
        await ctx.send("Party updated.")
    @pokemon.command(name="heal")
    async def heal(self,ctx):
        async with self.lock(("user",ctx.author.id)):
            conf=await self.config.user(ctx.author).all()
            party=set(conf["party"])
            healed=0
            for raw in conf["collection"]:
                if raw["instance_id"] not in party:
                    continue
                pokemon=OwnedPokemon.from_raw(raw)
                pokemon.move_pp={key:MOVES[key].pp for key in pokemon.moves}
                raw.update(pokemon.raw());healed+=1
            await self.config.user(ctx.author).set(conf)
        await ctx.send(f"Restored your party’s HP, status, and PP ({healed} Pokémon).")

    @pokemon.command(name="profile")
    async def profile(self,ctx):
        conf=await self.config.user(ctx.author).all()
        await ctx.send(f"Pokémon: **{len(conf['collection'])}** · Party: **{len(conf['party'])}/6** · Poké Balls: **{conf['balls']}**")
    @pokemon.group(name="set",invoke_without_command=True)
    @commands.admin_or_permissions(manage_guild=True)
    async def pokemon_set(self,ctx):await ctx.send_help()
    @pokemon_set.command(name="channel")
    async def set_channel(self,ctx,channel:discord.TextChannel):
        channels=await self.config.guild(ctx.guild).channels()
        if channel.id not in channels:channels.append(channel.id)
        await self.config.guild(ctx.guild).channels.set(channels);await self.config.guild(ctx.guild).enabled.set(True)
        await ctx.send(f"Wild encounters enabled in {channel.mention}.")
    @pokemon_set.command(name="removechannel")
    async def remove_channel(self,ctx,channel:discord.TextChannel):
        channels=await self.config.guild(ctx.guild).channels()
        if channel.id in channels:channels.remove(channel.id)
        await self.config.guild(ctx.guild).channels.set(channels);await ctx.send(f"Removed {channel.mention}.")
    @pokemon_set.command(name="status")
    async def spawn_status(self,ctx):
        conf=await self.config.guild(ctx.guild).all()
        channels=", ".join(f"<#{value}>" for value in conf["channels"]) or "None"
        await ctx.send(
            f"Enabled: **{conf['enabled']}**\n"
            f"Channels: {channels}\n"
            f"Threshold: {conf['threshold_min']}–{conf['threshold_max']}\n"
            f"Cooldown: {conf['spawn_cooldown']}s\n"
            f"Encounter/battle expiry: {conf['encounter_timeout']}s/{conf['battle_timeout']}s\n"
            f"Generations: {', '.join(map(str,conf['generations']))}\n"
            f"Active: {conf['active_encounter'] or 'None'}\n"
            f"Catalog species: {len(SPECIES)}"
        )
    @pokemon_set.command(name="threshold")
    async def threshold(self,ctx,minimum:int,maximum:int):
        if not 5<=minimum<=maximum<=500:await ctx.send("Use 5–500 with minimum <= maximum.");return
        await self.config.guild(ctx.guild).threshold_min.set(minimum);await self.config.guild(ctx.guild).threshold_max.set(maximum)
        await self.config.guild(ctx.guild).threshold.set(random.SystemRandom().randrange(minimum,maximum+1));await ctx.send("Spawn threshold updated.")
    @pokemon_set.command(name="cooldown")
    async def cooldown(self,ctx,seconds:int):
        if not 60<=seconds<=86400:await ctx.send("Use 60–86400 seconds.");return
        await self.config.guild(ctx.guild).spawn_cooldown.set(seconds);await ctx.send("Spawn cooldown updated.")
    @pokemon_set.command(name="expiry")
    async def expiry(self,ctx,encounter_minutes:int,battle_minutes:int):
        if not 1<=encounter_minutes<=1440 or not 5<=battle_minutes<=1440:await ctx.send("Encounter: 1–1440 minutes; battle: 5–1440.");return
        await self.config.guild(ctx.guild).encounter_timeout.set(encounter_minutes*60);await self.config.guild(ctx.guild).battle_timeout.set(battle_minutes*60);await ctx.send("Expiry updated.")
    @pokemon_set.command(name="generations")
    async def generations(self,ctx,*values:int):
        selected=sorted(set(values))
        if not selected or any(value<1 or value>9 for value in selected):await ctx.send("Choose generations 1–9.");return
        await self.config.guild(ctx.guild).generations.set(selected);await ctx.send(f"Enabled generations: {', '.join(map(str,selected))}.")
    @pokemon_set.command(name="spawn")
    async def force_spawn(self,ctx,channel:discord.TextChannel=None):
        channel=channel or ctx.channel
        if await self.config.guild(ctx.guild).active_encounter():await ctx.send("This server already has an encounter.");return
        await self.spawn(channel)
    @pokemon_set.command(name="catalogsync")
    @commands.is_owner()
    async def catalog_sync(self,ctx,generation:int):
        async with ctx.typing():
            try:count=await self.catalog.sync_generation(generation)
            except CatalogError as exc:await ctx.send(str(exc));return
        await ctx.send(f"Cached {count} generation {generation} species.")
    @pokemon_set.command(name="clear")
    async def clear_encounter(self,ctx):
        eid=await self.config.guild(ctx.guild).active_encounter()
        if eid:
            async with self.lock(("battle",eid)), self.lock(("encounter",eid)), self.lock("encounters"):
                encounters=await self.config.encounters();encounters.pop(str(eid),None);await self.config.encounters.set(encounters)
                self.battles.pop(eid,None)
        await self.config.guild(ctx.guild).active_encounter.set(None);await ctx.send("Active encounter cleared.")
    @pokemon_set.command(name="disable")
    async def disable(self,ctx):
        await self.config.guild(ctx.guild).enabled.set(False);await ctx.send("Wild encounters disabled.")
    async def red_delete_data_for_user(self,*,requester,user_id):
        await self.config.user_from_id(user_id).clear()
        removed=[]
        async with self.lock("encounters"):
            encounters=await self.config.encounters()
            for key in list(encounters):
                if encounters[key].get("battle",{}).get("user_id")==user_id:removed.append((int(key),int(encounters[key]["guild_id"])));encounters.pop(key);self.battles.pop(int(key),None)
            await self.config.encounters.set(encounters)
        for eid,guild_id in removed:await self.clear_guild(guild_id,eid)
