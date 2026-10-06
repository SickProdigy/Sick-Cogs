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
from .models import Battle,BattleError,OwnedPokemon,pokemon_max_hp
from .gyms import GYMS,earned_badges,gym_status_embed,next_gym,trainer_profile_embed
from .pokedex import POKEDEX_STYLES,PokedexSession,PokedexView,render_pokedex,resolve_style
from .renderer import BattleRenderer,ENCOUNTER_BACKDROPS,RenderError
from .views import BagView,BattleView,CollectionBrowserView,EncounterView,FightView,PartyPlacementView,PartyView,StarterView

log=logging.getLogger("red.sick-cogs.Pokemon")
CONFIG_IDENTIFIER=813604927242
GUILD={"enabled":False,"channels":[],"activity":0,"threshold":12,"threshold_min":8,"threshold_max":15,"active_encounter":None,"encounter_timeout":900,"battle_timeout":1800,"spawn_cooldown":120,"last_spawn_at":None,"generations":[1],"pace":"normal","center_channel":None}
USER={"collection":[],"party":[],"balls":10,"starter_chosen":False,"transactions":{},"pokedex_seen":[],"pokedex_caught":[],"pokedex_style":"default","badges":[],"items":{"potion":5,"revive":2},"center_last_at":None}
GLOBAL={"schema":4,"next_encounter":1,"encounters":{},"pokedex_default_style":"retro","encounter_timeout":900,"allowed_generations":[1],"minimum_threshold":8,"minimum_cooldown":120,"rarity_profile":"friendly","allow_special_species":False}
BOX_SIZE=30
MAX_BOXES=10
MAX_COLLECTION=BOX_SIZE*MAX_BOXES
COLLECTION_PAGE_SIZE=9
PACE={"active":(5,9,60),"normal":(8,15,120),"relaxed":(18,30,300)}
SPECIAL_SPECIES={144,145,146,150,151}
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

def encounter_gender(species,rng):
    if species.gender_rate<0:return "genderless"
    return "female" if rng.randrange(8)<species.gender_rate else "male"

def rarity_tier(species):
    if species.catch_rate>=190:return "common"
    if species.catch_rate>=90:return "uncommon"
    if species.catch_rate>=45:return "rare"
    return "very_rare"

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

class Pokemon(commands.Cog):
    """Catch globally owned Pokémon in opt-in guild channels."""
    __version__="0.19.4";__author__="SickProdigy"
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
                    for view in (BattleView, FightView, PartyView, BagView):
                        self.bot.add_view(view(self,battle.encounter_id),message_id=battle.message_id)
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
            self.battles.pop(eid,None);await self.clear_guild(int(raw["guild_id"]),eid)
            channel=self.bot.get_channel(int(raw["channel_id"]))
            if channel:
                try:
                    message=await channel.fetch_message(int(raw["message_id"]));await message.edit(content="This wild encounter expired.",view=None)
                except (discord.Forbidden,discord.NotFound,discord.HTTPException):pass
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
        if schema<4:await self.config.schema.set(4)
    def lock(self,key):return self.locks.setdefault(key,asyncio.Lock())
    async def put_encounter(self,eid,raw):
        async with self.lock("encounters"):
            encounters=await self.config.encounters();encounters[str(eid)]=raw;await self.config.encounters.set(encounters)
    @commands.Cog.listener()
    async def on_message_without_command(self,message):
        if not message.guild or message.author.bot or len((message.content or "").strip())<3:return
        conf=await self.config.guild(message.guild).all()
        if not conf["enabled"] or message.channel.id not in conf["channels"] or conf["active_encounter"]:return
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

    async def spawn(self,channel):
        async with self.lock("encounters"):
            eid=await self.config.next_encounter();await self.config.next_encounter.set(eid+1)
        conf=await self.config.guild(channel.guild).all();policy=await self.config.all()
        generations=effective_generations(conf["generations"],policy["allowed_generations"])
        pool=available_species(generations,policy["allow_special_species"])
        if not pool:raise RuntimeError("No Pokémon are available under the bot-wide encounter policy.")
        rng=random.SystemRandom()
        chosen=rng.choices(pool,weights=[spawn_weight(item,policy["rarity_profile"]) for item in pool],k=1)[0]
        sid=chosen.id;level=await self.spawn_level(channel.guild.id);gender=encounter_gender(chosen,rng);backdrop=rng.randrange(len(ENCOUNTER_BACKDROPS))
        embed=discord.Embed(title=f"A wild {SPECIES[sid].name} appeared!",description="Press **Encounter** to battle it.",color=discord.Color.green())
        try:
            image=await self.renderer.encounter(sid,level,gender,backdrop);file=discord.File(image,filename="encounter.png");embed.set_image(url="attachment://encounter.png")
            msg=await channel.send(embed=embed,file=file,view=EncounterView(self,eid))
        except RenderError:
            log.exception("Encounter rendering failed");embed.set_image(url=sprite(sid));msg=await channel.send(embed=embed,view=EncounterView(self,eid))
        raw={"state":"open","species_id":sid,"level":level,"gender":gender,"backdrop":backdrop,"level_locked":True,"guild_id":channel.guild.id,"channel_id":channel.id,"message_id":msg.id,"created_at":datetime.now(timezone.utc).isoformat(),"expires_at":(datetime.now(timezone.utc)+timedelta(seconds=policy["encounter_timeout"])).isoformat(),"encounter_timeout":policy["encounter_timeout"]}
        await self.put_encounter(eid,raw)
        self.activity[channel.guild.id]=0
        await self.config.guild(channel.guild).active_encounter.set(eid);await self.config.guild(channel.guild).activity.set(0)
        minimum,maximum,_=bounded_pace(conf["threshold_min"],conf["threshold_max"],conf["spawn_cooldown"],policy)
        await self.config.guild(channel.guild).threshold.set(random.SystemRandom().randrange(minimum,maximum+1))
        await self.config.guild(channel.guild).last_spawn_at.set(datetime.now(timezone.utc).isoformat())
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
            if not raw.get("level_locked") or int(raw.get("level",0))<2:
                raw["level"]=scaled_wild_level(owned.level,random.SystemRandom().randrange(-2,3))
                raw["gender"]=encounter_gender(SPECIES[int(raw["species_id"])],random.SystemRandom())
                raw["level_locked"]=True
            seen={int(value) for value in user.get("pokedex_seen",[])}
            seen.add(int(raw["species_id"]));user["pokedex_seen"]=sorted(seen)
            await self.config.user(i.user).set(user)
            collection={item["instance_id"]:item for item in user["collection"]}
            party=[OwnedPokemon.from_raw(collection[identity]) for identity in user["party"] if identity in collection]
            if not any((item.current_hp if item.current_hp is not None else pokemon_max_hp(item))>0 for item in party):
                await i.response.send_message("Your party has fainted. Visit a Pokémon Center or use a Revive.",ephemeral=True);return
            wild=SPECIES[raw["species_id"]];battle=Battle(eid,i.user.id,raw["guild_id"],raw["channel_id"],raw["message_id"],owned,raw["species_id"],raw["level"],Battle.stat(owned,"hp"),wild.hp+raw["level"]*2,seed=random.SystemRandom().randrange(1,2**31),wild_gender=raw.get("gender","unknown"));battle.initialize_party(party);battle.wild_hp=battle.wild_max_hp
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
        gym=GYMS.get(b.gym_key) if b.battle_kind=="gym" else None
        title=f"Gym Leader {gym.leader} · {wild.name} Lv. {b.wild_level}" if gym else f"Wild {wild.name} · Lv. {b.wild_level}"
        e=discord.Embed(title=title,description=b.result or b.last_action or f"Turn {b.turn}",color=discord.Color.gold() if gym else discord.Color.blurple())
        e.set_thumbnail(url=sprite(wild.id))
        e.add_field(name=f"{wild.name} HP",value=f"{b.wild_hp}/{b.wild_max_hp}",inline=True)
        e.add_field(name=f"{player.name} HP",value=f"{b.player_hp}/{b.max_hp(b.player)}",inline=True)
        e.add_field(name="Moves",value=" · ".join(f"{n+1}. {MOVES[k].name} ({b.player.move_pp.get(k,MOVES[k].pp)} PP)" for n,k in enumerate(b.player.moves)),inline=False)
        needed=b.player.level*b.player.level*10 if b.player.level<100 else 0
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
    @staticmethod
    def apply_battle_party(conf,battle):
        battle.party_hp[battle.player.instance_id]=battle.player_hp
        battle.party_status[battle.player.instance_id]=battle.player_status
        updates={}
        for item in battle.party:
            item.current_hp=max(0,min(pokemon_max_hp(item),int(battle.party_hp.get(item.instance_id,pokemon_max_hp(item)))))
            item.status=battle.party_status.get(item.instance_id,"")
            updates[item.instance_id]=item.raw()
        updates[battle.player.instance_id]=battle.player.raw()
        conf["collection"]=[updates.get(raw["instance_id"],raw) for raw in conf["collection"]]

    async def sync_battle_player(self,battle):
        conf=await self.config.user_from_id(battle.user_id).all()
        self.apply_battle_party(conf,battle)
        gym=GYMS.get(battle.gym_key) if battle.battle_kind=="gym" else None
        if gym:
            defeated=f"Gym Leader {gym.leader}'s {SPECIES[battle.wild_species_id].name} fainted."
            battle.result=(battle.result or "Victory!").replace("The wild Pokémon fainted.",defeated,1)
            badges=earned_badges(conf.get("badges",[]))
            if gym.key not in badges:
                badges.append(gym.key)
                battle.result+=f" Earned the {gym.badge}!"
            conf["badges"]=badges
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
            if battle.battle_kind=="gym":
                await i.response.send_message("Poké Balls cannot be used in a Gym battle.",ephemeral=True);return
            conf=await self.config.user(i.user).all();tx_key=f"{eid}:{battle.rolls}";tx=conf["transactions"].get(tx_key,{})
            if len(conf["collection"])>=MAX_COLLECTION and not tx.get("settled"):
                await i.response.send_message(f"Your {MAX_BOXES} boxes are full.",ephemeral=True);return
            if conf["balls"]<1 and not tx.get("ball_charged"):await i.response.send_message("You have no Poké Balls.",ephemeral=True);return
            if not tx.get("ball_charged"):
                conf["balls"]-=1;tx["ball_charged"]=True;conf["transactions"][tx_key]=tx
            try:caught=battle.throw_ball()
            except BattleError as e:await i.response.send_message(str(e),ephemeral=True);return
            if caught:
                self.apply_battle_party(conf,battle)
                identity=f"catch-{battle.user_id}-{eid}";pokemon=battle.caught(identity)
                if not any(p["instance_id"]==identity for p in conf["collection"]):conf["collection"].append(pokemon.raw())
                seen={int(value) for value in conf.get("pokedex_seen",[])}
                caught_ids={int(value) for value in conf.get("pokedex_caught",[])}
                seen.add(pokemon.species_id);caught_ids.add(pokemon.species_id)
                conf["pokedex_seen"]=sorted(seen);conf["pokedex_caught"]=sorted(caught_ids)
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
        if setup_hint:footer+=f" · Server setup: {setup_hint}"
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
            conf["collection"]=[pokemon.raw()];conf["party"]=[pokemon.instance_id];conf["starter_chosen"]=True;conf["pokedex_seen"]=[sid];conf["pokedex_caught"]=[sid]
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

    @pokemon.command(name="bag")
    async def pokemon_bag(self,ctx):
        """View your available medicine and items."""
        conf=await self.config.user(ctx.author).all();items=conf.get("items",{})
        await ctx.send(f"**Medicine**\nPotion: **{int(items.get('potion',0))}** · Revive: **{int(items.get('revive',0))}**")

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
            pokemon.current_hp=max(1,maximum//2);pokemon.status="";raw.update(pokemon.raw())
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
                pokemon=OwnedPokemon.from_raw(raw);pokemon.current_hp=pokemon_max_hp(pokemon);pokemon.status=""
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
            battle=Battle(eid,ctx.author.id,ctx.guild.id,ctx.channel.id,0,lead,gym.species_id,gym.level,Battle.stat(lead,"hp"),1,seed=random.SystemRandom().randrange(1,2**31),battle_kind="gym",gym_key=gym.key)
            battle.initialize_party(party);battle.wild_hp=battle.wild_max_hp
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
    async def profile(self,ctx):
        """View your trainer profile and Kanto badge case."""
        conf=await self.config.user(ctx.author).all()
        await ctx.send(embed=trainer_profile_embed(ctx.author,conf,MAX_COLLECTION))
    @commands.group(name="pokemonset",aliases=["pkmnset"],invoke_without_command=True)
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
        channels=await self.config.guild(ctx.guild).channels()
        if channel.id not in channels:channels.append(channel.id)
        await self.config.guild(ctx.guild).channels.set(channels);await self.config.guild(ctx.guild).enabled.set(True)
        await ctx.send(f"Wild encounters enabled in {channel.mention}.")
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

    @pokemon_set.command(name="status")
    async def spawn_status(self,ctx):
        """Show effective server encounter settings."""
        conf=await self.config.guild(ctx.guild).all();policy=await self.config.all()
        minimum,maximum,cooldown=bounded_pace(conf["threshold_min"],conf["threshold_max"],conf["spawn_cooldown"],policy)
        generations=effective_generations(conf["generations"],policy["allowed_generations"])
        channels=", ".join(f"<#{value}>" for value in conf["channels"]) or "None"
        await ctx.send(
            f"Enabled: **{conf['enabled']}**\n"
            f"Channels: {channels}\n"
            f"Threshold: {minimum}–{maximum}\n"
            f"Pace: {conf['pace']}\n"
            f"Cooldown: {cooldown}s\n"
            f"Encounter/battle expiry: {policy['encounter_timeout']}s/{conf['battle_timeout']}s\n"
            f"Generations: {', '.join(map(str,generations))}\n"
            f"Active: {conf['active_encounter'] or 'None'}\n"
            f"Catalog species: {len(SPECIES)}"
        )
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
    async def force_spawn(self,ctx,channel:discord.TextChannel=None):
        """Trigger a cooldown-limited test encounter."""
        channel=channel or ctx.channel
        conf=await self.config.guild(ctx.guild).all()
        if conf["active_encounter"]:await ctx.send("This server already has an encounter.");return
        if not await self.bot.is_owner(ctx.author) and conf["last_spawn_at"]:
            policy=await self.config.all();_,_,cooldown=bounded_pace(conf["threshold_min"],conf["threshold_max"],conf["spawn_cooldown"],policy)
            try:last=datetime.fromisoformat(conf["last_spawn_at"])
            except (TypeError,ValueError):last=None
            remaining=max(0,round((last+timedelta(seconds=cooldown)-datetime.now(timezone.utc)).total_seconds())) if last else 0
            if remaining:await ctx.send(f"The bot-wide spawn cooldown is active for another {remaining}s.");return
        await self.spawn(channel)
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
        if raw:
            channel=self.bot.get_channel(int(raw.get("channel_id",0)))
            if channel:
                try:
                    message=await channel.fetch_message(int(raw["message_id"]))
                    if raw.get("kind")=="gym":
                        content="This Gym challenge was ended by server staff."
                    else:
                        species=SPECIES.get(int(raw.get("species_id",0)))
                        content=f"The wild {species.name} got away." if species else "The wild Pokémon got away."
                    await message.edit(content=content,view=None)
                except (discord.Forbidden,discord.NotFound,discord.HTTPException,KeyError,TypeError,ValueError):
                    pass
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
