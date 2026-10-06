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
from .catalog_versions import CATALOG_VERSIONS
from .data import MOVES,SPECIES,experience_to_next,generation_for,moves_for_level,sprite
from .models import Battle,BattleError,OwnedPokemon,pokemon_max_hp
from .gyms import GYMS,earned_badges,gym_status_embed,next_gym,trainer_profile_embed
from .pokedex import POKEDEX_STYLES,PokedexSession,PokedexView,render_pokedex,resolve_style
from .renderer import BattleRenderer,ENCOUNTER_BACKDROPS,RenderError
from .views import BagView,BattleView,CollectionBrowserView,EncounterView,FightView,MedicineView,MoveLearnView,PartyPlacementView,PartyView,StarterView

log=logging.getLogger("red.sick-cogs.Pokemon")
CONFIG_IDENTIFIER=813604927242
GUILD={"enabled":False,"channels":[],"activity":0,"threshold":12,"threshold_min":8,"threshold_max":15,"active_encounter":None,"encounter_timeout":900,"battle_timeout":1800,"spawn_cooldown":120,"last_spawn_at":None,"generations":[1],"pace":"normal","center_channel":None,"spawn_mode":"timed","timer_minutes":60,"next_spawn_at":None,"expired_card_mode":"delete"}
USER={"collection":[],"party":[],"balls":10,"starter_chosen":False,"transactions":{},"pokedex_seen":[],"pokedex_caught":[],"pokedex_style":"default","trainer_card_style":"retro","badges":[],"items":{"potion":5,"revive":2,"great_ball":3,"ultra_ball":1},"center_last_at":None,"pokedex_stats":{},"recorded_battles":[],"achievement_rewards":[]}
GLOBAL={"schema":7,"next_encounter":1,"encounters":{},"pokedex_default_style":"retro","encounter_timeout":900,"allowed_generations":[1],"minimum_threshold":8,"minimum_cooldown":120,"rarity_profile":"friendly","allow_special_species":False}
BOX_SIZE=30
MAX_BOXES=10
MAX_COLLECTION=BOX_SIZE*MAX_BOXES
COLLECTION_PAGE_SIZE=9
PACE={"active":(5,9,60),"normal":(8,15,120),"relaxed":(18,30,300)}
SPECIAL_SPECIES={144,145,146,150,151}
COLLECTION_REWARDS={5:{"balls":5},10:{"great_ball":5},25:{"ultra_ball":3},50:{"balls":10,"great_ball":5,"ultra_ball":5},100:{"balls":20,"great_ball":10,"ultra_ball":10}}
VICTORY_REWARDS={5:{"potion":5},10:{"revive":3},25:{"potion":10,"revive":5},50:{"potion":15,"revive":8},100:{"potion":25,"revive":12}}
RARITY_PROFILES={
    "friendly":{"common":100,"uncommon":65,"rare":35,"very_rare":15},
    "standard":{"common":100,"uncommon":45,"rare":18,"very_rare":5},
    "challenging":{"common":100,"uncommon":30,"rare":8,"very_rare":1},
}

def pace_for_settings(minimum,maximum,cooldown):
    for name,values in PACE.items():
        if values==(minimum,maximum,cooldown):return name
    return "custom"

def scaled_wild_level(player_level,offset):
    return max(1,min(30,player_level+offset))

def activity_weight(active_users):
    return 1+min(2,max(0,active_users-1))

def encounter_level(levels,offset=0):
    strongest=max((max(1,min(100,int(value))) for value in levels),default=1)
    return scaled_wild_level(strongest,offset)

def encounter_shiny(rng):
    return rng.randrange(4096)==0

def encounter_gender(species,rng):
    if species.gender_rate<0:return "genderless"
    return "female" if rng.randrange(8)<species.gender_rate else "male"

def rarity_tier(species):
    if species.catch_rate>=190:return "common"
    if species.catch_rate>=90:return "uncommon"
    if species.catch_rate>=45:return "rare"
    return "very_rare"

def first_pokedex_registration(conf,species_id):
    return int(species_id) not in {int(value) for value in conf.get("pokedex_caught",[]) if str(value).isdigit()}

def spawn_weight(species,profile="friendly"):
    weights=RARITY_PROFILES.get(profile,RARITY_PROFILES["friendly"])
    return weights[rarity_tier(species)]

def available_species(generations,allow_special=False):
    return [item for item in SPECIES.values() if item.id not in {1,4,7} and (allow_special or item.id not in SPECIAL_SPECIES) and generation_for(item.id) in generations]

def effective_generations(selected,allowed):
    effective=sorted(set(selected)&set(allowed))
    return effective or sorted(set(allowed))

def bounded_pace(minimum,maximum,cooldown,policy):
    floor=max(5,int(policy.get("minimum_threshold",8)))
    minimum=max(floor,int(minimum));maximum=max(minimum,int(maximum))
    return minimum,maximum,max(int(policy.get("minimum_cooldown",120)),int(cooldown))

def encounter_is_expired(raw,now):
    try:due=datetime.fromisoformat(raw.get("expires_at",""))
    except (TypeError,ValueError):return False
    return raw.get("state") in {"open","battle"} and due<=now

def encounter_returns_after_timeout(raw):
    battle=raw.get("battle",{})
    return raw.get("kind","wild")=="wild" and raw.get("state")=="battle" and int(battle.get("action_count",0))==0

def migrate_ball_items(data):
    items=dict(data.get("items",{}));items.setdefault("great_ball",3);items.setdefault("ultra_ball",1);data["items"]=items
    return data

def migrated_pokedex_stats(data):
    stats={str(key):dict(value) for key,value in data.get("pokedex_stats",{}).items() if str(key).isdigit() and isinstance(value,dict)}
    caught_counts={}
    for raw in data.get("collection",[]):
        key=str(raw.get("species_id",""));caught_counts[key]=caught_counts.get(key,0)+1
    for species_id in data.get("pokedex_seen",[]):
        key=str(species_id);entry=stats.setdefault(key,{});entry["seen"]=max(1,int(entry.get("seen",0)))
    for species_id in data.get("pokedex_caught",[]):
        key=str(species_id);entry=stats.setdefault(key,{});entry["seen"]=max(1,int(entry.get("seen",0)));entry["caught"]=max(1,caught_counts.get(key,0),int(entry.get("caught",0)))
    for entry in stats.values():
        for field in ("seen","battled","defeated","caught","escaped"):entry[field]=max(0,int(entry.get(field,0)))
    return stats

def authentic_moves_raw(raw):
    pokemon=OwnedPokemon.from_raw(raw);old_pp=dict(pokemon.move_pp)
    pokemon.moves=moves_for_level(pokemon.species_id,pokemon.level)
    pokemon.move_pp={key:min(MOVES[key].pp,max(0,int(old_pp.get(key,MOVES[key].pp)))) for key in pokemon.moves}
    return pokemon.raw()

class Pokemon(commands.Cog):
    """Catch globally owned Pokémon in opt-in guild channels."""
    __version__="0.38.0";__author__="SickProdigy"
    def __init__(self,bot):
        self.bot=bot;self.config=Config.get_conf(self,identifier=CONFIG_IDENTIFIER,force_registration=True)
        self.config.register_guild(**GUILD);self.config.register_user(**USER);self.config.register_global(**GLOBAL)
        self.battles={};self.locks={};self.activity={};self.recent_users={};self.recent_content={};self.catalog=PokemonCatalog(cog_data_path(self)/"catalog.json",Path(__file__).with_name("gen1.json"));self.renderer=BattleRenderer(cog_data_path(self)/"sprites");self.cleanup_loop.start()
    async def cog_load(self):
        try:self.catalog.load()
        except CatalogError:log.exception("Pokémon catalog cache could not be loaded")
        await self._migrate()
        for key,raw in (await self.config.encounters()).items():
            if raw.get("battle"):
                battle=Battle.from_raw(raw["battle"]);self.battles[battle.encounter_id]=battle
                if battle.state=="active":
                    for view in (BattleView,FightView,PartyView,BagView):self.bot.add_view(view(self,battle.encounter_id),message_id=battle.message_id)
                    for item_key in ("potion","revive"):self.bot.add_view(MedicineView(self,battle.encounter_id,item_key),message_id=battle.message_id)
            elif raw.get("state")=="open":self.bot.add_view(EncounterView(self,int(key)),message_id=raw.get("message_id"))
    def cog_unload(self):
        self.cleanup_loop.cancel();self.bot.loop.create_task(self.renderer.close())
    @tasks.loop(seconds=60)
    async def cleanup_loop(self):
        now=datetime.now(timezone.utc);expired=[];returned=[]
        async with self.lock("encounters"):
            encounters=await self.config.encounters()
            for key,raw in encounters.items():
                if not encounter_is_expired(raw,now):continue
                if encounter_returns_after_timeout(raw):
                    raw["state"]="open";raw.pop("battle",None)
                    raw["expires_at"]=(now+timedelta(seconds=int(raw.get("encounter_timeout",900)))).isoformat()
                    returned.append((int(key),dict(raw)))
                else:
                    raw["state"]="expired";expired.append((int(key),dict(raw)))
            if expired or returned:await self.config.encounters.set(encounters)
        for eid,raw in returned:
            self.battles.pop(eid,None)
            channel=self.bot.get_channel(int(raw["channel_id"]))
            if channel:
                try:
                    message=await channel.fetch_message(int(raw["message_id"]));await message.edit(content="The previous trainer timed out. This encounter is available again.",view=EncounterView(self,eid))
                except (discord.Forbidden,discord.NotFound,discord.HTTPException):pass
        for eid,raw in expired:
            if raw.get("battle"):await self.settle_expired_battle(raw)
            self.battles.pop(eid,None);await self.clear_guild(int(raw["guild_id"]),eid)
            if raw.get("battle"):
                channel=self.bot.get_channel(int(raw["channel_id"]))
                if channel:
                    try:
                        message=await channel.fetch_message(int(raw["message_id"]));species=SPECIES.get(int(raw.get("species_id",0)));name=(("Shiny " if raw.get("shiny") else "")+species.name) if species else "Pokemon"
                        await message.edit(content=f"The wild {name} escaped.",view=None)
                    except (discord.Forbidden,discord.NotFound,discord.HTTPException):pass
            else:await self.expire_unclaimed_message(raw)
        await self.process_timed_spawns(now)
    async def settle_expired_battle(self,raw):
        try:battle=Battle.from_raw(raw["battle"])
        except (KeyError,TypeError,ValueError):return
        battle.state="expired"
        async with self.lock(("user",battle.user_id)):
            conf=await self.config.user_from_id(battle.user_id).all();self.apply_battle_party(conf,battle);self.record_battle_result(conf,battle)
            await self.config.user_from_id(battle.user_id).set(conf)

    async def process_timed_spawns(self,now=None):
        now=now or datetime.now(timezone.utc)
        for guild_id,conf in (await self.config.all_guilds()).items():
            if not conf.get("enabled") or conf.get("spawn_mode","timed")!="timed" or not conf.get("channels"):continue
            section=self.config.guild_from_id(int(guild_id));minutes=max(30,int(conf.get("timer_minutes",60)))
            try:due=datetime.fromisoformat(conf.get("next_spawn_at") or "")
            except (TypeError,ValueError):due=None
            if due is None:
                await section.next_spawn_at.set((now+timedelta(minutes=minutes)).isoformat());continue
            if due>now or conf.get("active_encounter"):continue
            channels=[self.bot.get_channel(int(value)) for value in conf["channels"]]
            channels=[channel for channel in channels if channel is not None]
            if not channels:continue
            channel=random.SystemRandom().choice(channels)
            try:await self.spawn(channel)
            except (discord.Forbidden,discord.HTTPException):
                log.exception("Timed Pokémon encounter could not be posted")
                await section.next_spawn_at.set((now+timedelta(minutes=minutes)).isoformat())

    @cleanup_loop.before_loop
    async def before_cleanup(self):await self.bot.wait_until_ready()
    async def _migrate(self):
        schema=await self.config.schema()
        if schema<2:
            for user_id,data in (await self.config.all_users()).items():
                collection=data.get("collection",[])
                party=data.get("party",[])
                if party and isinstance(party[0],int):data["party"]=[collection[x]["instance_id"] for x in party if 0<=x<len(collection)]
                data.setdefault("transactions",{})
                await self.config.user_from_id(int(user_id)).set(data)
        if schema<3:
            for guild_id,data in (await self.config.all_guilds()).items():
                pace=pace_for_settings(data.get("threshold_min",12),data.get("threshold_max",25),data.get("spawn_cooldown",300))
                await self.config.guild_from_id(int(guild_id)).pace.set(pace)
        if schema<4:
            for user_id,data in (await self.config.all_users()).items():
                collection=[];caught=set()
                for raw in data.get("collection",[]):
                    try:pokemon=OwnedPokemon.from_raw(raw)
                    except (KeyError,TypeError,ValueError):collection.append(raw);continue
                    collection.append(pokemon.raw());caught.add(pokemon.species_id)
                data["collection"]=collection
                previous_caught={int(value) for value in data.get("pokedex_caught",[]) if str(value).isdigit()}
                previous_seen={int(value) for value in data.get("pokedex_seen",[]) if str(value).isdigit()}
                data["pokedex_caught"]=sorted(previous_caught|caught)
                data["pokedex_seen"]=sorted(previous_seen|previous_caught|caught)
                await self.config.user_from_id(int(user_id)).set(data)
        if schema<5:
            for user_id,data in (await self.config.all_users()).items():
                data["collection"]=[authentic_moves_raw(raw) for raw in data.get("collection",[])]
                await self.config.user_from_id(int(user_id)).set(data)
            encounters=await self.config.encounters()
            for raw in encounters.values():
                battle=raw.get("battle")
                if not battle:continue
                battle["player"]=authentic_moves_raw(battle["player"])
                battle["party"]=[authentic_moves_raw(item) for item in battle.get("party",[])]
                battle["wild_pp"]={}
            await self.config.encounters.set(encounters)
        if schema<6:
            for user_id,data in (await self.config.all_users()).items():await self.config.user_from_id(int(user_id)).set(migrate_ball_items(data))
        if schema<7:
            for user_id,data in (await self.config.all_users()).items():
                data["pokedex_stats"]=migrated_pokedex_stats(data);data.setdefault("recorded_battles",[]);data.setdefault("achievement_rewards",[])
                await self.config.user_from_id(int(user_id)).set(data)
            await self.config.schema.set(7)
    def lock(self,key):return self.locks.setdefault(key,asyncio.Lock())
    async def put_encounter(self,eid,raw):
        async with self.lock("encounters"):
            encounters=await self.config.encounters();encounters[str(eid)]=raw;await self.config.encounters.set(encounters)
    @commands.Cog.listener()
    async def on_message_without_command(self,message):
        if not message.guild or message.author.bot or len((message.content or "").strip())<3:return
        await self.record_activity(message)
    @commands.Cog.listener()
    async def on_command_completion(self,ctx):
        await self.record_activity(ctx.message)
    async def record_activity(self,message):
        if not message.guild or message.author.bot:return
        conf=await self.config.guild(message.guild).all()
        if not conf["enabled"] or conf.get("spawn_mode","timed")!="activity" or message.channel.id not in conf["channels"] or conf["active_encounter"]:return
        policy=await self.config.all();minimum,maximum,cooldown=bounded_pace(conf["threshold_min"],conf["threshold_max"],conf["spawn_cooldown"],policy)
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
            if last and datetime.now(timezone.utc)<last+timedelta(seconds=cooldown):return
        if count<max(minimum,conf["threshold"]):return
        async with self.lock(("spawn",message.guild.id)):
            current=await self.config.guild(message.guild).active_encounter()
            if current:return
            await self.spawn(message.channel)
    async def spawn_level(self,guild_id):
        levels=[]
        for user_id in list(self.recent_users.get(guild_id,{}))[:12]:
            data=await self.config.user_from_id(int(user_id)).all()
            collection=data.get("collection",[])
            if collection:levels.append(max(int(item.get("level",1)) for item in collection))
        offset=random.SystemRandom().choice((-2,-1,0,0,1,1,2,2,3,4))
        return encounter_level(levels,offset)

    async def spawn(self,channel,*,force_shiny=False):
        async with self.lock("encounters"):
            eid=await self.config.next_encounter();await self.config.next_encounter.set(eid+1)
        conf=await self.config.guild(channel.guild).all();policy=await self.config.all()
        generations=effective_generations(conf["generations"],policy["allowed_generations"])
        pool=available_species(generations,policy["allow_special_species"])
        if not pool:raise RuntimeError("No Pokémon are available under the bot-wide encounter policy.")
        rng=random.SystemRandom()
        chosen=rng.choices(pool,weights=[spawn_weight(item,policy["rarity_profile"]) for item in pool],k=1)[0]
        sid=chosen.id;level=await self.spawn_level(channel.guild.id);gender=encounter_gender(chosen,rng);shiny=True if force_shiny else encounter_shiny(rng);backdrop=rng.randrange(len(ENCOUNTER_BACKDROPS))
        display=("Shiny " if shiny else "")+SPECIES[sid].name;embed=discord.Embed(title=f"A wild {display} appeared!",description="Press **Encounter** to battle it.",color=discord.Color.green())
        try:
            image=await self.renderer.encounter(sid,level,gender,backdrop,shiny=shiny);file=discord.File(image,filename="encounter.png");embed.set_image(url="attachment://encounter.png")
            msg=await channel.send(embed=embed,file=file,view=EncounterView(self,eid))
        except RenderError:
            log.exception("Encounter rendering failed");embed.set_image(url=sprite(sid,shiny=shiny));msg=await channel.send(embed=embed,view=EncounterView(self,eid))
        raw={"state":"open","species_id":sid,"level":level,"gender":gender,"shiny":shiny,"backdrop":backdrop,"level_locked":True,"guild_id":channel.guild.id,"channel_id":channel.id,"message_id":msg.id,"created_at":datetime.now(timezone.utc).isoformat(),"expires_at":(datetime.now(timezone.utc)+timedelta(seconds=policy["encounter_timeout"])).isoformat(),"encounter_timeout":policy["encounter_timeout"]}
        await self.put_encounter(eid,raw)
        self.activity[channel.guild.id]=0
        await self.config.guild(channel.guild).active_encounter.set(eid);await self.config.guild(channel.guild).activity.set(0)
        minimum,maximum,_=bounded_pace(conf["threshold_min"],conf["threshold_max"],conf["spawn_cooldown"],policy)
        await self.config.guild(channel.guild).threshold.set(random.SystemRandom().randrange(minimum,maximum+1))
        spawned_at=datetime.now(timezone.utc);section=self.config.guild(channel.guild)
        await section.last_spawn_at.set(spawned_at.isoformat())
        if conf.get("spawn_mode","timed")=="timed":await section.next_spawn_at.set((spawned_at+timedelta(minutes=max(30,int(conf.get("timer_minutes",60))))).isoformat())
    async def claim(self,i,eid):
        async with self.lock(("user",i.user.id)), self.lock(("encounter",eid)), self.lock("encounters"):
            if any(b.user_id==i.user.id and b.state=="active" for b in self.battles.values()):
                await i.response.send_message("Finish your active encounter first.",ephemeral=True);return
            encounters=await self.config.encounters();raw=encounters.get(str(eid))
            if not raw or raw.get("state")!="open":await i.response.send_message("This encounter was already claimed.",ephemeral=True);return
            user=await self.config.user(i.user).all()
            if not user["party"]:
                embed,files=await self.rendered_starter_choice(i.user)
                await i.response.send_message(embed=embed,files=files,view=StarterView(self,i.user.id,eid),ephemeral=True)
                return
            owned_raw=next((p for p in user["collection"] if p["instance_id"]==user["party"][0]),None)
            if not owned_raw:
                await i.response.send_message("Your active party needs repair.",ephemeral=True);return
            owned=OwnedPokemon.from_raw(owned_raw)
            if owned.pending_moves:
                await i.response.send_message("Your active Pokémon has an unfinished move choice. Use `pokemon moves` first.",ephemeral=True);return
            if not raw.get("level_locked") or int(raw.get("level",0))<2:
                raw["level"]=scaled_wild_level(owned.level,random.SystemRandom().randrange(-2,3))
                raw["gender"]=encounter_gender(SPECIES[int(raw["species_id"])],random.SystemRandom())
                raw["level_locked"]=True
            seen={int(value) for value in user.get("pokedex_seen",[])}
            seen.add(int(raw["species_id"]));user["pokedex_seen"]=sorted(seen);self.pokedex_stat(user,raw["species_id"])["seen"]+=1
            await self.config.user(i.user).set(user)
            collection={item["instance_id"]:item for item in user["collection"]}
            party=[OwnedPokemon.from_raw(collection[identity]) for identity in user["party"] if identity in collection]
            if not any((item.current_hp if item.current_hp is not None else pokemon_max_hp(item))>0 for item in party):
                await i.response.send_message("Your party has fainted. Visit a Pokémon Center or use a Revive.",ephemeral=True);return
            wild=SPECIES[raw["species_id"]];content_generation=generation_for(int(raw["species_id"]));catalog_version=CATALOG_VERSIONS.for_generation(content_generation,bundled=content_generation==1).key;battle=Battle(eid,i.user.id,raw["guild_id"],raw["channel_id"],raw["message_id"],owned,raw["species_id"],raw["level"],Battle.stat(owned,"hp"),wild.hp+raw["level"]*2,seed=random.SystemRandom().randrange(1,2**31),wild_gender=raw.get("gender","unknown"),content_generation=content_generation,catalog_version=catalog_version,wild_shiny=bool(raw.get("shiny",False)),trainer_name=str(getattr(i.user,"display_name",getattr(i.user,"name","Trainer")))[:24]);battle.initialize_party(party);battle.wild_hp=battle.wild_max_hp
            battle_seconds=await self.config.guild_from_id(int(raw["guild_id"])).battle_timeout()
            raw["state"]="battle";raw["expires_at"]=(datetime.now(timezone.utc)+timedelta(seconds=battle_seconds)).isoformat();raw["battle"]=battle.raw();encounters[str(eid)]=raw;await self.config.encounters.set(encounters);self.battles[eid]=battle
            embed,files=await self.rendered_battle(battle)
            await i.response.edit_message(embed=embed,attachments=files,view=BattleView(self,eid))
    async def rendered_battle(self,battle):
        embed=self.battle_embed(battle);avatar_data=None
        if battle.state in {"won","caught"}:
            user=self.bot.get_user(battle.user_id)
            if user:
                try:avatar_data=await user.display_avatar.with_size(128).read()
                except (discord.HTTPException,OSError):log.debug("Trainer avatar unavailable for battle result",exc_info=True)
        try:
            image=await self.renderer.battle(battle,avatar_data=avatar_data);embed.set_image(url="attachment://battle.png")
            return embed,[discord.File(image,filename="battle.png")]
        except RenderError:
            log.exception("Battle rendering failed")
            return embed,[]
    async def rendered_expired_encounter(self,raw):
        species=SPECIES.get(int(raw.get("species_id",0)));name=(("Shiny " if raw.get("shiny") else "")+species.name) if species else "Pokemon"
        embed=discord.Embed(title=f"The wild {name} got away!",description="No trainer encountered it in time.",color=discord.Color.light_grey())
        try:
            image=await self.renderer.encounter(int(raw["species_id"]),int(raw.get("level",1)),raw.get("gender","unknown"),int(raw.get("backdrop",0)),expired=True,shiny=bool(raw.get("shiny",False)))
            embed.set_image(url="attachment://encounter-expired.png")
            return embed,[discord.File(image,filename="encounter-expired.png")]
        except (RenderError,KeyError,TypeError,ValueError):
            log.exception("Expired encounter rendering failed")
            if species:embed.set_image(url=sprite(species.id,shiny=bool(raw.get("shiny",False))))
            return embed,[]
    async def expire_unclaimed_message(self,raw,mode=None):
        channel=self.bot.get_channel(int(raw.get("channel_id",0)))
        if not channel:return
        try:message=await channel.fetch_message(int(raw["message_id"]))
        except (discord.Forbidden,discord.NotFound,discord.HTTPException,KeyError,TypeError,ValueError):return
        if mode is None:mode=await self.config.guild_from_id(int(raw["guild_id"])).expired_card_mode()
        if mode=="delete":
            try:await message.delete();return
            except discord.NotFound:return
            except (discord.Forbidden,discord.HTTPException):pass
        try:
            embed,files=await self.rendered_expired_encounter(raw);await message.edit(content=None,embed=embed,attachments=files,view=None)
        except (discord.Forbidden,discord.NotFound,discord.HTTPException,KeyError,TypeError,ValueError):pass

    async def rendered_progression(self,pokemon,evolved_from=None,move_key=None,pending=False):
        species=SPECIES[pokemon.species_id]
        if evolved_from:title=f"{SPECIES[evolved_from].name} evolved!";description=f"Congratulations! Your {SPECIES[evolved_from].name} evolved into {species.name}!"
        elif pending:title=f"{species.name} wants to learn {MOVES[move_key].name}!";description="Choose one move to forget, or give up learning the new move."
        else:title=f"{species.name} learned {MOVES[move_key].name}!";description=f"{species.name} can now use {MOVES[move_key].name}."
        embed=discord.Embed(title=title,description=description,color=discord.Color.gold())
        try:
            image=await self.renderer.progression(pokemon,evolved_from,move_key,pending);embed.set_image(url="attachment://progression.png")
            return embed,[discord.File(image,filename="progression.png")]
        except RenderError:
            log.exception("Progression rendering failed");return embed,[]

    async def rendered_pokedex_registration(self,pokemon,trainer_name):
        species=SPECIES[pokemon.species_id];trainer=" ".join(str(trainer_name or "Trainer").split())[:24] or "Trainer"
        embed=discord.Embed(title=f"{species.name} was registered!",description=f"New Pokémon data was added to {trainer}'s Pokédex.",color=discord.Color.red())
        try:
            image=await self.renderer.pokedex_registration(pokemon,trainer);embed.set_image(url="attachment://pokedex-registration.png")
            return embed,[discord.File(image,filename="pokedex-registration.png")]
        except RenderError:
            log.exception("Pokédex registration rendering failed");return embed,[]

    async def rendered_trainer_card(self,user,conf):
        style=conf.get("trainer_card_style","retro")
        if style not in {"retro","gold"}:style="retro"
        embed=trainer_profile_embed(user,conf,MAX_COLLECTION)
        try:
            image=await self.renderer.trainer_card(user.display_name,conf,style);embed.set_image(url="attachment://trainer-card.png")
            return embed,[discord.File(image,filename="trainer-card.png")]
        except RenderError:
            log.exception("Trainer card rendering failed");return embed,[]

    async def send_progression(self,interaction,battle):
        events=battle.progression_events or [{"instance_id":battle.player.instance_id,"evolved_from":battle.evolved_from,"learned_moves":battle.learned_moves,"pending_moves":battle.pending_moves}]
        party={pokemon.instance_id:pokemon for pokemon in battle.party}
        for event in events:
            pokemon=party.get(event.get("instance_id"))
            if not pokemon:continue
            if event.get("evolved_from"):
                embed,files=await self.rendered_progression(pokemon,evolved_from=event["evolved_from"])
                await interaction.followup.send(embed=embed,files=files)
            for move in event.get("learned_moves",[]):
                embed,files=await self.rendered_progression(pokemon,move_key=move)
                await interaction.followup.send(embed=embed,files=files)
            pending=event.get("pending_moves",[])
            if pending:
                move=pending[0];embed,files=await self.rendered_progression(pokemon,move_key=move,pending=True)
                await interaction.followup.send(embed=embed,files=files,view=MoveLearnView(self,battle.user_id,pokemon,move))

    async def resolve_move_choice(self,interaction,identity,new_move,forgotten):
        async with self.lock(("user",interaction.user.id)):
            conf=await self.config.user(interaction.user).all();raw=next((item for item in conf.get("collection",[]) if item.get("instance_id")==identity),None)
            if not raw:
                await interaction.response.send_message("That Pokémon is no longer in your collection.",ephemeral=True);return
            pokemon=OwnedPokemon.from_raw(raw)
            if new_move not in pokemon.pending_moves:
                await interaction.response.send_message("That move choice is no longer pending.",ephemeral=True);return
            learned=forgotten in pokemon.moves if forgotten else False
            if learned:
                known=list(pokemon.moves);index=known.index(forgotten);known[index]=new_move;pokemon.moves=tuple(known)
                pokemon.move_pp.pop(forgotten,None);pokemon.move_pp[new_move]=MOVES[new_move].pp
            pokemon.pending_moves.remove(new_move)
            conf["collection"]=[pokemon.raw() if item.get("instance_id")==identity else item for item in conf["collection"]]
            await self.config.user(interaction.user).set(conf)
        if pokemon.pending_moves:
            next_move=pokemon.pending_moves[0];embed,files=await self.rendered_progression(pokemon,move_key=next_move,pending=True)
            await interaction.response.edit_message(embed=embed,attachments=files,view=MoveLearnView(self,interaction.user.id,pokemon,next_move));return
        if learned:
            embed,files=await self.rendered_progression(pokemon,move_key=new_move)
            await interaction.response.edit_message(embed=embed,attachments=files,view=None)
        else:
            embed=discord.Embed(title=f"{SPECIES[pokemon.species_id].name} did not learn {MOVES[new_move].name}.",color=discord.Color.gold())
            await interaction.response.edit_message(embed=embed,attachments=[],view=None)

    @staticmethod
    def move_label(key):return MOVES[key].name[:80]
    def battle_embed(self,b):
        player=SPECIES[b.player.species_id];wild=SPECIES[b.wild_species_id];wild_name=("Shiny " if b.wild_shiny else "")+wild.name
        gym=GYMS.get(b.gym_key) if b.battle_kind=="gym" else None
        if b.state!="active":
            trainer=" ".join(str(b.trainer_name or "Trainer").split())[:24] or "Trainer"
            if b.state=="caught":title=f"Gotcha! {wild_name} was caught by {trainer}!"
            elif b.state=="won":title=f"{trainer} defeated {wild_name}!"
            elif b.state=="lost":title=f"{wild_name} escaped from {trainer}!"
            else:title=f"{wild_name} escaped from {trainer}!"
            e=discord.Embed(title=title,description=b.result or b.last_action,color=discord.Color.gold())
            e.set_thumbnail(url=sprite(wild.id,shiny=b.wild_shiny))
            if b.state=="caught":e.add_field(name="Caught Pokémon",value=f"{wild_name} · Lv. {b.wild_level}",inline=True)
            e.add_field(name=f"{player.name} HP",value=f"{b.player_hp}/{b.max_hp(b.player)}",inline=True)
            needed=experience_to_next(b.player.species_id,b.player.level)
            e.add_field(name="Experience",value="MAX" if not needed else f"{b.player.experience}/{needed} XP",inline=True)
            return e
        title=f"Gym Leader {gym.leader} · {wild.name} Lv. {b.wild_level}" if gym else f"Wild {wild_name} · Lv. {b.wild_level}"
        e=discord.Embed(title=title,description=b.result or b.last_action or f"Turn {b.turn}",color=discord.Color.gold() if gym else discord.Color.blurple())
        e.set_thumbnail(url=sprite(wild.id,shiny=b.wild_shiny))
        e.add_field(name=f"{wild_name} HP",value=f"{b.wild_hp}/{b.wild_max_hp}",inline=True)
        e.add_field(name=f"{player.name} HP",value=f"{b.player_hp}/{b.max_hp(b.player)}",inline=True)
        e.add_field(name="Moves",value=" · ".join(f"{n+1}. {MOVES[k].name} ({b.player.move_pp.get(k,MOVES[k].pp)} PP)" for n,k in enumerate(b.player.moves)),inline=False)
        needed=experience_to_next(b.player.species_id,b.player.level)
        e.add_field(name="Experience",value="MAX" if not needed else f"{b.player.experience}/{needed} XP",inline=True)
        e.set_footer(text="Defeat the Gym Leader to earn the badge; switch Pokémon or forfeit." if gym else "Defeat it for XP, catch it from Bag, switch Pokémon, or run.")
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
            done=battle.state!="active"
            if done:await self.sync_battle_player(battle)
            await self.save_battle(battle)
            if done:await self.clear_guild(battle.guild_id,eid)
            embed,files=await self.rendered_battle(battle)
            await i.response.edit_message(embed=embed,attachments=files,view=None if done else BattleView(self,eid))
            if done:await self.send_progression(i,battle)
    @staticmethod
    def apply_battle_party(conf,battle):
        battle.party_hp[battle.player.instance_id]=battle.player_hp
        battle.party_status[battle.player.instance_id]=battle.player_status
        battle.party_status_turns[battle.player.instance_id]=battle.player_status_turns
        updates={}
        for item in battle.party:
            item.current_hp=max(0,min(pokemon_max_hp(item),int(battle.party_hp.get(item.instance_id,pokemon_max_hp(item)))))
            item.status=battle.party_status.get(item.instance_id,"")
            item.status_turns=battle.party_status_turns.get(item.instance_id,0)
            updates[item.instance_id]=item.raw()
        updates[battle.player.instance_id]=battle.player.raw()
        conf["collection"]=[updates.get(raw["instance_id"],raw) for raw in conf["collection"]]

    @staticmethod
    def pokedex_stat(conf,species_id):
        stats=conf.setdefault("pokedex_stats",{});entry=stats.setdefault(str(int(species_id)),{})
        for field in ("seen","battled","defeated","caught","escaped"):entry[field]=max(0,int(entry.get(field,0)))
        return entry

    @staticmethod
    def grant_achievement_rewards(conf):
        claimed=set(conf.setdefault("achievement_rewards",[]));awarded=[];items=conf.setdefault("items",{})
        unique_caught=len({int(value) for value in conf.get("pokedex_caught",[])})
        victories=sum(int(value.get("defeated",0)) for value in conf.get("pokedex_stats",{}).values())
        for group,total,rewards in (("collection",unique_caught,COLLECTION_REWARDS),("victories",victories,VICTORY_REWARDS)):
            for target,reward in rewards.items():
                key=f"{group}:{target}"
                if total<target or key in claimed:continue
                parts=[]
                for item,amount in reward.items():
                    if item=="balls":conf["balls"]=int(conf.get("balls",0))+amount;label="Poké Balls"
                    else:items[item]=int(items.get(item,0))+amount;label=item.replace("_"," ").title()+("s" if amount!=1 else "")
                    parts.append(f"{amount} {label}")
                claimed.add(key);awarded.append(f"{group.title()} goal {target}: "+", ".join(parts))
        conf["achievement_rewards"]=sorted(claimed);conf["items"]=items
        return awarded

    def record_battle_result(self,conf,battle):
        key=str(battle.encounter_id);recorded=list(conf.setdefault("recorded_battles",[]))
        if key in recorded:return []
        entry=self.pokedex_stat(conf,battle.wild_species_id);entry["battled"]+=1
        if battle.state=="won":entry["defeated"]+=1
        elif battle.state=="caught":entry["caught"]+=1
        elif battle.state in {"lost","ran","expired"}:entry["escaped"]+=1
        recorded.append(key);conf["recorded_battles"]=recorded[-500:]
        return self.grant_achievement_rewards(conf)

    async def sync_battle_player(self,battle):
        conf=await self.config.user_from_id(battle.user_id).all()
        self.apply_battle_party(conf,battle)
        rewards=self.record_battle_result(conf,battle);gym=GYMS.get(battle.gym_key) if battle.battle_kind=="gym" else None
        if gym:
            defeated=f"Gym Leader {gym.leader}'s {SPECIES[battle.wild_species_id].name} fainted."
            battle.result=(battle.result or "Victory!").replace("The wild Pokémon fainted.",defeated,1)
            badges=earned_badges(conf.get("badges",[]))
            if gym.key not in badges:
                badges.append(gym.key)
                battle.result+=f" Earned the {gym.badge}!"
            conf["badges"]=badges
        if rewards:battle.result=(battle.result or "")+" Reward: "+"; ".join(rewards)+"."
        await self.config.user_from_id(battle.user_id).set(conf)
    @staticmethod
    def ball_inventory(conf,ball_key):
        if ball_key=="poke_ball":return int(conf.get("balls",0))
        return int(conf.get("items",{}).get(ball_key,0))

    @staticmethod
    def consume_ball(conf,ball_key):
        if ball_key=="poke_ball":conf["balls"]=int(conf.get("balls",0))-1
        else:
            items=conf.setdefault("items",{});items[ball_key]=int(items.get(ball_key,0))-1

    async def open_battle_bag(self,interaction,eid):
        battle=self.battles.get(eid)
        if not battle or battle.state!="active":
            await interaction.response.send_message("This battle is unavailable.",ephemeral=True);return
        conf=await self.config.user(interaction.user).all();items=conf.get("items",{})
        inventory={"balls":int(conf.get("balls",0)),"great_ball":int(items.get("great_ball",0)),"ultra_ball":int(items.get("ultra_ball",0)),"potion":int(items.get("potion",0)),"revive":int(items.get("revive",0))}
        await interaction.response.edit_message(view=BagView(self,eid,inventory))

    async def open_battle_medicine(self,interaction,eid,item_key):
        battle=self.battles.get(eid)
        if not battle or battle.state!="active":await interaction.response.send_message("This battle is unavailable.",ephemeral=True);return
        if battle.needs_switch:await interaction.response.send_message("Switch Pokemon first.",ephemeral=True);return
        conf=await self.config.user(interaction.user).all()
        if int(conf.get("items",{}).get(item_key,0))<1:await interaction.response.send_message(f"You have no {item_key.title()}s.",ephemeral=True);return
        eligible=[]
        for pokemon in battle.party[:6]:
            hp=int(battle.party_hp.get(pokemon.instance_id,0));maximum=battle.max_hp(pokemon)
            if (item_key=="potion" and 0<hp<maximum) or (item_key=="revive" and hp<=0):eligible.append(pokemon)
        if not eligible:
            message="No conscious Pokemon needs healing." if item_key=="potion" else "No Pokemon has fainted."
            await interaction.response.send_message(message,ephemeral=True);return
        await interaction.response.edit_message(view=MedicineView(self,eid,item_key))

    async def use_battle_item(self,interaction,eid,item_key,index):
        battle=self.battles.get(eid)
        if not battle:await interaction.response.send_message("This battle is unavailable.",ephemeral=True);return
        async with self.lock(("battle",eid)),self.lock(("user",battle.user_id)):
            battle=self.battles.get(eid)
            if not battle or battle.state!="active":await interaction.response.send_message("This encounter is over.",ephemeral=True);return
            if battle.needs_switch:await interaction.response.send_message("Switch Pokemon first.",ephemeral=True);return
            if item_key not in {"potion","revive"} or not 0<=index<len(battle.party):await interaction.response.send_message("That item choice is unavailable.",ephemeral=True);return
            conf=await self.config.user(interaction.user).all();items=conf.setdefault("items",{})
            if int(items.get(item_key,0))<1:await interaction.response.send_message(f"You have no {item_key.title()}s.",ephemeral=True);return
            pokemon=battle.party[index];maximum=battle.max_hp(pokemon);current=int(battle.party_hp.get(pokemon.instance_id,0))
            if item_key=="potion":
                if current<=0 or current>=maximum:await interaction.response.send_message("That Pokemon cannot use a Potion now.",ephemeral=True);return
                healed=min(20,maximum-current);battle.party_hp[pokemon.instance_id]=current+healed;line=f"Used a Potion on {SPECIES[pokemon.species_id].name}. Restored {healed} HP."
            else:
                if current>0:await interaction.response.send_message("That Pokemon has not fainted.",ephemeral=True);return
                restored=max(1,maximum//2);battle.party_hp[pokemon.instance_id]=restored;battle.party_status[pokemon.instance_id]="";battle.party_status_turns[pokemon.instance_id]=0;line=f"Used a Revive on {SPECIES[pokemon.species_id].name}. Restored {restored} HP."
            if pokemon.instance_id==battle.player.instance_id:
                battle.player_hp=battle.party_hp[pokemon.instance_id];battle.player_status=battle.party_status.get(pokemon.instance_id,"");battle.player_status_turns=battle.party_status_turns.get(pokemon.instance_id,0)
            items[item_key]=int(items.get(item_key,0))-1;battle._wild_response();battle.last_action=f"{line} {battle.last_action}";battle._record(f"item:{item_key}:{pokemon.instance_id}")
            done=battle.state!="active"
            if done:
                rewards=self.record_battle_result(conf,battle)
                if rewards:battle.result=(battle.result or "")+" Reward: "+"; ".join(rewards)+"."
            self.apply_battle_party(conf,battle);conf["items"]=items;await self.config.user(interaction.user).set(conf);await self.save_battle(battle)
            if done:await self.clear_guild(battle.guild_id,eid)
            embed,files=await self.rendered_battle(battle)
            await interaction.response.edit_message(embed=embed,attachments=files,view=None if done else BattleView(self,eid))

    async def throw_ball(self,i,eid,ball_key="poke_ball"):
        battle=self.battles.get(eid)
        if not battle:
            await i.response.send_message("This battle is unavailable.",ephemeral=True);return
        async with self.lock(("battle",eid)), self.lock(("user",battle.user_id)):
            battle=self.battles.get(eid)
            if not battle:
                await i.response.send_message("This battle is unavailable.",ephemeral=True);return
            if battle.state!="active" or battle.needs_switch:
                await i.response.send_message("Switch Pokémon first." if battle.needs_switch else "This encounter is over.",ephemeral=True);return
            if battle.battle_kind=="gym":
                await i.response.send_message("Poké Balls cannot be used in a Gym battle.",ephemeral=True);return
            conf=await self.config.user(i.user).all();tx_key=f"{eid}:{battle.rolls}";tx=conf["transactions"].get(tx_key,{})
            if len(conf["collection"])>=MAX_COLLECTION and not tx.get("settled"):
                await i.response.send_message(f"Your {MAX_BOXES} boxes are full.",ephemeral=True);return
            ball_names={"poke_ball":"Poké Balls","great_ball":"Great Balls","ultra_ball":"Ultra Balls"}
            if ball_key not in ball_names:await i.response.send_message("That Poké Ball is unavailable.",ephemeral=True);return
            if self.ball_inventory(conf,ball_key)<1 and not tx.get("ball_charged"):await i.response.send_message(f"You have no {ball_names[ball_key]}.",ephemeral=True);return
            if not tx.get("ball_charged"):
                self.consume_ball(conf,ball_key);tx["ball_charged"]=True;tx["ball_key"]=ball_key;conf["transactions"][tx_key]=tx
            first_registration=False
            try:caught=battle.throw_ball(ball_key)
            except BattleError as e:await i.response.send_message(str(e),ephemeral=True);return
            if caught:
                self.apply_battle_party(conf,battle)
                identity=f"catch-{battle.user_id}-{eid}";pokemon=battle.caught(identity)
                if not any(p["instance_id"]==identity for p in conf["collection"]):conf["collection"].append(pokemon.raw())
                seen={int(value) for value in conf.get("pokedex_seen",[])}
                caught_ids={int(value) for value in conf.get("pokedex_caught",[])}
                first_registration=first_pokedex_registration(conf,pokemon.species_id)
                seen.add(pokemon.species_id);caught_ids.add(pokemon.species_id)
                conf["pokedex_seen"]=sorted(seen);conf["pokedex_caught"]=sorted(caught_ids)
                rewards=self.record_battle_result(conf,battle)
                if rewards:battle.result=(battle.result or "")+" Reward: "+"; ".join(rewards)+"."
                if len(conf["party"])<6 and identity not in conf["party"]:conf["party"].append(identity)
                tx["settled"]=True;tx["caught_id"]=identity;conf["transactions"][tx_key]=tx
            if battle.state!="active" and not caught:
                rewards=self.record_battle_result(conf,battle)
                if rewards:battle.result=(battle.result or "")+" Reward: "+"; ".join(rewards)+"."
                self.apply_battle_party(conf,battle)
            while len(conf["transactions"])>100:conf["transactions"].pop(next(iter(conf["transactions"])))
            await self.config.user(i.user).set(conf);await self.save_battle(battle)
            if battle.state!="active":await self.clear_guild(battle.guild_id,eid)
            embed,files=await self.rendered_battle(battle)
            await i.response.edit_message(embed=embed,attachments=files,view=None if battle.state!="active" else BattleView(self,eid))
            if first_registration:
                registration,registration_files=await self.rendered_pokedex_registration(pokemon,battle.trainer_name)
                await i.followup.send(embed=registration,files=registration_files)
            if battle.state!="active":await self.send_progression(i,battle)
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
    @commands.group(name="pokemon",aliases=["pkmn","poke"],invoke_without_command=True)
    async def pokemon(self,ctx):
        """Play the global Pokémon catching and battle game.

        New trainers can choose a first partner here. Server administration is kept separately under `[p]pokemonset`.
        """
        conf=await self.config.user(ctx.author).all()
        if not conf["starter_chosen"] and not conf["collection"]:
            setup_hint=f"{ctx.clean_prefix}pokemonset";embed,files=await self.rendered_starter_choice(ctx.author,setup_hint=setup_hint)
            embed.add_field(name="How to begin",value="Choose a partner, find a wild encounter, battle it, then use a Poké Ball to catch it.",inline=False)
            await ctx.send(embed=embed,files=files,view=StarterView(self,ctx.author.id,setup_hint=setup_hint))
            return
        await ctx.send_help()
    @staticmethod
    def starter_embed(user,selected=0,setup_hint=None):
        starter_ids=(1,4,7);sid=starter_ids[int(selected)%len(starter_ids)];species=SPECIES[sid]
        embed=discord.Embed(title=f"Choose {species.name}?",description="Use ◀ and ▶ to view each starter, then press **Choose**. Your first partner can only be selected once.",color=discord.Color.green())
        embed.add_field(name="Starter",value=f"{int(selected)%len(starter_ids)+1} of {len(starter_ids)}",inline=True)
        embed.set_image(url=sprite(sid))
        trainer=getattr(user,"display_name",getattr(user,"name",str(user)))
        footer=f"Trainer: {trainer}"
        embed.set_footer(text=footer)
        return embed

    async def rendered_starter_choice(self,user,selected=0,setup_hint=None):
        starter_ids=(1,4,7);sid=starter_ids[int(selected)%len(starter_ids)]
        embed=self.starter_embed(user,selected,setup_hint)
        try:
            image=await self.renderer.starter_choice(sid);embed.set_image(url="attachment://starter-choice.png")
            return embed,[discord.File(image,filename="starter-choice.png")]
        except RenderError:
            log.exception("Starter selection rendering failed")
            return embed,[]

    async def grant_starter(self,user,sid):
        async with self.lock(("user",user.id)):
            section=self.config.user(user);conf=await section.all()
            if conf["starter_chosen"] or conf["collection"]:
                return None
            pokemon=OwnedPokemon.create(__import__("uuid").uuid4().hex,sid,1,seed=random.SystemRandom().randrange(1,2**31))
            conf["collection"]=[pokemon.raw()];conf["party"]=[pokemon.instance_id];conf["starter_chosen"]=True;conf["pokedex_seen"]=[sid];conf["pokedex_caught"]=[sid];conf["pokedex_stats"]={str(sid):{"seen":1,"battled":0,"defeated":0,"caught":1,"escaped":0}}
            await section.set(conf)
            return pokemon

    async def rendered_starter(self,pokemon,trainer_name,encounter_id=None):
        species=SPECIES[pokemon.species_id];symbol={"female":"♀","male":"♂","genderless":"—"}.get(pokemon.gender,"?")
        description="Professor Oak entrusted this Pokémon to you. Your journey begins now."
        if encounter_id is not None:description+=" Press **Encounter** again when you are ready to battle."
        embed=discord.Embed(title=f"{trainer_name} received {species.name}!",description=description,color=discord.Color.green())
        embed.add_field(name="Partner",value=f"{species.name} · {symbol} · Lv. {pokemon.level}",inline=False)
        try:
            image=await self.renderer.starter(pokemon,trainer_name);embed.set_image(url="attachment://starter.png")
            return embed,[discord.File(image,filename="starter.png")]
        except RenderError:
            log.exception("Starter reveal rendering failed");embed.set_image(url=sprite(pokemon.species_id,shiny=pokemon.shiny));return embed,[]

    async def choose_starter(self,interaction,sid,encounter_id=None):
        pokemon=await self.grant_starter(interaction.user,sid)
        if pokemon is None:
            await interaction.response.edit_message(content="You already chose a starter.",embed=None,view=None)
            return
        trainer_name=getattr(interaction.user,"display_name",getattr(interaction.user,"name","Trainer"))
        embed,files=await self.rendered_starter(pokemon,trainer_name,encounter_id)
        await interaction.response.edit_message(content=None,embed=embed,attachments=files,view=None)

    @pokemon.command(name="starter")
    async def starter(self,ctx,choice:str=None):
        """Choose or view your first partner Pokémon."""
        choices={"bulbasaur":1,"charmander":4,"squirtle":7}
        if choice is None:
            embed,files=await self.rendered_starter_choice(ctx.author)
            await ctx.send(embed=embed,files=files,view=StarterView(self,ctx.author.id))
            return
        sid=choices.get(choice.casefold())
        if not sid:
            embed,files=await self.rendered_starter_choice(ctx.author)
            await ctx.send("Choose Bulbasaur, Charmander, or Squirtle.",embed=embed,files=files,view=StarterView(self,ctx.author.id))
            return
        pokemon=await self.grant_starter(ctx.author,sid)
        if pokemon is None:await ctx.send("You already chose a starter.");return
        trainer_name=getattr(ctx.author,"display_name",getattr(ctx.author,"name","Trainer"))
        embed,files=await self.rendered_starter(pokemon,trainer_name);await ctx.send(embed=embed,files=files)

    @staticmethod
    def sorted_collection(conf):
        return sorted(conf.get("collection",[]),key=lambda raw:((raw.get("nickname") or SPECIES[raw["species_id"]].name).casefold(),raw["species_id"],raw["instance_id"]))

    async def rendered_collection(self,user,page=1):
        conf=await self.config.user(user).all();ordered=self.sorted_collection(conf);total=len(ordered)
        pages=max(1,(total+COLLECTION_PAGE_SIZE-1)//COLLECTION_PAGE_SIZE);page=max(1,min(int(page),pages));start=(page-1)*COLLECTION_PAGE_SIZE
        raw_items=ordered[start:start+COLLECTION_PAGE_SIZE];items=[OwnedPokemon.from_raw(raw) for raw in raw_items]
        numbered=list(enumerate(raw_items,start+1));lines=[]
        for number,raw in numbered:
            species=SPECIES[raw["species_id"]];marker="Shiny " if raw.get("shiny") else ""
            lines.append(f"{number}. {marker}{raw.get('nickname') or species.name} · Lv. {raw['level']}")
        trainer=getattr(user,"display_name",getattr(user,"name",str(user)))
        embed=discord.Embed(title=f"{trainer}'s Collection · {page}/{pages}",description="\n".join(lines) or "Empty",color=discord.Color.gold())
        embed.set_footer(text=f"{total}/{MAX_COLLECTION} Pokémon · Select one below to manage your party")
        try:
            image=await self.renderer.collection_card(items,page,pages,total,trainer);embed.set_image(url="attachment://collection.png")
            files=[discord.File(image,filename="collection.png")]
        except RenderError:
            log.exception("Collection card rendering failed");files=[]
        return embed,files,page,pages,numbered

    @pokemon.command(name="collection",aliases=["box"])
    async def collection(self,ctx,page:int=1):
        """Browse and manage Pokémon in your global collection."""
        conf=await self.config.user(ctx.author).all()
        if not conf["collection"]:await ctx.send("Choose a starter first.");return
        embed,files,page,pages,items=await self.rendered_collection(ctx.author,page)
        await ctx.send(embed=embed,files=files,view=CollectionBrowserView(self,ctx.author.id,page,pages,items))

    async def collection_party_choice(self,interaction,identity):
        conf=await self.config.user(interaction.user).all();owned={raw["instance_id"]:raw for raw in conf.get("collection",[])};raw=owned.get(identity)
        if not raw:await interaction.response.send_message("That Pokémon is no longer in your collection.",ephemeral=True);return
        name=raw.get("nickname") or SPECIES[raw["species_id"]].name
        await interaction.response.send_message(f"Where should **{name}** go?",view=PartyPlacementView(self,interaction.user.id,identity,list(conf.get("party",[])),owned),ephemeral=True)

    async def place_collection_pokemon(self,interaction,identity,target=None):
        async with self.lock(("user",interaction.user.id)):
            conf=await self.config.user(interaction.user).all();owned={raw["instance_id"]:raw for raw in conf.get("collection",[])}
            if identity not in owned:await interaction.response.edit_message(content="That Pokémon is no longer in your collection.",view=None);return
            party=list(conf.get("party",[]));name=owned[identity].get("nickname") or SPECIES[owned[identity]["species_id"]].name
            if target==identity:await interaction.response.edit_message(content=f"**{name}** already occupies that slot.",view=None);return
            party=[value for value in party if value!=identity]
            if target is None:
                if len(party)>=6:await interaction.response.edit_message(content="Your party is full. Choose a slot to replace.",view=None);return
                party.append(identity);message=f"Added **{name}** to party slot {len(party)}."
            elif target in party:
                slot=party.index(target);party[slot]=identity;message=f"Placed **{name}** in party slot {slot+1}."
            else:await interaction.response.edit_message(content="That party slot is no longer available.",view=None);return
            conf["party"]=party;await self.config.user(interaction.user).set(conf)
        await interaction.response.edit_message(content=message,view=None)

    @pokemon.group(name="party",invoke_without_command=True)
    async def party(self,ctx):
        """View the Pokémon in your active party."""
        conf=await self.config.user(ctx.author).all();owned={p["instance_id"]:p for p in conf.get("collection",[])};party=[OwnedPokemon.from_raw(owned[identity]) for identity in conf.get("party",[]) if identity in owned]
        lines=[]
        for slot,item in enumerate(party,1):
            maximum=pokemon_max_hp(item);current=maximum if item.current_hp is None else item.current_hp;name=item.nickname or SPECIES[item.species_id].name
            lines.append(f"{slot}. {name} · Lv. {item.level} · HP {current}/{maximum}")
        trainer=getattr(ctx.author,"display_name",getattr(ctx.author,"name",str(ctx.author)))
        embed=discord.Embed(title=f"{trainer}'s Party",description="\n".join(lines) or "Empty",color=discord.Color.gold())
        try:
            image=await self.renderer.party_card(party,trainer);embed.set_image(url="attachment://party.png");files=[discord.File(image,filename="party.png")]
        except RenderError:
            log.exception("Party card rendering failed");files=[]
        await ctx.send(embed=embed,files=files)

    @party.command(name="add")
    async def party_add(self,ctx,identifier:str,slot:int=None):
        """Add a caught Pokémon to your active party."""
        async with self.lock(("user",ctx.author.id)):
            conf=await self.config.user(ctx.author).all()
            ordered=self.sorted_collection(conf);matches=[]
            if identifier.isdigit() and 1<=int(identifier)<=len(ordered):matches=[ordered[int(identifier)-1]["instance_id"]]
            else:
                wanted=identifier.casefold();matches=[raw["instance_id"] for raw in ordered if (raw.get("nickname") or SPECIES[raw["species_id"]].name).casefold()==wanted]
            if len(matches)!=1:await ctx.send("Use a collection number or unique Pokémon name.");return
            identity=matches[0];party=[value for value in conf["party"] if value!=identity]
            if slot is None:party.append(identity)
            elif 1<=slot<=6:
                party.insert(min(slot-1,len(party)),identity)
            else:await ctx.send("Slot must be 1–6.");return
            conf["party"]=party[:6];await self.config.user(ctx.author).set(conf)
        await ctx.send("Party updated.")
    @party.command(name="remove")
    async def party_remove(self,ctx,slot:int):
        """Remove a Pokémon from your active party."""
        async with self.lock(("user",ctx.author.id)):
            conf=await self.config.user(ctx.author).all()
            if not 1<=slot<=len(conf["party"]):await ctx.send("That party slot is empty.");return
            conf["party"].pop(slot-1);await self.config.user(ctx.author).set(conf)
        await ctx.send("Party updated.")
    @staticmethod
    def find_owned(conf,identifier):
        owned={item["instance_id"]:item for item in conf["collection"]}
        if str(identifier).isdigit():
            slot=int(identifier)
            if 1<=slot<=len(conf["party"]):return owned.get(conf["party"][slot-1])
        wanted=str(identifier).casefold();matches=[raw for key,raw in owned.items() if key.startswith(str(identifier)) or (raw.get("nickname") or SPECIES[raw["species_id"]].name).casefold()==wanted]
        return matches[0] if len(matches)==1 else None

    @pokemon.command(name="moves")
    async def moves(self,ctx):
        """Resume an unfinished move-learning choice."""
        conf=await self.config.user(ctx.author).all()
        pokemon=next((OwnedPokemon.from_raw(raw) for raw in conf.get("collection",[]) if raw.get("pending_moves")),None)
        if not pokemon:await ctx.send("None of your Pokémon are waiting to learn a move.");return
        move=pokemon.pending_moves[0];embed,files=await self.rendered_progression(pokemon,move_key=move,pending=True)
        await ctx.send(embed=embed,files=files,view=MoveLearnView(self,ctx.author.id,pokemon,move))

    @pokemon.command(name="bag")
    async def pokemon_bag(self,ctx):
        """View your available medicine and items."""
        conf=await self.config.user(ctx.author).all();items=conf.get("items",{})
        await ctx.send(f"**Poké Balls**\nPoké Ball: **{int(conf.get('balls',0))}** · Great Ball: **{int(items.get('great_ball',0))}** · Ultra Ball: **{int(items.get('ultra_ball',0))}**\n**Medicine**\nPotion: **{int(items.get('potion',0))}** · Revive: **{int(items.get('revive',0))}**")

    @pokemon.group(name="use",invoke_without_command=True)
    async def pokemon_use(self,ctx):
        """Use an item on one of your Pokémon."""
        await ctx.send_help()

    @pokemon_use.command(name="potion")
    async def use_potion(self,ctx,identifier:str):
        """Use a Potion to restore a Pokémon’s HP."""
        async with self.lock(("user",ctx.author.id)):
            conf=await self.config.user(ctx.author).all();raw=self.find_owned(conf,identifier)
            if not raw:await ctx.send("Choose a party slot or unique Pokémon name.");return
            pokemon=OwnedPokemon.from_raw(raw);maximum=pokemon_max_hp(pokemon)
            current=maximum if pokemon.current_hp is None else int(pokemon.current_hp)
            if current<=0:await ctx.send("That Pokémon has fainted. Use a Revive first.");return
            if current>=maximum:await ctx.send("That Pokémon already has full HP.");return
            items=conf.get("items",{})
            if int(items.get("potion",0))<1:await ctx.send("You have no Potions.");return
            items["potion"]=int(items.get("potion",0))-1;conf["items"]=items
            pokemon.current_hp=min(maximum,current+20);raw.update(pokemon.raw())
            await self.config.user(ctx.author).set(conf)
        await ctx.send(f"{SPECIES[pokemon.species_id].name} recovered {pokemon.current_hp-current} HP ({pokemon.current_hp}/{maximum}).")

    @pokemon_use.command(name="revive")
    async def use_revive(self,ctx,identifier:str):
        """Use a Revive on a fainted Pokémon."""
        async with self.lock(("user",ctx.author.id)):
            conf=await self.config.user(ctx.author).all();raw=self.find_owned(conf,identifier)
            if not raw:await ctx.send("Choose a party slot or unique Pokémon name.");return
            pokemon=OwnedPokemon.from_raw(raw);maximum=pokemon_max_hp(pokemon)
            current=maximum if pokemon.current_hp is None else int(pokemon.current_hp)
            if current>0:await ctx.send("That Pokémon has not fainted.");return
            items=conf.get("items",{})
            if int(items.get("revive",0))<1:await ctx.send("You have no Revives.");return
            items["revive"]=int(items.get("revive",0))-1;conf["items"]=items
            pokemon.current_hp=max(1,maximum//2);pokemon.status="";pokemon.status_turns=0;raw.update(pokemon.raw())
            await self.config.user(ctx.author).set(conf)
        await ctx.send(f"{SPECIES[pokemon.species_id].name} was revived with {pokemon.current_hp}/{maximum} HP.")

    @pokemon.command(name="center")
    @commands.guild_only()
    async def pokemon_center(self,ctx):
        """Heal your party at this server’s Pokémon Center."""
        center=await self.config.guild(ctx.guild).center_channel()
        if not center:await ctx.send("This server has not configured a Pokémon Center.");return
        if ctx.channel.id!=int(center):await ctx.send(f"Visit <#{center}> to use this server's Pokémon Center.");return
        async with self.lock(("user",ctx.author.id)):
            conf=await self.config.user(ctx.author).all();last=conf.get("center_last_at")
            now=datetime.now(timezone.utc)
            if last:
                try:remaining=300-(now-datetime.fromisoformat(last)).total_seconds()
                except (TypeError,ValueError):remaining=0
                if remaining>0:await ctx.send(f"The Pokémon Center will be ready again in {int(remaining)+1}s.");return
            party=set(conf["party"]);healed=0
            for raw in conf["collection"]:
                if raw["instance_id"] not in party:continue
                pokemon=OwnedPokemon.from_raw(raw);pokemon.current_hp=pokemon_max_hp(pokemon);pokemon.status="";pokemon.status_turns=0
                pokemon.move_pp={key:MOVES[key].pp for key in pokemon.moves};raw.update(pokemon.raw());healed+=1
            conf["center_last_at"]=now.isoformat();await self.config.user(ctx.author).set(conf)
        await ctx.send(f"Your party is fully restored. ({healed} Pokémon)")

    async def selected_pokedex_style(self,user):
        preference=await self.config.user(user).pokedex_style()
        if preference=="default":
            preference=await self.config.pokedex_default_style()
        return resolve_style(preference).key

    async def set_pokedex_style(self,user,style):
        if style not in POKEDEX_STYLES:
            raise ValueError("Unknown Pokédex style")
        await self.config.user(user).pokedex_style.set(style)

    @pokemon.command(name="pokedex",aliases=["dex"])
    async def pokedex(self,ctx,page:int=1):
        """Browse the Pokémon you have seen and caught."""
        conf=await self.config.user(ctx.author).all()
        session=PokedexSession(
            user_id=ctx.author.id,
            seen={int(value) for value in conf.get("pokedex_seen",[])},
            caught={int(value) for value in conf.get("pokedex_caught",[])},
            style=await self.selected_pokedex_style(ctx.author),
            page=max(0,page-1),
            stats=migrated_pokedex_stats(conf),
        )
        view=PokedexView(self,session)
        view.message=await ctx.send(embed=render_pokedex(session),view=view)

    @pokemon.command(name="pokedexstyle",aliases=["dexstyle"])
    async def pokedex_style(self,ctx,style:str=None):
        """Choose your Pokédex display style, or follow the bot default."""
        preference=await self.config.user(ctx.author).pokedex_style()
        active=await self.selected_pokedex_style(ctx.author)
        if style is None:
            choices=", ".join(["default",*POKEDEX_STYLES])
            await ctx.send(f"Pokédex style: **{preference}** (currently **{active}**). Choices: {choices}.")
            return
        style=style.casefold()
        if style=="default":
            await self.config.user(ctx.author).pokedex_style.set("default")
            active=await self.selected_pokedex_style(ctx.author)
            await ctx.send(f"Pokédex style now follows the bot default (**{active}**).")
            return
        if style not in POKEDEX_STYLES:
            await ctx.send("Unknown style. Choose: "+", ".join(POKEDEX_STYLES)+".")
            return
        await self.set_pokedex_style(ctx.author,style)
        await ctx.send(f"Pokédex style set to **{POKEDEX_STYLES[style].label}**.")

    @pokemon.group(name="gym",invoke_without_command=True)
    async def gym(self,ctx):
        """View your ordered Kanto Gym progress."""
        conf=await self.config.user(ctx.author).all()
        await ctx.send(embed=gym_status_embed(ctx.author,conf))

    @gym.command(name="challenge")
    @commands.guild_only()
    async def gym_challenge(self,ctx):
        """Challenge the next Kanto Gym Leader's signature Pokémon."""
        async with self.lock(("user",ctx.author.id)),self.lock(("spawn",ctx.guild.id)):
            if any(b.user_id==ctx.author.id and b.state=="active" for b in self.battles.values()):
                await ctx.send("Finish your active battle first.");return
            if await self.config.guild(ctx.guild).active_encounter():
                await ctx.send("This server already has an active encounter or Gym battle.");return
            conf=await self.config.user(ctx.author).all();gym=next_gym(conf.get("badges",[]))
            if not gym:
                await ctx.send("You already earned all eight Kanto badges.");return
            if not conf["party"]:
                await ctx.send("Choose a starter and prepare a party first.");return
            collection={item["instance_id"]:item for item in conf["collection"]}
            party=[OwnedPokemon.from_raw(collection[key]) for key in conf["party"] if key in collection]
            if not party:
                await ctx.send("Your active party needs repair.");return
            if not any((item.current_hp if item.current_hp is not None else pokemon_max_hp(item))>0 for item in party):
                await ctx.send("Your party has fainted. Visit a Pokémon Center or use a Revive.");return
            async with self.lock("encounters"):
                eid=await self.config.next_encounter();await self.config.next_encounter.set(eid+1)
            lead=party[0]
            battle=Battle(eid,ctx.author.id,ctx.guild.id,ctx.channel.id,0,lead,gym.species_id,gym.level,Battle.stat(lead,"hp"),1,seed=random.SystemRandom().randrange(1,2**31),battle_kind="gym",gym_key=gym.key,trainer_name=str(getattr(ctx.author,"display_name",getattr(ctx.author,"name","Trainer")))[:24])
            battle.initialize_party(party);battle.wild_hp=battle.wild_max_hp
            seen={int(value) for value in conf.get("pokedex_seen",[])};seen.add(gym.species_id);conf["pokedex_seen"]=sorted(seen);self.pokedex_stat(conf,gym.species_id)["seen"]+=1;await self.config.user(ctx.author).set(conf)
            self.battles[eid]=battle
            try:
                embed,files=await self.rendered_battle(battle)
                message=await ctx.send(embed=embed,files=files,view=BattleView(self,eid))
            except Exception:
                self.battles.pop(eid,None)
                raise
            battle.message_id=message.id
            seconds=await self.config.guild(ctx.guild).battle_timeout()
            raw={"kind":"gym","gym_key":gym.key,"state":"battle","guild_id":ctx.guild.id,"channel_id":ctx.channel.id,"message_id":message.id,"created_at":datetime.now(timezone.utc).isoformat(),"expires_at":(datetime.now(timezone.utc)+timedelta(seconds=seconds)).isoformat(),"battle":battle.raw()}
            try:
                await self.put_encounter(eid,raw)
                await self.config.guild(ctx.guild).active_encounter.set(eid)
            except Exception:
                self.battles.pop(eid,None)
                try:await message.edit(content="The Gym challenge could not be saved. Please try again.",view=None)
                except discord.HTTPException:pass
                raise

    @pokemon.command(name="profile")
    async def profile(self,ctx,user:discord.Member=None):
        """View your or another member’s trainer card and badge case."""
        target=user or ctx.author;conf=await self.config.user(target).all()
        embed,files=await self.rendered_trainer_card(target,conf)
        await ctx.send(embed=embed,files=files)

    @pokemon.command(name="profilestyle",aliases=["cardstyle"])
    async def profile_style(self,ctx,style:str=None):
        """Choose the Retro or Gold style for your trainer card."""
        current=await self.config.user(ctx.author).trainer_card_style()
        if style is None:
            await ctx.send(f"Trainer-card style: **{current}**. Choices: retro, gold.");return
        style=style.casefold()
        if style not in {"retro","gold"}:
            await ctx.send("Unknown style. Choose: retro or gold.");return
        await self.config.user(ctx.author).trainer_card_style.set(style)
        await ctx.send(f"Trainer-card style set to **{style}**.")
    @commands.group(name="pokemonset",aliases=["pokeset","pkmnset"],invoke_without_command=True)
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def pokemon_set(self,ctx):
        """Configure Pokémon without cluttering player commands.

        Server administrators control channels and slower local pacing. Bot-owner-only subcommands control global fairness and availability.
        """
        await ctx.send_help()
    @pokemon_set.command(name="channel")
    async def set_channel(self,ctx,channel:discord.TextChannel):
        """Enable wild encounters in a channel."""
        section=self.config.guild(ctx.guild);channels=await section.channels();was_enabled=await section.enabled();already=channel.id in channels
        if not already:channels.append(channel.id)
        await section.channels.set(channels);await section.enabled.set(True)
        if await section.spawn_mode()=="timed" and not await section.next_spawn_at():
            minutes=max(30,int(await section.timer_minutes()));await section.next_spawn_at.set((datetime.now(timezone.utc)+timedelta(minutes=minutes)).isoformat())
        if already and was_enabled:message=f"Wild encounters were already enabled in {channel.mention}."
        elif already:message=f"Wild encounters re-enabled in {channel.mention}."
        else:message=f"Wild encounters enabled in {channel.mention}."
        await ctx.send(message)
    @pokemon_set.command(name="removechannel")
    async def remove_channel(self,ctx,channel:discord.TextChannel):
        """Stop wild encounters in a channel."""
        channels=await self.config.guild(ctx.guild).channels()
        if channel.id in channels:channels.remove(channel.id)
        await self.config.guild(ctx.guild).channels.set(channels);await ctx.send(f"Removed {channel.mention}.")
    @pokemon_set.command(name="center")
    async def set_center(self,ctx,channel:discord.TextChannel):
        """Designate the server Pokémon Center."""
        await self.config.guild(ctx.guild).center_channel.set(channel.id)
        await ctx.send(f"{channel.mention} is now this server's Pokémon Center.")

    @pokemon_set.command(name="removecenter")
    async def remove_center(self,ctx):
        """Remove the server Pokémon Center."""
        await self.config.guild(ctx.guild).center_channel.set(None)
        await ctx.send("This server's Pokémon Center was removed.")

    @pokemon_set.command(name="status",aliases=["settings"])
    async def spawn_status(self,ctx):
        """Show channels, schedule, and effective server settings."""
        conf=await self.config.guild(ctx.guild).all();policy=await self.config.all();mode=conf.get("spawn_mode","timed")
        minimum,maximum,cooldown=bounded_pace(conf["threshold_min"],conf["threshold_max"],conf["spawn_cooldown"],policy)
        channels=", ".join(f"<#{value}>" for value in conf.get("channels",[])) or "None"
        center_id=conf.get("center_channel");center=f"<#{center_id}>" if center_id else "None";active=conf.get("active_encounter")
        if mode=="timed":
            minutes=max(30,int(conf.get("timer_minutes",60)));due_text="Scheduling now"
            try:due=datetime.fromisoformat(conf.get("next_spawn_at") or "")
            except (TypeError,ValueError):due=None
            if due:due_text=f"<t:{int(due.timestamp())}:R>"
            progress=f"Timer: **every {minutes} minutes** · Next encounter: {due_text}"
            next_spawn=f"Blocked by active encounter #{active}" if active else "A random configured channel will be selected"
        else:
            target=max(minimum,int(conf.get("threshold",minimum)));activity=max(0,int(self.activity.get(ctx.guild.id,conf.get("activity",0))))
            progress=f"Activity: **{activity}/{target}** points (new target range {minimum}–{maximum}) · Cooldown: **{cooldown}s**"
            next_spawn=f"Blocked by active encounter #{active}" if active else (f"Needs {target-activity} more activity points" if activity<target else "Ready on the next qualifying message")
        generations=effective_generations(conf.get("generations",[1]),policy.get("allowed_generations",[1]));generation_text=", ".join(map(str,generations))
        enabled=conf.get("enabled",False);encounter_minutes=int(policy.get("encounter_timeout",900))//60;battle_minutes=int(conf.get("battle_timeout",1800))//60
        rarity=policy.get("rarity_profile","friendly");specials="enabled" if policy.get("allow_special_species") else "event-only";expired_cards=conf.get("expired_card_mode","delete")
        await ctx.send(
            f"**Pokémon server settings**\nEnabled: **{enabled}** · Spawn mode: **{mode}**\nSpawn channels: {channels}\nPokémon Center: {center}\n"
            f"{progress}\nNext spawn: {next_spawn}\nEncounter lifetime: **{encounter_minutes}m** · Battle lifetime: **{battle_minutes}m**\n"
            f"Generations: **{generation_text}** · Rarity: **{rarity}** · Special species: **{specials}**\nExpired unattended cards: **{expired_cards}**\nCatalog species: **{len(SPECIES)}**"
        )

    @pokemon_set.command(name="expiredcards",aliases=["missedcards"])
    async def expired_cards(self,ctx,mode:str):
        """Delete expired wild cards or keep their dimmed result."""
        mode=mode.casefold()
        if mode not in {"delete","keep"}:await ctx.send("Choose delete or keep.");return
        await self.config.guild(ctx.guild).expired_card_mode.set(mode)
        await ctx.send("Expired unattended encounter cards will be deleted." if mode=="delete" else "Expired unattended encounter cards will remain as dimmed got-away cards.")

    @pokemon_set.command(name="mode")
    async def spawn_mode(self,ctx,mode:str):
        """Choose timed spawning or the optional activity system."""
        mode=mode.casefold()
        if mode not in {"timed","activity"}:await ctx.send("Choose timed or activity.");return
        section=self.config.guild(ctx.guild);await section.spawn_mode.set(mode)
        if mode=="timed":
            minutes=max(30,int(await section.timer_minutes()));due=datetime.now(timezone.utc)+timedelta(minutes=minutes);await section.next_spawn_at.set(due.isoformat())
            await ctx.send(f"Timed encounters enabled every {minutes} minutes. The next encounter is <t:{int(due.timestamp())}:R>.")
        else:
            await section.next_spawn_at.set(None);await ctx.send("Activity-based encounters enabled. Timed spawning is paused.")

    @pokemon_set.command(name="timer")
    async def spawn_timer(self,ctx,minutes:int):
        """Set the timed interval; bot owners may use 30 minutes."""
        owner=await self.bot.is_owner(ctx.author);minimum=30 if owner else 60
        if not minimum<=minutes<=10080:
            limit="30–10080 minutes" if owner else "60–10080 minutes; only the bot owner may use 30–59"
            await ctx.send(f"Use {limit}.");return
        section=self.config.guild(ctx.guild);await section.timer_minutes.set(minutes)
        if await section.spawn_mode()=="timed":
            due=datetime.now(timezone.utc)+timedelta(minutes=minutes);await section.next_spawn_at.set(due.isoformat())
            await ctx.send(f"Timed encounters set to every {minutes} minutes. The next encounter is <t:{int(due.timestamp())}:R>.")
        else:await ctx.send(f"Saved a {minutes}-minute timer. It will apply when timed mode is enabled.")

    @pokemon_set.command(name="pace")
    async def pace(self,ctx,setting:str):
        """Choose a preset encounter pace."""
        setting=setting.casefold()
        if setting not in PACE:
            await ctx.send("Choose active, normal, or relaxed.");return
        policy=await self.config.all();minimum,maximum,cooldown=bounded_pace(*PACE[setting],policy)
        await self.config.guild(ctx.guild).threshold_min.set(minimum)
        await self.config.guild(ctx.guild).threshold_max.set(maximum)
        await self.config.guild(ctx.guild).threshold.set(random.SystemRandom().randrange(minimum,maximum+1))
        await self.config.guild(ctx.guild).spawn_cooldown.set(cooldown)
        await self.config.guild(ctx.guild).pace.set(setting)
        await ctx.send(f"Encounter pace set to {setting}: {minimum}–{maximum} activity points, {cooldown}s cooldown.")

    @pokemon_set.command(name="threshold")
    async def threshold(self,ctx,minimum:int,maximum:int):
        """Set a slower custom activity threshold."""
        policy=await self.config.all();floor=policy["minimum_threshold"]
        if not floor<=minimum<=maximum<=500:await ctx.send(f"Use {floor}–500 with minimum <= maximum.");return
        await self.config.guild(ctx.guild).pace.set("custom")
        await self.config.guild(ctx.guild).threshold_min.set(minimum);await self.config.guild(ctx.guild).threshold_max.set(maximum)
        await self.config.guild(ctx.guild).threshold.set(random.SystemRandom().randrange(minimum,maximum+1));await ctx.send("Spawn threshold updated.")
    @pokemon_set.command(name="cooldown")
    async def cooldown(self,ctx,seconds:int):
        """Set a slower custom spawn cooldown."""
        policy=await self.config.all();floor=policy["minimum_cooldown"]
        if not floor<=seconds<=86400:await ctx.send(f"Use {floor}–86400 seconds.");return
        await self.config.guild(ctx.guild).pace.set("custom")
        await self.config.guild(ctx.guild).spawn_cooldown.set(seconds);await ctx.send("Spawn cooldown updated.")
    @pokemon_set.command(name="battleexpiry")
    async def battle_expiry(self,ctx,battle_minutes:int):
        """Set the server battle time limit."""
        if not 5<=battle_minutes<=1440:await ctx.send("Use 5–1440 minutes.");return
        await self.config.guild(ctx.guild).battle_timeout.set(battle_minutes*60);await ctx.send("Battle expiry updated. Wild encounter lifetime is controlled by the bot owner.")
    @pokemon_set.command(name="generations")
    async def generations(self,ctx,*values:int):
        """Choose from bot-enabled generations."""
        selected=sorted(set(values));allowed=await self.config.allowed_generations()
        if not selected or not set(selected)<=set(allowed):await ctx.send(f"Choose from bot-enabled generations: {', '.join(map(str,allowed))}.");return
        await self.config.guild(ctx.guild).generations.set(selected);await ctx.send(f"Enabled generations: {', '.join(map(str,selected))}.")
    @pokemon_set.command(name="spawn")
    async def force_spawn(self,ctx,target:str=None):
        """Trigger a test encounter; bot owners may use `shiny`."""
        force_shiny=(target or "").casefold()=="shiny"
        owner=await self.bot.is_owner(ctx.author)
        if force_shiny and not owner:
            await ctx.send("Only the bot owner can force a shiny encounter.");return
        if target and not force_shiny:
            try:channel=await commands.TextChannelConverter().convert(ctx,target)
            except commands.BadArgument:
                await ctx.send("Choose a text channel, or use `pokemonset spawn shiny` as the bot owner.");return
        else:channel=ctx.channel
        conf=await self.config.guild(ctx.guild).all()
        if conf["active_encounter"]:await ctx.send("This server already has an encounter.");return
        if not owner and conf["last_spawn_at"]:
            try:last=datetime.fromisoformat(conf["last_spawn_at"])
            except (TypeError,ValueError):last=None
            if conf.get("spawn_mode","timed")=="timed":
                due=last+timedelta(minutes=max(30,int(conf.get("timer_minutes",60)))) if last else None
                remaining=max(0,round((due-datetime.now(timezone.utc)).total_seconds())) if due else 0
                if remaining:await ctx.send(f"The server spawn timer is active for another {remaining}s.");return
            else:
                policy=await self.config.all();_,_,cooldown=bounded_pace(conf["threshold_min"],conf["threshold_max"],conf["spawn_cooldown"],policy)
                remaining=max(0,round((last+timedelta(seconds=cooldown)-datetime.now(timezone.utc)).total_seconds())) if last else 0
                if remaining:await ctx.send(f"The activity spawn cooldown is active for another {remaining}s.");return
        await self.spawn(channel,force_shiny=force_shiny)
    @pokemon_set.command(name="pokedexstyle")
    @commands.is_owner()
    async def default_pokedex_style(self,ctx,style:str=None):
        """Choose the default Pokédex style for users following the default."""
        current=resolve_style(await self.config.pokedex_default_style()).key
        if style is None:
            await ctx.send(f"Default Pokédex style: **{current}**. Choices: "+", ".join(POKEDEX_STYLES)+".")
            return
        style=style.casefold()
        if style not in POKEDEX_STYLES:
            await ctx.send("Unknown style. Choose: "+", ".join(POKEDEX_STYLES)+".")
            return
        await self.config.pokedex_default_style.set(style)
        await ctx.send(f"Default Pokédex style set to **{POKEDEX_STYLES[style].label}**.")

    @pokemon_set.command(name="globalstatus")
    @commands.is_owner()
    async def global_status(self,ctx):
        """Show the bot-wide encounter policy."""
        policy=await self.config.all()
        await ctx.send(f"Encounter lifetime: {policy['encounter_timeout']//60}m\nMinimum threshold/cooldown: {policy['minimum_threshold']} points/{policy['minimum_cooldown']}s\nAllowed generations: {', '.join(map(str,policy['allowed_generations']))}\nRarity: {policy['rarity_profile']}\nSpecial species: {'enabled' if policy['allow_special_species'] else 'event-only'}")

    @pokemon_set.command(name="encountertime")
    @commands.is_owner()
    async def encounter_time(self,ctx,minutes:int):
        """Set the global wild encounter lifetime."""
        if not 1<=minutes<=1440:await ctx.send("Use 1–1440 minutes.");return
        await self.config.encounter_timeout.set(minutes*60);await ctx.send(f"Global wild encounter lifetime set to {minutes} minutes.")

    @pokemon_set.command(name="globallimits")
    @commands.is_owner()
    async def global_limits(self,ctx,minimum_threshold:int,minimum_cooldown:int):
        """Set global spawn-rate floors."""
        if not 5<=minimum_threshold<=500 or not 60<=minimum_cooldown<=86400:await ctx.send("Threshold: 5–500; cooldown: 60–86400 seconds.");return
        await self.config.minimum_threshold.set(minimum_threshold);await self.config.minimum_cooldown.set(minimum_cooldown);await ctx.send("Global spawn-rate floors updated. Servers may only use slower settings.")

    @pokemon_set.command(name="globalgenerations")
    @commands.is_owner()
    async def global_generations(self,ctx,*values:int):
        """Set bot-wide available generations."""
        selected=sorted(set(values))
        if not selected or any(value<1 or value>9 for value in selected):await ctx.send("Choose generations 1–9.");return
        await self.config.allowed_generations.set(selected);await ctx.send(f"Bot-wide generations: {', '.join(map(str,selected))}.")

    @pokemon_set.command(name="rarity")
    @commands.is_owner()
    async def rarity(self,ctx,profile:str):
        """Choose the global rarity profile."""
        profile=profile.casefold()
        if profile not in RARITY_PROFILES:await ctx.send("Choose friendly, standard, or challenging.");return
        await self.config.rarity_profile.set(profile);await ctx.send(f"Global encounter rarity set to {profile}.")

    @pokemon_set.command(name="specials")
    @commands.is_owner()
    async def specials(self,ctx,enabled:bool):
        """Allow or gate special species."""
        await self.config.allow_special_species.set(enabled);await ctx.send("Special species may appear normally." if enabled else "Legendary and mythical species are event-only.")

    @pokemon_set.command(name="catalogsync")
    @commands.is_owner()
    async def catalog_sync(self,ctx,generation:int):
        """Cache catalog data for a generation."""
        async with ctx.typing():
            try:count=await self.catalog.sync_generation(generation)
            except CatalogError as exc:await ctx.send(str(exc));return
        await ctx.send(f"Cached {count} generation {generation} species.")
    @pokemon_set.command(name="resetplayer")
    @commands.is_owner()
    async def reset_player(self,ctx,user:discord.Member,confirmation:str):
        """Reset one complete Pokémon profile.

        This permanently clears the trainer starter, collection, party, Pokédex, badges, inventory, and active Pokémon battle. The final argument must be `confirm`.
        """
        if confirmation.casefold()!="confirm":
            await ctx.send(f"This clears all Pokémon progress for {user.mention}. Run `{ctx.clean_prefix}pokemonset resetplayer {user.mention} confirm` to proceed.")
            return
        removed=await self.reset_player_data(user.id)
        for raw in removed:
            channel=self.bot.get_channel(int(raw.get("channel_id",0)))
            if channel:
                try:
                    message=await channel.fetch_message(int(raw["message_id"]));await message.edit(content="This battle ended because the trainer profile was reset.",view=None)
                except (discord.Forbidden,discord.NotFound,discord.HTTPException,KeyError,TypeError,ValueError):pass
        await ctx.send(f"Reset {user.mention}'s Pokémon profile. They can run `{ctx.clean_prefix}pokemon` to choose a new starter.")

    @pokemon_set.command(name="clear")
    async def clear_encounter(self,ctx):
        """End the active server encounter."""
        eid=await self.config.guild(ctx.guild).active_encounter();raw=None
        if eid:
            async with self.lock(("battle",eid)),self.lock(("encounter",eid)),self.lock("encounters"):
                encounters=await self.config.encounters();raw=encounters.pop(str(eid),None)
                await self.config.encounters.set(encounters);self.battles.pop(eid,None)
        await self.config.guild(ctx.guild).active_encounter.set(None)
        if raw and not raw.get("battle") and raw.get("kind")!="gym":await self.expire_unclaimed_message(raw)
        elif raw:
            channel=self.bot.get_channel(int(raw.get("channel_id",0)))
            if channel:
                try:
                    message=await channel.fetch_message(int(raw["message_id"]))
                    if raw.get("kind")=="gym":await message.edit(content="This Gym challenge was ended by server staff.",view=None)
                    else:
                        species=SPECIES.get(int(raw.get("species_id",0)));name=species.name if species else "Pokemon"
                        await message.edit(content=f"The wild {name} escaped.",view=None)
                except (discord.Forbidden,discord.NotFound,discord.HTTPException,KeyError,TypeError,ValueError):pass
        await ctx.send("Active encounter cleared.")
    @pokemon_set.command(name="disable")
    async def disable(self,ctx):
        """Disable wild encounters in this server."""
        await self.config.guild(ctx.guild).enabled.set(False);await ctx.send("Wild encounters disabled.")
    async def reset_player_data(self,user_id):
        removed=[]
        async with self.lock(("user",user_id)),self.lock("encounters"):
            await self.config.user_from_id(user_id).clear()
            encounters=await self.config.encounters()
            for key in list(encounters):
                raw=encounters[key]
                if int(raw.get("battle",{}).get("user_id",0))==user_id:
                    removed.append(dict(raw));encounters.pop(key);self.battles.pop(int(key),None)
            await self.config.encounters.set(encounters)
        for raw in removed:await self.clear_guild(int(raw["guild_id"]),int(raw.get("battle",{}).get("encounter_id",0)))
        return removed

    async def red_delete_data_for_user(self,*,requester,user_id):
        await self.reset_player_data(user_id)
