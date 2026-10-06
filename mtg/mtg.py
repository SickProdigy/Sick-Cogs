import asyncio
import logging
import secrets
import math
import aiohttp
from typing import Dict
import discord
from redbot.core import Config, commands
from redbot.core.data_manager import cog_data_path
from .ai import DIFFICULTIES, advance_solo
from .art import HAND_PAGE_SIZE, ArtError, ScryfallArtCache, render_battlefield, render_hand
from .cards import BASE_CARDS, CARDS
from .catalog import ALPHA_CARDS, ALPHA_SET, search_alpha
from .engine import Game, GameError
from .views import CatalogDetailView, CatalogView, ChallengeView, GameView, HandPaginationView, LibrarySearchView, NaturalSelectionView, PrivateHandDecisionView

log=logging.getLogger("red.sick-cogs.MTG")
CONFIG_IDENTIFIER=813604927115
DEFAULTS={"schema":1,"next_game_id":1,"games":{}}
MATCH_TIMEOUT_SECONDS=7*24*60*60

class MTG(commands.Cog):
    """Play a deliberately bounded solo or two-player Magic rules prototype."""
    __author__="SickProdigy"
    __version__="0.120.0"
    def __init__(self,bot):
        self.bot=bot; self.config=Config.get_conf(self,identifier=CONFIG_IDENTIFIER,force_registration=True)
        self.config.register_global(**DEFAULTS); self.games:Dict[int,Game]={}; self.locks={}; self.channels={}
        self.storage_lock=asyncio.Lock(); self.cleanup_task=None; self.session=None; self.art_cache=None
    async def cog_load(self):
        self.session=aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20))
        self.art_cache=ScryfallArtCache(cog_data_path(self)/"art_cache",self.session)
        raw=await self.config.games()
        for key,value in raw.items():
            try:
                game=Game.from_raw(value); game.message_id=int(value.get("message_id",0))
                self.games[game.game_id]=game; self.channels[game.game_id]=int(value.get("channel_id",0))
            except Exception: log.exception("Could not restore MTG game %s",key)
        await self.cleanup_expired()
        resumed=await self.resume_solo_games()
        for game in self.games.values():
            if not game.finished: self.bot.add_view(GameView(self,game.game_id),message_id=game.message_id or None)
        for game in resumed: asyncio.create_task(self.refresh_message(game))
        self.cleanup_task=asyncio.create_task(self._cleanup_loop())
    def cog_unload(self):
        if self.cleanup_task: self.cleanup_task.cancel()
        if self.session and not self.session.closed: asyncio.create_task(self.session.close())
    def lock(self,gid): return self.locks.setdefault(gid,asyncio.Lock())
    def human_players(self,game):
        return [user for user in game.order if user != getattr(game,"ai_user",None)]
    def ensure_players_available(self,*users):
        for game in self.games.values():
            if not game.finished and any(user in self.human_players(game) for user in users):
                raise GameError("One player already has an active game.")
    async def create_game(self,a,b,channel):
        async with self.storage_lock:
            self.ensure_players_available(a,b)
            gid=await self.config.next_game_id(); await self.config.next_game_id.set(gid+1)
            users=[a,b]; secrets.SystemRandom().shuffle(users)
            game=Game(gid,users); game.message_id=0; self.games[gid]=game; self.channels[gid]=channel
            await self._save_unlocked(game)
        return game
    async def create_solo_game(self,human,ai,channel,deck,difficulty):
        async with self.storage_lock:
            self.ensure_players_available(human)
            gid=await self.config.next_game_id(); await self.config.next_game_id.set(gid+1)
            users=[human,ai]; secrets.SystemRandom().shuffle(users)
            other="green" if deck=="red" else "red"
            game=Game(gid,users,decks={human:deck,ai:other},ai_user=ai,ai_difficulty=difficulty)
            game.message_id=0; self.games[gid]=game; self.channels[gid]=channel
            advance_solo(game)
            await self._save_unlocked(game)
        return game
    async def _save_unlocked(self,game):
        raw=game.to_raw(); raw["channel_id"]=self.channels.get(game.game_id,0); raw["message_id"]=getattr(game,"message_id",0)
        games=await self.config.games(); games[str(game.game_id)]=raw; await self.config.games.set(games)
    async def save(self,game):
        async with self.storage_lock: await self._save_unlocked(game)
    async def resume_solo_games(self):
        resumed=[]
        for game in list(self.games.values()):
            async with self.lock(game.game_id):
                if advance_solo(game):
                    await self.save(game); resumed.append(game)
        return resumed
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
        if g.ai_user is not None:
            names[g.ai_user]=f"{names[g.ai_user]} ({(g.ai_difficulty or 'easy').title()} AI)"
        e=discord.Embed(title=f"MTG prototype · Game {g.game_id}",color=discord.Color.dark_green())
        if g.phase=="opening":
            e.description=f"Opening hands - **{names[g.active_user]}** will play first."
        else:
            e.description=f"Turn **{g.turn}** - **{g.phase.replace('_',' ').title()}**\nActive: **{names[g.active_user]}**"
            if g.phase=="camouflage": e.description+=f"\n**{names[g.opponent(g.active_user)]}** must divide chosen creatures into {len(g.attackers)} Camouflage piles; use battlefield positions with `camouflage`."
            elif g.priority_user: e.description+=f"\nPriority: **{names[g.priority_user]}**"
            elif g.phase=="attackers": e.description+=f"\nWaiting for **{names[g.active_user]}** to declare attackers."
            elif g.phase=="blockers": e.description+=f"\nWaiting for **{names[g.opponent(g.active_user)]}** to declare blockers."
        for user in g.order:
            p=g.players[user]; field=[]
            for n,x in enumerate(p.battlefield,1):
                c=g.card(x.uid); state=" ↷" if x.tapped else ""
                if x.owner in g.players and x.owner!=user: state+=f" - owned by {names[x.owner]}"
                stats=(lambda value:f" {value[0]}/{value[1]}")(g.current_stats(x)) if g.is_creature(x) else ""
                current_keywords=g.current_keywords(x); active=sorted(current_keywords-set(c.keywords)); suppressed=sorted(set(c.keywords)-current_keywords)
                active_protection=sorted(g.current_protections(x)-set(c.protection_colors))
                ability_parts=[c.ability_text] if c.ability_text else []
                if active: ability_parts.append("Active: "+", ".join(word.title() for word in active))
                if suppressed: ability_parts.append("Suppressed: "+", ".join(word.title() for word in suppressed))
                if active_protection: ability_parts.append("Active protection: "+"/".join(active_protection))
                current_colors=g.current_colors(x)
                if current_colors!=c.colors: ability_parts.append("Color: "+"/".join({"W":"White","U":"Blue","B":"Black","R":"Red","G":"Green"}[color] for color in current_colors))
                live_land_types=g.current_land_types(x)
                printed_land_types={kind for kind in ("plains","island","swamp","mountain","forest") if c.has_land_type(kind)}
                if live_land_types!=printed_land_types: ability_parts.append("Land type: "+"/".join(kind.title() for kind in sorted(live_land_types)))
                live_mana=g.current_mana_choices(x)
                if c.land and live_mana!=c.produces: ability_parts.append("Mana now: "+"/".join(live_mana))
                if x.animated_until_end_combat: ability_parts.append("Animated: 3/6 Golem artifact creature until end of combat")
                granted_regeneration=g.granted_regeneration_cost(x)
                if granted_regeneration: ability_parts.append(f"Granted: {granted_regeneration}: Regenerate this creature")
                if x.regeneration_shields: ability_parts.append(f"Regeneration shield ×{x.regeneration_shields}")
                if x.damage_prevention: ability_parts.append(f"Damage prevention remaining: {x.damage_prevention}")
                if c.hydra_damage_replacement: ability_parts.append("Hydra replacement order: "+("counters first" if x.hydra_counters_first else "shields/redirection first"))
                if x.redirect_damage_to_owner: ability_parts.append(f"Next damage redirected to owner: {x.redirect_damage_to_owner}")
                if x.redirect_source_damage_to_player:
                    sources=[g.card(uid).name if uid in g.cards else f"source {uid}" for uid in x.redirect_source_damage_to_player]
                    ability_parts.append("Jade Monolith redirects: "+", ".join(sources))
                if x.plus_one_counters: ability_parts.append(f"+1/+1 counters: {x.plus_one_counters}")
                if x.power_counters: ability_parts.append(f"+1/+0 counters: {x.power_counters}")
                if x.corpse_counters: ability_parts.append(f"Corpse counters: {x.corpse_counters}")
                if x.vitality_counters: ability_parts.append(f"Vitality counters: {x.vitality_counters}")
                mire_counters=sum(effect.get("kind")=="mire" for effect in x.land_type_effects)
                if mire_counters: ability_parts.append(f"Mire counters: {mire_counters}")
                if x.attached_to:
                    _,target=g.find_permanent(x.attached_to)
                    ability_parts.append("Attached to "+(g.card(target.uid).name if target else "missing permanent"))
                abilities=" ["+" / ".join(ability_parts)+"]" if ability_parts else ""
                field.append(f"{n}. {c.name}{stats}{abilities}{state}")
            pool=" ".join(f"{{{symbol}}}×{count}" for symbol,count in sorted(p.mana_pool.items()))
            value="\n".join(field) or "No permanents"
            value+=f"\nGraveyard: {len(p.graveyard)} · Exile: {len(p.exile)}"
            if pool: value+=f"\nMana pool: {pool}"
            if p.damage_prevention: value+=f"\nDamage prevention remaining: {p.damage_prevention}"
            if p.damage_taken_this_turn: value+=f"\nDamage taken this turn: {p.damage_taken_this_turn}"
            if p.channel_active: value+="\nChannel: pay life for {C} until end of turn"
            if p.guardian_angel_active: value+="\nGuardian Angel: pay {1} to prevent the next 1 damage to any target this turn"
            if p.source_damage_prevention:
                sources=[g.card(uid).name if uid in g.cards else f"source {uid}" for uid in p.source_damage_prevention]
                value+="\nChosen-source prevention: "+", ".join(sources)
            if p.source_damage_lifegain:
                sources=[g.card(uid).name if uid in g.cards else f"source {uid}" for uid in p.source_damage_lifegain]
                value+="\nReverse Damage awaiting: "+", ".join(sources)
            if p.source_damage_caps:
                sources=[g.card(uid).name if uid in g.cards else f"source {uid}" for uid in p.source_damage_caps]
                value+="\nForcefield awaiting: "+", ".join(sources)
            if p.bodyguard_choice:
                value+="\nVeteran Bodyguard choice: "+(g.card(p.bodyguard_choice).name if p.bodyguard_choice in g.cards else f"source {p.bodyguard_choice}")
            e.add_field(name=f"{names[user]} · {p.life} life · {len(p.hand)} cards",value=value,inline=False)
        if g.phase=="untap" and g.untap_pending:
            pending=set(g.untap_pending); player=g.player(g.active_user)
            choices=[f"{position}. {g.card(permanent.uid).name}" for position,permanent in enumerate(player.battlefield,1) if permanent.uid in pending]
            e.add_field(name="Restricted untap choice",value="Choose a maximal legal set: "+", ".join(choices),inline=False)
        if g.extra_turns:
            queued=" → ".join(names[user] for user in g.extra_turns)
            e.add_field(name="Extra turns queued",value=queued,inline=False)
        if g.turn_start_pending_user is not None:
            kind="extra turn" if g.turn_start_pending_extra else "turn"
            e.add_field(name="Time Vault turn choice",value=f"{names[g.turn_start_pending_user]} must take their {kind} or choose a tapped Time Vault and skip it.",inline=False)
        if g.sanctuary_draw_pending:
            e.add_field(name="Island Sanctuary draw choice",value=f"{names[g.active_user]} must choose Draw or Skip ({g.sanctuary_pending_draws} draw{'s' if g.sanctuary_pending_draws!=1 else ''} remaining).",inline=False)
        if g.phase=="cleanup_discard":
            e.add_field(name="Cleanup discard",value=f"{names[g.active_user]} must privately discard {max(0,len(g.player(g.active_user).hand)-7)} card(s) to reach seven.",inline=False)
        protected=[names[user] for user in g.order if g.player(user).island_sanctuary_active]
        if protected: e.add_field(name="Island Sanctuary protection",value=", ".join(protected)+" can be attacked only by creatures with flying or islandwalk.",inline=False)
        if g.prevent_combat_damage:
            e.add_field(name="Turn effect",value="All combat damage is prevented this turn.",inline=False)
        if g.forced_attackers:
            forced=[g.card(uid).name for uid in g.forced_attackers if g.find_permanent(uid)[1] is not None]
            if forced: e.add_field(name="Must attack this combat if able",value=", ".join(forced),inline=False)
        if g.attack_bands:
            bands=[" + ".join(g.card(uid).name for uid in band if g.find_permanent(uid)[1] is not None) for band in g.attack_bands]
            bands=[band for band in bands if band]
            if bands: e.add_field(name="Attacking bands",value="\n".join(bands),inline=False)
        if g.raging_river_rules:
            lines=[]
            for index,rule in enumerate(g.raging_river_rules,1):
                attackers=", ".join(f"{g.card(uid).name}: {side}" for uid,side in rule["attackers"].items() if uid in g.cards) or "No attackers"
                blockers=", ".join(f"{g.card(uid).name}: {side}" for uid,side in rule["blockers"].items() if uid in g.cards) or "No nonflying blockers"
                lines.append(f"River {index} — attackers: {attackers}; defenders: {blockers}")
            e.add_field(name="Raging River divisions",value="\n".join(lines),inline=False)
        if g.trample_assignments:
            choices=[]
            for uid,amount in g.trample_assignments.items():
                _,attacker=g.find_permanent(uid)
                if attacker is not None: choices.append(f"{g.card(uid).name}: {amount} to blocker")
            if choices: e.add_field(name="Trample assignments",value="\n".join(choices),inline=False)
        multi=[]
        for blocker_uid in g.all_blocker_uids():
            _,blocker=g.find_permanent(blocker_uid); attackers=[uid for uid in g.attackers_for(blocker_uid) if g.find_permanent(uid)[1] is not None]
            if blocker is None or len(attackers)<2: continue
            assignment=g.blocker_damage_assignments.get(blocker_uid,[])
            if assignment:
                detail=", ".join(f"{g.card(item['attacker']).name}: {item['damage']}" for item in assignment)
            else: detail="damage assignment required before its damage step"
            multi.append(f"{g.card(blocker_uid).name} blocks {len(attackers)} attackers · {detail}")
        for attacker_uid in g.attackers:
            blockers=[uid for uid in g.blockers_for(attacker_uid) if g.find_permanent(uid)[1] is not None]
            if len(blockers)<2: continue
            assignment=g.attacker_damage_assignments.get(attacker_uid,[])
            detail=", ".join(f"{g.card(item['blocker']).name}: {item['damage']}" for item in assignment) if assignment else "damage assignment required before its damage step"
            multi.append(f"{g.card(attacker_uid).name} is blocked by {len(blockers)} creatures · {detail}")
        if multi: e.add_field(name="Multiple blocking",value="\n".join(multi),inline=False)
        if g.stack:
            stack_lines=[]
            for position,item in enumerate(reversed(g.stack),1):
                label=("Face-down creature spell" if item.face_down else g.card(item.uid).name+(" ability" if item.ability_effect else ""))+(" copy" if item.is_copy else "")
                if item.color_override: label+=f" [{item.color_override}]"
                if (not item.ability_effect and "{X}" in g.card(item.uid).mana_cost) or g.card(item.uid).activation_x_choice: label+=f" (X={item.x_value})"
                if item.ability_effect=="add_power_counters": label+=f" (add {item.choice_value})"
                if not item.ability_effect and g.card(item.uid).effect in ("fireball","volcanic_eruption"):
                    targets=[]
                    for stable in (item.target or "").split(",") if item.target else []:
                        if ":" in stable:
                            _,target=g.find_permanent(int(stable.split(":")[1])); targets.append(g.card(target.uid).name if target is not None else "departed permanent")
                        else: targets.append(names.get(int(stable),stable))
                    label+=" (targets: "+(", ".join(targets) if targets else "none")+")"
                if not item.ability_effect and g.card(item.uid).effect=="sacrifice_mana": label+=f" (adds {{{g.card(item.uid).sacrifice_mana_color}}}×{item.choice_value})"
                if item.ability_effect=="upkeep_untapped_land_damage": label+=f" ({item.choice_value} damage snapshot)"
                if item.ability_effect=="prevent_source_damage" and item.target:
                    source_uid=int(item.target.split(":")[1]); label+=f" (source: {g.card(source_uid).name if source_uid in g.cards else source_uid})"
                if item.decision_pending:
                    if item.ability_effect=="mask_choose": label+=" (controller is privately choosing an eligible creature or declining)"
                    elif item.ability_effect=="word_choose": label+=f" ({item.choice_owner} is privately choosing a card from the targeted hand)"
                    elif item.ability_effect=="lich_damage": label+=f" ({item.choice_owner} must sacrifice {item.choice_value} nontoken permanents)"
                    elif item.ability_effect=="raging_river_split": label+=f" ({item.choice_owner} must divide nonflying defenders left/right)"
                    elif item.ability_effect=="raging_river_attackers": label+=f" ({item.choice_owner} must divide attackers left/right)"
                    elif item.is_copy and item.fork_retarget: label+=f" ({item.choice_owner} must choose new targets or keep the originals)"
                    elif not item.ability_effect and g.card(item.uid).effect=="search_library": label+=" (controller is searching their library)"
                    elif not item.ability_effect and g.card(item.uid).effect=="natural_selection": label+=" (controller is privately arranging the targeted library)"
                    elif not item.ability_effect and g.card(item.uid).effect in ("text_change_land","text_change_color"): label+=" (controller must choose the word replacement)"
                    elif not item.ability_effect and g.card(item.uid).effect=="false_orders": label+=" (controller must choose its new blocking assignment or decline)"
                    elif not item.ability_effect and g.card(item.uid).enters_copy_types: label+=" (controller is choosing a permanent to copy)"
                    elif not item.ability_effect and g.card(item.uid).effect=="drain_power": label+=" (target player is choosing land mana)"
                    elif not item.ability_effect and g.card(item.uid).effect=="power_sink": label+=f" (targeted spell's controller may {g.trigger_accept_label(item)} or decline)"
                    elif item.ability_effect=="discard_choice": label+=" (target player is choosing a card privately)"
                    elif item.ability_effect=="look_hand": label+=" (controller is viewing the targeted hand privately)"
                    elif item.ability_effect=="leng_discard": label+=f" ({item.choice_owner} is privately choosing a discard destination with Library of Leng)"
                    elif item.ability_effect=="power_leak": label+=" (enchanted enchantment's controller must choose how much mana to pay)"
                    elif item.ability_effect=="vesuvan_copy": label+=(" (controller must choose its creature target before responses)" if item.choice_value==0 else " (controller must choose whether to become the targeted copy)")
                    elif item.ability_effect=="kudzu_move": label+=" (the controller of the destroyed land may reattach Kudzu or decline)"
                    elif item.ability_effect.startswith("balance_"): label+=f" ({item.choice_owner} must complete the {item.ability_effect.split('_',1)[1]} choice)"
                    else: label+=(f" (chooser must {g.trigger_accept_label(item)})" if item.ability_effect in ("upkeep_sacrifice","opponent_land_sacrifice","tomb_cleanup") else f" (controller may {g.trigger_accept_label(item)} or Decline)")
                stack_lines.append(f"S:{position}. {label}")
            e.add_field(name="Stack · spells targetable with S:POSITION",value="\n".join(stack_lines),inline=False)
        if g.end_combat_destroys:
            pending=[]
            for item in g.end_combat_destroys:
                _,target=g.find_permanent(int(item.target.split(":")[1]))
                action="remove a +1/+0 counter from" if item.ability_effect=="end_combat_remove_power_counter" else "destroy"
                pending.append(f"{g.card(item.uid).name}: {action} "+(g.card(target.uid).name if target else "departed creature"))
            e.add_field(name="Pending end-of-combat triggers",value="\n".join(pending),inline=False)
        if g.end_step_sacrifices:
            e.add_field(name="Pending end-step trigger",value="Sacrifice "+", ".join(g.card(uid).name for uid in g.end_step_sacrifices)+" · players may respond",inline=False)
        if g.end_step_destroys:
            pending_names=[]
            for trigger in g.end_step_destroys:
                _,target=g.find_permanent(int(trigger.target.split(":",1)[1])) if trigger.target and ":" in trigger.target else (None,None)
                pending_names.append(g.card(target.uid).name if target is not None else "departed target")
            e.add_field(name="Next end-step destruction",value=", ".join(pending_names),inline=False)
        if g.finished: e.description=f"Winner: **{names[g.winner]}** - {g.finished_reason}." if g.winner else f"Match ended - {g.finished_reason}."
        e.set_footer(text="Experimental supported-card subset · hands are private")
        return e
    async def game_message(self,game):
        embed=self.game_embed(game); paths={}
        public_cards=[game.card(permanent.uid) for player in game.players.values() for permanent in player.battlefield]
        public_cards.extend(game.card(spell.uid) for spell in game.stack)
        for card in {card.key:card for card in public_cards}.values():
            try: paths[card.key]=await self.art_cache.get(card)
            except (ArtError,aiohttp.ClientError,asyncio.TimeoutError,OSError):
                log.warning("Could not cache public art for %s",card.key,exc_info=True)
        names={user:str(self.bot.get_user(user).display_name if self.bot.get_user(user) else user).replace("\n"," ")[:32] for user in game.order}
        try:
            background=__import__("pathlib").Path(__file__).with_name("assets")/"default_playmat.png"
            image=await asyncio.to_thread(render_battlefield,game,names,paths,background)
            file=discord.File(image,filename=f"mtg-table-{game.game_id}.png")
            embed.set_image(url=f"attachment://mtg-table-{game.game_id}.png")
            return embed,file
        except (ArtError,OSError):
            log.warning("Could not render public battlefield",exc_info=True)
            return embed,None
    async def refresh_message(self,game):
        try:
            channel=self.bot.get_channel(self.channels.get(game.game_id)); message=await channel.fetch_message(game.message_id)
            embed,file=await self.game_message(game)
            await message.edit(embed=embed,attachments=[file] if file else [],view=None if game.finished else GameView(self,game.game_id))
        except (discord.HTTPException,AttributeError): pass
    async def send_hand(self,interaction,game_id,page,editing=False):
        game=self.games.get(game_id)
        if not game or interaction.user.id not in game.order:
            content="This private hand is unavailable."
            if editing: await interaction.edit_original_response(content=content,attachments=[],view=None)
            else: await interaction.followup.send(content,ephemeral=True)
            return
        cards=game.hand(interaction.user.id); pages=max(1,math.ceil(len(cards)/HAND_PAGE_SIZE))
        page=max(0,min(page,pages-1)); start=page*HAND_PAGE_SIZE; visible=cards[start:start+HAND_PAGE_SIZE]
        visible_paths=[]
        for card in visible:
            try: visible_paths.append(await self.art_cache.get(card))
            except (ArtError,aiohttp.ClientError,asyncio.TimeoutError,OSError):
                log.warning("Could not cache art for %s",card.key,exc_info=True); visible_paths.append(None)
        paths=[None]*start+visible_paths
        text="\n".join(f"**{start+n}. {card.name}** - {card.kind}, {card.mana_cost or 'no mana cost'}" for n,card in enumerate(visible,1)) or "Your hand is empty."
        view=HandPaginationView(self,game_id,interaction.user.id,page,pages) if pages>1 else None
        try:
            image=await asyncio.to_thread(render_hand,cards,paths,page)
            file=discord.File(image,filename=f"mtg-hand-{game_id}-{page+1}.png")
            if editing: await interaction.edit_original_response(content=text,attachments=[file],view=view)
            else: await interaction.followup.send(text,file=file,view=view,ephemeral=True)
        except (ArtError,OSError):
            log.warning("Could not render private hand",exc_info=True)
            if editing: await interaction.edit_original_response(content=text,attachments=[],view=view)
            else: await interaction.followup.send(text,view=view,ephemeral=True)
    async def send_library_search(self,interaction,game_id,page,editing=False):
        game=self.games.get(game_id); user=interaction.user.id
        pending=bool(game and user in game.order and game.stack and game.stack[-1].decision_pending and game._spell_decider(game.stack[-1])==user and not game.stack[-1].ability_effect and game.card(game.stack[-1].uid).effect=="search_library")
        if not pending:
            content="This private library search is unavailable."
            if editing: await interaction.edit_original_response(content=content,view=None)
            else: await interaction.followup.send(content,ephemeral=True)
            return
        entries=game.library_search(user); pages=max(1,math.ceil(len(entries)/LibrarySearchView.page_size)); page=max(0,min(page,pages-1)); start=page*LibrarySearchView.page_size; visible=entries[start:start+LibrarySearchView.page_size]
        text="Demonic Tutor - private library search\n"+"\n".join(f"**{position}. {card.name}** - {card.kind}, {card.mana_cost or 'no mana cost'}" for position,card in visible)
        view=LibrarySearchView(self,game_id,user,page,pages,game)
        if editing: await interaction.edit_original_response(content=text,view=view)
        else: await interaction.followup.send(text,view=view,ephemeral=True)
    async def send_natural_selection(self,interaction,game_id,editing=False):
        game=self.games.get(game_id); user=interaction.user.id
        try: _,target,entries=game.natural_selection_decision(user) if game else (_ for _ in ()).throw(GameError("This private library choice is unavailable."))
        except GameError:
            content="This private library choice is unavailable."
            if editing: await interaction.edit_original_response(content=content,view=None)
            else: await interaction.followup.send(content,ephemeral=True)
            return
        text=f"Natural Selection - top of {target.user_id}'s library\n"+"\n".join(f"**{position}. {card.name}** - {card.kind}, {card.mana_cost or 'no mana cost'}" for position,card in entries)
        view=NaturalSelectionView(self,game_id,user,entries)
        if editing: await interaction.edit_original_response(content=text,view=view)
        else: await interaction.followup.send(text,view=view,ephemeral=True)
    async def complete_natural_selection(self,interaction,game_id,order,shuffle):
        game=self.games.get(game_id)
        if not game: await interaction.response.send_message("This match is unavailable.",ephemeral=True); return
        async with self.lock(game.game_id):
            try: game.choose_natural_selection(interaction.user.id,order,shuffle); game.record(interaction.user.id,"natural_selection_shuffle" if shuffle else "natural_selection_order"); advance_solo(game); await self.save(game)
            except (GameError,ValueError) as error: await interaction.response.send_message(str(error),ephemeral=True); return
        await interaction.response.edit_message(content="Natural Selection completed.",view=None); await self.refresh_message(game)

    async def send_private_hand_decision(self,interaction,game_id,page,editing=False):
        game=self.games.get(game_id); user=interaction.user.id
        try: item,entries=game.private_hand_decision(user) if game else (_ for _ in ()).throw(GameError("This private hand decision is unavailable."))
        except GameError:
            content="This private hand decision is unavailable."
            if editing: await interaction.edit_original_response(content=content,view=None)
            else: await interaction.followup.send(content,ephemeral=True)
            return
        pages=max(1,math.ceil(len(entries)/PrivateHandDecisionView.page_size)); page=max(0,min(page,pages-1)); start=page*PrivateHandDecisionView.page_size
        visible=entries[start:start+PrivateHandDecisionView.page_size]
        effect=item.ability_effect if item is not None else "cleanup_discard"; name=game.card(item.uid).name if item is not None else "Cleanup"
        if effect=="discard_choice": heading=f"{name} - choose one card to discard"
        elif effect=="balance_hand": heading=f"{name} - privately choose {game._balance_required(item,user)} cards to discard"
        elif effect=="cleanup_discard": heading=f"Cleanup - privately choose {len(game.player(user).hand)-7} cards to discard"
        elif effect=="leng_discard": heading=f"Library of Leng - choose a destination for {visible[0][1].name}"
        elif effect=="word_choose": heading=f"Word of Command - choose a card to play from {item.target}s hand; use the word command for targets or X"
        elif effect=="mask_choose": heading=f"Illusionary Mask - privately choose an eligible creature or decline"
        else: heading=f"{name} - targeted hand"
        text=heading+"\n"+("\n".join(f"**{position}. {card.name}** - {card.kind}, {card.mana_cost or 'no mana cost'}" for position,card in visible) or "The targeted hand is empty.")
        view=PrivateHandDecisionView(self,game_id,user,page,pages,effect,entries)
        if editing: await interaction.edit_original_response(content=text,view=view)
        else: await interaction.followup.send(text,view=view,ephemeral=True)
    async def complete_private_hand_interaction(self,interaction,game_id,position):
        game=self.games.get(game_id)
        if not game:
            await interaction.response.send_message("This match is unavailable.",ephemeral=True); return
        async with self.lock(game.game_id):
            try:
                effect="cleanup_discard" if game.phase=="cleanup_discard" else game.stack[-1].ability_effect
                if effect=="cleanup_discard": game.choose_cleanup_discard(interaction.user.id,position if isinstance(position,(list,tuple)) else [position]); action="cleanup_discard"
                elif effect=="balance_hand": game.choose_balance(interaction.user.id,position); action="balance_hand_choice"
                elif effect=="word_choose": game.choose_word_command(interaction.user.id,position); action="word_of_command_choice"
                elif effect=="mask_choose": game.choose_illusionary_mask(interaction.user.id,position); action="illusionary_mask_choice"
                else: game.choose_private_hand(interaction.user.id,position); action="private_discard" if effect=="discard_choice" else "private_hand_view"
                game.record(interaction.user.id,action); advance_solo(game); await self.save(game)
            except (GameError,IndexError,ValueError) as error:
                await interaction.response.send_message(str(error),ephemeral=True); return
        message=("Illusionary Mask choice completed." if effect=="mask_choose" else "Word of Command choice completed." if effect=="word_choose" else "Card discarded." if position is not None else "Hand view completed.")
        await interaction.response.edit_message(content=message,view=None); await self.refresh_message(game)

    async def complete_discard_destination(self,interaction,game_id,to_library):
        game=self.games.get(game_id)
        if not game:
            await interaction.response.send_message("This match is unavailable.",ephemeral=True); return
        async with self.lock(game.game_id):
            try:
                game.choose_discard_destination(interaction.user.id,to_library); game.record(interaction.user.id,"library_of_leng_top" if to_library else "library_of_leng_graveyard"); advance_solo(game); await self.save(game)
            except (GameError,IndexError,ValueError) as error:
                await interaction.response.send_message(str(error),ephemeral=True); return
        await interaction.response.edit_message(content="Discard destination chosen.",view=None); await self.refresh_message(game)

    async def choose_library_interaction(self,interaction,game_id,position):
        game=self.games.get(game_id)
        if not game:
            await interaction.response.send_message("This match is unavailable.",ephemeral=True); return
        async with self.lock(game.game_id):
            try:
                game.choose_library(interaction.user.id,position); game.record(interaction.user.id,"search_library"); advance_solo(game); await self.save(game)
            except (GameError,IndexError,ValueError) as error:
                await interaction.response.send_message(str(error),ephemeral=True); return
        await interaction.response.edit_message(content="Library search completed; the chosen card was added to your hand.",view=None)
        await self.refresh_message(game)
    async def act(self,i,game_id,action,label):
        game=self.games.get(game_id)
        if not game:
            await i.response.send_message("This match is unavailable.",ephemeral=True); return
        async with self.lock(game.game_id):
            try:
                action(game); game.record(i.user.id,label); advance_solo(game); await self.save(game)
            except (GameError,IndexError,ValueError) as e: await i.response.send_message(str(e),ephemeral=True); return
            await i.response.defer()
            embed,file=await self.game_message(game)
            await i.edit_original_response(embed=embed,attachments=[file] if file else [],view=None if game.finished else GameView(self,game.game_id))
    async def mutate_ctx(self,ctx,action,label):
        game=self.find(ctx.author.id)
        async with self.lock(game.game_id):
            try:
                action(game); game.record(ctx.author.id,label); advance_solo(game); await self.save(game)
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
    @mtg.command(name="solo")
    @commands.guild_only()
    async def solo(self,ctx,deck:str="red",difficulty:str="easy"):
        """Start a reward-free solo match. Deck: red/green; difficulty: easy/normal."""
        deck=deck.casefold(); difficulty=difficulty.casefold()
        if deck in DIFFICULTIES and difficulty=="easy": difficulty,deck=deck,"red"
        if deck not in ("red","green") or difficulty not in DIFFICULTIES:
            await ctx.send(f"Use `{ctx.clean_prefix}mtg solo [red|green] [easy|normal]`."); return
        if not self.bot.user:
            await ctx.send("The solo opponent is not ready yet."); return
        try: game=await self.create_solo_game(ctx.author.id,self.bot.user.id,ctx.channel.id,deck,difficulty)
        except GameError as e: await ctx.send(str(e)); return
        embed,file=await self.game_message(game)
        message=await ctx.send(embed=embed,file=file,view=GameView(self,game.game_id)) if file else await ctx.send(embed=embed,view=GameView(self,game.game_id))
        game.message_id=message.id; await self.save(game)
    @mtg.command(name="status")
    async def status(self,ctx):
        try: game=self.find(ctx.author.id)
        except GameError as e: await ctx.send(str(e)); return
        async with self.lock(game.game_id):
            if advance_solo(game): await self.save(game)
        embed,file=await self.game_message(game)
        if file: await ctx.send(embed=embed,file=file)
        else: await ctx.send(embed=embed)
    def catalog_records(self,scope,search=""):
        records=[]
        if scope=="all":
            records.extend(("playable",card) for card in BASE_CARDS.values())
            records.extend(("alpha",card) for card in ALPHA_CARDS)
        elif scope=="playable":
            records.extend(("alpha" if card.set_code=="lea" else "playable",card) for card in CARDS.values())
        else: records.extend(("alpha",card) for card in ALPHA_CARDS)
        if search:
            folded=search.casefold()
            records=[item for item in records if folded in item[1].name.casefold() or folded in item[1].key.casefold()]
        return sorted(records,key=lambda item:(item[1].name.casefold(),item[1].key.casefold()))

    def catalog_embed(self,browser):
        start=browser.page*browser.page_size; visible=browser.records[start:start+browser.page_size]
        def cell(value,width):
            value=str(value).replace("\n"," ").replace("\r"," ").replace("`","'")
            return (value if len(value)<=width else value[:width-1]+"…").ljust(width)
        lines=[f"{'#':>3}  {'Card':<25} {'Mana':<9} {'Type':<12} Status",f"{'—'*3}  {'—'*25} {'—'*9} {'—'*12} {'—'*9}"]
        for number,(source,card) in enumerate(visible,start+1):
            if source=="alpha":
                status="Ready" if card.engine_status=="playable" else "Reference"
                card_type=card.type_line.split(" — ",1)[0]
            else:
                status="Ready"; card_type=card.kind.title()
            lines.append(f"{number:>3}  {cell(card.name,25)} {cell(card.mana_cost or '—',9)} {cell(card_type,12)} {status}")
        title={"all":"MTG card catalog","playable":"Supported playable catalog","alpha":"Limited Edition Alpha catalog"}[browser.scope]
        description="Choose a numbered row from the menu below to open its card.\n\n```text\n"+"\n".join(lines)+"\n```"
        embed=discord.Embed(title=title,description=description,color=discord.Color.dark_green())
        summary=f"Page {browser.page+1}/{browser.pages} · {len(browser.records)} matching records"
        if not browser.search and browser.scope=="all": summary+=f" · {len(CARDS)} playable definitions · {len(BASE_CARDS)} core + {ALPHA_SET['printing_count']} Alpha printings"
        embed.set_footer(text=summary+" · Select a row for card details")
        return embed

    def find_catalog_card(self,query):
        raw=query.strip(); folded=raw.casefold(); alpha_only=folded.startswith("alpha " )
        if alpha_only: raw=raw[6:].strip(); folded=raw.casefold()
        matches=[] if alpha_only else [card for card in CARDS.values() if folded==card.key.casefold() or folded==card.name.casefold()]
        if not matches and not alpha_only: matches=[card for card in CARDS.values() if folded in card.name.casefold()]
        alpha_matches=search_alpha(raw) if alpha_only or not matches else []
        if matches and matches[0].set_code=="lea": alpha_matches=search_alpha(matches[0].key); matches=[]
        if alpha_matches: return "alpha",alpha_matches[0],len(alpha_matches)
        if matches: return "playable",matches[0],len(matches)
        return None,None,0

    async def card_presentation(self,source,card,match_count=1):
        if source=="alpha":
            description=card.oracle_text or "No current Oracle rules text."
            color=discord.Color.dark_green() if card.engine_status=="playable" else discord.Color.dark_gold()
            embed=discord.Embed(title=card.name,description=description,color=color)
            embed.add_field(name="Type",value=card.type_line,inline=False)
            embed.add_field(name="Mana",value=card.mana_cost or "None")
            if card.power is not None: embed.add_field(name="Power / toughness",value=f"{card.power} / {card.toughness}")
            embed.add_field(name="Alpha printing",value=f"`{card.key}` · #{card.collector_number} · {card.rarity.title()}",inline=False)
            status=("Playable in the supported rules subset; not included in the fixed starters or pack pools." if card.engine_status=="playable" else "Reference only — its mechanics are not implemented yet.")
            embed.add_field(name="Engine status",value=status,inline=False)
            if match_count>1: embed.add_field(name="Alternate Alpha art",value=f"{match_count} printings share this card name; use an exact `lea:NUMBER` key.",inline=False)
            source_url=f"https://scryfall.com/card/lea/{card.collector_number}"
        else:
            embed=discord.Embed(title=card.name,description=card.text or "No rules text.",color=discord.Color.dark_green())
            embed.add_field(name="Type",value=card.kind); embed.add_field(name="Mana",value=card.mana_cost or "None")
            if card.creature: embed.add_field(name="Power / toughness",value=f"{card.power} / {card.toughness}")
            embed.add_field(name="Catalog key",value=f"`{card.key}`")
            embed.add_field(name="Engine status",value="Playable in the supported rules subset.",inline=False)
            source_url=f"https://scryfall.com/card/{card.scryfall_id}"
        embed.add_field(name="Oracle ID",value=f"`{card.oracle_id}`",inline=False)
        embed.add_field(name="Source",value=f"[Scryfall]({source_url})",inline=False)
        embed.set_footer(text="Card data and images: Scryfall. Unofficial fan content; not approved by Wizards.")
        filename=card.key.replace(":","-")+".jpg"
        try:
            path=await self.art_cache.get(card); file=discord.File(path,filename=filename); embed.set_image(url=f"attachment://{filename}")
            return embed,file
        except (ArtError,aiohttp.ClientError,asyncio.TimeoutError,OSError):
            log.warning("Could not load card detail art for %s",card.key,exc_info=True); return embed,None

    async def show_catalog_page(self,interaction,browser,page):
        view=CatalogView(self,browser.user_id,browser.records,browser.scope,browser.search,page)
        kwargs={"embed":self.catalog_embed(view),"attachments":[],"view":view}
        if interaction.response.is_done(): await interaction.edit_original_response(**kwargs)
        else: await interaction.response.edit_message(**kwargs)

    async def show_catalog_detail(self,interaction,browser,index):
        source,card=browser.records[index]
        embed,file=await self.card_presentation(source,card)
        attribution=embed.footer.text
        embed.set_footer(text=f"Card {index+1}/{len(browser.records)} · Up returns to page {browser.page+1}/{browser.pages} · {attribution}")
        await interaction.edit_original_response(embed=embed,attachments=[file] if file else [],view=CatalogDetailView(browser,index))

    @mtg.command(name="card")
    async def card_detail(self,ctx,*,query:str):
        """Show a supported card or an original Alpha printing."""
        if not query.strip(): await ctx.send("Give me a card name, catalog key, or Alpha printing key."); return
        source,card,count=self.find_catalog_card(query)
        if not card: await ctx.send("No catalog card matched that search."); return
        embed,file=await self.card_presentation(source,card,count)
        if file: await ctx.send(embed=embed,file=file,allowed_mentions=discord.AllowedMentions.none())
        else: await ctx.send(embed=embed,allowed_mentions=discord.AllowedMentions.none())

    @mtg.command(name="catalog")
    async def catalog(self,ctx,*,query:str=None):
        """Interactively browse cards. Syntax: catalog [all|playable|alpha] [page|search]."""
        tokens=(query or "").split(); scope="all"; page=1
        if tokens and tokens[0].casefold() in ("all","playable","alpha"): scope=tokens.pop(0).casefold()
        if tokens and tokens[0].isdigit(): page=int(tokens.pop(0))
        search=" ".join(tokens); records=self.catalog_records(scope,search)
        if not records: await ctx.send("No catalog cards matched that search."); return
        pages=math.ceil(len(records)/CatalogView.page_size)
        if page<1 or page>pages: await ctx.send(f"Choose a page from 1 to {pages}."); return
        view=CatalogView(self,ctx.author.id,records,scope,search,page-1)
        await ctx.send(embed=self.catalog_embed(view),view=view,allowed_mentions=discord.AllowedMentions.none())
    @mtg.command(name="graveyard")
    async def graveyard(self,ctx,member:discord.Member=None):
        """List a player graveyard and its G:POSITION spell targets."""
        game=self.find(ctx.author.id); target=member.id if member else ctx.author.id
        player=game.player(target)
        names={user:str(self.bot.get_user(user).display_name if self.bot.get_user(user) else user).replace("\n"," ")[:32] for user in game.order}
        lines=[f"{position}. {game.card(uid).name}" for position,uid in enumerate(player.graveyard,1)]
        if not lines: await ctx.send(f"{names[target]} has no cards in their graveyard.",allowed_mentions=discord.AllowedMentions.none()); return
        pages=[]; current=f"**{names[target]} graveyard** — bottom → top; target your own cards with G:POSITION or another player with USER_ID:G:POSITION\n"
        for line in lines:
            if len(current)+len(line)+1>1900: pages.append(current); current=""
            current+=line+"\n"
        pages.append(current)
        for page in pages: await ctx.send(page,allowed_mentions=discord.AllowedMentions.none())
    @mtg.command(name="mana")
    async def mana(self,ctx,position:int,color:str=None):
        """Tap a supported mana permanent. Multi-color sources require W/U/B/R/G; sick creatures cannot tap."""
        await self.mutate_ctx(ctx,lambda g:g.activate_mana(ctx.author.id,position,color),"mana")
    @mtg.command(name="vault")
    async def vault(self,ctx,choice:str,position:int=None):
        """Choose `take` or `skip POSITION` when a tapped Time Vault would let you skip a turn."""
        normalized=choice.casefold()
        if normalized not in ("take","skip") or (normalized=="skip" and position is None): await ctx.send("Choose `take` or `skip POSITION`."); return
        await self.mutate_ctx(ctx,lambda g:g.choose_time_vault_turn(ctx.author.id,normalized=="skip",position),f"vault_{normalized}")
    @mtg.command(name="sanctuary")
    async def sanctuary(self,ctx,choice:str):
        """Choose `draw` or `skip` for Island Sanctuary during your draw step."""
        normalized=choice.casefold()
        if normalized not in ("draw","skip"): await ctx.send("Choose `draw` or `skip`."); return
        await self.mutate_ctx(ctx,lambda g:g.choose_sanctuary_draw(ctx.author.id,normalized=="skip"),f"sanctuary_{normalized}")
    @mtg.command(name="channel")
    async def channel(self,ctx,amount:int=1):
        """While Channel is active, pay life to add that much colorless mana."""
        await self.mutate_ctx(ctx,lambda g:g.activate_channel(ctx.author.id,amount),"channel")
    @mtg.command(name="angel")
    async def angel(self,ctx,target:str):
        """While Guardian Angel is active, pay {1} to prevent the next 1 damage to PLAYER_ID or USER_ID:POSITION."""
        await self.mutate_ctx(ctx,lambda g:g.activate_guardian_angel(ctx.author.id,target),"guardian_angel")
    @mtg.command(name="incarnation")
    async def incarnation(self,ctx,controller_id:int,position:int):
        """Activate a Personal Incarnation you own while another player controls it."""
        await self.mutate_ctx(ctx,lambda g:g.activate_personal_incarnation(ctx.author.id,controller_id,position),"activate_owned_incarnation")
    @mtg.command(name="hydra")
    async def hydra(self,ctx,position:int,mode:str):
        """Use Rock Hydra `prevent` or upkeep-only `counter`."""
        await self.mutate_ctx(ctx,lambda g:g.activate_hydra(ctx.author.id,position,mode),f"hydra_{mode.casefold()}")
    @mtg.command(name="hydraorder")
    async def hydraorder(self,ctx,position:int,order:str):
        """Choose Rock Hydra replacement priority: `counters` or `shields`."""
        await self.mutate_ctx(ctx,lambda g:g.choose_hydra_order(ctx.author.id,position,order),"hydra_order")
    @mtg.command(name="mask")
    async def mask(self,ctx,position:int,x_value:int):
        """Activate Illusionary Mask at a battlefield position and pay X."""
        await self.mutate_ctx(ctx,lambda g:g.activate_illusionary_mask(ctx.author.id,position,x_value),"illusionary_mask")
    @mtg.command(name="maskpick")
    async def maskpick(self,ctx,choice:str):
        """Choose an eligible hand position for Illusionary Mask, or `decline`."""
        position=None
        if choice.casefold()!="decline":
            try: position=int(choice)
            except ValueError:
                await ctx.send("Choose an eligible hand position or `decline`."); return
        await self.mutate_ctx(ctx,lambda g:g.choose_illusionary_mask(ctx.author.id,position),"illusionary_mask_choice")
    @mtg.command(name="activate")
    async def activate(self,ctx,position:int,target:str=None,x_value:int=None,choice_value:int=None):
        """Activate an ability. Supply a target when needed; Clockwork Beast uses `- X COUNTERS`; Jade Monolith uses `SOURCE>TARGET`."""
        normalized=None if target and target.casefold() in {"-","none"} else target
        await self.mutate_ctx(ctx,lambda g:g.activate_ability(ctx.author.id,position,normalized,x_value,choice_value),"activate")
    @mtg.command(name="play")
    async def play(self,ctx,position:int,target:str=None,x_value:int=None):
        """Play/cast a hand position with optional target and X; comma-separated multi-target choices support Fireball and Volcanic Eruption; modal choices include tap:/untap: for Twiddle, life:/prevent: for Healing Salve, a source as S:POSITION or USER_ID:POSITION for Reverse Damage, TYPE:USER_ID:POSITION for Phantasmal Terrain, and sacrifice:FIELD_POSITION for Sacrifice."""
        normalized=None if target and target.casefold() in {"-","none"} else target
        await self.mutate_ctx(ctx,lambda g:g.play(ctx.author.id,position,normalized,x_value),"play")
    @mtg.command(name="forktarget")
    async def forktarget(self,ctx,target:str="keep"):
        """Keep Fork copy targets or provide the copied spell's normal target syntax."""
        await self.mutate_ctx(ctx,lambda g:g.choose_fork_target(ctx.author.id,target),"fork_target_choice")
    @mtg.command(name="attack")
    async def attack(self,ctx,*groups:str):
        """Declare attackers; join positions with `+` to form a band, such as `1 2+3`."""
        try:
            parsed=[[int(position) for position in group.split("+")] for group in groups]
        except ValueError:
            await ctx.send("Use battlefield positions, joining band members with `+`, such as `1 2+3`."); return
        positions=[position for group in parsed for position in group]; bands=[group for group in parsed if len(group)>1]
        await self.mutate_ctx(ctx,lambda g:g.declare_attackers(ctx.author.id,positions,bands),"attack")
    @mtg.command(name="bodyguard")
    async def bodyguard(self,ctx,position:int):
        """Choose which untapped Veteran Bodyguard receives unblocked-creature combat damage."""
        await self.mutate_ctx(ctx,lambda g:g.choose_bodyguard(ctx.author.id,position),"choose_bodyguard")
    @mtg.command(name="trample")
    async def trample(self,ctx,position:int,damage_to_blocker:int):
        """Choose how much trample damage an attacker assigns to its blocker; defaults to lethal."""
        await self.mutate_ctx(ctx,lambda g:g.assign_trample(ctx.author.id,position,damage_to_blocker),"trample")
    @mtg.command(name="block")
    async def block(self,ctx,*assignments:str):
        """Block as ATTACKER_POSITION:BLOCKER_POSITION; no values means no blocks."""
        def run(g):
            pairs=[]
            for item in assignments:
                a,b=item.split(":",1); pairs.append((int(a),int(b)))
            g.declare_blockers(ctx.author.id,pairs)
        await self.mutate_ctx(ctx,run,"block")
    @mtg.command(name="attackdamage")
    async def attackdamage(self,ctx,attacker_position:int,*assignments:str):
        """Divide an attacker’s damage in order as BLOCKER_POSITION:DAMAGE."""
        def run(g):
            parsed=[]
            for item in assignments:
                blocker,damage=item.split(":",1); parsed.append((int(blocker),int(damage)))
            g.assign_attacker_damage(ctx.author.id,attacker_position,parsed)
        await self.mutate_ctx(ctx,run,"attacker_damage")
    @mtg.command(name="blockdamage")
    async def blockdamage(self,ctx,blocker_position:int,*assignments:str):
        """Divide a multi-blocker's damage in order as ATTACKER_POSITION:DAMAGE."""
        def run(g):
            parsed=[]
            for item in assignments:
                attacker,damage=item.split(":",1); parsed.append((int(attacker),int(damage)))
            g.assign_blocker_damage(ctx.author.id,blocker_position,parsed)
        await self.mutate_ctx(ctx,run,"blocker_damage")
    @mtg.command(name="untap")
    async def untap(self,ctx,*positions:int):
        """Choose the battlefield positions to untap when Smoke or Winter Orb restricts the untap step."""
        await self.mutate_ctx(ctx,lambda g:g.choose_untap(ctx.author.id,positions),"untap")
    @mtg.command(name="pass")
    async def pass_(self,ctx): await self.mutate_ctx(ctx,lambda g:g.pass_priority(ctx.author.id),"pass")
    @mtg.command(name="trigger")
    async def trigger(self,ctx,choice:str,position:int=None):
        """Resolve a trigger with `pay`, `draw`, `decline`, or `sacrifice POSITION`."""
        normalized=choice.casefold()
        if normalized not in ("pay","draw","decline","sacrifice"): await ctx.send("Choose `pay`, `draw`, `decline`, or `sacrifice POSITION`."); return
        if normalized=="sacrifice" and position is None: await ctx.send("Provide the battlefield position to sacrifice."); return
        await self.mutate_ctx(ctx,lambda g:g.choose_trigger(ctx.author.id,normalized!="decline",position if normalized=="sacrifice" else None),f"trigger_{normalized}")
    @mtg.command(name="wording")
    async def wording(self,ctx,source:str,target:str):
        """Resolve Magical Hack or Sleight of Mind with FROM TO."""
        await self.mutate_ctx(ctx,lambda g:g.choose_word_change(ctx.author.id,source,target),"word_change_choice")

    @mtg.command(name="orders")
    async def orders(self,ctx,choice:str):
        """Resolve False Orders with an attacker position or `decline`."""
        if choice.casefold()=="decline": position=None
        else:
            try: position=int(choice)
            except ValueError: await ctx.send("Use `mtg orders ATTACKER_POSITION` or `mtg orders decline`."); return
        await self.mutate_ctx(ctx,lambda g:g.choose_false_orders(ctx.author.id,position),"false_orders_choice")
    @mtg.command(name="kudzu")
    async def kudzu(self,ctx,choice:str,position:int=None):
        """Reattach Kudzu with USER_ID POSITION, or `decline`."""
        if choice.casefold()=="decline": controller=None
        else:
            try: controller=int(choice)
            except ValueError: await ctx.send("Use `mtg kudzu USER_ID POSITION` or `mtg kudzu decline`."); return
            if position is None: await ctx.send("Provide the target land battlefield position."); return
        await self.mutate_ctx(ctx,lambda g:g.choose_kudzu(ctx.author.id,controller,position),"kudzu_choice")

    @mtg.command(name="balance")
    async def balance(self,ctx,*positions:int):
        """Complete the pending Balance choice with exact battlefield or private-hand positions."""
        await self.mutate_ctx(ctx,lambda g:g.choose_balance(ctx.author.id,positions),"balance_choice")

    @mtg.command(name="leak")
    async def leak(self,ctx,amount:int):
        """Choose how much mana to pay for a resolving Power Leak trigger."""
        await self.mutate_ctx(ctx,lambda g:g.choose_power_leak(ctx.author.id,amount),"power_leak")
    @mtg.command(name="selection")
    async def selection(self,ctx,*choices:str):
        """Resolve Natural Selection with `shuffle` or a top-to-bottom position order."""
        if len(choices)==1 and choices[0].casefold()=="shuffle": await self.mutate_ctx(ctx,lambda g:g.choose_natural_selection(ctx.author.id,shuffle=True),"natural_selection_shuffle"); return
        try: order=tuple(int(value) for value in choices)
        except ValueError: await ctx.send("Use `mtg selection shuffle` or `mtg selection 2 1 3`."); return
        await self.mutate_ctx(ctx,lambda g:g.choose_natural_selection(ctx.author.id,order),"natural_selection_order")
    @mtg.command(name="copy")
    async def copy(self,ctx,controller_id:str="none",position:int=None):
        """Choose a permanent for Clone or Copy Artifact, or use `none`."""
        if controller_id.casefold()=="none": await self.mutate_ctx(ctx,lambda g:g.choose_copy(ctx.author.id),"copy_none"); return
        try: owner=int(controller_id)
        except ValueError: await ctx.send("Use `mtg copy USER_ID POSITION` or `mtg copy none`."); return
        if position is None: await ctx.send("Provide the battlefield position to copy."); return
        await self.mutate_ctx(ctx,lambda g:g.choose_copy(ctx.author.id,owner,position),"copy_choice")
    @mtg.command(name="doppelganger")
    async def doppelganger(self,ctx,choice:str,position:int=None):
        """Choose `USER_ID POSITION` as the upkeep target, then `copy` or `keep` after responses."""
        normalized=choice.casefold()
        if normalized in ("copy","keep"):
            await self.mutate_ctx(ctx,lambda g:g.choose_vesuvan_copy(ctx.author.id,accept=normalized=="copy"),f"vesuvan_{normalized}"); return
        try: owner=int(choice)
        except ValueError: await ctx.send("Choose `USER_ID POSITION`, then use `copy` or `keep` when it resolves."); return
        if position is None: await ctx.send("Provide the battlefield position to target."); return
        await self.mutate_ctx(ctx,lambda g:g.choose_vesuvan_copy(ctx.author.id,owner,position),"vesuvan_copy_target")
    @mtg.command(name="concede")
    async def concede(self,ctx): await self.mutate_ctx(ctx,lambda g:g.concede(ctx.author.id),"concede")
    async def red_delete_data_for_user(self,*,requester,user_id):
        async with self.storage_lock:
            changed=False; games=await self.config.games()
            for key in list(games):
                if user_id in [int(x) for x in games[key].get("order",[])]:
                    games.pop(key); gid=int(key); self.games.pop(gid,None); self.channels.pop(gid,None); self.locks.pop(gid,None); changed=True
            if changed: await self.config.games.set(games)
