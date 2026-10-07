import asyncio
import logging
import random
import time
from datetime import datetime,timedelta,timezone
from pathlib import Path
import discord
from discord.ext import tasks
from redbot.core import Config,bank,commands
from redbot.core.data_manager import cog_data_path
from .catalog import CatalogError,PokemonCatalog
from .catalog_versions import CATALOG_VERSIONS
from .data import EVOLUTIONS,MOVES,SPECIES,experience_to_next,generation_for,moves_for_level,sprite
from .models import Battle,BattleError,OwnedPokemon,pokemon_max_hp
from .gyms import GYMS,earned_badges,gym_status_embed,next_gym,trainer_profile_embed
from .pokedex import POKEDEX_STYLES,PokedexSession,PokedexView,render_pokedex,resolve_style
from .renderer import BattleRenderer,ENCOUNTER_BACKDROPS,RenderError
from .views import BagView,BattleView,CollectionBrowserView,EncounterView,FightView,MedicineView,MoveLearnView,PartyPlacementView,PartyView,StarterView,MainMenuView,CenterCollectView,TradeView,GymChallengeView,TradeCollectionView

log=logging.getLogger("red.sick-cogs.Pokemon")
CONFIG_IDENTIFIER=813604927242
GUILD={"enabled":False,"channels":[],"activity":0,"threshold":12,"threshold_min":8,"threshold_max":15,"active_encounter":None,"encounter_timeout":900,"battle_timeout":1800,"spawn_cooldown":120,"last_spawn_at":None,"generations":[1],"pace":"normal","center_channel":None,"spawn_mode":"timed","timer_minutes":60,"next_spawn_at":None,"expired_card_mode":"delete","max_active_encounters":1,"concurrency_owner_override":False,"timer_owner_override":False}
USER={"collection":[],"party":[],"balls":10,"starter_chosen":False,"transactions":{},"pokedex_seen":[],"pokedex_caught":[],"pokedex_style":"default","trainer_card_style":"retro","badges":[],"items":{"potion":5,"revive":2,"great_ball":3,"ultra_ball":1},"center_last_at":None,"pokedex_stats":{},"recorded_battles":[],"achievement_rewards":[],"daily_research":{},"menu_style":"retro"}
MART_ITEMS={
    "poke_ball":("Poké Ball","balls",50),
    "great_ball":("Great Ball","great_ball",150),
    "ultra_ball":("Ultra Ball","ultra_ball",300),
    "potion":("Potion","potion",75),
    "revive":("Revive","revive",400),
}
MART_ALIASES={"pokeball":"poke_ball","poke":"poke_ball","greatball":"great_ball","great":"great_ball","ultraball":"ultra_ball","ultra":"ultra_ball"}
GLOBAL={"schema":14,"next_encounter":1,"encounters":{},"pokedex_default_style":"retro","encounter_timeout":900,"allowed_generations":[1],"minimum_threshold":8,"minimum_cooldown":120,"rarity_profile":"friendly","allow_special_species":False,"mart_prices":{key:value[2] for key,value in MART_ITEMS.items()},"center_cooldown":1800,"maximum_concurrency":5,"minimum_timer":15,"next_trade":1,"trades":{}}
BOX_SIZE=30
MAX_BOXES=10
MAX_COLLECTION=BOX_SIZE*MAX_BOXES
CENTER_TREATMENT_SECONDS=5
TRADE_TIMEOUT_SECONDS=900
SERVER_TIMER_MINUTES=(1,10080)
DEFAULT_SERVER_TIMER_MINUTES=60
OWNER_TIMER_MINUTES=(1,10080)
COLLECTION_PAGE_SIZE=9
PACE={"active":(5,9,60),"normal":(8,15,120),"relaxed":(18,30,300)}
SPECIAL_SPECIES={144,145,146,150,151}
COLLECTION_REWARDS={5:{"balls":5},10:{"great_ball":5},25:{"ultra_ball":3},50:{"balls":10,"great_ball":5,"ultra_ball":5},100:{"balls":20,"great_ball":10,"ultra_ball":10}}
VICTORY_REWARDS={5:{"potion":5},10:{"revive":3},25:{"potion":10,"revive":5},50:{"potion":15,"revive":8},100:{"potion":25,"revive":12}}
ENCOUNTER_REWARDS={10:{"balls":3},25:{"balls":5},50:{"great_ball":3},100:{"great_ball":5},250:{"ultra_ball":3},500:{"ultra_ball":5}}
TYPE_REWARDS={3:{"balls":2},5:{"great_ball":1},10:{"ultra_ball":1}}
DAILY_RESEARCH_TASKS=(
    ("encounters",3,{"balls":2},"Complete 3 encounters"),
    ("victories",2,{"potion":2},"Defeat 2 Pokémon"),
    ("catches",1,{"great_ball":1},"Catch 1 Pokémon"),
)
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
    return max(1,min(100,player_level+offset))

def activity_weight(active_users):
    return 1+min(2,max(0,active_users-1))

def encounter_level(levels,offset=0):
    strongest=max((max(1,min(100,int(value))) for value in levels),default=1)
    return scaled_wild_level(strongest,offset)

def encounter_shiny(rng):
    return rng.randrange(4096)==0

def active_guild_encounters(encounters,guild_id):
    return {int(key):raw for key,raw in encounters.items() if int(raw.get("guild_id",0))==int(guild_id) and raw.get("state") in {"open","battle"}}

def effective_concurrency(conf,policy):
    selected=max(1,min(5,int(conf.get("max_active_encounters",1))))
    if conf.get("concurrency_owner_override"):return selected
    return min(selected,max(1,min(5,int(policy.get("maximum_concurrency",5)))))

def effective_timer_minutes(conf,policy):
    requested=max(SERVER_TIMER_MINUTES[0],min(SERVER_TIMER_MINUTES[1],int(conf.get("timer_minutes",DEFAULT_SERVER_TIMER_MINUTES))))
    if conf.get("timer_owner_override",False):return requested
    floor=max(SERVER_TIMER_MINUTES[0],min(SERVER_TIMER_MINUTES[1],int(policy.get("minimum_timer",GLOBAL["minimum_timer"]))))
    return max(requested,floor)

def jittered_spawn_due(now,minutes,rng=None):
    rng=rng or random.SystemRandom()
    seconds=max(60,int(minutes)*60)
    return now+timedelta(seconds=max(60,round(seconds*rng.uniform(.8,1.2))))

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

def mart_item_key(value):
    key=str(value).casefold().replace("-","_").replace(" ","_")
    return MART_ALIASES.get(key,key) if MART_ALIASES.get(key,key) in MART_ITEMS else None

def mart_prices(configured=None):
    configured=configured or {}
    return {key:max(1,int(configured.get(key,details[2]))) for key,details in MART_ITEMS.items()}

def grant_mart_item(conf,key,quantity):
    storage=MART_ITEMS[key][1]
    if storage=="balls":conf["balls"]=int(conf.get("balls",0))+quantity
    else:
        items=conf.setdefault("items",{});items[storage]=int(items.get(storage,0))+quantity;conf["items"]=items

def achievement_totals(conf):
    caught_species={int(value) for value in conf.get("pokedex_caught",[]) if str(value).isdigit()}
    types={}
    for species_id in caught_species:
        species=SPECIES.get(species_id)
        if not species:continue
        for pokemon_type in set(species.types):types[pokemon_type]=types.get(pokemon_type,0)+1
    stats=conf.get("pokedex_stats",{})
    return {
        "collection":len(caught_species),
        "victories":sum(int(value.get("defeated",0)) for value in stats.values()),
        "encounters":sum(int(value.get("battled",0)) for value in stats.values()),
        "catches":sum(int(value.get("caught",0)) for value in stats.values()),
        "types":types,
    }

def grant_supply_items(conf,reward):
    items=conf.setdefault("items",{});parts=[]
    for item,amount in reward.items():
        if item=="balls":conf["balls"]=int(conf.get("balls",0))+amount;label="Poké Balls"
        else:items[item]=int(items.get(item,0))+amount;label=item.replace("_"," ").title()+("s" if amount!=1 else "")
        parts.append(f"{amount} {label}")
    conf["items"]=items
    return parts

def reward_summary(reward):
    labels={"balls":"Poké Balls","great_ball":"Great Balls","ultra_ball":"Ultra Balls","potion":"Potions","revive":"Revives"}
    return ", ".join(f"{amount} {labels.get(item,item.replace(chr(95),chr(32)).title())}" for item,amount in reward.items())

def spawn_weight(species,profile="friendly"):
    weights=RARITY_PROFILES.get(profile,RARITY_PROFILES["friendly"])
    return weights[rarity_tier(species)]

EVOLUTION_LEVELS={evolved:level for evolved,level in EVOLUTIONS.values()}

def minimum_spawn_level(species_id):
    return EVOLUTION_LEVELS.get(int(species_id),1)

def available_species(generations,allow_special=False,level=None):
    maximum=max(1,int(level)) if level is not None else 100
    return [item for item in SPECIES.values() if item.id not in {1,4,7} and (allow_special or item.id not in SPECIAL_SPECIES) and generation_for(item.id) in generations and minimum_spawn_level(item.id)<=maximum]

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
    __version__="0.52.1";__author__="SickProdigy"
    def __init__(self,bot):
        self.bot=bot;self.config=Config.get_conf(self,identifier=CONFIG_IDENTIFIER,force_registration=True)
        self.config.register_guild(**GUILD);self.config.register_user(**USER);self.config.register_global(**GLOBAL)
        self.battles={};self.locks={};self.activity={};self.recent_users={};self.recent_content={};self.catalog=PokemonCatalog(cog_data_path(self)/"catalog.json",Path(__file__).with_name("gen1.json"));self.renderer=BattleRenderer(cog_data_path(self)/"sprites");self.cleanup_loop.start()
    async def cog_load(self):
        try:self.catalog.load()
        except CatalogError:log.exception("Pokémon catalog cache could not be loaded")
        await self._migrate()
        await self.recover_trades()
        for key,raw in (await self.config.encounters()).items():
            if raw.get("battle"):
                battle=Battle.from_raw(raw["battle"]);self.battles[battle.encounter_id]=battle
                if battle.state=="active":
                    for view in (BattleView,FightView,PartyView,BagView):self.bot.add_view(view(self,battle.encounter_id),message_id=battle.message_id)
                    for item_key in ("potion","revive"):self.bot.add_view(MedicineView(self,battle.encounter_id,item_key),message_id=battle.message_id)
            elif raw.get("state")=="open":self.bot.add_view(EncounterView(self,int(key)),message_id=raw.get("message_id"))
        for key,raw in (await self.config.trades()).items():
            if raw.get("state")=="offered" and raw.get("message_id"):
                self.bot.add_view(TradeView(self,int(key),int(raw["offerer_id"]),int(raw["recipient_id"])),message_id=int(raw["message_id"]))
    def cog_unload(self):
        self.cleanup_loop.cancel();self.bot.loop.create_task(self.renderer.close())
    @tasks.loop(seconds=60)
    async def cleanup_loop(self):
        now=datetime.now(timezone.utc);await self.expire_trades(now);expired=[];returned=[]
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
        now=now or datetime.now(timezone.utc);policy=await self.config.all()
        for guild_id,conf in (await self.config.all_guilds()).items():
            if not conf.get("enabled") or conf.get("spawn_mode","timed")!="timed" or not conf.get("channels"):continue
            section=self.config.guild_from_id(int(guild_id));minutes=effective_timer_minutes(conf,policy)
            try:due=datetime.fromisoformat(conf.get("next_spawn_at") or "")
            except (TypeError,ValueError):due=None
            if due is None:
                await section.next_spawn_at.set(jittered_spawn_due(now,minutes).isoformat());continue
            if due>now:continue
            async with self.lock(("spawn",int(guild_id))):
                active=await self.guild_encounters(int(guild_id),conf)
                if len(active)>=effective_concurrency(conf,policy):continue
                occupied={int(raw.get("channel_id",0)) for raw in active.values()}
                channels=[self.bot.get_channel(int(value)) for value in conf["channels"] if int(value) not in occupied]
                channels=[channel for channel in channels if channel is not None]
                if not channels:continue
                channel=random.SystemRandom().choice(channels)
                try:await self.spawn(channel)
                except (discord.Forbidden,discord.HTTPException):
                    log.exception("Timed Pokémon encounter could not be posted")
                    await section.next_spawn_at.set(jittered_spawn_due(now,minutes).isoformat())

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
        if schema<8:
            await self.config.mart_prices.set(mart_prices(await self.config.mart_prices()))
            await self.config.schema.set(8)
        if schema<9:
            for guild_id in (await self.config.all_guilds()):
                await self.config.guild_from_id(int(guild_id)).active_encounter.set(None)
            await self.config.schema.set(9)
        if schema<10:
            await self.config.schema.set(10)
        if schema<11:
            for user_id,data in (await self.config.all_users()).items():
                data.setdefault("daily_research",{});await self.config.user_from_id(int(user_id)).set(data)
            await self.config.schema.set(11)
        if schema<12:
            for user_id,data in (await self.config.all_users()).items():
                data.setdefault("menu_style","retro");await self.config.user_from_id(int(user_id)).set(data)
            await self.config.schema.set(12)
        if schema<13:
            await self.config.trades.set({});await self.config.next_trade.set(1)
        if schema<14:
            minimum=max(SERVER_TIMER_MINUTES[0],min(SERVER_TIMER_MINUTES[1],int(await self.config.minimum_timer())));await self.config.minimum_timer.set(minimum)
            for guild_id,data in (await self.config.all_guilds()).items():
                data.setdefault("timer_owner_override",int(data.get("timer_minutes",DEFAULT_SERVER_TIMER_MINUTES))<DEFAULT_SERVER_TIMER_MINUTES)
                await self.config.guild_from_id(int(guild_id)).set(data)
        if schema<=14:await self.config.schema.set(14)
    def lock(self,key):
        if not hasattr(self,"locks"):self.locks={}
        return self.locks.setdefault(key,asyncio.Lock())
    @staticmethod
    def trade_collection_after(conf,outgoing_id,incoming_raw):
        incoming_id=incoming_raw["instance_id"];updated=dict(conf)
        collection=[dict(raw) for raw in conf.get("collection",[]) if raw.get("instance_id") not in {outgoing_id,incoming_id}]
        collection.append(dict(incoming_raw));updated["collection"]=collection
        owned={raw["instance_id"] for raw in collection};party=[identity for identity in conf.get("party",[]) if identity!=outgoing_id and identity in owned]
        if not party:party=[incoming_id]
        updated["party"]=party[:6];return updated

    def trainer_in_active_battle(self,user_id):
        return any(battle.user_id==user_id and battle.state=="active" for battle in self.battles.values())

    @staticmethod
    def trade_reserved(trades,instance_id,exclude=None):
        return any(str(key)!=str(exclude) and raw.get("state") in {"offered","settling"} and instance_id in {raw.get("offered_id"),raw.get("requested_id")} for key,raw in trades.items())

    async def recover_trades(self):
        trades=await self.config.trades()
        for key,raw in list(trades.items()):
            if raw.get("state")!="settling":continue
            try:await self.settle_trade(int(key),recovering=True)
            except Exception:log.exception("Pokémon trade recovery remains pending",extra={"trade_id":key})

    async def settle_trade(self,trade_id,recovering=False):
        async with self.lock("trades"):
            trades=await self.config.trades();record=trades.get(str(trade_id))
            if not record or record.get("state") not in ({"settling"} if recovering else {"offered","settling"}):return None
            first=min(int(record["offerer_id"]),int(record["recipient_id"]));second=max(int(record["offerer_id"]),int(record["recipient_id"]))
            async with self.lock(("user",first)),self.lock(("user",second)):
                offerer_section=self.config.user_from_id(int(record["offerer_id"]));recipient_section=self.config.user_from_id(int(record["recipient_id"]))
                offerer=await offerer_section.all();recipient=await recipient_section.all()
                if record.get("state")=="offered":
                    offered=next((dict(raw) for raw in offerer.get("collection",[]) if raw.get("instance_id")==record["offered_id"]),None)
                    requested=next((dict(raw) for raw in recipient.get("collection",[]) if raw.get("instance_id")==record["requested_id"]),None)
                    if not offered or not requested:record["state"]="invalid";trades[str(trade_id)]=record;await self.config.trades.set(trades);return record
                    if self.trainer_in_active_battle(int(record["offerer_id"])) or self.trainer_in_active_battle(int(record["recipient_id"])):raise ValueError("Finish both trainers’ active battles before accepting the trade.")
                    record["offered_pokemon"]=offered;record["requested_pokemon"]=requested;record["state"]="settling";trades[str(trade_id)]=record;await self.config.trades.set(trades)
                offered=dict(record["offered_pokemon"]);requested=dict(record["requested_pokemon"])
                offerer=self.trade_collection_after(offerer,record["offered_id"],requested);recipient=self.trade_collection_after(recipient,record["requested_id"],offered)
                await offerer_section.set(offerer);await recipient_section.set(recipient)
                record["state"]="completed";record["completed_at"]=datetime.now(timezone.utc).isoformat();trades[str(trade_id)]=record;await self.config.trades.set(trades);return record

    async def close_trade_message(self,record,content):
        channel=self.bot.get_channel(int(record.get("channel_id",0)))
        if not channel or not record.get("message_id"):return
        try:message=await channel.fetch_message(int(record["message_id"]));await message.edit(content=content,view=None)
        except (discord.Forbidden,discord.NotFound,discord.HTTPException):pass

    async def expire_trades(self,now=None):
        now=now or datetime.now(timezone.utc);expired=[]
        async with self.lock("trades"):
            trades=await self.config.trades()
            for key,raw in trades.items():
                if raw.get("state")!="offered":continue
                try:due=datetime.fromisoformat(raw["expires_at"])
                except (KeyError,TypeError,ValueError):due=now
                if due<=now:raw["state"]="expired";expired.append(dict(raw))
            if expired:await self.config.trades.set(trades)
        for raw in expired:await self.close_trade_message(raw,"This Pokémon trade offer expired.")

    async def guild_encounters(self,guild_id,conf=None):
        try:return active_guild_encounters(await self.config.encounters(),guild_id)
        except AttributeError:
            legacy=(conf or {}).get("active_encounter")
            return {int(legacy):{"channel_id":0,"state":"open"}} if legacy else {}
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
        if not conf["enabled"] or conf.get("spawn_mode","timed")!="activity" or message.channel.id not in conf["channels"]:return
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
            active=await self.guild_encounters(message.guild.id,conf)
            limit=effective_concurrency(conf,policy)
            if len(active)>=limit or message.channel.id in {int(raw.get("channel_id",0)) for raw in active.values()}:return
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
        level=await self.spawn_level(channel.guild.id);pool=available_species(generations,policy["allow_special_species"],level)
        if not pool:raise RuntimeError("No Pokémon are available under the bot-wide encounter policy.")
        rng=random.SystemRandom()
        chosen=rng.choices(pool,weights=[spawn_weight(item,policy["rarity_profile"]) for item in pool],k=1)[0]
        sid=chosen.id;gender=encounter_gender(chosen,rng);shiny=True if force_shiny else encounter_shiny(rng);backdrop=rng.randrange(len(ENCOUNTER_BACKDROPS))
        display=("Shiny " if shiny else "")+SPECIES[sid].name;embed=discord.Embed(title=f"A wild {display} appeared!",description="Press **Encounter** to battle it.",color=discord.Color.green())
        try:
            image=await self.renderer.encounter(sid,level,gender,backdrop,shiny=shiny);file=discord.File(image,filename="encounter.png");embed.set_image(url="attachment://encounter.png")
            msg=await channel.send(embed=embed,file=file,view=EncounterView(self,eid))
        except RenderError:
            log.exception("Encounter rendering failed");embed.set_image(url=sprite(sid,shiny=shiny));msg=await channel.send(embed=embed,view=EncounterView(self,eid))
        raw={"state":"open","species_id":sid,"level":level,"gender":gender,"shiny":shiny,"backdrop":backdrop,"level_locked":True,"guild_id":channel.guild.id,"channel_id":channel.id,"message_id":msg.id,"created_at":datetime.now(timezone.utc).isoformat(),"expires_at":(datetime.now(timezone.utc)+timedelta(seconds=policy["encounter_timeout"])).isoformat(),"encounter_timeout":policy["encounter_timeout"]}
        await self.put_encounter(eid,raw)
        self.activity[channel.guild.id]=0
        await self.config.guild(channel.guild).activity.set(0)
        minimum,maximum,_=bounded_pace(conf["threshold_min"],conf["threshold_max"],conf["spawn_cooldown"],policy)
        await self.config.guild(channel.guild).threshold.set(random.SystemRandom().randrange(minimum,maximum+1))
        spawned_at=datetime.now(timezone.utc);section=self.config.guild(channel.guild)
        await section.last_spawn_at.set(spawned_at.isoformat())
        if conf.get("spawn_mode","timed")=="timed":await section.next_spawn_at.set(jittered_spawn_due(spawned_at,effective_timer_minutes(conf,policy)).isoformat())
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

    async def rendered_center(self,user,party,complete=False):
        trainer=getattr(user,"display_name",getattr(user,"name","Trainer"));title="Your party is fully restored!" if complete else "Healing your Pokémon…"
        description="Collect your refreshed party when you are ready." if complete else f"The restoration cycle takes about {CENTER_TREATMENT_SECONDS} seconds."
        embed=discord.Embed(title=title,description=description,color=discord.Color.green() if complete else discord.Color.gold())
        try:
            image=await self.renderer.pokemon_center(party,trainer,complete);embed.set_image(url="attachment://pokemon-center.png");return embed,[discord.File(image,filename="pokemon-center.png")]
        except RenderError:
            log.exception("Pokémon Center rendering failed");return embed,[]

    async def rendered_main_menu(self,user,conf):
        style=conf.get("menu_style","retro")
        if style not in {"retro","modern"}:style="retro"
        trainer=getattr(user,"display_name",getattr(user,"name","Trainer"))
        embed=discord.Embed(title=f"{trainer}’s Pokémon Menu",description=f"**{style.title()} style** · Choose an option below.",color=discord.Color.green() if style=="retro" else discord.Color.blurple())
        avatar_data=None
        avatar=getattr(user,"display_avatar",None)
        if avatar:
            try:avatar_data=await avatar.with_size(128).read()
            except (discord.HTTPException,OSError):log.debug("Trainer avatar unavailable for main menu",exc_info=True)
        try:
            image=await self.renderer.main_menu(trainer,conf,style,avatar_data=avatar_data);embed.set_image(url="attachment://pokemon-menu.png")
            return embed,[discord.File(image,filename="pokemon-menu.png")]
        except RenderError:
            log.exception("Pokémon main-menu rendering failed");return embed,[]

    @staticmethod
    def accomplishments_embed(user,conf):
        totals=achievement_totals(conf);claimed=set(conf.get("achievement_rewards",[]));trainer=getattr(user,"display_name","Trainer")
        embed=discord.Embed(title=f"{trainer}'s Accomplishments",color=discord.Color.gold())
        for key,label,rewards in (("collection","Unique collection",COLLECTION_REWARDS),("victories","Victories",VICTORY_REWARDS),("encounters","Completed encounters",ENCOUNTER_REWARDS)):
            total=totals[key];next_goal=next(((target,reward) for target,reward in rewards.items() if total<target),None)
            value="All milestones complete." if next_goal is None else f"**{total}/{next_goal[0]}** · Next: {reward_summary(next_goal[1])}"
            earned=sum(1 for target in rewards if f"{key}:{target}" in claimed);embed.add_field(name=label,value=f"{value}\nClaimed: {earned}/{len(rewards)}",inline=False)
        lines=[]
        for pokemon_type,total in sorted(totals["types"].items(),key=lambda item:(-item[1],item[0])):
            next_goal=next(((target,reward) for target,reward in TYPE_REWARDS.items() if total<target),None)
            lines.append(f"**{pokemon_type.title()}** {total}/{next_goal[0]} · {reward_summary(next_goal[1])}" if next_goal else f"**{pokemon_type.title()}** complete")
        embed.add_field(name="Type specialists",value="\n".join(lines) or "Catch Pokémon to begin type-specialist goals.",inline=False);embed.set_footer(text="Rewards are granted automatically when a battle settles.")
        return embed

    @staticmethod
    def research_embed(conf):
        totals=achievement_totals(conf);state=conf.get("daily_research",{});baseline=state.get("baseline",{});claimed=set(state.get("claimed",[]))
        embed=discord.Embed(title="Professor Research · Daily Tasks",description="Complete these before the next UTC day. Rewards are delivered automatically.",color=discord.Color.green())
        for key,target,reward,label in DAILY_RESEARCH_TASKS:
            progress=min(target,max(0,totals[key]-int(baseline.get(key,0))));done=key in claimed;embed.add_field(name=("✅ " if done else "")+label,value=f"**{progress}/{target}** · {reward_summary(reward)}",inline=False)
        embed.set_footer(text=f"Resets daily at 00:00 UTC · {len(claimed)}/{len(DAILY_RESEARCH_TASKS)} complete");return embed

    async def open_menu_section(self,interaction,section):
        user=interaction.user
        if section=="style":
            async with self.lock(("user",user.id)):
                conf=await self.config.user(user).all();conf["menu_style"]="modern" if conf.get("menu_style","retro")=="retro" else "retro";await self.config.user(user).set(conf)
            embed,files=await self.rendered_main_menu(user,conf);await interaction.response.edit_message(embed=embed,attachments=files,view=MainMenuView(self,user.id));return
        conf=await self.config.user(user).all()
        if section=="party":
            owned={p["instance_id"]:p for p in conf.get("collection",[])};party=[OwnedPokemon.from_raw(owned[key]) for key in conf.get("party",[]) if key in owned];lines=[]
            for slot,item in enumerate(party,1):
                maximum=pokemon_max_hp(item);current=maximum if item.current_hp is None else item.current_hp;lines.append(f"{slot}. {item.nickname or SPECIES[item.species_id].name} · Lv. {item.level} · HP {current}/{maximum}")
            trainer=getattr(user,"display_name","Trainer");embed=discord.Embed(title=f"{trainer}’s Party",description="\n".join(lines) or "Empty",color=discord.Color.gold())
            try:image=await self.renderer.party_card(party,trainer);embed.set_image(url="attachment://party.png");files=[discord.File(image,filename="party.png")]
            except RenderError:files=[]
            await interaction.response.send_message(embed=embed,files=files,ephemeral=True);return
        if section=="collection":
            embed,files,page,pages,items=await self.rendered_collection(user,1);await interaction.response.send_message(embed=embed,files=files,view=CollectionBrowserView(self,user.id,page,pages,items),ephemeral=True);return
        if section=="pokedex":
            session=PokedexSession(user_id=user.id,seen={int(x) for x in conf.get("pokedex_seen",[])},caught={int(x) for x in conf.get("pokedex_caught",[])},style=await self.selected_pokedex_style(user),stats=migrated_pokedex_stats(conf));view=PokedexView(self,session)
            await interaction.response.send_message(embed=render_pokedex(session),view=view,ephemeral=True);view.message=await interaction.original_response();return
        if section=="bag":
            items=conf.get("items",{});counts=(int(conf.get("balls",0)),int(items.get("great_ball",0)),int(items.get("ultra_ball",0)),int(items.get("potion",0)),int(items.get("revive",0)))
            text="**Poké Balls**\nPoké Ball: **{}** · Great Ball: **{}** · Ultra Ball: **{}**\n**Medicine**\nPotion: **{}** · Revive: **{}**".format(*counts);await interaction.response.send_message(text,ephemeral=True);return
        if section=="research":
            async with self.lock(("user",user.id)):
                conf=await self.config.user(user).all();self.ensure_daily_research(conf);await self.config.user(user).set(conf)
            await interaction.response.send_message(embed=self.research_embed(conf),ephemeral=True);return
        if section=="profile":
            embed,files=await self.rendered_trainer_card(user,conf);await interaction.response.send_message(embed=embed,files=files,ephemeral=True);return
        if section=="achievements":await interaction.response.send_message(embed=self.accomplishments_embed(user,conf),ephemeral=True);return
        if section=="gym":
            upcoming=next_gym(conf.get("badges",[]));view=GymChallengeView(self,user.id,upcoming.leader) if upcoming else None
            await interaction.response.send_message(embed=gym_status_embed(user,conf),view=view,ephemeral=True);return
        if section=="trade":
            await interaction.response.send_message("Start an exact trade with `poke trade  <your collection number> <their collection number>`. The other trainer must confirm before anything moves.",ephemeral=True);return
        if section=="mart":
            if interaction.guild is None:await interaction.response.send_message("The Poké Mart is available inside a server.",ephemeral=True);return
            prices=mart_prices(await self.config.mart_prices());currency=await bank.get_currency_name(interaction.guild);balance=await bank.get_balance(user);lines=[f"**{label}** — {prices[key]:,} {currency}" for key,(label,_,_) in MART_ITEMS.items()]
            await interaction.response.send_message("**Poké Mart**\n"+"\n".join(lines)+f"\n\nYour balance: **{balance:,} {currency}**\nBuy with `poke buy <item> [quantity]`.",ephemeral=True);return
        await interaction.response.send_message("That menu option is unavailable.",ephemeral=True)

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
            elif b.state=="won":title=f"{trainer} defeated Gym Leader {gym.leader}!" if gym else f"{trainer} defeated {wild_name}!"
            elif b.state=="lost":title=f"Gym Leader {gym.leader} defeated {trainer}!" if gym else f"{wild_name} escaped from {trainer}!"
            else:title=f"{wild_name} escaped from {trainer}!"
            e=discord.Embed(title=title,description=b.result or b.last_action,color=discord.Color.gold())
            e.set_thumbnail(url=sprite(wild.id,shiny=b.wild_shiny))
            if b.state=="caught":e.add_field(name="Caught Pokémon",value=f"{wild_name} · Lv. {b.wild_level}",inline=True)
            e.add_field(name=f"{player.name} HP",value=f"{b.player_hp}/{b.max_hp(b.player)}",inline=True)
            needed=experience_to_next(b.player.species_id,b.player.level)
            e.add_field(name="Experience",value="MAX" if not needed else f"{b.player.experience}/{needed} XP",inline=True)
            return e
        title=f"Gym Leader {gym.leader} · {wild.name} Lv. {b.wild_level} · Pokemon {b.opponent_index+1}/{b.opponent_total}" if gym else f"Wild {wild_name} · Lv. {b.wild_level}"
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
        claimed=set(conf.setdefault("achievement_rewards",[]));awarded=[];totals=achievement_totals(conf)
        groups=(("collection",totals["collection"],COLLECTION_REWARDS),("victories",totals["victories"],VICTORY_REWARDS),("encounters",totals["encounters"],ENCOUNTER_REWARDS))
        for group,total,rewards in groups:
            for target,reward in rewards.items():
                key=f"{group}:{target}"
                if total<target or key in claimed:continue
                claimed.add(key);awarded.append(f"{group.title()} goal {target}: "+", ".join(grant_supply_items(conf,reward)))
        for pokemon_type,total in sorted(totals["types"].items()):
            for target,reward in TYPE_REWARDS.items():
                key=f"type:{pokemon_type}:{target}"
                if total<target or key in claimed:continue
                claimed.add(key);awarded.append(f"{pokemon_type.title()} specialist {target}: "+", ".join(grant_supply_items(conf,reward)))
        conf["achievement_rewards"]=sorted(claimed)
        return awarded

    @staticmethod
    def ensure_daily_research(conf,now=None):
        now=now or datetime.now(timezone.utc);day=now.date().isoformat();state=conf.get("daily_research",{})
        if state.get("date")!=day:
            totals=achievement_totals(conf)
            state={"date":day,"baseline":{key:totals[key] for key in ("encounters","victories","catches")},"claimed":[]}
            conf["daily_research"]=state
        return state

    @classmethod
    def grant_daily_research(cls,conf,now=None):
        state=cls.ensure_daily_research(conf,now);totals=achievement_totals(conf);claimed=set(state.get("claimed",[]));awarded=[]
        baseline=state.get("baseline",{})
        for key,target,reward,label in DAILY_RESEARCH_TASKS:
            progress=max(0,totals[key]-int(baseline.get(key,0)))
            if progress<target or key in claimed:continue
            claimed.add(key);awarded.append(f"Research — {label}: "+", ".join(grant_supply_items(conf,reward)))
        state["claimed"]=sorted(claimed);conf["daily_research"]=state
        return awarded

    def record_battle_result(self,conf,battle):
        key=str(battle.encounter_id);recorded=list(conf.setdefault("recorded_battles",[]))
        if key in recorded:return []
        self.ensure_daily_research(conf)
        if battle.battle_kind=="gym" and battle.opponent_party:
            encountered=battle.opponent_party[:battle.opponent_index+1]
            for index,raw in enumerate(encountered):
                species_id=int(raw["species_id"]);entry=self.pokedex_stat(conf,species_id);entry["battled"]+=1
                if index:entry["seen"]+=1
                seen={int(value) for value in conf.get("pokedex_seen",[])};seen.add(species_id);conf["pokedex_seen"]=sorted(seen)
            for species_id in battle.opponents_defeated:self.pokedex_stat(conf,species_id)["defeated"]+=1
        else:
            entry=self.pokedex_stat(conf,battle.wild_species_id);entry["battled"]+=1
            if battle.state=="won":entry["defeated"]+=1
            elif battle.state=="caught":entry["caught"]+=1
            elif battle.state in {"lost","ran","expired"}:entry["escaped"]+=1
        recorded.append(key);conf["recorded_battles"]=recorded[-500:]
        return self.grant_achievement_rewards(conf)+self.grant_daily_research(conf)

    async def sync_battle_player(self,battle):
        conf=await self.config.user_from_id(battle.user_id).all()
        self.apply_battle_party(conf,battle)
        rewards=self.record_battle_result(conf,battle);gym=GYMS.get(battle.gym_key) if battle.battle_kind=="gym" else None
        if gym and battle.state=="won":
            battle.result=(battle.result or "Victory!")+f" Gym Leader {gym.leader} was defeated."
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
        embed,files=await self.rendered_main_menu(ctx.author,conf)
        await ctx.send(embed=embed,files=files,view=MainMenuView(self,ctx.author.id))

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

    async def rendered_collection(self,user,page=1,manage=True):
        conf=await self.config.user(user).all();ordered=self.sorted_collection(conf);total=len(ordered)
        pages=max(1,(total+COLLECTION_PAGE_SIZE-1)//COLLECTION_PAGE_SIZE);page=max(1,min(int(page),pages));start=(page-1)*COLLECTION_PAGE_SIZE
        party_slots={identity:index+1 for index,identity in enumerate(conf.get("party",[])[:6])};raw_items=[dict(raw) for raw in ordered[start:start+COLLECTION_PAGE_SIZE]]
        for raw in raw_items:raw["party_slot"]=party_slots.get(raw.get("instance_id"))
        items=[OwnedPokemon.from_raw(raw) for raw in raw_items]
        numbered=list(enumerate(raw_items,start+1));lines=[]
        for number,raw in numbered:
            species=SPECIES[raw["species_id"]];marker="Shiny " if raw.get("shiny") else ""
            party=f"P{raw['party_slot']} · " if raw.get("party_slot") else ""
            lines.append(f"{number}. {party}{marker}{raw.get('nickname') or species.name} · Lv. {raw['level']}")
        trainer=getattr(user,"display_name",getattr(user,"name",str(user)))
        embed=discord.Embed(title=f"{trainer}'s Collection · {page}/{pages}",description="\n".join(lines) or "Empty",color=discord.Color.gold())
        embed.set_footer(text=f"{total}/{MAX_COLLECTION} Pokémon" + (" · Select one below to manage your party" if manage else " · Use these numbers when proposing a trade"))
        try:
            image=await self.renderer.collection_card(items,page,pages,total,trainer,[raw.get("party_slot") for raw in raw_items]);embed.set_image(url="attachment://collection.png")
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
        await interaction.response.send_message(f"Where should **{name} · Lv.{raw['level']}** go?",view=PartyPlacementView(self,interaction.user.id,identity,list(conf.get("party",[])),owned,getattr(interaction,"message",None)),ephemeral=True)

    async def place_collection_pokemon(self,interaction,identity,target=None,source_message=None):
        async with self.lock(("user",interaction.user.id)):
            conf=await self.config.user(interaction.user).all();owned={raw["instance_id"]:raw for raw in conf.get("collection",[])}
            if identity not in owned:await interaction.response.edit_message(content="That Pokémon is no longer in your collection.",view=None);return
            party=[value for value in conf.get("party",[]) if value in owned];name=owned[identity].get("nickname") or SPECIES[owned[identity]["species_id"]].name
            if target==identity:await interaction.response.edit_message(content=f"**{name}** already occupies that slot.",view=None);return
            source=party.index(identity) if identity in party else None
            if target is None:
                if source is not None:await interaction.response.edit_message(content=f"**{name}** is already in your party.",view=None);return
                if len(party)>=6:await interaction.response.edit_message(content="Your party is full. Choose a slot to replace.",view=None);return
                party.append(identity);message=f"Added **{name}** to party slot {len(party)}."
            elif target in party:
                slot=party.index(target)
                if source is None:party[slot]=identity;message=f"Placed **{name}** in party slot {slot+1}."
                else:
                    displaced=owned[target].get("nickname") or SPECIES[owned[target]["species_id"]].name
                    party[source],party[slot]=party[slot],party[source];message=f"Moved **{name}** to party slot {slot+1}; **{displaced}** moved to slot {source+1}."
            else:await interaction.response.edit_message(content="That party slot is no longer available.",view=None);return
            conf["party"]=party;await self.config.user(interaction.user).set(conf)
        if source_message is not None:
            await interaction.response.defer()
            try:await source_message.delete()
            except (discord.Forbidden,discord.NotFound,discord.HTTPException):pass
            try:await interaction.delete_original_response()
            except (discord.Forbidden,discord.NotFound,discord.HTTPException):pass
            return
        await interaction.response.edit_message(content=message,view=None)

    @staticmethod
    def trade_embed(record,final=False):
        title="Trade completed!" if final else "Pokémon Trade Offer"
        symbols={"male":"♂","female":"♀","genderless":"—"};offered=("Shiny " if record.get("offered_shiny") else "")+record["offered_name"];requested=("Shiny " if record.get("requested_shiny") else "")+record["requested_name"]
        description=(f"**{record['offerer_name']}** offers **{offered} · {symbols.get(record.get('offered_gender'),'?')} · Lv.{record['offered_level']}**\n" f"for **{record['recipient_name']}’s {requested} · {symbols.get(record.get('requested_gender'),'?')} · Lv.{record['requested_level']}**")
        if not final:description+="\n\nOnly the receiving trainer can accept. Either trainer can cancel. This offer expires in 15 minutes."
        embed=discord.Embed(title=title,description=description,color=discord.Color.green() if final else discord.Color.gold());embed.set_footer(text=f"Trade #{record['trade_id']}");return embed

    @pokemon.group(name="trade",invoke_without_command=True)
    @commands.guild_only()
    async def trade(self,ctx,member:discord.Member=None,your_slot:int=None,their_slot:int=None):
        """Offer an exact Pokémon-for-Pokémon trade."""
        if member is None or your_slot is None or their_slot is None:
            await ctx.send("Use `pokemon trade @trainer <your collection number> <their collection number>`. View another trainer’s numbered choices with `pokemon trade collection @trainer`. Both Pokémon are shown for confirmation.");return
        if member.id==ctx.author.id:await ctx.send("Choose another trainer to trade with.");return
        if member.bot:await ctx.send("Bots cannot own or trade Pokémon.");return
        first=min(ctx.author.id,member.id);second=max(ctx.author.id,member.id)
        async with self.lock("trades"),self.lock(("user",first)),self.lock(("user",second)):
            if self.trainer_in_active_battle(ctx.author.id) or self.trainer_in_active_battle(member.id):await ctx.send("Finish both trainers’ active battles before creating a trade.");return
            offerer=await self.config.user(ctx.author).all();recipient=await self.config.user(member).all();offered_list=self.sorted_collection(offerer);requested_list=self.sorted_collection(recipient)
            if not 1<=your_slot<=len(offered_list):await ctx.send("Your collection number is unavailable. Check `pokemon collection`.");return
            if not 1<=their_slot<=len(requested_list):await ctx.send(f"{member.display_name}’s collection number is unavailable.");return
            offered=offered_list[your_slot-1];requested=requested_list[their_slot-1];trades=await self.config.trades()
            if self.trade_reserved(trades,offered["instance_id"]):await ctx.send("Your selected Pokémon is already reserved in another trade.");return
            if self.trade_reserved(trades,requested["instance_id"]):await ctx.send("Their selected Pokémon is already reserved in another trade.");return
            trade_id=int(await self.config.next_trade());await self.config.next_trade.set(trade_id+1);now=datetime.now(timezone.utc)
            record={"trade_id":trade_id,"state":"offered","offerer_id":ctx.author.id,"recipient_id":member.id,"offerer_name":ctx.author.display_name,"recipient_name":member.display_name,"offered_id":offered["instance_id"],"requested_id":requested["instance_id"],"offered_name":offered.get("nickname") or SPECIES[offered["species_id"]].name,"requested_name":requested.get("nickname") or SPECIES[requested["species_id"]].name,"offered_level":int(offered["level"]),"requested_level":int(requested["level"]),"offered_gender":offered.get("gender","unknown"),"requested_gender":requested.get("gender","unknown"),"offered_shiny":bool(offered.get("shiny",False)),"requested_shiny":bool(requested.get("shiny",False)),"created_at":now.isoformat(),"expires_at":(now+timedelta(seconds=TRADE_TIMEOUT_SECONDS)).isoformat(),"guild_id":ctx.guild.id,"channel_id":ctx.channel.id,"message_id":None}
            trades[str(trade_id)]=record;await self.config.trades.set(trades)
        view=TradeView(self,trade_id,ctx.author.id,member.id)
        try:message=await ctx.send(content=member.mention,embed=self.trade_embed(record),view=view,allowed_mentions=discord.AllowedMentions(users=True,roles=False,everyone=False))
        except Exception:
            async with self.lock("trades"):
                trades=await self.config.trades();current=trades.get(str(trade_id))
                if current and current.get("state")=="offered":current["state"]="delivery_failed";trades[str(trade_id)]=current;await self.config.trades.set(trades)
            raise
        async with self.lock("trades"):
            trades=await self.config.trades();current=trades.get(str(trade_id))
            if current and current.get("state")=="offered":current["message_id"]=message.id;trades[str(trade_id)]=current;await self.config.trades.set(trades)

    @trade.command(name="collection")
    async def trade_collection(self,ctx,member:discord.Member,page:int=1):
        """View another trainer’s numbered Pokémon for a trade."""
        conf=await self.config.user(member).all()
        if not conf.get("collection"):await ctx.send(f"{member.display_name} has no Pokémon to trade.");return
        embed,files,page,pages,items=await self.rendered_collection(member,page,manage=False)
        await ctx.send(embed=embed,files=files,view=TradeCollectionView(self,ctx.author.id,member,page,pages))

    @trade.command(name="cancel")
    async def trade_cancel(self,ctx,trade_id:int):
        """Cancel one of your pending trade offers."""
        async with self.lock("trades"):
            trades=await self.config.trades();record=trades.get(str(trade_id))
            if not record or record.get("state")!="offered":await ctx.send("That trade is no longer pending.");return
            if ctx.author.id not in {int(record["offerer_id"]),int(record["recipient_id"])}:await ctx.send("That trade belongs to other trainers.");return
            record["state"]="cancelled";trades[str(trade_id)]=record;await self.config.trades.set(trades)
        await self.close_trade_message(record,f"Trade #{trade_id} was cancelled.");await ctx.send(f"Trade #{trade_id} cancelled.")

    async def accept_trade(self,interaction,trade_id):
        try:record=await self.settle_trade(trade_id)
        except ValueError as exc:await interaction.response.send_message(str(exc),ephemeral=True);return
        except Exception:
            log.exception("Pokémon trade settlement remains pending recovery",extra={"trade_id":trade_id});await interaction.response.send_message("The trade is safely recorded and will finish during recovery. No Pokémon will be rerolled.",ephemeral=True);return
        if not record or record.get("state")!="completed":await interaction.response.edit_message(content="This trade is no longer available.",view=None);return
        await interaction.response.edit_message(content=None,embed=self.trade_embed(record,True),view=None)

    async def cancel_trade(self,interaction,trade_id):
        async with self.lock("trades"):
            trades=await self.config.trades();record=trades.get(str(trade_id))
            if not record or record.get("state")!="offered":await interaction.response.edit_message(content="This trade is no longer available.",view=None);return
            if interaction.user.id not in {int(record["offerer_id"]),int(record["recipient_id"])}:await interaction.response.send_message("This trade belongs to other trainers.",ephemeral=True);return
            record["state"]="declined" if interaction.user.id==int(record["recipient_id"]) else "cancelled";trades[str(trade_id)]=record;await self.config.trades.set(trades)
        action="declined" if record["state"]=="declined" else "cancelled";await interaction.response.edit_message(content=f"Trade #{trade_id} was {action}.",view=None)

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

    @pokemon.command(name="achievements",aliases=["goals"])
    async def achievements(self,ctx):
        """View accomplishment progress and upcoming supply rewards."""
        conf=await self.config.user(ctx.author).all();await ctx.send(embed=self.accomplishments_embed(ctx.author,conf))

    @pokemon.command(name="research",aliases=["daily"])
    async def professor_research(self,ctx):
        """View today’s Professor research tasks and supply rewards."""
        async with self.lock(("user",ctx.author.id)):
            conf=await self.config.user(ctx.author).all();self.ensure_daily_research(conf);await self.config.user(ctx.author).set(conf)
        await ctx.send(embed=self.research_embed(conf))

    @pokemon.command(name="mart")
    @commands.guild_only()
    async def pokemon_mart(self,ctx):
        """View supplies sold for Red bank credits."""
        prices=mart_prices(await self.config.mart_prices());currency=await bank.get_currency_name(ctx.guild);balance=await bank.get_balance(ctx.author)
        lines=[f"**{label}** — {prices[key]:,} {currency}" for key,(label,_,_) in MART_ITEMS.items()]
        scope="global" if await bank.is_global() else "server-local"
        await ctx.send("**Poké Mart**\n"+"\n".join(lines)+f"\n\nYour balance: **{balance:,} {currency}** ({scope})\nBuy with `{ctx.clean_prefix}poke buy <item> [quantity]`.")

    @pokemon.command(name="buy")
    @commands.guild_only()
    async def pokemon_buy(self,ctx,item:str,quantity:int=1):
        """Buy Poké Mart supplies with Red bank credits."""
        key=mart_item_key(item)
        if not key:await ctx.send("Choose pokeball, greatball, ultraball, potion, or revive.");return
        if not 1<=quantity<=100:await ctx.send("Choose a quantity from 1 to 100.");return
        prices=mart_prices(await self.config.mart_prices());cost=prices[key]*quantity;currency=await bank.get_currency_name(ctx.guild);label=MART_ITEMS[key][0]
        async with self.lock(("user",ctx.author.id)):
            if not await bank.can_spend(ctx.author,cost):
                balance=await bank.get_balance(ctx.author);await ctx.send(f"You need **{cost:,} {currency}** but only have **{balance:,}**.");return
            charged=False
            try:
                await bank.withdraw_credits(ctx.author,cost);charged=True
                conf=await self.config.user(ctx.author).all();grant_mart_item(conf,key,quantity);await self.config.user(ctx.author).set(conf)
            except Exception:
                if charged:
                    try:await bank.deposit_credits(ctx.author,cost)
                    except Exception:
                        log.critical("Could not refund failed Poké Mart purchase for user %s",ctx.author.id,exc_info=True)
                        await ctx.send("The purchase failed and its refund also failed. Please contact the bot owner.");return
                log.exception("Poké Mart purchase failed for user %s",ctx.author.id)
                await ctx.send("The purchase failed. Your payment was returned.");return
        balance=await bank.get_balance(ctx.author);plural="" if quantity==1 else "s"
        await ctx.send(f"Purchased **{quantity} {label}{plural}** for **{cost:,} {currency}**. Balance: **{balance:,}**.")

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
        if ctx.channel.id!=int(center):await ctx.send(f"Visit <#{center}> to use this server’s Pokémon Center.");return
        async with self.lock(("user",ctx.author.id)):
            conf=await self.config.user(ctx.author).all();last=conf.get("center_last_at");now=datetime.now(timezone.utc)
            if last:
                try:remaining=max(60,int(await self.config.center_cooldown()))-(now-datetime.fromisoformat(last)).total_seconds()
                except (TypeError,ValueError):remaining=0
                if remaining>0:
                    seconds=int(remaining)+1;minutes,seconds=divmod(seconds,60);wait=f"{minutes}m {seconds}s" if minutes else f"{seconds}s";await ctx.send(f"The Pokémon Center will be ready again in {wait}.");return
            owned={raw["instance_id"]:raw for raw in conf.get("collection",[])};party=[]
            for identity in conf.get("party",[])[:6]:
                raw=owned.get(identity)
                if not raw:continue
                pokemon=OwnedPokemon.from_raw(raw);pokemon.current_hp=pokemon_max_hp(pokemon);pokemon.status="";pokemon.status_turns=0;pokemon.move_pp={key:MOVES[key].pp for key in pokemon.moves};raw.update(pokemon.raw());party.append(pokemon)
            if not party:await ctx.send("Choose a starter and prepare a party first.");return
            conf["center_last_at"]=now.isoformat();await self.config.user(ctx.author).set(conf)
        embed,files=await self.rendered_center(ctx.author,party,False);message=await ctx.send(embed=embed,files=files)
        await asyncio.sleep(CENTER_TREATMENT_SECONDS)
        embed,files=await self.rendered_center(ctx.author,party,True)
        try:await message.edit(embed=embed,attachments=files,view=CenterCollectView(ctx.author.id))
        except (discord.Forbidden,discord.NotFound,discord.HTTPException):pass

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
        conf=await self.config.user(ctx.author).all();upcoming=next_gym(conf.get("badges",[]))
        command=f"{getattr(ctx,'clean_prefix','[p]')}poke gym challenge"
        await ctx.send(embed=gym_status_embed(ctx.author,conf,command),view=GymChallengeView(self,ctx.author.id,upcoming.leader) if upcoming else None)

    @gym.command(name="challenge")
    @commands.guild_only()
    async def gym_challenge(self,ctx):
        """Challenge the next Kanto Gym Leader's team."""
        await self.start_gym_challenge(ctx.author,ctx.guild,ctx.channel,ctx.send)

    async def gym_challenge_interaction(self,interaction):
        async def send(*args,**kwargs):
            if "embed" not in kwargs:kwargs.setdefault("ephemeral",True)
            await interaction.response.send_message(*args,**kwargs)
            return await interaction.original_response()
        started=await self.start_gym_challenge(interaction.user,interaction.guild,interaction.channel,send)
        if started:
            try:await interaction.message.delete()
            except (discord.Forbidden,discord.NotFound,discord.HTTPException):pass

    async def start_gym_challenge(self,user,guild,channel,send):
        async with self.lock(("user",user.id)),self.lock(("spawn",guild.id)):
            if any(b.user_id==user.id and b.state=="active" for b in self.battles.values()):
                await send("Finish your active battle first.");return
            guild_conf=await self.config.guild(guild).all();policy=await self.config.all();active=await self.guild_encounters(guild.id,guild_conf)
            limit=effective_concurrency(guild_conf,policy)
            if len(active)>=limit:
                await send(f"This server is already using all {limit} active encounter slots.");return
            if channel.id in {int(raw.get("channel_id",0)) for raw in active.values()}:
                await send("This channel already has an active encounter or Gym battle.");return
            conf=await self.config.user(user).all();gym=next_gym(conf.get("badges",[]))
            if not gym:
                await send("You already earned all eight Kanto badges.");return
            if not conf["party"]:
                await send("Choose a starter and prepare a party first.");return
            collection={item["instance_id"]:item for item in conf["collection"]}
            party=[OwnedPokemon.from_raw(collection[key]) for key in conf["party"] if key in collection]
            if not party:
                await send("Your active party needs repair.");return
            if not any((item.current_hp if item.current_hp is not None else pokemon_max_hp(item))>0 for item in party):
                await send("Your party has fainted. Visit a Pokémon Center or use a Revive.");return
            async with self.lock("encounters"):
                eid=await self.config.next_encounter();await self.config.next_encounter.set(eid+1)
            lead=party[0];first_species,first_level=gym.team[0]
            battle=Battle(eid,user.id,guild.id,channel.id,0,lead,first_species,first_level,Battle.stat(lead,"hp"),1,seed=random.SystemRandom().randrange(1,2**31),battle_kind="gym",gym_key=gym.key,trainer_name=str(getattr(user,"display_name",getattr(user,"name","Trainer")))[:24])
            battle.initialize_party(party);battle.initialize_opponents(gym.team)
            seen={int(value) for value in conf.get("pokedex_seen",[])};seen.add(first_species);conf["pokedex_seen"]=sorted(seen);self.pokedex_stat(conf,first_species)["seen"]+=1;await self.config.user(user).set(conf)
            self.battles[eid]=battle
            try:
                embed,files=await self.rendered_battle(battle)
                message=await send(embed=embed,files=files,view=BattleView(self,eid))
            except Exception:
                self.battles.pop(eid,None)
                raise
            battle.message_id=message.id
            seconds=await self.config.guild(guild).battle_timeout()
            raw={"kind":"gym","gym_key":gym.key,"state":"battle","guild_id":guild.id,"channel_id":channel.id,"message_id":message.id,"created_at":datetime.now(timezone.utc).isoformat(),"expires_at":(datetime.now(timezone.utc)+timedelta(seconds=seconds)).isoformat(),"battle":battle.raw()}
            try:
                await self.put_encounter(eid,raw)
            except Exception:
                self.battles.pop(eid,None)
                try:await message.edit(content="The Gym challenge could not be saved. Please try again.",view=None)
                except discord.HTTPException:pass
                raise
        return True

    @pokemon.command(name="profile")
    async def profile(self,ctx,user:discord.Member=None):
        """View your or another member’s trainer card and badge case."""
        target=user or ctx.author;conf=await self.config.user(target).all()
        embed,files=await self.rendered_trainer_card(target,conf)
        await ctx.send(embed=embed,files=files)

    @pokemon.command(name="menustyle")
    async def menu_style(self,ctx,style:str=None):
        """Choose the Retro or Modern Pokémon main-menu style."""
        current=await self.config.user(ctx.author).menu_style()
        if style is None:await ctx.send(f"Pokémon menu style: **{current}**. Choices: retro, modern.");return
        style=style.casefold()
        if style not in {"retro","modern"}:await ctx.send("Unknown style. Choose: retro or modern.");return
        await self.config.user(ctx.author).menu_style.set(style);await ctx.send(f"Pokémon menu style set to **{style}**.")

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
    @commands.group(name="pokemonownerset",aliases=["pokeownerset","pkmnownerset"],invoke_without_command=True)
    @commands.guild_only()
    @commands.is_owner()
    async def pokemon_owner_set(self,ctx):
        """Configure bot-wide Pokémon policy and owner overrides."""
        await ctx.send_help()

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
            conf=await section.all();policy=await self.config.all();minutes=effective_timer_minutes(conf,policy);await section.next_spawn_at.set(jittered_spawn_due(datetime.now(timezone.utc),minutes).isoformat())
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

    @pokemon_owner_set.command(name="centercooldown")
    async def center_cooldown(self,ctx,minutes:int):
        """Set the bot-wide free Pokémon Center cooldown."""
        if not 1<=minutes<=1440:await ctx.send("Use 1–1440 minutes.");return
        await self.config.center_cooldown.set(minutes*60)
        await ctx.send(f"Free Pokémon Center healing now has a {minutes}-minute per-user cooldown.")

    @pokemon_set.command(name="status",aliases=["settings"])
    async def spawn_status(self,ctx):
        """Show channels, schedule, and effective server settings."""
        conf=await self.config.guild(ctx.guild).all();policy=await self.config.all();mode=conf.get("spawn_mode","timed")
        minimum,maximum,cooldown=bounded_pace(conf["threshold_min"],conf["threshold_max"],conf["spawn_cooldown"],policy)
        channels=", ".join(f"<#{value}>" for value in conf.get("channels",[])) or "None"
        center_id=conf.get("center_channel");center=f"<#{center_id}>" if center_id else "None";active=await self.guild_encounters(ctx.guild.id,conf);slot_limit=effective_concurrency(conf,policy)
        if mode=="timed":
            minutes=effective_timer_minutes(conf,policy);due_text="Scheduling now"
            try:due=datetime.fromisoformat(conf.get("next_spawn_at") or "")
            except (TypeError,ValueError):due=None
            if due:due_text=f"<t:{int(due.timestamp())}:R>"
            progress=f"Timer: **every {minutes} minutes** · Next encounter: {due_text}"
            next_spawn=f"Waiting for a free slot ({len(active)}/{slot_limit} active)" if len(active)>=slot_limit else "A random free configured channel will be selected"
        else:
            target=max(minimum,int(conf.get("threshold",minimum)));activity=max(0,int(self.activity.get(ctx.guild.id,conf.get("activity",0))))
            progress=f"Activity: **{activity}/{target}** points (new target range {minimum}–{maximum}) · Cooldown: **{cooldown}s**"
            next_spawn=f"Waiting for a free slot ({len(active)}/{slot_limit} active)" if len(active)>=slot_limit else (f"Needs {target-activity} more activity points" if activity<target else "Ready on the next qualifying message")
        generations=effective_generations(conf.get("generations",[1]),policy.get("allowed_generations",[1]));generation_text=", ".join(map(str,generations))
        enabled=conf.get("enabled",False);encounter_minutes=int(policy.get("encounter_timeout",900))//60;battle_minutes=int(conf.get("battle_timeout",1800))//60
        rarity=policy.get("rarity_profile","friendly");specials="enabled" if policy.get("allow_special_species") else "event-only";expired_cards=conf.get("expired_card_mode","delete")
        await ctx.send(
            f"**Pokémon server settings**\nEnabled: **{enabled}** · Spawn mode: **{mode}**\nSpawn channels: {channels}\nPokémon Center: {center} · Free-heal cooldown: **{max(60,int(policy.get('center_cooldown',1800)))//60}m**\n"
            f"{progress}\nActive encounters: **{len(active)}/{slot_limit}** · Next spawn: {next_spawn}\nEncounter lifetime: **{encounter_minutes}m** · Battle lifetime: **{battle_minutes}m**\n"
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
            conf=await section.all();policy=await self.config.all();minutes=effective_timer_minutes(conf,policy);due=jittered_spawn_due(datetime.now(timezone.utc),minutes);await section.next_spawn_at.set(due.isoformat())
            await ctx.send(f"Timed encounters enabled around every {minutes} minutes with jitter. The next encounter is <t:{int(due.timestamp())}:R>.")
        else:
            await section.next_spawn_at.set(None);await ctx.send("Activity-based encounters enabled. Timed spawning is paused.")

    async def set_spawn_timer(self,ctx,minutes,owner_override=False):
        section=self.config.guild(ctx.guild);await section.timer_minutes.set(minutes);await section.timer_owner_override.set(owner_override)
        if await section.spawn_mode()=="timed":
            due=jittered_spawn_due(datetime.now(timezone.utc),minutes);await section.next_spawn_at.set(due.isoformat())
            await ctx.send(f"Timed encounters set around every {minutes} minutes with jitter. The next encounter is <t:{int(due.timestamp())}:R>.")
        else:await ctx.send(f"Saved a {minutes}-minute jittered timer. It will apply when timed mode is enabled.")

    @pokemon_set.command(name="timer")
    async def spawn_timer(self,ctx,minutes:int):
        """Set this server’s jittered timer within the bot-owner floor."""
        minimum=max(SERVER_TIMER_MINUTES[0],min(SERVER_TIMER_MINUTES[1],int(await self.config.minimum_timer())))
        if not minimum<=minutes<=SERVER_TIMER_MINUTES[1]:await ctx.send(f"Use {minimum}–{SERVER_TIMER_MINUTES[1]} minutes.");return
        await self.set_spawn_timer(ctx,minutes,owner_override=False)

    @pokemon_owner_set.command(name="timermin",aliases=["minimumtimer"])
    async def minimum_spawn_timer(self,ctx,minutes:int):
        """Set the fastest timer ordinary server administrators may choose."""
        if not SERVER_TIMER_MINUTES[0]<=minutes<=SERVER_TIMER_MINUTES[1]:await ctx.send("Use 1–10080 minutes.");return
        await self.config.minimum_timer.set(minutes);await ctx.send(f"Server administrators may now set encounter timers from {minutes}–{SERVER_TIMER_MINUTES[1]} minutes.")

    @pokemon_owner_set.command(name="timer")
    async def owner_spawn_timer(self,ctx,minutes:int):
        """Override this server’s jittered timer from 1 minute to one week."""
        if not OWNER_TIMER_MINUTES[0]<=minutes<=OWNER_TIMER_MINUTES[1]:await ctx.send("Use 1–10080 minutes.");return
        await self.set_spawn_timer(ctx,minutes,owner_override=True)

    @pokemon_set.command(name="concurrency",aliases=["slots"])
    async def spawn_concurrency(self,ctx,limit:int):
        """Set this server’s simultaneous encounters within the bot limit."""
        maximum=max(1,min(5,int(await self.config.maximum_concurrency())))
        if not 1<=limit<=maximum:await ctx.send(f"Use 1–{maximum} active encounters.");return
        section=self.config.guild(ctx.guild);await section.max_active_encounters.set(limit);await section.concurrency_owner_override.set(False)
        await ctx.send(f"This server may now have up to {limit} simultaneous encounters across different channels.")

    @pokemon_owner_set.command(name="concurrency",aliases=["slots"])
    async def owner_spawn_concurrency(self,ctx,limit:int):
        """Override this server’s simultaneous encounter limit."""
        if not 1<=limit<=5:await ctx.send("Use 1–5 active encounters.");return
        section=self.config.guild(ctx.guild);await section.max_active_encounters.set(limit);await section.concurrency_owner_override.set(True)
        await ctx.send(f"This server may now have up to {limit} simultaneous encounters across different channels (bot-owner override).")

    @pokemon_owner_set.command(name="globalconcurrency")
    async def global_concurrency(self,ctx,limit:int):
        """Set the concurrency ceiling for ordinary server administrators."""
        if not 1<=limit<=5:await ctx.send("Use a global concurrency limit from 1–5.");return
        await self.config.maximum_concurrency.set(limit)
        await ctx.send(f"Server administrators may now configure up to {limit} simultaneous encounters.")

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
        async with self.lock(("spawn",ctx.guild.id)):
            policy=await self.config.all();active=await self.guild_encounters(ctx.guild.id,conf);limit=effective_concurrency(conf,policy)
            if len(active)>=limit:await ctx.send(f"This server is already using all {limit} active encounter slots.");return
            if channel.id in {int(raw.get("channel_id",0)) for raw in active.values()}:await ctx.send("This channel already has an active encounter or Gym battle.");return
            if not owner and conf["last_spawn_at"]:
                try:last=datetime.fromisoformat(conf["last_spawn_at"])
                except (TypeError,ValueError):last=None
                if conf.get("spawn_mode","timed")=="timed":
                    due=last+timedelta(minutes=effective_timer_minutes(conf,policy)) if last else None
                    remaining=max(0,round((due-datetime.now(timezone.utc)).total_seconds())) if due else 0
                    if remaining:await ctx.send(f"The server spawn timer is active for another {remaining}s.");return
                else:
                    policy=await self.config.all();_,_,cooldown=bounded_pace(conf["threshold_min"],conf["threshold_max"],conf["spawn_cooldown"],policy)
                    remaining=max(0,round((last+timedelta(seconds=cooldown)-datetime.now(timezone.utc)).total_seconds())) if last else 0
                    if remaining:await ctx.send(f"The activity spawn cooldown is active for another {remaining}s.");return
            await self.spawn(channel,force_shiny=force_shiny)
    @pokemon_owner_set.command(name="martprice")
    async def mart_price(self,ctx,item:str,price:int):
        """Set a bot-wide Poké Mart item price."""
        key=mart_item_key(item)
        if not key:await ctx.send("Choose pokeball, greatball, ultraball, potion, or revive.");return
        if not 1<=price<=1_000_000_000:await ctx.send("Use a price from 1 to 1,000,000,000 credits.");return
        prices=mart_prices(await self.config.mart_prices());prices[key]=price;await self.config.mart_prices.set(prices)
        await ctx.send(f"{MART_ITEMS[key][0]} now costs {price:,} credits.")

    @pokemon_owner_set.command(name="pokedexstyle")
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

    @pokemon_owner_set.command(name="globalstatus",aliases=["settings","status"])
    async def global_status(self,ctx):
        """Show all bot-wide Pokémon policies and owner limits."""
        policy=await self.config.all();minimum_timer=max(SERVER_TIMER_MINUTES[0],min(SERVER_TIMER_MINUTES[1],int(policy.get("minimum_timer",GLOBAL["minimum_timer"]))));prices=mart_prices(policy.get("mart_prices",{}));generations=", ".join(map(str,policy.get("allowed_generations",[1])))
        price_text=" · ".join(f"{MART_ITEMS[key][0]}: **{prices[key]:,}**" for key in MART_ITEMS)
        await ctx.send(
            f"**Pokémon bot-wide policy**\n"
            f"Fresh-install defaults: admin timer floor **{GLOBAL['minimum_timer']}m** · wild lifetime **{GLOBAL['encounter_timeout']//60}m** · Center cooldown **{GLOBAL['center_cooldown']//60}m** · admin concurrency ceiling **{GLOBAL['maximum_concurrency']}**\n"
            f"Timer limits: server administrators **{minimum_timer}–{SERVER_TIMER_MINUTES[1]:,}m** · bot-owner override **{OWNER_TIMER_MINUTES[0]}–{OWNER_TIMER_MINUTES[1]:,}m**\n"
            f"Global wild encounter lifetime: current **{max(60,int(policy.get('encounter_timeout',900)))//60}m** · owner-set range **1–1,440m** (new encounters on every server)\n"
            f"Global free-Center cooldown: current **{max(60,int(policy.get('center_cooldown',1800)))//60}m** · owner-set range **1–1,440m**\n"
            f"Activity-mode floors: **{policy.get('minimum_threshold',8)} points** · **{policy.get('minimum_cooldown',120)}s cooldown**\n"
            f"Concurrency limits: server-admin ceiling **{max(1,min(5,int(policy.get('maximum_concurrency',5))))}** (owner-set range **1–5**) · per-server owner override **1–5**\n"
            f"Allowed generations: **{generations}** · Rarity: **{policy.get('rarity_profile','friendly')}** · Special species: **{'enabled' if policy.get('allow_special_species') else 'event-only'}**\n"
            f"Default Pokédex style: **{resolve_style(policy.get('pokedex_default_style','retro')).label}**\n"
            f"Poké Mart prices: {price_text}"
        )

    @pokemon_owner_set.command(name="encountertime")
    async def encounter_time(self,ctx,minutes:int):
        """Set the global wild encounter lifetime."""
        if not 1<=minutes<=1440:await ctx.send("Use 1–1440 minutes.");return
        await self.config.encounter_timeout.set(minutes*60);await ctx.send(f"Global wild encounter lifetime set to {minutes} minutes.")

    @pokemon_owner_set.command(name="globallimits")
    async def global_limits(self,ctx,minimum_threshold:int,minimum_cooldown:int):
        """Set global spawn-rate floors."""
        if not 5<=minimum_threshold<=500 or not 60<=minimum_cooldown<=86400:await ctx.send("Threshold: 5–500; cooldown: 60–86400 seconds.");return
        await self.config.minimum_threshold.set(minimum_threshold);await self.config.minimum_cooldown.set(minimum_cooldown);await ctx.send("Global spawn-rate floors updated. Servers may only use slower settings.")

    @pokemon_owner_set.command(name="globalgenerations")
    async def global_generations(self,ctx,*values:int):
        """Set bot-wide available generations."""
        selected=sorted(set(values))
        if not selected or any(value<1 or value>9 for value in selected):await ctx.send("Choose generations 1–9.");return
        await self.config.allowed_generations.set(selected);await ctx.send(f"Bot-wide generations: {', '.join(map(str,selected))}.")

    @pokemon_owner_set.command(name="rarity")
    async def rarity(self,ctx,profile:str):
        """Choose the global rarity profile."""
        profile=profile.casefold()
        if profile not in RARITY_PROFILES:await ctx.send("Choose friendly, standard, or challenging.");return
        await self.config.rarity_profile.set(profile);await ctx.send(f"Global encounter rarity set to {profile}.")

    @pokemon_owner_set.command(name="specials")
    async def specials(self,ctx,enabled:bool):
        """Allow or gate special species."""
        await self.config.allow_special_species.set(enabled);await ctx.send("Special species may appear normally." if enabled else "Legendary and mythical species are event-only.")

    @pokemon_owner_set.command(name="catalogsync")
    async def catalog_sync(self,ctx,generation:int):
        """Cache catalog data for a generation."""
        async with ctx.typing():
            try:count=await self.catalog.sync_generation(generation)
            except CatalogError as exc:await ctx.send(str(exc));return
        await ctx.send(f"Cached {count} generation {generation} species.")
    @pokemon_owner_set.command(name="resetplayer")
    async def reset_player(self,ctx,user:discord.Member,confirmation:str):
        """Reset one complete Pokémon profile.

        This permanently clears the trainer starter, collection, party, Pokédex, badges, inventory, and active Pokémon battle. The final argument must be `confirm`.
        """
        if confirmation.casefold()!="confirm":
            await ctx.send(f"This clears all Pokémon progress for {user.mention}. Run `{ctx.clean_prefix}pokemonownerset resetplayer {user.mention} confirm` to proceed.")
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
        """End the encounter in this channel, or the server’s only encounter."""
        raw=None;eid=None
        async with self.lock("encounters"):
            encounters=await self.config.encounters();active=active_guild_encounters(encounters,ctx.guild.id)
            channel_id=int(getattr(getattr(ctx,"channel",None),"id",0))
            matches=[key for key,value in active.items() if int(value.get("channel_id",0))==channel_id]
            if matches:eid=matches[0]
            elif len(active)==1:eid=next(iter(active))
            elif len(active)>1:
                await ctx.send("Multiple encounters are active. Run this command in the channel whose encounter you want to end.");return
            if eid is not None:
                raw=encounters.pop(str(eid),None);await self.config.encounters.set(encounters);self.battles.pop(eid,None)
        if eid is not None:await self.clear_guild(ctx.guild.id,eid)
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
        await ctx.send("Active encounter cleared." if raw else "There is no active encounter to clear here.")
    @pokemon_set.command(name="disable")
    async def disable(self,ctx):
        """Disable wild encounters in this server."""
        await self.config.guild(ctx.guild).enabled.set(False);await ctx.send("Wild encounters disabled.")
    async def reset_player_data(self,user_id):
        await self.recover_trades();removed=[];cancelled=[]
        async with self.lock("trades"),self.lock(("user",user_id)),self.lock("encounters"):
            trades=await self.config.trades()
            for raw in trades.values():
                if raw.get("state") in {"offered","settling"} and user_id in {int(raw.get("offerer_id",0)),int(raw.get("recipient_id",0))}:raw["state"]="cancelled_deleted_user";cancelled.append(dict(raw))
            await self.config.trades.set(trades);await self.config.user_from_id(user_id).clear()
            encounters=await self.config.encounters()
            for key in list(encounters):
                raw=encounters[key]
                if int(raw.get("battle",{}).get("user_id",0))==user_id:
                    removed.append(dict(raw));encounters.pop(key);self.battles.pop(int(key),None)
            await self.config.encounters.set(encounters)
        for raw in removed:await self.clear_guild(int(raw["guild_id"]),int(raw.get("battle",{}).get("encounter_id",0)))
        for raw in cancelled:await self.close_trade_message(raw,"This Pokémon trade was cancelled because a trainer's data was deleted.")
        return removed

    async def red_delete_data_for_user(self,*,requester,user_id):
        await self.reset_player_data(user_id)
