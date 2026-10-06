import random
import re
import time
from itertools import combinations
from dataclasses import asdict, dataclass, field, replace
from typing import Dict, List, Optional
from .cards import CARDS, TOKENS, starter

class GameError(ValueError): pass

@dataclass
class Permanent:
    uid: int
    key: str
    owner: int = 0
    base_controller: int = 0
    tapped: bool = False
    sick: bool = True
    damage: int = 0
    bonus: int = 0
    power_bonus: int = 0
    toughness_bonus: int = 0
    exile_on_death: bool = False
    temporary_keywords: List[str] = field(default_factory=list)
    activations_this_turn: int = 0
    sacrifice_at_end_step: bool = False
    regeneration_shields: int = 0
    cant_regenerate: bool = False
    attached_to: Optional[int] = None
    color_override: str = ""
    color_timestamp: int = 0
    animated_until_end_combat: bool = False
    damage_prevention: int = 0
    hydra_counters_first: bool = False
    redirect_damage_to_owner: int = 0
    redirect_source_damage_to_player: Dict[int, int] = field(default_factory=dict)
    plus_one_counters: int = 0
    power_counters: int = 0
    corpse_counters: int = 0
    vitality_counters: int = 0
    damage_source_uids: List[int] = field(default_factory=list)
    chosen_land_type: str = ""
    layer_timestamp: int = 0
    aura_effect_enabled: bool = False
    last_known_toughness: int = 0
    land_type_effects: List[Dict[str, object]] = field(default_factory=list)
    copy_key: str = ""
    copy_added_types: List[str] = field(default_factory=list)
    copy_colors: List[str] = field(default_factory=list)
    copy_upkeep_creature: bool = False
    temporary_max_blocks: int = 0
    must_block_all: bool = False
    land_word_changes: Dict[str, str] = field(default_factory=dict)
    color_word_changes: Dict[str, str] = field(default_factory=dict)

@dataclass
class Player:
    user_id: int
    deck: str
    life: int = 20
    library: List[int] = field(default_factory=list)
    hand: List[int] = field(default_factory=list)
    graveyard: List[int] = field(default_factory=list)
    exile: List[int] = field(default_factory=list)
    mana_pool: Dict[str, int] = field(default_factory=dict)
    battlefield: List[Permanent] = field(default_factory=list)
    land_played: bool = False
    lands_played_this_turn: int = 0
    kept: bool = False
    mulligans: int = 0
    damage_prevention: int = 0
    source_damage_prevention: List[int] = field(default_factory=list)
    source_damage_lifegain: List[int] = field(default_factory=list)
    source_damage_caps: Dict[int, int] = field(default_factory=dict)
    guardian_angel_active: bool = False
    turn_start_untapped_lands: int = 0
    channel_active: bool = False
    damage_taken_this_turn: int = 0
    bodyguard_choice: int = 0
    island_sanctuary_active: bool = False
    sanctuary_landwalk_type: str = "island"

@dataclass
class Spell:
    owner: int
    uid: int
    key: str
    target: Optional[str] = None
    passes: int = 0
    x_value: int = 0
    ability_effect: str = ""
    source_uid: Optional[int] = None
    color_override: str = ""
    decision_pending: bool = False
    batch_id: int = 0
    source_power: int = 0
    choice_value: int = 0
    choice_owner: Optional[int] = None
    mana_choices: Dict[str, str] = field(default_factory=dict)
    land_word_changes: Dict[str, str] = field(default_factory=dict)
    color_word_changes: Dict[str, str] = field(default_factory=dict)

class Game:
    """Serializable two-player rules subset; Discord is only a view of this state."""
    def __init__(self, game_id, users, seed=None, decks=None, ai_user=None, ai_difficulty=None):
        if len(users) != 2 or users[0] == users[1]: raise GameError("Two different players are required.")
        self.game_id, self.order = int(game_id), [int(x) for x in users]
        decks = {int(user): color for user, color in (decks or {}).items()}
        self.players = {
            user: Player(user, decks.get(user, color))
            for user, color in zip(self.order, ("red", "green"))
        }
        self.ai_user = int(ai_user) if ai_user is not None else None
        self.ai_difficulty = ai_difficulty
        self.cards, self.next_uid = {}, 1
        self.next_layer_timestamp = 1
        self.active_index, self.phase, self.turn = 0, "opening", 0
        self.stack, self.attackers, self.blocks = [], [], {}
        self.additional_blocks = {}
        self.attack_bands = []
        self.attacker_damage_assignments = {}
        self.blocker_damage_assignments = {}
        self.blocked_attackers=[]
        self.combat_participants=[]
        self.attacked_this_turn=[]
        self.forced_attackers=[]
        self.trample_assignments={}
        self.end_step_sacrifices=[]
        self.end_step_destroys=[]
        self.end_combat_destroys=[]
        self.tomb_cleanup_sources=[]
        self.extra_turns=[]
        self.turn_start_pending_user=None
        self.turn_start_pending_extra=False
        self.untap_pending=[]
        self.skip_draw_step=False
        self.sanctuary_draw_pending=False
        self.sanctuary_pending_draws=0
        self.sanctuary_resume_draw_step=False
        self.sanctuary_resume_mass_draw=False
        self.sanctuary_mass_draw_failed=[]
        self.prevent_combat_damage=False
        self.creatures_died_this_turn=0
        self.phase_passes = 0
        self.priority_user = self.winner = self.finished_reason = None
        self.log, self.history = [], []
        self.created_at = self.updated_at = int(time.time())
        rng = random.Random(seed)
        for user in self.order:
            p=self.players[user]
            for key in starter(p.deck):
                self.cards[self.next_uid]=key; p.library.append(self.next_uid); self.next_uid+=1
            rng.shuffle(p.library); self._draw(p,7)
        self.log.append("Both players drew seven cards.")
        self.record(None,"game_created","Both players drew seven cards.")

    @property
    def active_user(self): return self.order[self.active_index]
    @property
    def finished(self): return self.phase=="finished"
    def record(self,user,action,detail=""):
        self.updated_at=int(time.time())
        event={"seq":len(self.history)+1,"at":self.updated_at,"user":user,"action":action}
        if detail: event["detail"]=detail
        self.history.append(event)
    def is_expired(self,now,timeout): return not self.finished and int(now)-self.updated_at>=timeout
    def expire(self):
        if self.finished: return False
        self.winner=None; self.finished_reason="inactivity timeout"; self.phase="finished"; self.priority_user=None
        self.record(None,"match_expired"); return True
    def opponent(self,user): self.player(user); return self.order[1] if user==self.order[0] else self.order[0]
    def player(self,user):
        try: return self.players[int(user)]
        except KeyError as e: raise GameError("You are not in this game.") from e
    def _word_change_maps(self,uid):
        permanent=next((item for player in self.players.values() for item in player.battlefield if item.uid==uid),None) if hasattr(self,"players") else None
        spell=next((item for item in self.stack if item.uid==uid),None) if hasattr(self,"stack") else None
        source=permanent or spell
        return (source.land_word_changes,source.color_word_changes) if source is not None else ({},{})

    def changed_land_word(self,uid,value):
        return self._word_change_maps(uid)[0].get(value.casefold(),value.casefold())

    def changed_color_word(self,uid,value):
        return self._word_change_maps(uid)[1].get(value.upper(),value.upper())

    def _text_changed_card(self,uid,card,land_changes=None,color_changes=None):
        lands,colors=self._word_change_maps(uid) if land_changes is None and color_changes is None else (land_changes or {},color_changes or {})
        if not lands and not colors: return card
        updates={}
        for field_name in ("land_type","animate_land_type","upkeep_land_type_damage","global_land_from_type","global_land_to_type","attack_requires_defender_land_type","sacrifice_without_land_type","aura_set_land_type"):
            value=getattr(card,field_name)
            if value: updates[field_name]=lands.get(value.casefold(),value)
        for field_name in ("target_color","color_change","aura_protection","prevent_source_color","global_buff_color","cast_life_color","animate_land_color"):
            value=getattr(card,field_name)
            if value: updates[field_name]=colors.get(value.upper(),value)
        if card.protection_colors: updates["protection_colors"]=tuple(colors.get(value,value) for value in card.protection_colors)
        def keyword(value):
            lower=value.casefold()
            return lands.get(lower[:-4],lower)+"walk" if lower.endswith("walk") and lower[:-4] in ("plains","island","swamp","mountain","forest") else value
        updates["keywords"]=tuple(keyword(value) for value in card.keywords)
        if card.aura_keyword: updates["aura_keyword"]=keyword(card.aura_keyword)
        if card.lord_keyword: updates["lord_keyword"]=keyword(card.lord_keyword)
        characteristic=card.characteristic_pt or ""
        if characteristic in ("plains","islands","swamps","mountains","forests"):
            roots={"plains":"plains","islands":"island","swamps":"swamp","mountains":"mountain","forests":"forest"}; root=roots[characteristic]; replacement=lands.get(root,root); updates["characteristic_pt"]="plains" if replacement=="plains" else replacement+"s"
        elif characteristic=="gaea_liege" and "forest" in lands: updates["characteristic_pt"]="gaea_liege:"+lands["forest"]
        text=card.text; activation_text=card.activation_text
        for source,target in lands.items():
            text=re.sub(rf"\b{source}",lambda match: target.title() if match.group(0)[0].isupper() else target,text,flags=re.I); activation_text=re.sub(rf"\b{source}",lambda match: target.title() if match.group(0)[0].isupper() else target,activation_text,flags=re.I)
        names={"W":"white","U":"blue","B":"black","R":"red","G":"green"}
        for source,target in colors.items():
            text=re.sub(rf"\b{names[source]}",lambda match: names[target].title() if match.group(0)[0].isupper() else names[target],text,flags=re.I); activation_text=re.sub(rf"\b{names[source]}",lambda match: names[target].title() if match.group(0)[0].isupper() else names[target],activation_text,flags=re.I)
        updates["text"]=text; updates["activation_text"]=activation_text
        return replace(card,**updates)

    def card(self,uid):
        key=self.cards[uid]; permanent=None
        if hasattr(self,"players"):
            permanent=next((item for player in self.players.values() for item in player.battlefield if item.uid==uid),None)
        copy_key=permanent.copy_key if permanent is not None else ""
        card=CARDS.get(copy_key or key) or TOKENS[copy_key or key]
        if copy_key and permanent.copy_added_types:
            main,*subtypes=card.type_line.split(" — ",1); words=main.split()
            for added in permanent.copy_added_types:
                if added not in words: words.append(added)
            card=replace(card,type_line=" ".join(words)+(f" — {subtypes[0]}" if subtypes else ""))
        if copy_key and permanent.copy_colors: card=replace(card,colors=tuple(permanent.copy_colors))
        if copy_key and permanent.copy_upkeep_creature: card=replace(card,upkeep_copy_creature=True)
        return self._text_changed_card(uid,card)
    def is_token(self,uid): return self.cards.get(uid,"").startswith("token:")
    def _make_permanent(self,uid,key,**kwargs):
        kwargs.setdefault("layer_timestamp",self.next_layer_timestamp); self.next_layer_timestamp+=1
        permanent=Permanent(uid,key,**kwargs)
        effective=(CARDS.get(permanent.copy_key) or TOKENS.get(permanent.copy_key)) if permanent.copy_key else self.card(uid)
        permanent.power_counters=effective.enters_power_counters
        return permanent
    def _global_land_animation_sources(self,permanent):
        return [source for player in self.players.values() for source in player.battlefield if self.card(source.uid).animate_land_type and self.has_current_land_type(permanent,self.card(source.uid).animate_land_type)]
    def _global_land_animation(self,permanent): return bool(self._global_land_animation_sources(permanent))
    def _aura_artifact_animation(self,permanent):
        return self.card(permanent.uid).has_type("Artifact") and any(self.card(aura.uid).aura_animate_mana_value for aura in self.attached_auras(permanent))
    def is_creature(self,permanent): return self.card(permanent.uid).creature or permanent.animated_until_end_combat or self._global_land_animation(permanent) or self._aura_artifact_animation(permanent)
    def current_land_types(self,permanent):
        card=self.card(permanent.uid)
        if not card.land: return set()
        types={kind for kind in ("plains","island","swamp","mountain","forest") if card.has_land_type(kind)}
        setters=[]
        for player in self.players.values():
            for source in player.battlefield:
                rule=self.card(source.uid)
                if rule.global_land_from_type: setters.append((source.layer_timestamp or source.uid,rule.global_land_from_type,rule.global_land_to_type))
        for aura in self.attached_auras(permanent):
            rule=self.card(aura.uid); chosen=aura.chosen_land_type if rule.aura_choose_land_type else rule.aura_set_land_type
            if chosen: setters.append((aura.layer_timestamp or aura.uid,"",chosen))
        for effect in permanent.land_type_effects:
            if effect.get("kind")=="mire": setters.append((int(effect.get("effect_timestamp",0)),"",str(effect.get("land_type","swamp"))))
            else:
                _,source=self.find_permanent(int(effect.get("source_uid",0)))
                if source is not None and source.layer_timestamp==int(effect.get("source_timestamp",0)):
                    setters.append((int(effect.get("effect_timestamp",0)),"",str(effect.get("land_type",""))))
        for _,required,replacement in sorted(setters):
            if not required or required in types: types={replacement}
        return types
    def has_current_land_type(self,permanent,land_type): return land_type.casefold() in self.current_land_types(permanent)
    def current_mana_choices(self,permanent):
        card=self.card(permanent.uid); mapping={"plains":"W","island":"U","swamp":"B","mountain":"R","forest":"G"}
        types=self.current_land_types(permanent)
        return tuple(mapping[kind] for kind in ("plains","island","swamp","mountain","forest") if kind in types) if types else card.produces
    def has_current_type(self,permanent,card_type): return self.is_creature(permanent) if card_type=="Creature" else self.card(permanent.uid).has_type(card_type)
    def hand(self,user): return [self.card(x) for x in self.player(user).hand]
    def library_search(self,user): return [(position,self.card(uid)) for position,uid in enumerate(reversed(self.player(user).library),1)]
    def characteristic_stats(self,user,card,entering=False,permanent_uid=None):
        player=self.player(user)
        land_characteristics={"plains":"plains","islands":"island","swamps":"swamp","mountains":"mountain","forests":"forest"}
        if card.characteristic_pt in land_characteristics:
            value=sum(self.has_current_land_type(x,land_characteristics[card.characteristic_pt]) for x in player.battlefield)
        elif card.characteristic_pt=="gaea_liege" or (card.characteristic_pt or "").startswith("gaea_liege:"):
            land_type=card.characteristic_pt.split(":",1)[1] if ":" in card.characteristic_pt else "forest"; lands=self.player(self.opponent(user) if permanent_uid in self.attackers else user)
            value=sum(self.has_current_land_type(x,land_type) for x in lands.battlefield)
        elif card.characteristic_pt=="plague_rats":
            value=sum(self.card(x.uid).name=="Plague Rats" for p in self.players.values() for x in p.battlefield)+(1 if entering else 0)
        elif card.characteristic_pt=="non_wall_creatures":
            value=sum(self.is_creature(x) and "Wall" not in self.card(x.uid).type_line for x in player.battlefield)+(1 if entering else 0)
        else:
            return card.power,card.toughness
        return value,value
    def permanent_owner(self,permanent,fallback=None):
        if permanent.owner in self.players: return self.player(permanent.owner)
        controller,_=self.find_permanent(permanent.uid)
        owner=controller or fallback
        if owner is not None: permanent.owner=owner.user_id
        return owner
    def find_permanent(self,uid):
        for player in self.players.values():
            permanent=next((x for x in player.battlefield if x.uid==uid),None)
            if permanent is not None: return player,permanent
        return None,None
    def attached_auras(self,permanent):
        return [aura for player in self.players.values() for aura in player.battlefield if aura.attached_to==permanent.uid and self.card(aura.uid).aura_target_types]
    def _reconcile_control(self):
        moves=[]
        for controller in self.players.values():
            for permanent in list(controller.battlefield):
                if permanent.owner not in self.players: permanent.owner=controller.user_id
                control_auras=[]
                for aura in self.attached_auras(permanent):
                    if self.card(aura.uid).aura_control:
                        aura_controller,_=self.find_permanent(aura.uid)
                        if aura_controller is not None: control_auras.append((aura.layer_timestamp,aura_controller.user_id))
                desired=max(control_auras)[1] if control_auras else (permanent.base_controller if permanent.base_controller in self.players else permanent.owner)
                if desired!=controller.user_id: moves.append((controller,self.player(desired),permanent))
        for old,new,permanent in moves:
            if permanent not in old.battlefield: continue
            self._remove_from_combat(permanent.uid); old.battlefield.remove(permanent); permanent.sick=True; new.battlefield.append(permanent)
            self.log.append(f"{new.user_id} gained control of {self.card(permanent.uid).name}.")
    def aura_stats(self,aura):
        card=self.card(aura.uid)
        if not card.aura_forest_scaling: return card.aura_power,card.aura_toughness
        controller,_=self.find_permanent(aura.uid)
        land_type=self.changed_land_word(aura.uid,"forest"); lands=sum(self.has_current_land_type(x,land_type) for x in controller.battlefield)
        return lands//2,(lands+1)//2
    def continuous_lords(self,permanent):
        controller=next((player for player in self.players.values() if permanent in player.battlefield),None)
        if controller is None: return []
        target=self.card(permanent.uid)
        return [source for source in controller.battlefield if source.uid!=permanent.uid and self.card(source.uid).lord_subtype and self._has_subtype(target,self.card(source.uid).lord_subtype)]
    def granted_regeneration_cost(self,permanent):
        return next((self.card(source.uid).lord_regeneration_cost for source in self.continuous_lords(permanent) if self.card(source.uid).lord_regeneration_cost),"")
    def _activation_profile(self,permanent):
        card=self.card(permanent.uid)
        if card.activation_cost or card.activation_effect: return card.activation_cost,card.activation_effect,card.activation_tap,True
        granted=self.granted_regeneration_cost(permanent)
        return (granted,"regenerate",False,False) if granted else ("","",False,False)
    @staticmethod
    def _has_subtype(card,subtype):
        return subtype in card.type_line.split(" — ",1)[-1].split()
    def _aura_type_legal(self,aura_card,target):
        target_card=self.card(target.uid)
        return any(self.has_current_type(target,kind) for kind in aura_card.aura_target_types) and all(self._has_subtype(target_card,subtype) for subtype in aura_card.aura_target_subtypes)
    def current_colors(self,permanent):
        layers=[(permanent.color_timestamp,permanent.color_override)] if permanent.color_override else []
        layers.extend((source.layer_timestamp or source.uid,self.card(source.uid).animate_land_color) for source in self._global_land_animation_sources(permanent) if self.card(source.uid).animate_land_color)
        return (max(layers)[1],) if layers else self.card(permanent.uid).colors
    def spell_colors(self,spell):
        return (spell.color_override,) if spell.color_override else self.card(spell.uid).colors
    def spell_mana_value(self,spell):
        card=self.card(spell.uid); return card.cost+card.mana_cost.count("{X}")*spell.x_value
    def ability_source_colors(self,spell):
        _,source=self.find_permanent(spell.source_uid)
        return self.current_colors(source) if source is not None else self.spell_colors(spell)
    def current_protections(self,permanent,exclude_aura_uid=None):
        protections=set(self.card(permanent.uid).protection_colors)
        protections.update(self.card(aura.uid).aura_protection for aura in self.attached_auras(permanent) if aura.uid!=exclude_aura_uid and self.card(aura.uid).aura_protection)
        return protections
    def _protected_from(self,permanent,source_card,source_colors=None):
        return bool(set(source_card.colors if source_colors is None else source_colors) & self.current_protections(permanent))
    def _aura_can_attach(self,aura_card,target,aura=None,colors=None):
        if not self._aura_type_legal(aura_card,target): return False
        if any(existing.uid!=(aura.uid if aura is not None else None) and self.card(existing.uid).aura_excludes_other_auras for existing in self.attached_auras(target)): return False
        excluded=aura.uid if aura is not None and aura_card.protection_self_exception else None
        aura_colors=self.current_colors(aura) if aura is not None else aura_card.colors if colors is None else colors
        return not bool(set(aura_colors) & self.current_protections(target,excluded))
    def is_indestructible(self,permanent):
        return any(self.card(aura.uid).aura_indestructible for aura in self.attached_auras(permanent))
    def _stable_target_permanent(self,target):
        if not target or target.upper().startswith(("S:","G:")): return None
        parts=target.split(":")
        try: uid=int(parts[-1])
        except (TypeError,ValueError): return None
        return self.find_permanent(uid)[1] if len(parts) in (2,3) else None
    def current_stats(self,permanent):
        owner=next((p.user_id for p in self.players.values() if permanent in p.battlefield),None)
        if owner is None: raise GameError("Permanent is not on the battlefield.")
        card=self.card(permanent.uid)
        if permanent.animated_until_end_combat: power,toughness=3,6
        elif card.creature: power,toughness=self.characteristic_stats(owner,card,permanent_uid=permanent.uid)
        elif self._global_land_animation(permanent): power,toughness=1,1
        elif self._aura_artifact_animation(permanent): power=toughness=card.cost
        else: power,toughness=card.power,card.toughness
        swamp_bonus=1 if card.conditional_swamp_bonus and any(self.has_current_land_type(x,self.changed_land_word(permanent.uid,"swamp")) for x in self.player(owner).battlefield) else 0
        auras=self.attached_auras(permanent)
        lords=[self.card(source.uid) for source in self.continuous_lords(permanent)]
        globals_=[source for source_user,player in self.players.items() for source in player.battlefield if self.global_buff_applies(source,source_user,permanent,owner)]
        aura_bonuses=[self.aura_stats(aura) for aura in auras]
        return power+swamp_bonus+sum(bonus[0] for bonus in aura_bonuses)+sum(lord.lord_power for lord in lords)+sum(self.card(source.uid).global_power for source in globals_)+permanent.bonus+permanent.power_bonus+permanent.plus_one_counters+permanent.power_counters,toughness+swamp_bonus+sum(bonus[1] for bonus in aura_bonuses)+sum(lord.lord_toughness for lord in lords)+sum(self.card(source.uid).global_toughness for source in globals_)+permanent.bonus+permanent.toughness_bonus+permanent.plus_one_counters
    def global_buff_applies(self,source,source_user,target,target_user):
        effect=self.card(source.uid); target_card=self.card(target.uid)
        if not self.is_creature(target) or not (effect.global_power or effect.global_toughness): return False
        if effect.global_controller_only and source_user!=target_user: return False
        if effect.global_buff_color and effect.global_buff_color not in self.current_colors(target): return False
        if effect.global_requires_untapped and target.tapped: return False
        if effect.global_requires_attacking and target.uid not in self.attackers: return False
        return True
    def projected_stats(self,user,card):
        return self.characteristic_stats(user,card,entering=bool(card.characteristic_pt))
    def current_keywords(self,permanent):
        auras=self.attached_auras(permanent)
        aura_keywords={self.card(aura.uid).aura_keyword for aura in auras if self.card(aura.uid).aura_keyword}
        lord_keywords={self.card(source.uid).lord_keyword for source in self.continuous_lords(permanent) if self.card(source.uid).lord_keyword}
        keywords=set(self.card(permanent.uid).keywords) | set(permanent.temporary_keywords) | aura_keywords | lord_keywords
        if any(aura.aura_effect_enabled and self.card(aura.uid).aura_enter_flying_damage for aura in auras): keywords.discard("flying")
        return keywords
    def can_attack_permanent(self,permanent):
        card=self.card(permanent.uid); keywords=self.current_keywords(permanent)
        auras=self.attached_auras(permanent)
        defender_override=any(self.card(aura.uid).aura_attack_override for aura in auras)
        attack_haste=any(self.card(aura.uid).aura_attack_haste for aura in auras)
        defender=self.player(self.opponent(self.active_user))
        required=card.attack_requires_defender_land_type
        has_required=not required or any(self.has_current_land_type(x,required) for x in defender.battlefield)
        sanctuary_allowed=not defender.island_sanctuary_active or "flying" in keywords or f"{defender.sanctuary_landwalk_type}walk" in keywords
        return self.is_creature(permanent) and not permanent.tapped and (not permanent.sick or card.haste or "haste" in keywords or attack_haste) and ("defender" not in keywords or defender_override) and has_required and sanctuary_allowed

    def _draw_now(self,p,n=1):
        for _ in range(n):
            if not p.library: self._finish(self.opponent(p.user_id),"empty library"); return
            p.hand.append(p.library.pop())

    def _draw(self,p,n=1):
        sanctuary=self.phase=="draw" and p.user_id==self.active_user and any(self.card(source.uid).draw_step_sanctuary for source in p.battlefield)
        if sanctuary and n>0:
            self.sanctuary_pending_draws+=n; self.sanctuary_draw_pending=True; self.priority_user=p.user_id; return False
        self._draw_now(p,n); return True

    def _finish_failed_draws(self,failed):
        failed=set(failed)
        if len(failed)==2: self._finish(None,"both players drew from empty libraries")
        elif failed: self._finish(self.opponent(next(iter(failed))),"empty library")

    def _draw_each(self,n):
        active=self.player(self.active_user)
        sanctuary=self.phase=="draw" and any(self.card(source.uid).draw_step_sanctuary for source in active.battlefield)
        if sanctuary:
            failed=set(); other=self.player(self.opponent(self.active_user))
            for _ in range(n):
                if other.library: other.hand.append(other.library.pop())
                else: failed.add(other.user_id)
            self.sanctuary_mass_draw_failed=list(failed); self.sanctuary_resume_mass_draw=True; self.sanctuary_pending_draws+=n; self.sanctuary_draw_pending=True; self.priority_user=self.active_user; return
        failed=set()
        for user in (self.active_user,self.opponent(self.active_user)):
            player=self.player(user)
            for _ in range(n):
                if player.library: player.hand.append(player.library.pop())
                else: failed.add(user)
        self._finish_failed_draws(failed)

    def mulligan(self,user,keep):
        if self.phase!="opening": raise GameError("Opening hands are complete.")
        p=self.player(user)
        if p.kept: raise GameError("You already kept.")
        if keep:
            for _ in range(min(p.mulligans, len(p.hand))): p.library.insert(0, p.hand.pop())
            p.kept=True
        else:
            p.library+=p.hand; p.hand=[]; random.SystemRandom().shuffle(p.library); self._draw(p,7)
            p.mulligans+=1
        if all(x.kept for x in self.players.values()): self._start_turn(True)

    def _graveyard_upkeep_return_eligible(self,user,uid):
        graveyard=self.player(user).graveyard
        if uid not in graveyard or not self.card(uid).graveyard_upkeep_return: return False
        position=graveyard.index(uid)
        return sum(self.card(card_uid).creature for card_uid in graveyard[position+1:])>=3

    def _turn_step_triggers(self,step):
        triggers=[]; active=self.active_user
        for controller_id in (active,self.opponent(active)):
            controller=self.player(controller_id)
            for source in controller.battlefield:
                card=self.card(source.uid); effect=""; trigger_owner=controller.user_id; trigger_target=str(active); choice_owner=None
                if step=="upkeep" and card.upkeep_copy_creature and active==controller.user_id: effect="vesuvan_copy"; trigger_target=""; choice_owner=active
                elif step=="upkeep" and card.upkeep_untap_cost and active==controller.user_id: effect="upkeep_untap"
                elif step=="upkeep" and card.upkeep_cost and active==controller.user_id: effect="upkeep_cost"
                elif step=="upkeep" and card.upkeep_sacrifice_other and active==controller.user_id: effect="upkeep_sacrifice"
                elif step=="upkeep" and card.upkeep_each_damage: effect="upkeep_damage"
                elif step=="upkeep" and card.upkeep_land_type_damage: effect="upkeep_land_type_damage"
                elif step=="upkeep" and card.aura_power_leak:
                    attached_controller,attached=self.find_permanent(source.attached_to)
                    if attached is not None and active==attached_controller.user_id: effect="power_leak"; trigger_owner=active; choice_owner=active
                elif step=="upkeep" and card.aura_upkeep_damage:
                    attached_controller,attached=self.find_permanent(source.attached_to)
                    if attached is not None and active==attached_controller.user_id: effect="aura_upkeep_damage"
                elif step=="upkeep" and card.aura_controller_upkeep_cost:
                    attached_controller,attached=self.find_permanent(source.attached_to)
                    if attached is not None and active==attached_controller.user_id:
                        effect="aura_upkeep_life"; trigger_owner=active; choice_owner=active
                elif step=="upkeep" and card.aura_damage_vitality and active==controller.user_id and source.vitality_counters:
                    effect="upkeep_vitality"; trigger_owner=active; choice_owner=active; trigger_target=f"{active}:{source.uid}"
                elif step=="upkeep" and card.aura_upkeep_untap_cost:
                    attached_controller,attached=self.find_permanent(source.attached_to)
                    if attached is not None and active==attached_controller.user_id:
                        effect="aura_upkeep_untap"; choice_owner=active; trigger_target=f"{active}:{attached.uid}"
                elif step=="upkeep" and card.upkeep_turn_start_untapped_damage:
                    effect="upkeep_untapped_land_damage"
                elif step=="upkeep" and card.upkeep_opponent_hand_damage and active==self.opponent(controller.user_id): effect="upkeep_hand_damage"
                elif step=="draw" and card.draw_step_extra and not source.tapped: effect="draw_step_draw"
                elif step=="draw" and card.draw_tapped_damage and active==controller.user_id and source.tapped: effect="draw_tapped_damage"
                if not effect: continue
                uid=self.next_uid; self.next_uid+=1; trigger_key="lea:87" if effect=="vesuvan_copy" else card.key; self.cards[uid]=trigger_key
                trigger=Spell(trigger_owner,uid,trigger_key,trigger_target,ability_effect=effect,source_uid=source.uid,color_override=source.color_override,choice_owner=choice_owner,choice_value=self.player(active).turn_start_untapped_lands if effect=="upkeep_untapped_land_damage" else 0,decision_pending=effect=="vesuvan_copy")
                if effect=="vesuvan_copy" and not self.vesuvan_choices(trigger): self.cards.pop(uid,None); continue
                triggers.append(trigger)
            if step=="upkeep" and active==controller_id:
                for source_uid in controller.graveyard:
                    if not self._graveyard_upkeep_return_eligible(controller_id,source_uid): continue
                    uid=self.next_uid; self.next_uid+=1; key=self.card(source_uid).key; self.cards[uid]=key
                    triggers.append(Spell(controller_id,uid,key,str(controller_id),ability_effect="graveyard_return",source_uid=source_uid))
        if step=="upkeep":
            for cleanup in self.tomb_cleanup_sources:
                if int(cleanup["owner"])!=active: continue
                source_uid=int(cleanup["source_uid"]); source_timestamp=int(cleanup["source_timestamp"])
                if not self.tomb_cleanup_choices(source_uid,source_timestamp): continue
                uid=self.next_uid; self.next_uid+=1; self.cards[uid]="lea:240"
                triggers.append(Spell(active,uid,"lea:240",str(active),ability_effect="tomb_cleanup",source_uid=source_uid,choice_owner=active,choice_value=source_timestamp))
        triggers.sort(key=lambda trigger:trigger.owner!=active)
        return triggers

    def _finish_draw_step(self,draw=True):
        if draw and not self._draw(self.player(self.active_user)):
            self.sanctuary_resume_draw_step=True; return
        if self.finished: return
        triggers=self._turn_step_triggers("draw")
        if triggers:
            self.stack.extend(triggers); self.phase="draw"; self.priority_user=self.active_user
        else:
            self.phase="precombat_main"; self.priority_user=self.active_user

    def _begin_draw_step(self):
        if self.skip_draw_step:
            self.skip_draw_step=False; self.phase="precombat_main"; self.priority_user=self.active_user; return
        self.phase="draw"; self._finish_draw_step()

    def choose_sanctuary_draw(self,user,skip):
        if self.finished: raise GameError("Game is over.")
        if not self.sanctuary_draw_pending or user!=self.active_user: raise GameError("You do not have an Island Sanctuary draw choice to make.")
        self.sanctuary_pending_draws-=1
        if skip:
            self.player(user).island_sanctuary_active=True; source=next((x for x in self.player(user).battlefield if self.card(x.uid).draw_step_sanctuary),None); self.player(user).sanctuary_landwalk_type=self.changed_land_word(source.uid,"island") if source else "island"; self.log.append(f"{user} skipped a draw for Island Sanctuary.")
        else:
            if self.sanctuary_resume_mass_draw:
                player=self.player(user)
                if player.library: player.hand.append(player.library.pop())
                elif user not in self.sanctuary_mass_draw_failed: self.sanctuary_mass_draw_failed.append(user)
            else: self._draw_now(self.player(user))
            self.log.append(f"{user} chose to draw instead of using Island Sanctuary.")
        if self.finished: self.sanctuary_draw_pending=False; self.sanctuary_pending_draws=0; return
        if self.sanctuary_pending_draws>0: return
        self.sanctuary_draw_pending=False
        if self.sanctuary_resume_mass_draw:
            failed=list(self.sanctuary_mass_draw_failed); self.sanctuary_resume_mass_draw=False; self.sanctuary_mass_draw_failed=[]; self._finish_failed_draws(failed)
            if self.finished: return
        if self.sanctuary_resume_draw_step:
            self.sanctuary_resume_draw_step=False; self._finish_draw_step(False)
        else: self.priority_user=self.active_user

    def _begin_upkeep(self):
        self.phase_passes=0; triggers=self._turn_step_triggers("upkeep")
        if triggers:
            self.stack.extend(triggers); self.phase="upkeep"; top=self.stack[-1]; self.priority_user=top.choice_owner if top.decision_pending and top.choice_owner is not None else self.active_user
        else: self._begin_draw_step()

    def _untap_limits(self):
        creature=[]; land=[]; skip=False
        for player in self.players.values():
            for source in player.battlefield:
                card=self.card(source.uid)
                if card.skip_all_untap: skip=True
                if card.untap_creature_limit and (not card.untap_limit_requires_untapped or not source.tapped): creature.append(card.untap_creature_limit)
                if card.untap_land_limit and (not card.untap_limit_requires_untapped or not source.tapped): land.append(card.untap_land_limit)
        return skip,(min(creature) if creature else None),(min(land) if land else None)

    def untap_choices(self):
        if self.phase!="untap" or not self.untap_pending: return []
        p=self.player(self.active_user); positions={x.uid:i for i,x in enumerate(p.battlefield,1)}
        candidates=[x for x in p.battlefield if x.uid in self.untap_pending]
        _,creature_limit,land_limit=self._untap_limits()
        maximum=(creature_limit or 0)+(land_limit or 0)
        def legal(group):
            return (creature_limit is None or sum(self.is_creature(x) for x in group)<=creature_limit) and (land_limit is None or sum(self.card(x.uid).land for x in group)<=land_limit)
        choices=[]
        for size in range(min(len(candidates),maximum)+1):
            for group in combinations(candidates,size):
                if not legal(group): continue
                if any(legal(group+(other,)) for other in candidates if other not in group): continue
                choices.append(tuple(sorted(positions[x.uid] for x in group)))
        return choices

    def choose_untap(self,user,positions):
        if self.phase!="untap" or user!=self.active_user: raise GameError("You do not have a restricted untap choice to make.")
        choice=tuple(sorted(set(int(x) for x in positions)))
        if choice not in self.untap_choices(): raise GameError("Choose a maximal legal set of restricted permanents to untap.")
        p=self.player(user); names=[]
        for position in choice:
            permanent=p.battlefield[position-1]; permanent.tapped=False; names.append(self.card(permanent.uid).name)
        self.untap_pending=[]; self.log.append(f"{user} untapped "+(", ".join(names) if names else "no restricted permanents")+".")
        self._begin_upkeep()

    def time_vault_choices(self,user):
        return [(position,permanent) for position,permanent in enumerate(self.player(user).battlefield,1) if self.card(permanent.uid).key=="lea:274" and permanent.tapped]

    def _offer_turn_start(self,user,was_extra=False):
        if self.time_vault_choices(user):
            self.turn_start_pending_user=user; self.turn_start_pending_extra=bool(was_extra); self.phase="turn_choice"; self.phase_passes=0; self.priority_user=user; return
        self.turn_start_pending_user=None; self.turn_start_pending_extra=False; self.active_index=self.order.index(user); self._start_turn()

    def choose_time_vault_turn(self,user,skip,position=None):
        if self.finished: raise GameError("Game is over.")
        if self.turn_start_pending_user!=user: raise GameError("You do not have a Time Vault turn choice to make.")
        choices=dict(self.time_vault_choices(user)); was_extra=self.turn_start_pending_extra
        self.turn_start_pending_user=None; self.turn_start_pending_extra=False
        if not skip:
            self.active_index=self.order.index(user); self.log.append(f"{user} chose to take their {'extra ' if was_extra else ''}turn with Time Vault tapped."); self._start_turn(); return
        if position not in choices:
            self.turn_start_pending_user=user; self.turn_start_pending_extra=was_extra; raise GameError("Choose the battlefield position of a tapped Time Vault.")
        vault=choices[position]; vault.tapped=False; self.log.append(f"{user} skipped their {'extra ' if was_extra else ''}turn to untap Time Vault.")
        next_extra=bool(self.extra_turns); next_user=self.extra_turns.pop(0) if next_extra else self.opponent(user); self._offer_turn_start(next_user,next_extra)

    def _start_turn(self,first=False):
        self.turn+=1; p=self.players[self.active_user]; p.island_sanctuary_active=False; p.turn_start_untapped_lands=sum(self.card(x.uid).land and not x.tapped for x in p.battlefield); p.land_played=False; p.lands_played_this_turn=0
        self._cleanup(); self.untap_pending=[]
        power_limits=[self.card(source.uid).untap_power_limit for player in self.players.values() for source in player.battlefield if self.card(source.uid).untap_power_limit]
        skip,creature_limit,land_limit=self._untap_limits()
        for x in p.battlefield: x.sick=False
        self.skip_draw_step=bool(first and self.turn==1); self.log.append(f"Turn {self.turn}: {self.active_user}.")
        if skip:
            self._begin_upkeep(); return
        for x in p.battlefield:
            restricted_power=self.is_creature(x) and any(self.current_stats(x)[0]>=limit for limit in power_limits)
            aura_blocks_untap=any(self.card(aura.uid).aura_skip_untap for aura in self.attached_auras(x))
            eligible=x.tapped and not self.card(x.uid).skip_untap and not aura_blocks_untap and not restricted_power
            constrained=(creature_limit is not None and self.is_creature(x)) or (land_limit is not None and self.card(x.uid).land)
            if eligible and constrained: self.untap_pending.append(x.uid)
            elif eligible: x.tapped=False
        if self.untap_pending:
            self.phase="untap"; self.phase_passes=0; self.priority_user=self.active_user
            choices=self.untap_choices()
            if len(choices)==1: self.choose_untap(self.active_user,choices[0])
            return
        self._begin_upkeep()

    @staticmethod
    def _mana_requirements(card,x_value=0,mana_cost=None):
        symbols=re.findall(r"\{([^}]+)\}",card.mana_cost if mana_cost is None else mana_cost)
        generic=0; colored=[]
        for symbol in symbols:
            if symbol.isdigit(): generic+=int(symbol)
            elif symbol=="X":
                if card.x_mana_color: colored.extend([card.x_mana_color]*x_value)
                else: generic+=x_value
            elif symbol in {"W","U","B","R","G"}: colored.append(symbol)
            else: raise GameError(f"{card.name} uses an unsupported mana symbol: {{{symbol}}}.")
        return generic,colored

    def _mana_output(self,permanent,symbol):
        card=self.card(permanent.uid); output={symbol:card.mana_amount}
        if card.land:
            flares=sum(self.card(source.uid).mana_flare for player in self.players.values() for source in player.battlefield)
            output[symbol]+=flares
            gauntlets=sum(self.card(source.uid).mountain_extra_red and self.has_current_land_type(permanent,self.changed_land_word(source.uid,"mountain")) for player in self.players.values() for source in player.battlefield)
            if gauntlets:
                output["R"]=output.get("R",0)+gauntlets
            for aura in self.attached_auras(permanent):
                extra=self.card(aura.uid).aura_extra_mana
                if extra: output[extra]=output.get(extra,0)+1
        return output

    def extra_land_sources(self,user):
        return [source for source in self.player(user).battlefield if self.card(source.uid).extra_land_damage]

    def can_play_land(self,user):
        player=self.player(user)
        return not player.land_played or bool(self.extra_land_sources(user))

    def _land_event_triggers(self,user,event,played_extra=False):
        field="land_enter_damage" if event=="enter" else "land_grave_damage"
        triggers=[]
        for controller_id in (self.active_user,self.opponent(self.active_user)):
            controller=self.player(controller_id)
            for source in controller.battlefield:
                card=self.card(source.uid)
                if not getattr(card,field) and not (played_extra and controller_id==user and card.extra_land_damage): continue
                uid=self.next_uid; self.next_uid+=1; self.cards[uid]=card.key
                triggers.append(Spell(controller.user_id,uid,card.key,str(user),ability_effect="land_event_damage",source_uid=source.uid,color_override=source.color_override))
        return triggers

    def _tap_triggers(self,user,permanent,mana_symbol):
        tapped_card=self.card(permanent.uid)
        if not tapped_card.land: return []
        sources=[]
        if mana_symbol:
            sources.extend((controller.user_id,source,"tap_damage",str(user)) for controller in self.players.values() for source in controller.battlefield if self.card(source.uid).land_tap_damage)
        sources.extend((controller.user_id,aura,"tap_damage",str(user)) for controller in self.players.values() for aura in controller.battlefield if aura.attached_to==permanent.uid and self.card(aura.uid).aura_tap_damage)
        sources.extend((controller.user_id,aura,"kudzu_destroy",f"{user}:{permanent.uid}") for controller in self.players.values() for aura in controller.battlefield if aura.attached_to==permanent.uid and self.card(aura.uid).aura_kudzu)
        sources.extend((controller.user_id,source,"tap_life",str(controller.user_id)) for controller in self.players.values() if controller.user_id!=user for source in controller.battlefield if self.card(source.uid).opponent_forest_tap_life and self.has_current_land_type(permanent,self.changed_land_word(source.uid,"forest")))
        triggers=[]
        for owner,source,effect,target in sources:
            uid=self.next_uid; self.next_uid+=1; card=self.card(source.uid); self.cards[uid]=card.key
            triggers.append(Spell(owner,uid,card.key,target,ability_effect=effect,source_uid=source.uid,color_override=source.color_override,choice_owner=user if effect=="kudzu_destroy" else None))
        return triggers

    def _spell_cast_triggers(self,spell):
        colors=self.spell_colors(spell); spell_card=self.card(spell.uid); triggers=[]
        for controller_id in (self.active_user,self.opponent(self.active_user)):
            for source in self.player(controller_id).battlefield:
                card=self.card(source.uid)
                effect=""
                if card.cast_life_color and card.cast_life_color in colors: effect="cast_life"
                elif card.enchantment_cast_draw and controller_id==spell.owner and spell_card.has_type("Enchantment"): effect="cast_draw"
                if not effect: continue
                uid=self.next_uid; self.next_uid+=1; self.cards[uid]=card.key
                triggers.append(Spell(controller_id,uid,card.key,str(controller_id),ability_effect=effect,source_uid=source.uid,color_override=source.color_override))
        return triggers

    def _tap_permanent(self,user,permanent,mana_symbol=None,add_mana=False,pending_triggers=None):
        if permanent.tapped: return {}
        permanent.tapped=True; player=self.player(user)
        triggers=self._tap_triggers(user,permanent,mana_symbol)
        if pending_triggers is None: self.stack.extend(triggers)
        else: pending_triggers.extend(triggers)
        output=self._mana_output(permanent,mana_symbol) if mana_symbol else {}
        if add_mana:
            for symbol,count in output.items(): player.mana_pool[symbol]=player.mana_pool.get(symbol,0)+count
        return output

    def _mana_payment(self,player,card,x_value=0,mana_cost=None,excluded_uids=(),activation_colors=(),activation_is_enchantment=False):
        generic,colored=self._mana_requirements(card,x_value,mana_cost)
        for battlefield in self.players.values():
            for source in battlefield.battlefield:
                tax=self.card(source.uid)
                taxed_color=self.changed_color_word(source.uid,"W")
                if mana_cost is None and taxed_color in getattr(card,"colors",()): generic+=tax.tax_white_spells
                elif mana_cost is not None and activation_is_enchantment and taxed_color in activation_colors: generic+=tax.tax_white_enchantment_abilities
        mana_conversions={(self.changed_color_word(source.uid,"W"),self.changed_color_word(source.uid,"R")) for source in player.battlefield if self.card(source.uid).white_as_red}
        order=("W","U","B","R","G"); initial=tuple(colored.count(symbol) for symbol in order)+(generic,)
        items=[]
        for symbol,count in player.mana_pool.items():
            for number in range(count): items.append(("pool",f"{symbol}:{number}",None,((symbol,{symbol:1}),)))
        for permanent in player.battlefield:
            source=self.card(permanent.uid)
            if permanent.uid in excluded_uids: continue
            mana_choices=self.current_mana_choices(permanent)
            if mana_choices and source.mana_amount==1 and not source.mana_activation_cost and not source.sacrifice_for_mana and not permanent.tapped and (not self.is_creature(permanent) or not permanent.sick or source.haste or "haste" in self.current_keywords(permanent)):
                options=tuple((symbol,self._mana_output(permanent,symbol)) for symbol in mana_choices)
                items.append(("permanent",str(permanent.uid),permanent,options))

        def reduce_requirements(requirements,output):
            remaining=list(requirements); spare=0
            for index,symbol in enumerate(order):
                amount=output.get(symbol,0); used=min(remaining[index],amount); remaining[index]-=used; amount-=used
                for source_color,target_color in mana_conversions:
                    if symbol!=source_color or source_color==target_color: continue
                    target_index=order.index(target_color); converted=min(remaining[target_index],amount); remaining[target_index]-=converted; amount-=converted
                spare+=amount
            spare+=output.get("C",0); remaining[5]=max(0,remaining[5]-spare)
            return tuple(remaining)
        def score(plan):
            return sum(item[0]=="permanent" for item in plan),len(plan)

        plans={initial:[]}
        for kind,identifier,permanent,options in items:
            updated=dict(plans)
            for requirements,plan in plans.items():
                for symbol,output in options:
                    reduced=reduce_requirements(requirements,output)
                    if reduced==requirements: continue
                    candidate=plan+[(kind,identifier,permanent,symbol,output)]
                    if reduced not in updated or score(candidate)<score(updated[reduced]): updated[reduced]=candidate
            plans=updated
        plan=plans.get((0,0,0,0,0,0))
        if plan is None: return None
        sources=[]; choices={}; remaining=dict(player.mana_pool)
        for kind,identifier,permanent,symbol,output in plan:
            if kind!="permanent": continue
            sources.append(permanent); choices[permanent.uid]=symbol
            for produced,count in output.items(): remaining[produced]=remaining.get(produced,0)+count
        for symbol in order:
            for _ in range(colored.count(symbol)):
                paid_symbol=symbol if remaining.get(symbol,0) else next((source for source,target in mana_conversions if target==symbol and remaining.get(source,0)),symbol)
                remaining[paid_symbol]-=1
                if not remaining[paid_symbol]: remaining.pop(paid_symbol)
        for _ in range(generic):
            symbol=next((choice for choice in ("C","W","U","B","R","G") if remaining.get(choice,0)),None)
            if symbol is None: return None
            remaining[symbol]-=1
            if not remaining[symbol]: remaining.pop(symbol)
        return sources,remaining,choices

    def can_pay(self,user,card,x_value=0):
        return self._mana_payment(self.player(user),card,x_value) is not None
    def max_payable_x(self,user,card):
        if "{X}" not in card.mana_cost: return 0
        value=0
        while self.can_pay(user,card,value+1): value+=1
        return value

    def _remember_source_power(self,permanent):
        power,toughness=self.current_stats(permanent); permanent.last_known_toughness=toughness
        for item in self.stack:
            if item.source_uid==permanent.uid: item.source_power=power

    def _target_for_activation(self,card,user,target,source):
        if card.activation_effect=="prevent_source_damage":
            if target and target.upper().startswith("S:"):
                source=self._target_stack(target); colors=self.spell_colors(source); source_uid=source.uid
            else:
                if not target or ":" not in target: raise GameError("Source must be S:POSITION or USER_ID:POSITION.")
                try: target_user,pos=(int(x) for x in target.split(":"))
                except (TypeError,ValueError) as e: raise GameError("Source must be S:POSITION or USER_ID:POSITION.") from e
                battlefield=self.player(target_user).battlefield
                if not 1<=pos<=len(battlefield): raise GameError("No permanent at that battlefield position.")
                source=battlefield[pos-1]; colors=self.current_colors(source); source_uid=source.uid
            if card.prevent_source_color not in colors: raise GameError(f"Chosen source must be {card.prevent_source_color}.")
            return f"D:{source_uid}:{card.prevent_source_color}"
        if card.activation_effect=="force_attack":
            if user==self.active_user or self.phase not in ("upkeep","draw","precombat_main"): raise GameError("Activate Nettling Imp only during an opponent’s turn before attackers are declared.")
        if card.activation_effect=="cap_unblocked_damage":
            if self.phase not in ("after_blockers","after_first_strike") or self.active_user==user: raise GameError("Choose an unblocked attacking creature after blockers.")
            if not target or ":" not in target: raise GameError("Target must be USER_ID:POSITION.")
            try: target_user,pos=(int(x) for x in target.split(":"))
            except (TypeError,ValueError) as error: raise GameError("Target must be USER_ID:POSITION.") from error
            battlefield=self.player(target_user).battlefield
            if target_user!=self.active_user or not 1<=pos<=len(battlefield): raise GameError("Choose an unblocked attacking creature.")
            attacker=battlefield[pos-1]
            if attacker.uid not in self.attackers or attacker.uid in self.blocks: raise GameError("Choose an unblocked attacking creature.")
            return f"{target_user}:{attacker.uid}"
        if card.activation_effect=="redirect_source_to_creature":
            parts=(target or "").split(">",1)
            if len(parts)!=2: raise GameError("Choose SOURCE>TARGET using S:POSITION or USER_ID:POSITION for the source and USER_ID:POSITION for the creature.")
            source_text,target_text=parts
            if source_text.upper().startswith("S:"):
                try: stack_position=int(source_text.split(":",1)[1])
                except ValueError as error: raise GameError("Source stack position is invalid.") from error
                visible=list(reversed(self.stack))
                if not 1<=stack_position<=len(visible): raise GameError("No source at that stack position.")
                item=visible[stack_position-1]; source_uid=item.source_uid if item.ability_effect and item.source_uid is not None else item.uid
            else:
                if ":" not in source_text: raise GameError("Source must be S:POSITION or USER_ID:POSITION.")
                try: source_user,source_position=(int(x) for x in source_text.split(":"))
                except ValueError as error: raise GameError("Source must be S:POSITION or USER_ID:POSITION.") from error
                battlefield=self.player(source_user).battlefield
                if not 1<=source_position<=len(battlefield): raise GameError("No source at that battlefield position.")
                source_uid=battlefield[source_position-1].uid
            target_user,permanent=self._target_creature(target_text,"Target creature must be USER_ID:POSITION.")
            return f"J:{source_uid}:{target_user}:{permanent.uid}"
        if card.activation_effect=="counter_color":
            spell=self._target_stack(target)
            if card.target_color not in self.spell_colors(spell): raise GameError(f"Target spell must be {card.target_color}.")
            return f"S:{spell.uid}"
        if card.activation_effect in ("draw_self","destroy_all_nonland","create_token","prevent_player_damage","damage_all","take_extra_turn"): return str(user)
        if card.activation_effect in ("discard_choice","look_hand"):
            try: target_user=int(target)
            except (TypeError,ValueError) as error: raise GameError("Target must be a player ID.") from error
            self.player(target_user); return str(target_user)
        if card.activation_effect in ("untap_self","animate_self","add_power_counters"): return f"{user}:{source.uid}"
        if card.activation_effect=="redirect_one_to_owner": return f"{user}:{source.uid}"
        if card.activation_attached:
            controller,attached=self.find_permanent(source.attached_to)
            if attached is None or not self._aura_can_attach(card,attached,source): raise GameError(f"{card.name} is not attached to a legal permanent.")
            return f"{controller.user_id}:{attached.uid}"
        if not card.activation_effect or card.activation_effect in ("regenerate","corpse_regenerate"): return f"{user}:{source.uid}"
        if card.activation_effect in ("damage_any","prevent_any_damage") and target and ":" not in target:
            try: target_user=int(target)
            except (TypeError,ValueError) as e: raise GameError("Target must be a player ID or USER_ID:POSITION.") from e
            self.player(target_user); return str(target_user)
        if not target or ":" not in target: raise GameError("Target must be a player ID or USER_ID:POSITION." if card.activation_effect in ("damage_any","prevent_any_damage") else "Target must be USER_ID:POSITION.")
        try: target_user,pos=(int(x) for x in target.split(":"))
        except (TypeError,ValueError) as e: raise GameError("Target must be USER_ID:POSITION.") from e
        battlefield=self.player(target_user).battlefield
        if not 1<=pos<=len(battlefield): raise GameError("No permanent at that battlefield position.")
        permanent=battlefield[pos-1]; target_card=self.card(permanent.uid)
        if card.activation_effect in ("damage_any","prevent_any_damage") and not self.is_creature(permanent): raise GameError("Target permanent is not a creature.")
        if card.activation_effect=="destroy_black_permanent" and "B" not in self.current_colors(permanent): raise GameError("Target permanent is not black.")
        if card.activation_effect=="destroy_tapped_creature" and (not self.is_creature(permanent) or not permanent.tapped): raise GameError("Target must be a tapped creature.")
        if card.activation_effect=="destroy_wall" and "Wall" not in target_card.type_line.split(" — ",1)[-1].split(): raise GameError("Target must be a Wall.")
        if card.activation_effect=="grant_banding" and not self.is_creature(permanent): raise GameError("Target must be a creature.")
        if card.activation_effect=="unblockable" and (not self.is_creature(permanent) or self.current_stats(permanent)[0]>2): raise GameError("Target must be a creature with power 2 or less.")
        if card.activation_effect=="grant_flying_delayed_destroy" and (target_user!=user or not self.is_creature(permanent) or self.current_stats(permanent)[1]>=self.current_stats(source)[0]): raise GameError("Target must be a creature you control with toughness less than this creature’s power.")
        if card.activation_effect=="force_attack" and (target_user!=self.active_user or not self.is_creature(permanent) or self._has_subtype(target_card,"Wall") or permanent.sick): raise GameError("Target must be a non-Wall creature the active player controlled since the turn began.")
        if card.activation_effect in ("untap_land","destroy_land","set_land_forest","add_mire_counter") and not target_card.land: raise GameError("Target must be a land.")
        if card.activation_effect=="add_mire_counter" and self.has_current_land_type(permanent,self.changed_land_word(source.uid,"swamp")): raise GameError("Target must not already have the named basic land type.")
        if card.activation_effect=="tap_permanent" and not any(self.has_current_type(permanent,kind) for kind in ("Artifact","Creature","Land")): raise GameError("Target must be an artifact, creature, or land.")
        if card.activation_effect=="chaos_orb_destroy" and self.is_token(permanent.uid): raise GameError("Chaos Orb must target a nontoken permanent.")
        return f"{target_user}:{permanent.uid}"

    def can_activate(self,user,position,target=None,x_value=None,choice_value=None):
        player=self.player(user)
        if not 1<=position<=len(player.battlefield): return False
        permanent=player.battlefield[position-1]; card=self.card(permanent.uid)
        activation_cost,activation_effect,activation_tap,_=self._activation_profile(permanent)
        if not (activation_cost or activation_effect): return False
        if activation_effect=="corpse_regenerate" and permanent.corpse_counters<=0: return False
        if card.activation_upkeep_only and (self.phase!="upkeep" or self.active_user!=user): return False
        if activation_effect=="force_attack" and (user==self.active_user or self.phase not in ("upkeep","draw","precombat_main")): return False
        if card.activation_controller_turn_only and self.active_user!=user: return False
        if card.activation_owner_only and permanent.owner!=user: return False
        if card.activation_once_per_turn and permanent.activations_this_turn: return False
        if card.activation_x_choice:
            if not isinstance(x_value,int) or not isinstance(choice_value,int) or x_value<0 or choice_value<0 or choice_value>x_value or permanent.power_counters+choice_value>7: return False
        else: x_value=0
        if card.animate_combat and self.phase not in ("after_attackers","after_blockers","after_first_strike"): return False
        if activation_tap and (permanent.tapped or (self.is_creature(permanent) and permanent.sick and not card.haste and "haste" not in self.current_keywords(permanent))): return False
        try:
            stable_target=self._target_for_activation(card,user,target,permanent)
            protected=self._stable_target_permanent(stable_target)
            if protected is not None and not card.activation_attached and activation_effect not in ("","regenerate","add_power_counters","redirect_one_to_owner","cap_unblocked_damage","hydra_prevent","hydra_counter") and self._protected_from(protected,card,self.current_colors(permanent)): return False
        except GameError: return False
        excluded=(permanent.uid,) if activation_tap else ()
        return self._mana_payment(player,card,x_value=x_value,mana_cost=activation_cost,excluded_uids=excluded,activation_colors=self.current_colors(permanent),activation_is_enchantment=card.has_type("Enchantment")) is not None

    def activate_ability(self,user,position,target=None,x_value=None,choice_value=None):
        self._priority(user); player=self.player(user)
        if not 1<=position<=len(player.battlefield): raise GameError("No permanent at that battlefield position.")
        permanent=player.battlefield[position-1]; card=self.card(permanent.uid)
        activation_cost,activation_effect,activation_tap,native=self._activation_profile(permanent)
        if not (activation_cost or activation_effect): raise GameError("That permanent has no supported activated ability.")
        if activation_effect=="corpse_regenerate" and permanent.corpse_counters<=0: raise GameError(f"{card.name} has no corpse counters to remove.")
        if card.activation_upkeep_only and (self.phase!="upkeep" or self.active_user!=user): raise GameError(f"{card.name} can be activated only during your upkeep.")
        if activation_effect=="force_attack" and (user==self.active_user or self.phase not in ("upkeep","draw","precombat_main")): raise GameError(f"{card.name} can be activated only during an opponent’s turn before attackers are declared.")
        if card.activation_controller_turn_only and self.active_user!=user: raise GameError(f"{card.name} can be activated only during your turn.")
        if card.activation_owner_only and permanent.owner!=user: raise GameError(f"Only {card.name}’s owner may activate it.")
        if card.activation_once_per_turn and permanent.activations_this_turn: raise GameError(f"{card.name} can be activated only once each turn.")
        if card.activation_x_choice:
            if not isinstance(x_value,int) or x_value<0: raise GameError(f"Choose a nonnegative X value for {card.name}.")
            if not isinstance(choice_value,int) or choice_value<0 or choice_value>x_value: raise GameError(f"Choose counters from zero through X for {card.name}.")
            if permanent.power_counters+choice_value>7: raise GameError(f"{card.name} cannot have more than seven +1/+0 counters.")
        else: x_value=0; choice_value=0
        if card.animate_combat and self.phase not in ("after_attackers","after_blockers","after_first_strike"): raise GameError(f"{card.name} can be activated only during combat.")
        if activation_tap and permanent.tapped: raise GameError(f"{card.name} is already tapped.")
        if activation_tap and self.is_creature(permanent) and permanent.sick and not card.haste and "haste" not in self.current_keywords(permanent): raise GameError(f"{card.name} has summoning sickness.")
        stable_target=self._target_for_activation(card,user,target,permanent)
        protected=self._stable_target_permanent(stable_target)
        if protected is not None and not card.activation_attached and activation_effect not in ("","regenerate","add_power_counters","redirect_one_to_owner","cap_unblocked_damage","hydra_prevent","hydra_counter") and self._protected_from(protected,card,self.current_colors(permanent)): raise GameError(f"{card.name} cannot target a permanent with protection from its color.")
        excluded=(permanent.uid,) if activation_tap else ()
        payment=self._mana_payment(player,card,x_value=x_value,mana_cost=activation_cost,excluded_uids=excluded,activation_colors=self.current_colors(permanent),activation_is_enchantment=card.has_type("Enchantment"))
        if payment is None: raise GameError(f"You cannot pay {activation_cost or 'that cost'} for {card.name}.")
        sources,remaining,choices=payment; pending_triggers=[]
        for source in sources: self._tap_permanent(user,source,choices[source.uid],pending_triggers=pending_triggers)
        player.mana_pool=remaining
        if activation_tap: permanent.tapped=True
        if activation_effect=="corpse_regenerate": permanent.corpse_counters-=1
        permanent.activations_this_turn+=1
        if card.sacrifice_after_activations and permanent.activations_this_turn>=card.sacrifice_after_activations:
            permanent.sacrifice_at_end_step=True
        ability_uid=self.next_uid; self.next_uid+=1; self.cards[ability_uid]=card.key
        stored_choice=permanent.layer_timestamp if activation_effect in ("set_land_forest","add_mire_counter") else choice_value
        choice_owner=int(stable_target) if activation_effect=="discard_choice" else (user if activation_effect=="look_hand" else None)
        self.stack.append(Spell(user,ability_uid,card.key,stable_target,x_value=x_value,ability_effect=activation_effect or "self",source_uid=permanent.uid,color_override=permanent.color_override,source_power=self.current_stats(permanent)[0],choice_value=stored_choice,choice_owner=choice_owner)); self.stack.extend(pending_triggers)
        self._sba(); self._life()
        self.phase_passes=0
        for item in self.stack[:-1]: item.passes=0
        if not self.finished: self.priority_user=self.opponent(user)
        ability_text=card.ability_text if native else f"{activation_cost}: Regenerate this creature (granted)"
        self.log.append(f"{user} activated {card.name}: {ability_text}.")

    def activate_personal_incarnation(self,user,controller_id,position):
        self._priority(user); controller=self.player(controller_id)
        if not 1<=position<=len(controller.battlefield): raise GameError("No permanent at that battlefield position.")
        permanent=controller.battlefield[position-1]; card=self.card(permanent.uid)
        if card.key!="lea:31" or permanent.owner!=user: raise GameError("Choose a Personal Incarnation you own.")
        ability_uid=self.next_uid; self.next_uid+=1; self.cards[ability_uid]=card.key
        self.stack.append(Spell(user,ability_uid,card.key,f"{controller_id}:{permanent.uid}",ability_effect="redirect_one_to_owner",source_uid=permanent.uid,color_override=permanent.color_override))
        self.phase_passes=0
        for item in self.stack[:-1]: item.passes=0
        self.priority_user=self.opponent(user); self.log.append(f"{user} activated {card.name} as its owner.")

    def activate_mana(self,user,position,color=None):
        self._priority(user); player=self.player(user)
        if not 1<=position<=len(player.battlefield): raise GameError("No permanent at that battlefield position.")
        permanent=player.battlefield[position-1]; card=self.card(permanent.uid)
        mana_choices=self.current_mana_choices(permanent)
        if not mana_choices: raise GameError("That permanent has no supported mana ability.")
        if permanent.tapped: raise GameError(f"{card.name} is already tapped.")
        if self.is_creature(permanent) and permanent.sick and not card.haste and "haste" not in self.current_keywords(permanent): raise GameError(f"{card.name} has summoning sickness.")
        symbol=(color or (mana_choices[0] if len(mana_choices)==1 else "")).upper()
        if symbol not in mana_choices: raise GameError(f"Choose one of: {', '.join(mana_choices)}.")
        pending_triggers=[]
        if card.mana_activation_cost:
            payment=self._mana_payment(player,card,mana_cost=card.mana_activation_cost,excluded_uids=(permanent.uid,))
            if payment is None: raise GameError(f"You cannot pay {card.mana_activation_cost} for {card.name}.")
            sources,remaining,choices=payment
            for source in sources: self._tap_permanent(user,source,choices[source.uid],pending_triggers=pending_triggers)
            player.mana_pool=remaining
        output=self._tap_permanent(user,permanent,symbol,pending_triggers=pending_triggers)
        for produced,count in output.items(): player.mana_pool[produced]=player.mana_pool.get(produced,0)+count
        self.stack.extend(pending_triggers)
        if card.sacrifice_for_mana:
            self._remember_source_power(permanent); player.battlefield.remove(permanent); self._dies(player,permanent)
        self._sba(); self._life()
        self.phase_passes=0
        for spell in self.stack: spell.passes=0
        produced=" ".join(f"{{{mana}}}"+(f"×{count}" if count>1 else "") for mana,count in output.items())
        self.log.append(f"{user} added {produced}.")

    def activate_channel(self,user,amount=1):
        self._priority(user); player=self.player(user)
        if not player.channel_active: raise GameError("Channel is not active for you.")
        try: amount=int(amount)
        except (TypeError,ValueError) as error: raise GameError("Channel amount must be a positive whole number.") from error
        if amount<1: raise GameError("Channel amount must be a positive whole number.")
        if amount>player.life: raise GameError("You cannot pay more life than you have.")
        player.life-=amount; player.mana_pool["C"]=player.mana_pool.get("C",0)+amount
        self._life(); self.phase_passes=0
        for spell in self.stack: spell.passes=0
        self.log.append(f"{user} paid {amount} life through Channel and added {{C}}×{amount}.")

    def activate_guardian_angel(self,user,target):
        self._priority(user); player=self.player(user)
        if not player.guardian_angel_active: raise GameError("Guardian Angel is not active for you this turn.")
        card=CARDS["lea:21"]; payment=self._mana_payment(player,card,mana_cost="{1}")
        if payment is None: raise GameError("You cannot pay {1} for Guardian Angel.")
        if target and ":" in str(target):
            target_user,permanent=self._target_creature(str(target),"Guardian Angel target must be a player ID or USER_ID:POSITION."); stable=f"{target_user}:{permanent.uid}"
        else:
            try: target_user=int(target)
            except (TypeError,ValueError) as error: raise GameError("Guardian Angel target must be a player ID or USER_ID:POSITION.") from error
            self.player(target_user); stable=str(target_user)
        sources,remaining,choices=payment; pending=[]
        for source in sources: self._tap_permanent(user,source,choices[source.uid],pending_triggers=pending)
        player.mana_pool=remaining
        if ":" in stable: permanent.damage_prevention+=1
        else: self.player(int(stable)).damage_prevention+=1
        self.stack.extend(pending); self.phase_passes=0
        for item in self.stack: item.passes=0
        self.log.append(f"{user} paid {{1}} through Guardian Angel to prevent the next 1 damage to {stable}.")

    def activate_hydra(self,user,position,mode):
        self._priority(user); player=self.player(user)
        if not 1<=position<=len(player.battlefield): raise GameError("No permanent at that battlefield position.")
        permanent=player.battlefield[position-1]; card=self.card(permanent.uid)
        if not card.hydra_damage_replacement: raise GameError("Choose a Rock Hydra you control.")
        mode=str(mode).casefold(); cost="{R}" if mode=="prevent" else "{R}{R}{R}" if mode=="counter" else ""
        if not cost: raise GameError("Choose `prevent` or `counter` for Rock Hydra.")
        if mode=="counter" and (self.active_user!=user or self.phase!="upkeep"): raise GameError("Rock Hydra can add a counter only during your upkeep.")
        payment=self._mana_payment(player,card,mana_cost=cost)
        if payment is None: raise GameError(f"You cannot pay {cost} for Rock Hydra.")
        sources,remaining,choices=payment; pending=[]
        for source in sources: self._tap_permanent(user,source,choices[source.uid],pending_triggers=pending)
        player.mana_pool=remaining; uid=self.next_uid; self.next_uid+=1; self.cards[uid]=card.key
        effect="hydra_prevent" if mode=="prevent" else "hydra_counter"
        self.stack.append(Spell(user,uid,card.key,f"{user}:{permanent.uid}",ability_effect=effect,source_uid=permanent.uid,color_override=permanent.color_override))
        self.stack.extend(pending); self.phase_passes=0
        for item in self.stack: item.passes=0
        self.log.append(f"{user} activated Rock Hydra to {'prevent 1 damage' if mode=='prevent' else 'add a +1/+1 counter'}.")

    def choose_hydra_order(self,user,position,order):
        self._priority(user); player=self.player(user)
        if not 1<=position<=len(player.battlefield): raise GameError("No permanent at that battlefield position.")
        permanent=player.battlefield[position-1]
        if not self.card(permanent.uid).hydra_damage_replacement: raise GameError("Choose a Rock Hydra you control.")
        order=str(order).casefold()
        if order not in ("counters","shields"): raise GameError("Choose `counters` or `shields` first.")
        permanent.hydra_counters_first=order=="counters"; self.phase_passes=0
        for item in self.stack: item.passes=0
        self.log.append(f"{user} chose Rock Hydra {order} first for replacement effects.")

    def _empty_mana(self):
        for player in self.players.values(): player.mana_pool.clear()

    def play(self,user,index,target=None,x_value=None):
        self._priority(user); p=self.player(user)
        if not 1<=index<=len(p.hand): raise GameError("No card at that hand position.")
        uid=p.hand[index-1]; c=self.card(uid)
        uses_x="{X}" in c.mana_cost
        if uses_x:
            if x_value is None: raise GameError(f"{c.name} requires a nonnegative X value.")
            try: x_value=int(x_value)
            except (TypeError,ValueError) as e: raise GameError(f"{c.name} requires a nonnegative X value.") from e
            if x_value<0: raise GameError(f"{c.name} requires a nonnegative X value.")
        elif x_value is not None: raise GameError(f"{c.name} has no X value.")
        else: x_value=0
        if c.land:
            if user!=self.active_user: raise GameError("Only the active player can play a land.")
            if self.phase not in ("precombat_main","postcombat_main") or self.stack: raise GameError("Land requires an empty-stack main phase.")
            if not self.can_play_land(user): raise GameError("You already played a land.")
            prior_plays=max(p.lands_played_this_turn,int(p.land_played)); played_extra=prior_plays>0
            p.hand.pop(index-1); p.battlefield.append(self._make_permanent(uid,c.key,owner=user)); p.land_played=True; p.lands_played_this_turn=prior_plays+1; self.phase_passes=0
            self.stack.extend(self._land_event_triggers(user,"enter",played_extra))
            self.log.append(f"{user} played {c.name}."); return
        if c.kind!="Instant" and (user!=self.active_user or self.phase not in ("precombat_main","postcombat_main") or self.stack): raise GameError("Cast that during your main phase with an empty stack.")
        if c.effect=="berserk" and self.phase not in ("upkeep","draw","precombat_main","after_attackers","after_blockers","after_first_strike"):
            raise GameError("Berserk can be cast only before the combat damage step.")
        if c.effect=="blaze_of_glory" and (self.phase!="after_attackers" or user==self.active_user):
            raise GameError("Blaze of Glory can be cast only while defending after attackers and before blockers.")
        if c.effect=="false_orders" and self.phase!="after_blockers":
            raise GameError("False Orders can be cast only after blockers and before combat damage.")
        if c.effect=="siren_call" and (user==self.active_user or self.phase not in ("upkeep","draw","precombat_main")):
            raise GameError("Siren’s Call can be cast only during an opponent’s turn before attackers are declared.")
        sacrificed=None
        if c.additional_sacrifice_creature:
            parts=(target or "").casefold().split(":")
            if len(parts)!=2 or parts[0]!="sacrifice": raise GameError(f"{c.name} requires sacrifice:FIELD_POSITION.")
            try: sacrifice_position=int(parts[1])
            except ValueError as error: raise GameError(f"{c.name} requires sacrifice:FIELD_POSITION.") from error
            if not 1<=sacrifice_position<=len(p.battlefield): raise GameError("No permanent at that battlefield position.")
            sacrificed=p.battlefield[sacrifice_position-1]
            if not self.is_creature(sacrificed): raise GameError(f"{c.name} requires a creature you control.")
            target=None
        target=self._target_for_cast(c,user,target,x_value)
        if c.effect=="counter_mana_value_x":
            target_uid=int(target.split(":",1)[1]); target_spell=next((item for item in self.stack if item.uid==target_uid and not item.ability_effect),None)
            if target_spell is None or self.spell_mana_value(target_spell)!=x_value: raise GameError(f"{c.name} X must equal the target spell mana value.")
        protected=self._stable_target_permanent(target)
        if protected is not None and self._protected_from(protected,c): raise GameError(f"{c.name} cannot target a permanent with protection from its color.")
        payment_cost=c.mana_cost+(f"{{{max(0,len(target.split(chr(44)))-1)}}}" if c.effect=="fireball" and target else "")
        payment=self._mana_payment(p,c,x_value,mana_cost=payment_cost if c.effect=="fireball" else None)
        if payment is None: raise GameError(f"You cannot pay {c.mana_cost or c.cost} with your available mana.")
        sources,remaining,choices=payment; pending_triggers=[]
        for permanent in sources: self._tap_permanent(user,permanent,choices[permanent.uid],pending_triggers=pending_triggers)
        p.mana_pool=remaining
        p.hand.pop(index-1); self.phase_passes=0
        for spell in self.stack: spell.passes=0
        sacrifice_value=self.card(sacrificed.uid).cost if sacrificed is not None else 0
        choice_owner=None
        if c.effect=="drain_power": choice_owner=int(target)
        elif c.effect=="power_sink":
            target_uid=int(target.split(":",1)[1]); target_spell=next(item for item in self.stack if item.uid==target_uid)
            choice_owner=target_spell.owner
        spell=Spell(user,uid,c.key,target,x_value=x_value,choice_value=sacrifice_value,choice_owner=choice_owner)
        self.stack.append(spell)
        if sacrificed is not None:
            sacrificed_name=self.card(sacrificed.uid).name; was_land=self.card(sacrificed.uid).land; death_sources=self._death_trigger_sources(); batch=self.next_uid
            self._remember_source_power(sacrificed); self._remove_from_combat(sacrificed.uid); p.battlefield.remove(sacrificed); self._dies(p,sacrificed,batch,death_sources)
            if was_land and sacrificed.uid in p.graveyard: self.stack.extend(self._land_event_triggers(user,"grave"))
            self.log.append(f"{user} sacrificed {sacrificed_name} as an additional cost for {c.name}.")
        self.stack.extend(pending_triggers); self.stack.extend(self._spell_cast_triggers(spell)); self._sba(); self._life()
        if not self.finished: self.priority_user=self.opponent(user)
        suffix=f" with X={x_value}" if uses_x else ""
        self.log.append(f"{user} cast {c.name}{suffix}.")

    def _target_creature(self,target,message="Target must be USER_ID:POSITION."):
        if not target or ":" not in target: raise GameError(message)
        try: target_user,pos=(int(x) for x in target.split(":"))
        except (TypeError,ValueError) as e: raise GameError(message) from e
        battlefield=self.player(target_user).battlefield
        if not 1<=pos<=len(battlefield): raise GameError("No permanent at that battlefield position.")
        permanent=battlefield[pos-1]
        if not self.is_creature(permanent): raise GameError("Target is not a creature.")
        return target_user,permanent

    def _target_stack(self,target):
        if not target or not target.upper().startswith("S:"): raise GameError("Stack target must be S:POSITION.")
        try: position=int(target.split(":",1)[1])
        except (TypeError,ValueError) as e: raise GameError("Stack target must be S:POSITION.") from e
        visible=list(reversed(self.stack))
        if not 1<=position<=len(visible): raise GameError("No spell at that stack position.")
        item=visible[position-1]
        if item.ability_effect: raise GameError("That stack item is an ability, not a spell.")
        return item

    def _target_graveyard(self,user,target,creature_only):
        if not target or not target.upper().startswith("G:"): raise GameError("Graveyard target must be G:POSITION.")
        try: position=int(target.split(":",1)[1])
        except (TypeError,ValueError) as e: raise GameError("Graveyard target must be G:POSITION.") from e
        graveyard=self.player(user).graveyard
        if not 1<=position<=len(graveyard): raise GameError("No card at that graveyard position.")
        uid=graveyard[position-1]
        if creature_only and not self.card(uid).creature: raise GameError("Target graveyard card is not a creature.")
        return str(uid)

    def _target_for_cast(self,c,user,target,x_value=0):
        if c.effect in ("fireball","volcanic_eruption"):
            raw=[] if not target else [item.strip() for item in str(target).split(",") if item.strip()]
            if len(raw)!=len(set(raw)): raise GameError(f"{c.name} targets must be distinct.")
            if c.effect=="volcanic_eruption" and len(raw)!=x_value: raise GameError(f"{c.name} requires exactly X Mountain targets.")
            stable=[]
            for item in raw:
                if c.effect=="fireball" and ":" not in item:
                    try: target_user=int(item)
                    except ValueError as error: raise GameError("Fireball targets must be player IDs or USER_ID:POSITION, separated by commas.") from error
                    self.player(target_user); stable.append(str(target_user)); continue
                if ":" not in item: raise GameError("Volcanic Eruption targets must be USER_ID:POSITION, separated by commas.")
                try: target_user,pos=(int(value) for value in item.split(":"))
                except ValueError as error: raise GameError(f"{c.name} targets must use USER_ID:POSITION.") from error
                battlefield=self.player(target_user).battlefield
                if not 1<=pos<=len(battlefield): raise GameError("No permanent at that battlefield position.")
                permanent=battlefield[pos-1]
                if c.effect=="fireball":
                    if not self.is_creature(permanent): raise GameError("Fireball permanent targets must be creatures.")
                elif not self.has_current_land_type(permanent,"mountain"): raise GameError("Volcanic Eruption must target Mountains.")
                if self._protected_from(permanent,c): raise GameError(f"{c.name} cannot target a permanent with protection from its color.")
                stable.append(f"{target_user}:{permanent.uid}")
            if len(stable)!=len(set(stable)): raise GameError(f"{c.name} targets must be distinct.")
            return ",".join(stable)
        if c.aura_reanimate:
            if not target: raise GameError("Animate Dead target must be G:POSITION or USER_ID:G:POSITION.")
            parts=target.upper().split(":")
            try:
                if len(parts)==2 and parts[0]=="G": target_user,position=user,int(parts[1])
                elif len(parts)==3 and parts[1]=="G": target_user,position=int(parts[0]),int(parts[2])
                else: raise ValueError
            except (TypeError,ValueError) as error: raise GameError("Animate Dead target must be G:POSITION or USER_ID:G:POSITION.") from error
            graveyard=self.player(target_user).graveyard
            if not 1<=position<=len(graveyard): raise GameError("No card at that graveyard position.")
            uid=graveyard[position-1]
            if not self.card(uid).creature: raise GameError("Animate Dead must target a creature card in a graveyard.")
            return f"G:{target_user}:{uid}"
        if c.aura_target_types:
            chosen_land_type=""
            if c.aura_choose_land_type:
                parts=(target or "").casefold().split(":")
                if len(parts)!=3 or parts[0] not in ("plains","island","swamp","mountain","forest"): raise GameError("Choose a basic land type with TYPE:USER_ID:POSITION.")
                chosen_land_type=parts[0]; target=":".join(parts[1:])
            if not target or ":" not in target: raise GameError("Aura target must be USER_ID:POSITION.")
            try: target_user,pos=(int(x) for x in target.split(":"))
            except (TypeError,ValueError) as error: raise GameError("Aura target must be USER_ID:POSITION.") from error
            battlefield=self.player(target_user).battlefield
            if not 1<=pos<=len(battlefield): raise GameError("No permanent at that battlefield position.")
            permanent=battlefield[pos-1]
            if not self._aura_can_attach(c,permanent): raise GameError(f"{c.name} cannot enchant that permanent.")
            stable=f"{target_user}:{permanent.uid}"
            return f"{chosen_land_type}:{stable}" if chosen_land_type else stable
        if c.effect=="guardian_angel":
            if target and ":" in target:
                target_user,permanent=self._target_creature(target,"Guardian Angel target must be a player ID or USER_ID:POSITION.")
                return f"{target_user}:{permanent.uid}"
            try: target_user=int(target)
            except (TypeError,ValueError) as error: raise GameError("Guardian Angel target must be a player ID or USER_ID:POSITION.") from error
            self.player(target_user); return str(target_user)
        if c.effect=="reverse_damage":
            if target and target.upper().startswith("S:"):
                source=self._target_stack(target); return f"D:{source.uid}"
            if not target or ":" not in target: raise GameError("Reverse Damage source must be S:POSITION or USER_ID:POSITION.")
            try: target_user,pos=(int(x) for x in target.split(":"))
            except (TypeError,ValueError) as error: raise GameError("Reverse Damage source must be S:POSITION or USER_ID:POSITION.") from error
            battlefield=self.player(target_user).battlefield
            if not 1<=pos<=len(battlefield): raise GameError("No permanent at that battlefield position.")
            return f"D:{battlefield[pos-1].uid}"
        if c.effect=="healing_salve":
            if not target: raise GameError("Healing Salve target must be life:PLAYER_ID, prevent:PLAYER_ID, or prevent:USER_ID:POSITION.")
            parts=target.casefold().split(":")
            if parts[0]=="life" and len(parts)==2:
                try: target_user=int(parts[1])
                except ValueError as error: raise GameError("Healing Salve target must be life:PLAYER_ID, prevent:PLAYER_ID, or prevent:USER_ID:POSITION.") from error
                self.player(target_user); return f"life:{target_user}"
            if parts[0]=="prevent" and len(parts)==2:
                try: target_user=int(parts[1])
                except ValueError as error: raise GameError("Healing Salve target must be life:PLAYER_ID, prevent:PLAYER_ID, or prevent:USER_ID:POSITION.") from error
                self.player(target_user); return f"prevent:{target_user}"
            if parts[0]=="prevent" and len(parts)==3:
                try: target_user,pos=int(parts[1]),int(parts[2])
                except ValueError as error: raise GameError("Healing Salve target must be life:PLAYER_ID, prevent:PLAYER_ID, or prevent:USER_ID:POSITION.") from error
                battlefield=self.player(target_user).battlefield
                if not 1<=pos<=len(battlefield): raise GameError("No permanent at that battlefield position.")
                permanent=battlefield[pos-1]
                if not self.is_creature(permanent): raise GameError("Healing Salve prevention target is not a creature.")
                return f"prevent:{target_user}:{permanent.uid}"
            raise GameError("Healing Salve target must be life:PLAYER_ID, prevent:PLAYER_ID, or prevent:USER_ID:POSITION.")
        if c.effect in ("counter_spell","counter_mana_value_x","power_sink","elemental_blast"):
            if target and target.upper().startswith("S:"):
                spell=self._target_stack(target); target_card=self.card(spell.uid)
                if c.target_color and c.target_color not in self.spell_colors(spell): raise GameError(f"Target spell must be {c.target_color}.")
                return f"S:{spell.uid}"
            if c.effect in ("counter_spell","counter_mana_value_x","power_sink"): raise GameError(f"{c.name} requires an S:POSITION stack target.")
            if not target or ":" not in target: raise GameError("Elemental Blast target must be S:POSITION or USER_ID:POSITION.")
            try: target_user,pos=(int(x) for x in target.split(":"))
            except (TypeError,ValueError) as e: raise GameError("Elemental Blast target must be S:POSITION or USER_ID:POSITION.") from e
            battlefield=self.player(target_user).battlefield
            if not 1<=pos<=len(battlefield): raise GameError("No permanent at that battlefield position.")
            permanent=battlefield[pos-1]; target_card=self.card(permanent.uid)
            if c.target_color not in self.current_colors(permanent): raise GameError(f"Target permanent must be {c.target_color}.")
            return f"{target_user}:{permanent.uid}"
        if c.effect in ("set_color","text_change_land","text_change_color"):
            label="Color-change" if c.effect=="set_color" else "Word-change"
            if target and target.upper().startswith("S:"):
                spell=self._target_stack(target); return f"S:{spell.uid}"
            if not target or ":" not in target: raise GameError(f"{label} target must be S:POSITION or USER_ID:POSITION.")
            try: target_user,pos=(int(x) for x in target.split(":"))
            except (TypeError,ValueError) as e: raise GameError(f"{label} target must be S:POSITION or USER_ID:POSITION.") from e
            battlefield=self.player(target_user).battlefield
            if not 1<=pos<=len(battlefield): raise GameError("No permanent at that battlefield position.")
            return f"{target_user}:{battlefield[pos-1].uid}"
        if c.effect=="damage":
            try: target_user=int(target) if target is not None else self.opponent(user)
            except (TypeError,ValueError) as e: raise GameError("Target must be a player ID.") from e
            self.player(target_user); return str(target_user)
        if c.effect in ("damage_any","damage_x_exile","drain_life_x"):
            if target and ":" in target:
                target_user,permanent=self._target_creature(target,"Target must be a player ID or USER_ID:POSITION.")
                return f"{target_user}:{permanent.uid}"
            try: target_user=int(target)
            except (TypeError,ValueError) as e: raise GameError("Target must be a player ID or USER_ID:POSITION.") from e
            self.player(target_user); return str(target_user)
        if c.effect=="blaze_of_glory":
            target_user,permanent=self._target_creature(target)
            if target_user!=self.opponent(self.active_user) or target_user!=user: raise GameError("Blaze of Glory must target a creature the defending player controls.")
            return f"{target_user}:{permanent.uid}"
        if c.effect=="false_orders":
            target_user,permanent=self._target_creature(target)
            if target_user!=self.opponent(self.active_user): raise GameError("False Orders must target a creature the defending player controls.")
            return f"{target_user}:{permanent.uid}"
        if c.effect=="return_creature_hand":
            target_user,permanent=self._target_creature(target)
            return f"{target_user}:{permanent.uid}"
        if c.effect in ("return_grave_creature_hand","reanimate_creature"):
            return self._target_graveyard(user,target,True)
        if c.effect=="return_grave_card_hand":
            return self._target_graveyard(user,target,False)
        if c.effect in ("regenerate_target","grant_keyword","destroy_wall"):
            target_user,permanent=self._target_creature(target)
            if c.effect=="destroy_wall" and "Wall" not in self.card(permanent.uid).type_line.split(" — ",1)[-1].split(): raise GameError("Target must be a Wall.")
            return f"{target_user}:{permanent.uid}"
        if c.effect=="tap_or_untap":
            if not target or target.count(":")!=2: raise GameError("Twiddle target must be tap:USER_ID:POSITION or untap:USER_ID:POSITION.")
            mode,user_text,pos_text=target.casefold().split(":")
            if mode not in ("tap","untap"): raise GameError("Twiddle mode must be tap or untap.")
            try: target_user,pos=int(user_text),int(pos_text)
            except ValueError as error: raise GameError("Twiddle target must be tap:USER_ID:POSITION or untap:USER_ID:POSITION.") from error
            battlefield=self.player(target_user).battlefield
            if not 1<=pos<=len(battlefield): raise GameError("No permanent at that battlefield position.")
            permanent=battlefield[pos-1]; target_card=self.card(permanent.uid)
            if not any(self.has_current_type(permanent,kind) for kind in c.target_types): raise GameError("Twiddle must target an artifact, creature, or land.")
            return f"{mode}:{target_user}:{permanent.uid}"
        if c.effect in ("destroy_creature","exile_creature_life"):
            target_user,permanent=self._target_creature(target)
            target_card=self.card(permanent.uid)
            if c.target_nonartifact and "Artifact" in target_card.type_line: raise GameError("Target must be a nonartifact creature.")
            if c.target_nonblack and "B" in self.current_colors(permanent): raise GameError("Target must be a nonblack creature.")
            return f"{target_user}:{permanent.uid}"
        if c.effect in ("pump","pump_blocking","pump_power_x","berserk"):
            target_user,permanent=self._target_creature(target)
            if c.effect=="pump_blocking" and permanent.uid not in self.all_blocker_uids():
                raise GameError(f"{c.name} must target a blocking creature.")
            return f"{target_user}:{permanent.uid}"
        if c.effect=="simulacrum":
            target_user,permanent=self._target_creature(target)
            if target_user!=user: raise GameError("Simulacrum must target a creature you control.")
            return f"{target_user}:{permanent.uid}"
        if c.effect in ("draw_target","draw_target_x","life_target_x","discard_random_x","mana_short","drain_power","natural_selection"):
            try: target_user=int(target)
            except (TypeError,ValueError) as e: raise GameError("Target must be a player ID.") from e
            self.player(target_user); return str(target_user)
        if c.effect=="destroy_permanent":
            if not target or ":" not in target: raise GameError("Target must be USER_ID:POSITION.")
            try: target_user,pos=(int(x) for x in target.split(":"))
            except (TypeError,ValueError) as e: raise GameError("Target must be USER_ID:POSITION.") from e
            battlefield=self.player(target_user).battlefield
            if not 1<=pos<=len(battlefield): raise GameError("No permanent at that battlefield position.")
            permanent=battlefield[pos-1]; target_card=self.card(permanent.uid)
            if not any(self.has_current_type(permanent,kind) for kind in c.target_types): raise GameError("Target has an unsupported permanent type.")
            return f"{target_user}:{permanent.uid}"
        if c.effect=="destroy_land":
            if not target or ":" not in target: raise GameError("Target must be USER_ID:POSITION.")
            try: target_user,pos=(int(x) for x in target.split(":"))
            except (TypeError,ValueError) as e: raise GameError("Target must be USER_ID:POSITION.") from e
            battlefield=self.player(target_user).battlefield
            if not 1<=pos<=len(battlefield): raise GameError("No permanent at that battlefield position.")
            permanent=battlefield[pos-1]
            if not self.card(permanent.uid).land: raise GameError("Target is not a land.")
            return f"{target_user}:{permanent.uid}"
        if target is not None: raise GameError(f"{c.name} does not use a target.")
        return None

    def pass_priority(self,user):
        self._priority(user)
        if self.stack:
            s=self.stack[-1]; s.passes+=1
            if s.passes==2:
                if s.ability_effect=="graveyard_return" and not self._graveyard_upkeep_return_eligible(s.owner,s.source_uid):
                    self.stack.pop(); self.cards.pop(s.uid,None); self.log.append(f"{self.card(s.source_uid).name} did not return because its graveyard condition was no longer true.")
                    if self.stack: self.stack[-1].passes=0
                    self.priority_user=self.active_user; return
                if s.ability_effect=="tomb_cleanup" and not self.tomb_cleanup_choices(s.source_uid,s.choice_value):
                    self.stack.pop(); self.cards.pop(s.uid,None)
                    if self.stack: self.stack[-1].passes=0
                    self.priority_user=self.active_user; return
                if s.ability_effect=="vesuvan_copy" and s.choice_value==1:
                    target=self._stable_target_permanent(s.target); _,source=self.find_permanent(s.source_uid)
                    if source is None or target is None or not self.is_creature(target) or self._protected_from(target,CARDS["lea:87"],self.ability_source_colors(s)):
                        self.stack.pop(); self._resolve(s)
                        if self.stack: self.stack[-1].passes=0
                        if not self.finished: self.priority_user=self.active_user
                        return
                    s.decision_pending=True; s.choice_value=2; self.priority_user=s.choice_owner; return
                if s.ability_effect=="kudzu_destroy":
                    target=self._stable_target_permanent(s.target); controller=self.find_permanent(target.uid)[0] if target is not None else None
                    self.stack.pop(); before=len(self.stack)
                    if target is not None and controller is not None and self.card(target.uid).land: self._destroy(controller,target)
                    queued=self.stack[before:]; del self.stack[before:]
                    _,aura=self.find_permanent(s.source_uid)
                    if aura is not None and self.card(aura.uid).aura_kudzu and self.kudzu_choices(s):
                        s.ability_effect="kudzu_move"; s.decision_pending=True; s.passes=0; self.stack.extend(queued); self.stack.append(s); self.priority_user=s.choice_owner; return
                    self.cards.pop(s.uid,None); self.stack.extend(queued); self._sba(); self._life()
                    if self.stack: self.stack[-1].passes=0
                    if not self.finished: self.priority_user=self.active_user
                    return
                if not s.ability_effect and self.card(s.uid).effect=="balance":
                    s.ability_effect="balance_lands"; s.mana_choices={}; s.choice_value=min(sum(self.card(x.uid).land for x in player.battlefield) for player in self.players.values()); self._advance_balance(s); return
                if not s.ability_effect and self.card(s.uid).enters_copy_types and self.copy_choices(s):
                    s.decision_pending=True; s.choice_owner=s.owner; self.priority_user=s.owner; return
                if s.ability_effect=="upkeep_sacrifice" and not self.trigger_sacrifice_choices(s):
                    self.stack.pop(); self._resolve(s)
                    if self.stack: self.stack[-1].passes=0
                    if not self.finished: self.priority_user=self.active_user
                    return
                if s.ability_effect in ("discard_choice","look_hand") and (s.ability_effect=="look_hand" or self.player(int(s.target)).hand):
                    s.decision_pending=True; self.priority_user=s.choice_owner; return
                if s.ability_effect in ("cast_life","cast_draw","death_life","upkeep_untap","aura_upkeep_untap","aura_upkeep_life","upkeep_vitality","upkeep_cost","graveyard_return","upkeep_sacrifice","tomb_cleanup","power_leak"):
                    s.decision_pending=True; self.priority_user=s.choice_owner if s.choice_owner is not None else s.owner; return
                if not s.ability_effect and self.card(s.uid).effect=="search_library" and self.player(s.owner).library:
                    s.decision_pending=True; self.priority_user=s.owner; return
                if not s.ability_effect and self.card(s.uid).effect=="natural_selection" and self.player(int(s.target)).library:
                    s.decision_pending=True; s.choice_owner=s.owner; self.priority_user=s.owner; return
                if not s.ability_effect and self.card(s.uid).effect in ("text_change_land","text_change_color"):
                    target=self._word_change_target(s.target)
                    if target is not None and not (isinstance(target,Permanent) and self._protected_from(target,self.card(s.uid),self.spell_colors(s))):
                        s.decision_pending=True; s.choice_owner=s.owner; self.priority_user=s.owner; return
                if not s.ability_effect and self.card(s.uid).effect=="false_orders":
                    target=self._stable_target_permanent(s.target)
                    if target is not None and self.find_permanent(target.uid)[0].user_id==self.opponent(self.active_user) and self.is_creature(target) and not self._protected_from(target,self.card(s.uid),self.spell_colors(s)):
                        s.decision_pending=True; s.choice_owner=s.owner; self.priority_user=s.owner; return
                if not s.ability_effect and self.card(s.uid).effect=="drain_power" and self._drain_power_lands(s):
                    s.decision_pending=True; self.priority_user=s.choice_owner; return
                if not s.ability_effect and self.card(s.uid).effect=="power_sink" and self._power_sink_target(s) is not None:
                    s.decision_pending=True; self.priority_user=s.choice_owner; return
                self.stack.pop(); self._resolve(s)
                if self.stack: self.stack[-1].passes=0
                if not self.finished: self.priority_user=self.active_user
            else: self.priority_user=self.opponent(user)
        else:
            if self.phase in ("attackers","blockers"): raise GameError("Complete the required combat declaration.")
            self.phase_passes+=1
            if self.phase_passes<2: self.priority_user=self.opponent(user)
            else:
                self.phase_passes=0
                if self.phase=="ending" and self.end_step_sacrifices:
                    self._resolve_end_step_sacrifices(); self.priority_user=self.active_user
                else:
                    self._empty_mana(); self._advance()

    def false_orders_choices(self,user):
        if not self.stack or not self.stack[-1].decision_pending or self.stack[-1].choice_owner!=user or self.stack[-1].ability_effect or self.card(self.stack[-1].uid).effect!="false_orders":
            raise GameError("You do not have a resolving False Orders choice.")
        target=self._stable_target_permanent(self.stack[-1].target)
        if target is None: return []
        return [(position,attacker) for position,uid in enumerate(self.attackers,1) if (attacker:=self.find_permanent(uid)[1]) is not None and any(self.can_block(member,target.uid)[0] for member in self.attack_band(uid))]

    def _word_change_target(self,target):
        if not target: return None
        if target.startswith("S:"):
            try: uid=int(target.split(":",1)[1])
            except ValueError: return None
            return next((item for item in self.stack if item.uid==uid and not item.ability_effect),None)
        return self._stable_target_permanent(target)

    def word_change_choices(self):
        if not self.stack or not self.stack[-1].decision_pending: return ()
        effect=self.card(self.stack[-1].uid).effect
        values=("plains","island","swamp","mountain","forest") if effect=="text_change_land" else ("W","U","B","R","G") if effect=="text_change_color" else ()
        return tuple((source,target) for source in values for target in values if source!=target)

    def choose_word_change(self,user,source,target):
        if not self.stack or not self.stack[-1].decision_pending or self.stack[-1].choice_owner!=user or self.card(self.stack[-1].uid).effect not in ("text_change_land","text_change_color"):
            raise GameError("No word-change choice is waiting for you.")
        spell=self.stack[-1]; choices=self.word_change_choices(); source=source.casefold() if self.card(spell.uid).effect=="text_change_land" else source.upper(); target=target.casefold() if self.card(spell.uid).effect=="text_change_land" else target.upper()
        if (source,target) not in choices: raise GameError("Choose two different supported basic land types or colors.")
        changed=self._word_change_target(spell.target)
        if changed is None or (isinstance(changed,Permanent) and self._protected_from(changed,self.card(spell.uid),self.spell_colors(spell))):
            self.stack.pop(); self.player(spell.owner).graveyard.append(spell.uid); self.log.append(f"{self.card(spell.uid).name} fizzled because its target was gone or illegal.")
        else:
            mapping=changed.land_word_changes if self.card(spell.uid).effect=="text_change_land" else changed.color_word_changes
            source_was_mapped=source in mapping
            for key,value in list(mapping.items()):
                if value==source: mapping[key]=target
            if not source_was_mapped: mapping[source]=target
            self.stack.pop(); self.player(spell.owner).graveyard.append(spell.uid); self.log.append(f"{user} changed {source} to {target} with {self.card(spell.uid).name}.")
        spell.decision_pending=False; self._sba(); self._life(); self.phase_passes=0
        if self.stack: self.stack[-1].passes=0
        if not self.finished: self.priority_user=self.active_user

    def choose_false_orders(self,user,attacker_position=None):
        choices={position:attacker for position,attacker in self.false_orders_choices(user)}; spell=self.stack[-1]; target=self._stable_target_permanent(spell.target)
        if attacker_position is not None:
            try: attacker_position=int(attacker_position)
            except (TypeError,ValueError) as error: raise GameError("Choose an attacking-creature position or decline.") from error
            if attacker_position not in choices: raise GameError("That creature cannot be blocked by the False Orders target.")
        self.stack.pop(); prior=list(self.attackers_for(target.uid))
        for attacker_uid in prior:
            before=self.blockers_for(attacker_uid); remaining=[uid for uid in before if uid!=target.uid]
            if remaining: self.blocks[attacker_uid]=remaining[0]
            else: self.blocks.pop(attacker_uid,None)
            if len(remaining)>1: self.additional_blocks[attacker_uid]=remaining[1:]
            else: self.additional_blocks.pop(attacker_uid,None)
            if len(before)==1 and attacker_uid in self.blocked_attackers: self.blocked_attackers.remove(attacker_uid)
            self.attacker_damage_assignments.pop(attacker_uid,None); self.trample_assignments.pop(attacker_uid,None)
        self.blocker_damage_assignments.pop(target.uid,None)
        if attacker_position is not None:
            attacker=choices[attacker_position]; members=self.attack_band(attacker.uid)
            for member_uid in members:
                blockers=self.blockers_for(member_uid)
                self.attacker_damage_assignments.pop(member_uid,None); self.trample_assignments.pop(member_uid,None)
                if target.uid not in blockers: blockers.append(target.uid)
                self.blocks[member_uid]=blockers[0]
                if len(blockers)>1: self.additional_blocks[member_uid]=blockers[1:]
                else: self.additional_blocks.pop(member_uid,None)
                if member_uid not in self.blocked_attackers: self.blocked_attackers.append(member_uid)
            if target.uid not in self.combat_participants: self.combat_participants.append(target.uid)
            active=self.player(self.active_user); defending=self.player(self.opponent(self.active_user)); pending=[]
            for member_uid in members:
                member=self.find_permanent(member_uid)[1]
                if member is None: continue
                for owner,source,other,other_owner in ((active.user_id,member,target,defending.user_id),(defending.user_id,target,member,active.user_id)):
                    source_card=self.card(source.uid)
                    if source_card.combat_destroy_nonwall and not self._has_subtype(self.card(other.uid),"Wall"):
                        uid=self.next_uid; self.next_uid+=1; self.cards[uid]=source_card.key; pending.append(Spell(owner,uid,source_card.key,f"{other_owner}:{other.uid}",ability_effect="end_combat_destroy",source_uid=source.uid,color_override=source.color_override))
            pending.sort(key=lambda trigger:0 if trigger.owner==self.active_user else 1); self.end_combat_destroys.extend(pending)
        self.player(spell.owner).graveyard.append(spell.uid); spell.decision_pending=False; self.log.append(f"{user} resolved False Orders"+(f" and blocked with {self.card(target.uid).name}." if attacker_position is not None else " without a new block."))
        if self.stack: self.stack[-1].passes=0
        self.priority_user=self.stack[-1].choice_owner if self.stack and self.stack[-1].decision_pending else self.active_user; self.phase_passes=0

    def vesuvan_choices(self,trigger=None):
        trigger=trigger or (self.stack[-1] if self.stack else None)
        if trigger is None or trigger.ability_effect!="vesuvan_copy": return []
        _,source=self.find_permanent(trigger.source_uid)
        if source is None: return []
        colors=self.current_colors(source)
        return [(controller.user_id,position,permanent) for controller in self.players.values() for position,permanent in enumerate(controller.battlefield,1) if self.is_creature(permanent) and not self._protected_from(permanent,CARDS["lea:87"],colors)]

    def choose_vesuvan_copy(self,user,controller_id=None,position=None,accept=None):
        if self.finished: raise GameError("Game is over.")
        if not self.stack or not self.stack[-1].decision_pending or self.stack[-1].ability_effect!="vesuvan_copy" or self.stack[-1].choice_owner!=user:
            raise GameError("You do not have a Vesuvan Doppelganger copy choice to make.")
        trigger=self.stack[-1]
        if trigger.choice_value==0:
            try: key=(int(controller_id),int(position))
            except (TypeError,ValueError) as error: raise GameError("Choose a listed creature target.") from error
            choices={(owner,index):permanent for owner,index,permanent in self.vesuvan_choices(trigger)}; target=choices.get(key)
            if target is None: raise GameError("Choose a listed creature target.")
            trigger.target=f"{key[0]}:{target.uid}"; trigger.decision_pending=False; trigger.choice_value=1; trigger.passes=0; self.priority_user=self.active_user; self.phase_passes=0
            self.log.append(f"{user} chose {self.card(target.uid).name} for Vesuvan Doppelganger; players may respond."); return
        if trigger.choice_value!=2 or accept is None: raise GameError("Choose whether Vesuvan Doppelganger becomes the targeted copy.")
        self.stack.pop(); trigger.decision_pending=False
        if accept: self._resolve(trigger)
        else: self.cards.pop(trigger.uid,None); self.log.append(f"{user} kept Vesuvan Doppelganger's current form.")
        if self.stack and self.stack[-1].decision_pending: self.priority_user=self.stack[-1].choice_owner
        elif not self.finished: self.priority_user=self.active_user
        self.phase_passes=0

    def copy_choices(self,spell=None):
        spell=spell or (self.stack[-1] if self.stack else None)
        if spell is None or spell.ability_effect: return []
        card=CARDS.get(spell.key)
        if card is None or not card.enters_copy_types: return []
        return [(controller.user_id,position,permanent) for controller in self.players.values() for position,permanent in enumerate(controller.battlefield,1) if any(self.has_current_type(permanent,kind) for kind in card.enters_copy_types)]

    def choose_copy(self,user,controller_id=None,position=None):
        if self.finished: raise GameError("Game is over.")
        if not self.stack or not self.stack[-1].decision_pending or self.stack[-1].choice_owner!=user or self.stack[-1].ability_effect or not self.card(self.stack[-1].uid).enters_copy_types:
            raise GameError("You do not have a copy choice to make.")
        spell=self.stack[-1]; target=None
        if controller_id is not None or position is not None:
            try: key=(int(controller_id),int(position))
            except (TypeError,ValueError) as error: raise GameError("Choose a listed permanent to copy or choose none.") from error
            choices={(owner,index):permanent for owner,index,permanent in self.copy_choices(spell)}
            target=choices.get(key)
            if target is None: raise GameError("Choose a listed permanent to copy or choose none.")
            spell.target=f"{key[0]}:{target.uid}"
        else: spell.target=""
        spell.decision_pending=False; self.stack.pop(); self._resolve(spell)
        if self.stack: self.stack[-1].passes=0
        self.phase_passes=0
        if not self.finished: self.priority_user=self.active_user

    def choose_library(self,user,position):
        if self.finished: raise GameError("Game is over.")
        if not self.stack or not self.stack[-1].decision_pending or self.stack[-1].owner!=user or self.stack[-1].ability_effect or self.card(self.stack[-1].uid).effect!="search_library":
            raise GameError("You do not have a library search to complete.")
        player=self.player(user); choices=list(reversed(player.library))
        if not 1<=position<=len(choices): raise GameError("Choose a valid private library position.")
        chosen=choices[position-1]; spell=self.stack.pop(); player.library.remove(chosen); player.hand.append(chosen); random.SystemRandom().shuffle(player.library); player.graveyard.append(spell.uid)
        if self.stack: self.stack[-1].passes=0
        self.phase_passes=0; self.priority_user=self.active_user
        self.log.append(f"{user} searched their library with {self.card(spell.uid).name}, put a card into their hand, then shuffled.")

    def natural_selection_decision(self,user):
        if self.finished: raise GameError("Game is over.")
        if not self.stack or not self.stack[-1].decision_pending or self.stack[-1].choice_owner!=user or self.stack[-1].ability_effect or self.card(self.stack[-1].uid).effect!="natural_selection":
            raise GameError("You do not have a Natural Selection choice to make.")
        spell=self.stack[-1]; target=self.player(int(spell.target)); top=list(reversed(target.library[-3:]))
        return spell,target,[(position,self.card(uid)) for position,uid in enumerate(top,1)]

    def choose_natural_selection(self,user,order=None,shuffle=False):
        spell,target,entries=self.natural_selection_decision(user); size=len(entries)
        if bool(shuffle)==(order is not None): raise GameError("Choose either an ordering or shuffle.")
        if shuffle: random.SystemRandom().shuffle(target.library); result=f"{user} had {target.user_id} shuffle their library with Natural Selection."
        else:
            try: order=tuple(int(value) for value in order)
            except (TypeError,ValueError) as error: raise GameError(f"Order must contain each position from 1 to {size} exactly once.") from error
            if len(order)!=size or set(order)!=set(range(1,size+1)): raise GameError(f"Order must contain each position from 1 to {size} exactly once.")
            top=list(reversed(target.library[-size:])); target.library[-size:]=list(reversed([top[position-1] for position in order])); result=f"{user} arranged the top {size} cards of {target.user_id}'s library with Natural Selection."
        self.stack.pop(); self.player(spell.owner).graveyard.append(spell.uid)
        if self.stack: self.stack[-1].passes=0
        self.phase_passes=0
        if not self.finished: self.priority_user=self.active_user
        self.log.append(result)

    def private_hand_decision(self,user):
        if self.finished: raise GameError("Game is over.")
        if not self.stack or not self.stack[-1].decision_pending or self.stack[-1].choice_owner!=user or self.stack[-1].ability_effect not in ("discard_choice","look_hand","balance_hand"):
            raise GameError("You do not have a private hand decision to complete.")
        item=self.stack[-1]; target=self.player(user) if item.ability_effect=="balance_hand" else self.player(int(item.target))
        return item,[(position,self.card(uid)) for position,uid in enumerate(target.hand,1)]

    def choose_private_hand(self,user,position=None):
        item,choices=self.private_hand_decision(user); card=self.card(item.uid)
        if item.ability_effect=="discard_choice":
            try: position=int(position)
            except (TypeError,ValueError) as error: raise GameError("Choose a valid private hand position.") from error
            if not 1<=position<=len(choices): raise GameError("Choose a valid private hand position.")
            target=self.player(int(item.target)); chosen=target.hand.pop(position-1); target.graveyard.append(chosen)
            result=f"{target.user_id} discarded {self.card(chosen).name} for {card.name}."
        else:
            if position is not None: raise GameError("Glasses of Urza only needs confirmation after viewing the hand.")
            result=f"{user} looked at {self.player(int(item.target)).user_id}'s hand with {card.name}."
        self.stack.pop(); self.cards.pop(item.uid,None)
        if self.stack: self.stack[-1].passes=0
        self.phase_passes=0
        if not self.finished: self.priority_user=self.active_user
        self.log.append(result); self._sba(); self._life()

    def _power_sink_target(self,spell):
        if not spell.target or not spell.target.startswith("S:"): return None
        target_uid=int(spell.target.split(":",1)[1])
        return next((item for item in self.stack if item.uid==target_uid and not item.ability_effect),None)

    def _drain_power_lands(self,spell):
        if spell.choice_owner is None: return []
        player=self.player(spell.choice_owner); choices=[]
        for position,permanent in enumerate(player.battlefield,1):
            mana=self.current_mana_choices(permanent)
            if self.card(permanent.uid).land and not permanent.tapped and mana:
                choices.append((position,permanent,tuple(mana)))
        return choices

    def drain_power_choice(self,user):
        if self.finished: raise GameError("Game is over.")
        if not self.stack or not self.stack[-1].decision_pending or self.stack[-1].choice_owner!=user or self.stack[-1].ability_effect or self.card(self.stack[-1].uid).effect!="drain_power":
            raise GameError("You do not have a Drain Power choice to make.")
        spell=self.stack[-1]
        for position,permanent,mana in self._drain_power_lands(spell):
            if str(permanent.uid) not in spell.mana_choices: return position,permanent,mana
        return None

    def choose_drain_power(self,user,position,symbol):
        choice=self.drain_power_choice(user)
        if choice is None: raise GameError("Drain Power has no remaining land choice.")
        expected_position,permanent,mana=choice
        if position!=expected_position or symbol not in mana: raise GameError("Choose a listed mana ability for the next land.")
        spell=self.stack[-1]; spell.mana_choices[str(permanent.uid)]=symbol
        if self.drain_power_choice(user) is not None: return
        self.stack.pop(); self._resolve(spell)
        if self.stack: self.stack[-1].passes=0
        self.phase_passes=0
        if not self.finished: self.priority_user=self.active_user

    def choose_power_sink(self,user,pay):
        if self.finished: raise GameError("Game is over.")
        if not self.stack or not self.stack[-1].decision_pending or self.stack[-1].choice_owner!=user or self.stack[-1].ability_effect or self.card(self.stack[-1].uid).effect!="power_sink":
            raise GameError("You do not have a Power Sink choice to make.")
        spell=self.stack[-1]; target=self._power_sink_target(spell); player=self.player(user); pending=[]
        if target is None:
            self.stack.pop(); self.player(spell.owner).graveyard.append(spell.uid); self.log.append("Power Sink fizzled because its target was gone.")
        elif pay:
            cost=f"{{{spell.x_value}}}"; payment=self._mana_payment(player,self.card(spell.uid),mana_cost=cost)
            if payment is None: raise GameError(f"You cannot pay {cost} for Power Sink.")
            sources,remaining,choices=payment
            for source in sources: self._tap_permanent(user,source,choices[source.uid],pending_triggers=pending)
            player.mana_pool=remaining; self.stack.pop(); self.player(spell.owner).graveyard.append(spell.uid); self.log.append(f"{user} paid {cost} for Power Sink; the targeted spell was not countered.")
        else:
            self.stack.pop(); self.stack.remove(target); self.player(target.owner).graveyard.append(target.uid)
            for permanent in player.battlefield:
                if self.card(permanent.uid).land and self.current_mana_choices(permanent): self._tap_permanent(user,permanent,pending_triggers=pending)
            player.mana_pool.clear(); self.player(spell.owner).graveyard.append(spell.uid); self.log.append(f"{user} declined Power Sink; {self.card(target.uid).name} was countered and their mana was emptied.")
        self.stack.extend(pending)
        if self.stack: self.stack[-1].passes=0
        self.phase_passes=0
        if not self.finished: self.priority_user=self.active_user

    def tomb_cleanup_choices(self,source_uid,source_timestamp):
        choices=[]
        for controller in self.players.values():
            for position,permanent in enumerate(controller.battlefield,1):
                matching=[effect for effect in permanent.land_type_effects if effect.get("kind")=="mire" and int(effect.get("source_uid",0))==int(source_uid) and int(effect.get("source_timestamp",0))==int(source_timestamp)]
                if matching: choices.append((controller.user_id,position,permanent))
        return choices

    def choose_tomb_cleanup(self,user,controller_id,position):
        if self.finished: raise GameError("Game is over.")
        if not self.stack or not self.stack[-1].decision_pending or self.stack[-1].ability_effect!="tomb_cleanup" or self.stack[-1].choice_owner!=user:
            raise GameError("You do not have a Cyclopean Tomb cleanup choice to make.")
        trigger=self.stack[-1]; choices={(owner,index):permanent for owner,index,permanent in self.tomb_cleanup_choices(trigger.source_uid,trigger.choice_value)}
        permanent=choices.get((controller_id,position))
        if permanent is None: raise GameError("Choose a land carrying a mire counter from that Cyclopean Tomb.")
        before=len(permanent.land_type_effects)
        permanent.land_type_effects=[effect for effect in permanent.land_type_effects if not (effect.get("kind")=="mire" and int(effect.get("source_uid",0))==trigger.source_uid and int(effect.get("source_timestamp",0))==trigger.choice_value)]
        removed=before-len(permanent.land_type_effects); self.stack.pop(); self.cards.pop(trigger.uid,None)
        self.log.append(f"{user} removed {removed} mire counter{'s' if removed!=1 else ''} from {self.card(permanent.uid).name}.")
        if self.stack: self.stack[-1].passes=0
        self.phase_passes=0; self.priority_user=self.active_user; self._sba(); self._life()

    def balance_choices(self,spell,user):
        if spell.choice_owner!=user or not spell.ability_effect.startswith("balance_"): return []
        player=self.player(user); stage=spell.ability_effect.split("_",1)[1]
        if stage=="lands": return [(position,permanent) for position,permanent in enumerate(player.battlefield,1) if self.card(permanent.uid).land]
        if stage=="creatures": return [(position,permanent) for position,permanent in enumerate(player.battlefield,1) if self.is_creature(permanent)]
        if stage=="hand": return [(position,self.card(uid)) for position,uid in enumerate(player.hand,1)]
        return []

    def _balance_required(self,spell,user):
        choices=self.balance_choices(spell,user)
        return len(choices)-spell.choice_value if spell.ability_effect=="balance_hand" else spell.choice_value

    @staticmethod
    def _balance_selected(spell,stage,user):
        value=spell.mana_choices.get(f"{stage}:{user}","")
        return {int(uid) for uid in value.split(",") if uid}

    def _apply_balance_stage(self,spell,stage):
        self.stack.pop(); before=len(self.stack)
        if stage=="hand":
            for user in self.order:
                player=self.player(user); selected=self._balance_selected(spell,stage,user); discarded=[uid for uid in player.hand if uid in selected]
                player.hand=[uid for uid in player.hand if uid not in selected]; player.graveyard.extend(discarded)
        else:
            doomed=[]
            for user in self.order:
                player=self.player(user); kept=self._balance_selected(spell,stage,user)
                doomed.extend((player,permanent) for permanent in player.battlefield if (self.card(permanent.uid).land if stage=="lands" else self.is_creature(permanent)) and permanent.uid not in kept)
            trigger_batch=self.next_uid; death_sources=self._death_trigger_sources()
            for player,permanent in doomed: self._remember_source_power(permanent); self._remove_from_combat(permanent.uid); player.battlefield.remove(permanent)
            for player,permanent in doomed: self._dies(player,permanent,trigger_batch,death_sources)
            for player,permanent in doomed:
                if stage=="lands" and permanent.uid in self.permanent_owner(permanent).graveyard: self.stack.extend(self._land_event_triggers(player.user_id,"grave"))
        queued=self.stack[before:]; del self.stack[before:]; self.stack.extend(queued); self.stack.append(spell)

    def _advance_balance(self,spell):
        stage=spell.ability_effect.split("_",1)[1]
        for user in (self.active_user,self.opponent(self.active_user)):
            key=f"{stage}:{user}"
            if key in spell.mana_choices: continue
            spell.choice_owner=user; choices=self.balance_choices(spell,user); required=self._balance_required(spell,user)
            if required in (0,len(choices)):
                chosen=choices if required else []; spell.mana_choices[key]=",".join(str(item.uid if stage!="hand" else self.player(user).hand[position-1]) for position,item in chosen)
                continue
            spell.decision_pending=True; spell.passes=0; self.priority_user=user; return
        spell.decision_pending=False; self._apply_balance_stage(spell,stage)
        if stage=="lands": next_stage="hand"; counts=[len(player.hand) for player in self.players.values()]
        elif stage=="hand": next_stage="creatures"; counts=[sum(self.is_creature(x) for x in player.battlefield) for player in self.players.values()]
        else:
            self.stack.pop(); self.player(spell.owner).graveyard.append(spell.uid); self.log.append("Balance resolved."); self._sba(); self._life()
            if self.stack: self.stack[-1].passes=0
            if not self.finished: self.priority_user=self.active_user
            return
        spell.ability_effect=f"balance_{next_stage}"; spell.mana_choices={}; spell.choice_value=min(counts); self._advance_balance(spell)

    def choose_balance(self,user,positions):
        if self.finished: raise GameError("Game is over.")
        if not self.stack or not self.stack[-1].decision_pending or not self.stack[-1].ability_effect.startswith("balance_") or self.stack[-1].choice_owner!=user:
            raise GameError("You do not have a Balance choice to make.")
        spell=self.stack[-1]; stage=spell.ability_effect.split("_",1)[1]; choices=self.balance_choices(spell,user); indexed={position:item for position,item in choices}
        try: positions=tuple(int(position) for position in positions)
        except (TypeError,ValueError) as error: raise GameError("Choose valid distinct positions for Balance.") from error
        required=self._balance_required(spell,user)
        if len(positions)!=required or len(set(positions))!=required or any(position not in indexed for position in positions): raise GameError(f"Choose exactly {required} distinct {stage} positions for Balance.")
        if stage=="hand": selected=[self.player(user).hand[position-1] for position in positions]
        else: selected=[indexed[position].uid for position in positions]
        spell.mana_choices[f"{stage}:{user}"]=",".join(map(str,selected)); spell.decision_pending=False; self.log.append(f"{user} completed their private Balance choice." if stage=="hand" else f"{user} chose {stage} for Balance."); self._advance_balance(spell)

    def kudzu_choices(self,trigger):
        _,aura=self.find_permanent(trigger.source_uid)
        if aura is None or not self.card(aura.uid).aura_kudzu: return []
        card=self.card(aura.uid); choices=[]
        for controller in self.players.values():
            for position,permanent in enumerate(controller.battlefield,1):
                if self.card(permanent.uid).land and self._aura_can_attach(card,permanent,aura): choices.append((controller.user_id,position,permanent))
        return choices

    def choose_kudzu(self,user,controller_id=None,position=None):
        if self.finished: raise GameError("Game is over.")
        if not self.stack or not self.stack[-1].decision_pending or self.stack[-1].ability_effect!="kudzu_move" or self.stack[-1].choice_owner!=user:
            raise GameError("You do not have a Kudzu attachment choice to make.")
        trigger=self.stack[-1]; _,aura=self.find_permanent(trigger.source_uid)
        if controller_id is None and position is not None: raise GameError("Choose both a controller and battlefield position, or decline.")
        if controller_id is not None:
            choices={(owner,index):permanent for owner,index,permanent in self.kudzu_choices(trigger)}; target=choices.get((controller_id,position))
            if target is None: raise GameError("Choose a land Kudzu can legally enchant.")
            if aura is None: raise GameError("Kudzu is no longer on the battlefield.")
            aura.attached_to=target.uid; result=f" attached Kudzu to {self.card(target.uid).name}."
        else: result=" declined to reattach Kudzu."
        self.stack.pop(); self.cards.pop(trigger.uid,None); self.log.append(f"{user}{result}")
        if self.stack: self.stack[-1].passes=0
        self.phase_passes=0; self.priority_user=self.active_user; self._sba(); self._life()

    def trigger_sacrifice_choices(self,trigger):
        if trigger.ability_effect=="upkeep_sacrifice":
            return [(position,permanent) for position,permanent in enumerate(self.player(trigger.owner).battlefield,1) if permanent.uid!=trigger.source_uid and self.is_creature(permanent)]
        if trigger.ability_effect=="opponent_land_sacrifice":
            return [(position,permanent) for position,permanent in enumerate(self.player(int(trigger.target)).battlefield,1) if self.card(permanent.uid).land]
        return []

    def trigger_cost(self,trigger):
        card=self.card(trigger.uid)
        if not trigger.ability_effect and card.effect=="power_sink": return f"{{{trigger.x_value}}}"
        if trigger.ability_effect in ("cast_draw","graveyard_return","upkeep_vitality","power_leak"): return ""
        if trigger.ability_effect=="upkeep_untap": return card.upkeep_untap_cost
        if trigger.ability_effect=="aura_upkeep_untap": return card.aura_upkeep_untap_cost
        if trigger.ability_effect=="aura_upkeep_life": return card.aura_controller_upkeep_cost
        if trigger.ability_effect=="upkeep_cost": return card.upkeep_cost
        return "{1}"

    def trigger_accept_label(self,trigger):
        if not trigger.ability_effect and self.card(trigger.uid).effect=="power_sink": return f"Pay {{{trigger.x_value}}}"
        if trigger.ability_effect=="cast_draw": return "Draw a card"
        if trigger.ability_effect=="graveyard_return": return "Return to battlefield"
        if trigger.ability_effect=="upkeep_vitality": return "Remove a vitality counter and gain 1 life"
        if trigger.ability_effect=="upkeep_sacrifice": return "Choose a creature"
        if trigger.ability_effect=="opponent_land_sacrifice": return "Choose a land"
        if trigger.ability_effect=="tomb_cleanup": return "Choose a mire-counter land"
        if trigger.ability_effect=="power_leak": return "Choose a mana amount"
        return f"Pay {self.trigger_cost(trigger)}"

    def power_leak_amounts(self,trigger=None,limit=24):
        trigger=trigger or (self.stack[-1] if self.stack else None)
        if trigger is None or trigger.ability_effect!="power_leak" or trigger.choice_owner is None: return []
        player=self.player(trigger.choice_owner); card=self.card(trigger.uid); amounts=[]
        for amount in range(max(0,int(limit))+1):
            if amount and self._mana_payment(player,card,mana_cost=f"{{{amount}}}") is None: break
            amounts.append(amount)
        return amounts

    def choose_power_leak(self,user,amount):
        if self.finished: raise GameError("Game is over.")
        if not self.stack or not self.stack[-1].decision_pending or self.stack[-1].ability_effect!="power_leak" or self.stack[-1].choice_owner!=user:
            raise GameError("You do not have a Power Leak choice to make.")
        try: amount=int(amount)
        except (TypeError,ValueError) as error: raise GameError("Choose a nonnegative mana amount.") from error
        if amount<0: raise GameError("Choose a nonnegative mana amount.")
        trigger=self.stack[-1]; card=self.card(trigger.uid); player=self.player(user); pending=[]
        if amount:
            cost=f"{{{amount}}}"; payment=self._mana_payment(player,card,mana_cost=cost)
            if payment is None: raise GameError(f"You cannot pay {cost} for Power Leak.")
            sources,remaining,choices=payment
            for source in sources: self._tap_permanent(user,source,choices[source.uid],pending_triggers=pending)
            player.mana_pool=remaining
        self.stack.pop(); self.cards.pop(trigger.uid,None)
        damage=max(0,card.aura_power_leak-amount)
        if damage: self._damage_player(user,damage,source_uid=trigger.source_uid)
        self.log.append(f"{user} paid {{{amount}}} for {card.name} and was dealt {damage} damage.")
        self.stack.extend(pending)
        if self.stack: self.stack[-1].passes=0
        self.phase_passes=0; self.priority_user=self.active_user; self._sba(); self._life()

    def choose_trigger(self,user,pay,sacrifice_position=None):
        if self.finished: raise GameError("Game is over.")
        if not self.stack or not self.stack[-1].decision_pending or (self.stack[-1].choice_owner if self.stack[-1].choice_owner is not None else self.stack[-1].owner)!=user:
            raise GameError("You do not have a trigger choice to make.")
        trigger=self.stack[-1]; card=self.card(trigger.uid)
        if trigger.ability_effect=="power_leak": raise GameError("Choose a mana amount for Power Leak.")
        if not trigger.ability_effect and card.effect=="power_sink":
            self.choose_power_sink(user,pay); return
        if trigger.ability_effect in ("upkeep_sacrifice","opponent_land_sacrifice"):
            required="another creature" if trigger.ability_effect=="upkeep_sacrifice" else "a land"
            if not pay: raise GameError(f"{card.name} requires you to sacrifice {required} if able.")
            choices=dict(self.trigger_sacrifice_choices(trigger))
            if sacrifice_position not in choices: raise GameError(f"Choose the battlefield position of {required}.")
            permanent=choices[sacrifice_position]; player=self.player(user if trigger.ability_effect=="upkeep_sacrifice" else int(trigger.target)); sacrificed_name=self.card(permanent.uid).name; was_land=self.card(permanent.uid).land; self.stack.pop()
            self._remember_source_power(permanent); self._remove_from_combat(permanent.uid); player.battlefield.remove(permanent); self._dies(player,permanent)
            if was_land and permanent.uid in player.graveyard: self.stack.extend(self._land_event_triggers(player.user_id,"grave"))
            self.cards.pop(trigger.uid,None)
            if trigger.ability_effect=="upkeep_sacrifice": self.log.append(f"{user} sacrificed {sacrificed_name} for {card.name}.")
            else: self.log.append(f"{user} chose {sacrificed_name} to be sacrificed for {card.name}.")
            if self.stack: self.stack[-1].passes=0
            self.phase_passes=0; self.priority_user=self.active_user; self._sba(); self._life(); return
        trigger=self.stack.pop(); pending=[]; cost=self.trigger_cost(trigger)
        if pay:
            player=self.player(user)
            if cost:
                payment=self._mana_payment(player,card,mana_cost=cost)
                if payment is None:
                    self.stack.append(trigger); raise GameError(f"You cannot pay {cost} for this trigger.")
                sources,remaining,choices=payment
                for source in sources: self._tap_permanent(user,source,choices[source.uid],pending_triggers=pending)
                player.mana_pool=remaining
            if trigger.ability_effect=="cast_draw":
                self._draw(player,1); result=" and drew a card"
            elif trigger.ability_effect=="graveyard_return":
                if not self._graveyard_upkeep_return_eligible(user,trigger.source_uid):
                    self.stack.append(trigger); raise GameError(f"{card.name} no longer has three creature cards above it.")
                player.graveyard.remove(trigger.source_uid); player.battlefield.append(self._make_permanent(trigger.source_uid,card.key,owner=player.user_id)); result=" and returned it to the battlefield"
            elif trigger.ability_effect in ("cast_life","death_life"):
                player.life+=1; result=" and gained 1 life"
            elif trigger.ability_effect=="aura_upkeep_life":
                player.life+=card.aura_controller_upkeep_life; result=f" and gained {card.aura_controller_upkeep_life} life"
            elif trigger.ability_effect=="upkeep_vitality":
                _,source=self.find_permanent(trigger.source_uid)
                if source is not None and source.vitality_counters:
                    source.vitality_counters-=1; player.life+=1; result=" and removed a vitality counter to gain 1 life"
                else: result=" but no vitality counter remained"
            elif trigger.ability_effect=="upkeep_untap":
                _,source=self.find_permanent(trigger.source_uid)
                if source is not None: source.tapped=False
                result=" and untapped it" if source is not None else ""
            elif trigger.ability_effect=="aura_upkeep_untap":
                target=self._stable_target_permanent(trigger.target)
                if target is not None: target.tapped=False
                result=" and untapped the enchanted creature" if target is not None else ""
            else: result=""
            verb=f"paid {cost} for" if cost else "accepted"
            self.log.append(f"{user} {verb} {card.name}{result}.")
        else:
            self.log.append(f"{user} declined {card.name}.")
            if trigger.ability_effect=="upkeep_cost":
                source_controller,source=self.find_permanent(trigger.source_uid)
                if card.upkeep_unpaid_effect=="tap_opponent_land_sacrifice":
                    if source is not None: self._tap_permanent(source_controller.user_id,source,pending_triggers=pending)
                    trigger.owner=self.opponent(user); trigger.target=str(user); trigger.ability_effect="opponent_land_sacrifice"; trigger.decision_pending=True; trigger.passes=0
                    if self.trigger_sacrifice_choices(trigger):
                        self.stack.extend(pending); self.stack.append(trigger); self.phase_passes=0; self.priority_user=trigger.owner; return
                elif card.upkeep_unpaid_effect=="sacrifice" and source is not None:
                    self._remember_source_power(source); self._remove_from_combat(source.uid); source_controller.battlefield.remove(source); self._dies(source_controller,source)
                elif card.upkeep_unpaid_effect=="damage": self._damage_player(user,card.upkeep_unpaid_damage,source_uid=trigger.source_uid)
        self.cards.pop(trigger.uid,None); self.stack.extend(pending)
        if self.stack: self.stack[-1].passes=0
        self.phase_passes=0; self.priority_user=self.active_user; self._sba(); self._life()

    def _advance(self):
        if self.phase=="upkeep": self._begin_draw_step(); return
        if self.phase=="draw": self.phase="precombat_main"; self.priority_user=self.active_user; return
        if self.phase=="precombat_main": self.phase="attackers"; self.priority_user=None; return
        elif self.phase=="after_attackers": self.phase="blockers"; self.priority_user=None; return
        elif self.phase=="after_blockers":
            if self._combat_has_first_strike():
                missing=self._missing_attacker_damage_assignments(True)+self._missing_blocker_damage_assignments(True)
                if missing: raise GameError("Assign combat damage for "+", ".join(self.card(item.uid).name for item in missing)+" before passing.")
                self._combat_damage(first_strike=True)
                if self.finished: return
                self.phase="after_first_strike"
            else:
                missing=self._missing_attacker_damage_assignments(False)+self._missing_blocker_damage_assignments(False)
                if missing: raise GameError("Assign combat damage for "+", ".join(self.card(item.uid).name for item in missing)+" before passing.")
                self._combat_damage(first_strike=False)
                if self.finished: return
                if self.stack: self.phase="after_combat_damage"
                else:
                    has_end_triggers=self._end_combat(); self.phase="end_combat" if has_end_triggers else "postcombat_main"
        elif self.phase=="after_first_strike":
            missing=self._missing_attacker_damage_assignments(False)+self._missing_blocker_damage_assignments(False)
            if missing: raise GameError("Assign combat damage for "+", ".join(self.card(item.uid).name for item in missing)+" before passing.")
            self._combat_damage(first_strike=False)
            if self.finished: return
            if self.stack: self.phase="after_combat_damage"
            else:
                has_end_triggers=self._end_combat(); self.phase="end_combat" if has_end_triggers else "postcombat_main"
        elif self.phase=="after_combat_damage":
            has_end_triggers=self._end_combat(); self.phase="end_combat" if has_end_triggers else "postcombat_main"
        elif self.phase=="end_combat": self.phase="postcombat_main"
        elif self.phase=="postcombat_main": self.phase="ending"; self._begin_end_step()
        elif self.phase=="ending":
            next_extra=bool(self.extra_turns); next_user=self.extra_turns.pop(0) if next_extra else self.opponent(self.active_user)
            self._offer_turn_start(next_user,next_extra); return
        else: raise GameError("Complete combat first.")
        self.priority_user=self.active_user

    def declare_attackers(self,user,positions,bands=()):
        self.player(user)
        if self.finished: raise GameError("Game is over.")
        if self.phase!="attackers" or user!=self.active_user: raise GameError("Not your attack declaration.")
        p=self.player(user); chosen=[]
        for pos in positions:
            if not 1<=pos<=len(p.battlefield): raise GameError("Bad attacker position.")
            x=p.battlefield[pos-1]; c=self.card(x.uid)
            if not self.can_attack_permanent(x): raise GameError(f"{c.name} cannot attack.")
            if x.uid in chosen: raise GameError("Duplicate attacker.")
            chosen.append(x.uid)
        formed=[]
        for band_positions in bands:
            try: members=[p.battlefield[int(pos)-1] for pos in band_positions]
            except (TypeError,ValueError,IndexError): raise GameError("Bad band position.")
            member_uids=[x.uid for x in members]
            if len(member_uids)<2 or len(set(member_uids))!=len(member_uids) or any(uid not in chosen for uid in member_uids): raise GameError("Each band must contain at least two distinct declared attackers.")
            if sum("banding" not in self.current_keywords(x) for x in members)>1: raise GameError("An attacking band may contain at most one creature without banding.")
            if any(uid in prior for prior in formed for uid in member_uids): raise GameError("An attacker can belong to only one band.")
            formed.append(member_uids)
        required=[x for x in p.battlefield if (self.card(x.uid).attacks_each_combat or x.uid in self.forced_attackers) and self.can_attack_permanent(x) and x.uid not in chosen]
        if required: raise GameError(", ".join(self.card(x.uid).name for x in required)+" must attack this combat if able.")
        self.attackers=chosen; self.attack_bands=formed; self.attacked_this_turn.extend(uid for uid in chosen if uid not in self.attacked_this_turn); self.blocks={}; self.additional_blocks={}; self.blocked_attackers=[]; self.combat_participants=list(chosen); self.trample_assignments={}; self.attacker_damage_assignments={}; self.phase_passes=0; self.blocker_damage_assignments={}
        for x in list(p.battlefield):
            if x.uid in chosen and "vigilance" not in self.current_keywords(x): x.tapped=True
        self._sba(); self._life()
        self.phase="after_attackers" if self.attackers else "postcombat_main"
        self.priority_user=user

    def blockers_for(self,attacker_uid):
        primary=self.blocks.get(attacker_uid); return ([primary] if primary is not None else [])+list(self.additional_blocks.get(attacker_uid,[]))

    def attackers_for(self,blocker_uid):
        return [uid for uid in self.attackers if blocker_uid in self.blockers_for(uid)]

    def all_blocker_uids(self):
        return set(self.blocks.values())|{blocker for blockers in self.additional_blocks.values() for blocker in blockers}

    def attack_band(self,attacker_uid):
        return next((band for band in self.attack_bands if attacker_uid in band),[attacker_uid])

    def attack_groups(self):
        grouped={uid for band in self.attack_bands for uid in band}
        return list(self.attack_bands)+[[uid] for uid in self.attackers if uid not in grouped]

    def _add_block(self,proposed,attacker_uid,blocker_uid):
        for member_uid in self.attack_band(attacker_uid):
            if blocker_uid not in proposed.setdefault(member_uid,[]): proposed[member_uid].append(blocker_uid)

    def declare_blockers(self,user,assignments):
        if self.phase!="blockers" or user!=self.opponent(self.active_user): raise GameError("You cannot block now.")
        p=self.player(user); counts={}; proposed={}; pairs=list(assignments.items()) if hasattr(assignments,"items") else list(assignments)
        for a,b in pairs:
            if not 1<=a<=len(self.attackers) or not 1<=b<=len(p.battlefield): raise GameError("Bad combat position.")
            blocker=p.battlefield[b-1]; attacker_uid=self.attackers[a-1]; capacity=blocker.temporary_max_blocks or self.card(blocker.uid).max_blocks
            if blocker.uid in proposed.get(attacker_uid,[]) or counts.get(blocker.uid,0)>=capacity: raise GameError("Invalid blocker.")
            results=[self.can_block(uid,blocker.uid) for uid in self.attack_band(attacker_uid)]
            legal=any(result[0] for result in results); reason=next((result[1] for result in results if not result[0]),"That band cannot be blocked.")
            if not legal: raise GameError(reason)
            counts[blocker.uid]=counts.get(blocker.uid,0)+1; self._add_block(proposed,attacker_uid,blocker.uid)
        for blocker in p.battlefield:
            if not blocker.must_block_all: continue
            legal={tuple(group) for group in self.attack_groups() if any(self.can_block(uid,blocker.uid)[0] for uid in group)}; assigned={tuple(group) for group in self.attack_groups() if any(blocker.uid in proposed.get(uid,[]) for uid in group)}
            if legal!=assigned: raise GameError(f"{self.card(blocker.uid).name} must block every attacking creature it can block.")
        lured=[group for group in self.attack_groups() if any(any(self.card(aura.uid).aura_lure for aura in self.attached_auras(self.find_permanent(uid)[1])) for uid in group)]
        if lured:
            maximum=0; actual=0
            for blocker in p.battlefield:
                if not self.is_creature(blocker) or blocker.tapped: continue
                legal=[group for group in lured if any(self.can_block(uid,blocker.uid)[0] for uid in group)]; capacity=blocker.temporary_max_blocks or self.card(blocker.uid).max_blocks
                maximum+=min(capacity,len(legal)); actual+=sum(any(blocker.uid in proposed.get(uid,[]) for uid in group) for group in legal)
            if actual!=maximum: raise GameError("All creatures able to block a creature enchanted by Lure must do so.")
        self.blocks={uid:blockers[0] for uid,blockers in proposed.items() if blockers}; self.additional_blocks={uid:blockers[1:] for uid,blockers in proposed.items() if len(blockers)>1}; self.blocked_attackers=list(proposed); self.attacker_damage_assignments={}; self.blocker_damage_assignments={}
        self.combat_participants.extend(uid for blockers in proposed.values() for uid in blockers if uid not in self.combat_participants)
        self._schedule_combat_destroy_triggers(); self.phase="after_blockers"; self.phase_passes=0; self.priority_user=self.active_user

    def _schedule_combat_destroy_triggers(self):
        active=self.player(self.active_user); defending=self.player(self.opponent(self.active_user)); pending=[]
        for attacker_uid in self.attackers:
            for blocker_uid in self.blockers_for(attacker_uid):
                attacker=next((x for x in active.battlefield if x.uid==attacker_uid),None)
                blocker=next((x for x in defending.battlefield if x.uid==blocker_uid),None)
                if attacker is None or blocker is None: continue
                for owner,source,target,target_owner in ((active.user_id,attacker,blocker,defending.user_id),(defending.user_id,blocker,attacker,active.user_id)):
                    source_card=self.card(source.uid); target_card=self.card(target.uid)
                    if source_card.combat_destroy_nonwall and not self._has_subtype(target_card,"Wall"):
                        uid=self.next_uid; self.next_uid+=1; self.cards[uid]=source_card.key
                        pending.append(Spell(owner,uid,source_card.key,f"{target_owner}:{target.uid}",ability_effect="end_combat_destroy",source_uid=source.uid,color_override=source.color_override))
        pending.sort(key=lambda trigger:0 if trigger.owner==self.active_user else 1)
        self.end_combat_destroys.extend(pending)

    def choose_bodyguard(self,user,position):
        self._priority(user)
        if user!=self.opponent(self.active_user) or self.phase not in ("after_blockers","after_first_strike"): raise GameError("Choose a Veteran Bodyguard after blockers while defending.")
        battlefield=self.player(user).battlefield
        if not 1<=position<=len(battlefield): raise GameError("No permanent at that battlefield position.")
        permanent=battlefield[position-1]
        if not self.card(permanent.uid).redirects_unblocked_combat_damage or permanent.tapped: raise GameError("Choose an untapped Veteran Bodyguard.")
        self.player(user).bodyguard_choice=permanent.uid; self.phase_passes=0
        for item in self.stack: item.passes=0
        self.log.append(f"{user} chose {self.card(permanent.uid).name} for unblocked combat damage.")

    def _bodyguard_for(self,user):
        player=self.player(user); choices=[x for x in player.battlefield if self.card(x.uid).redirects_unblocked_combat_damage and not x.tapped]
        return next((x for x in choices if x.uid==player.bodyguard_choice),choices[0] if choices else None)

    def assign_trample(self,user,position,damage_to_blocker):
        self._priority(user)
        if user!=self.active_user or self.phase not in ("after_blockers","after_first_strike") or self.stack:
            raise GameError("Set trample assignment after blockers with an empty stack.")
        battlefield=self.player(user).battlefield
        if not 1<=position<=len(battlefield): raise GameError("No permanent at that battlefield position.")
        attacker=battlefield[position-1]
        if attacker.uid not in self.attackers or "trample" not in self.current_keywords(attacker): raise GameError("Choose an attacking creature with trample.")
        blocker_uids=[uid for uid in self.blockers_for(attacker.uid) if self.find_permanent(uid)[1] is not None]
        if len(blocker_uids)!=1: raise GameError("Use attacker damage assignment when an attacker has multiple blockers.")
        blocker_uid=blocker_uids[0]; _,blocker=self.find_permanent(blocker_uid)
        power=max(0,self.current_stats(attacker)[0]); lethal=max(0,self.current_stats(blocker)[1]-blocker.damage)
        try: damage_to_blocker=int(damage_to_blocker)
        except (TypeError,ValueError) as e: raise GameError("Damage to blocker must be a whole number.") from e
        minimum=min(power,lethal)
        if not minimum<=damage_to_blocker<=power: raise GameError(f"Assign between {minimum} and {power} damage to the blocker.")
        self.trample_assignments[attacker.uid]=damage_to_blocker; self.phase_passes=0
        self.log.append(f"{user} assigned {damage_to_blocker} damage from {self.card(attacker.uid).name} to its blocker.")

    def attacker_damage_owner(self,attacker_uid):
        return self.opponent(self.active_user) if any("banding" in self.current_keywords(self.find_permanent(uid)[1]) for uid in self.blockers_for(attacker_uid) if self.find_permanent(uid)[1] is not None) else self.active_user

    def blocker_damage_owner(self,blocker_uid):
        return self.active_user if any("banding" in self.current_keywords(self.find_permanent(uid)[1]) for uid in self.attackers_for(blocker_uid) if self.find_permanent(uid)[1] is not None) else self.opponent(self.active_user)

    def assign_attacker_damage(self,user,attacker_position,assignments):
        self._priority(user)
        if user not in self.order or self.phase not in ("after_blockers","after_first_strike") or self.stack: raise GameError("Assign attacker damage after blockers with an empty stack.")
        battlefield=self.player(self.active_user).battlefield
        if not 1<=attacker_position<=len(battlefield): raise GameError("No permanent at that battlefield position.")
        attacker=battlefield[attacker_position-1]; blocker_uids=[uid for uid in self.blockers_for(attacker.uid) if self.find_permanent(uid)[1] is not None]
        if attacker.uid not in self.attackers or len(blocker_uids)<2 or user!=self.attacker_damage_owner(attacker.uid): raise GameError("Choose an attacking creature blocked by multiple creatures.")
        first_step=self._combat_has_first_strike() and self.phase=="after_blockers"; strikes=("first_strike" in self.current_keywords(attacker))==first_step
        if not strikes: raise GameError("That attacker does not assign damage in the next combat damage step.")
        parsed=[]
        try:
            defending=self.player(self.opponent(self.active_user))
            for blocker_position,amount in assignments:
                blocker_position=int(blocker_position); amount=int(amount)
                if not 1<=blocker_position<=len(defending.battlefield) or amount<0: raise ValueError
                parsed.append((defending.battlefield[blocker_position-1].uid,amount))
        except (TypeError,ValueError) as error: raise GameError("Use BLOCKER_POSITION:DAMAGE with nonnegative whole numbers.") from error
        if len(parsed)!=len(blocker_uids) or {uid for uid,_ in parsed}!=set(blocker_uids): raise GameError("Assign damage to each creature blocking this attacker exactly once.")
        power=max(0,self.current_stats(attacker)[0]); total=sum(amount for _,amount in parsed); trample="trample" in self.current_keywords(attacker)
        if (not trample and total!=power) or (trample and total>power):
            wording="at most" if trample else "exactly"; raise GameError(f"Assign {wording} {power} total damage to blockers.")
        remaining=power
        for index,(uid,amount) in enumerate(parsed):
            blocker=self.find_permanent(uid)[1]; lethal=max(0,self.current_stats(blocker)[1]-blocker.damage); later=any(value for _,value in parsed[index+1:]) or (trample and total<power)
            if user==self.active_user and later and amount<min(remaining,lethal): raise GameError("Assign lethal damage to each earlier blocker before assigning damage to the next target.")
            remaining-=amount
        self.attacker_damage_assignments[attacker.uid]=[{"blocker":uid,"damage":amount} for uid,amount in parsed]; self.phase_passes=0
        self.log.append(f"{user} assigned combat damage from {self.card(attacker.uid).name} among {len(parsed)} blockers.")

    def _missing_attacker_damage_assignments(self,first_strike):
        missing=[]
        for attacker_uid in self.attackers:
            attacker=self.find_permanent(attacker_uid)[1]; blockers=[uid for uid in self.blockers_for(attacker_uid) if self.find_permanent(uid)[1] is not None]
            if attacker is None or len(blockers)<2 or max(0,self.current_stats(attacker)[0])==0 or (("first_strike" in self.current_keywords(attacker))!=first_strike): continue
            assigned=self.attacker_damage_assignments.get(attacker_uid,[]); total=sum(item.get("damage",0) for item in assigned); power=max(0,self.current_stats(attacker)[0]); trample="trample" in self.current_keywords(attacker)
            if {item.get("blocker") for item in assigned}!=set(blockers) or (total!=power and not (trample and total<=power)): missing.append(attacker)
        return missing

    def assign_blocker_damage(self,user,blocker_position,assignments):
        self._priority(user)
        if user not in self.order or self.phase not in ("after_blockers","after_first_strike") or self.stack: raise GameError("Assign blocker damage after blockers with an empty stack.")
        battlefield=self.player(self.opponent(self.active_user)).battlefield
        if not 1<=blocker_position<=len(battlefield): raise GameError("No permanent at that battlefield position.")
        blocker=battlefield[blocker_position-1]; blocked=[uid for uid in self.attackers_for(blocker.uid) if self.find_permanent(uid)[1] is not None]
        if len(blocked)<2 or user!=self.blocker_damage_owner(blocker.uid): raise GameError("Choose a creature whose combat damage you control and that blocks multiple attackers.")
        first_step=self._combat_has_first_strike() and self.phase=="after_blockers"; strikes=("first_strike" in self.current_keywords(blocker))==first_step
        if not strikes: raise GameError("That blocker does not assign damage in the next combat damage step.")
        parsed=[]
        try:
            for attacker_position,amount in assignments:
                attacker_position=int(attacker_position); amount=int(amount)
                if not 1<=attacker_position<=len(self.attackers) or amount<0: raise ValueError
                parsed.append((self.attackers[attacker_position-1],amount))
        except (TypeError,ValueError) as error: raise GameError("Use ATTACKER_POSITION:DAMAGE with nonnegative whole numbers.") from error
        if len(parsed)!=len(blocked) or {uid for uid,_ in parsed}!=set(blocked): raise GameError("Assign damage to each creature this blocker is blocking exactly once.")
        power=max(0,self.current_stats(blocker)[0])
        if sum(amount for _,amount in parsed)!=power: raise GameError(f"Assign exactly {power} total damage.")
        remaining=power
        for uid,amount in parsed[:-1]:
            attacker=self.find_permanent(uid)[1]; lethal=max(0,self.current_stats(attacker)[1]-attacker.damage); minimum=min(remaining,lethal)
            if user==self.opponent(self.active_user) and amount<minimum: raise GameError("Assign lethal damage to each earlier creature before assigning damage to the next.")
            remaining-=amount
        self.blocker_damage_assignments[blocker.uid]=[{"attacker":uid,"damage":amount} for uid,amount in parsed]; self.phase_passes=0
        self.log.append(f"{user} assigned combat damage from {self.card(blocker.uid).name} among {len(parsed)} attackers.")

    def _missing_blocker_damage_assignments(self,first_strike):
        missing=[]
        for blocker_uid in self.all_blocker_uids():
            blocker=self.find_permanent(blocker_uid)[1]; blocked=[uid for uid in self.attackers_for(blocker_uid) if self.find_permanent(uid)[1] is not None]
            if blocker is None or len(blocked)<2 or max(0,self.current_stats(blocker)[0])==0 or (("first_strike" in self.current_keywords(blocker))!=first_strike): continue
            assigned=self.blocker_damage_assignments.get(blocker_uid,[])
            if {item.get("attacker") for item in assigned}!=set(blocked) or sum(item.get("damage",0) for item in assigned)!=max(0,self.current_stats(blocker)[0]): missing.append(blocker)
        return missing

    def can_block(self,attacker_uid,blocker_uid):
        defender=self.players[self.opponent(self.active_user)]
        attacker=self.card(attacker_uid)
        attacker_perm=next(x for x in self.players[self.active_user].battlefield if x.uid==attacker_uid)
        blocker_perm=next((x for x in defender.battlefield if x.uid==blocker_uid),None)
        if blocker_perm is None: return False,"That blocker is no longer on the battlefield."
        blocker=self.card(blocker_uid)
        if not self.is_creature(blocker_perm) or blocker_perm.tapped: return False,"Invalid blocker."
        attacker_keywords=self.current_keywords(attacker_perm); blocker_keywords=self.current_keywords(blocker_perm)
        if "unblockable" in attacker_keywords: return False,f"{attacker.name} can't be blocked this turn."
        fear_sources=[attacker_perm.uid] if "fear" in self.card(attacker_perm.uid).keywords else []
        fear_sources.extend(aura.uid for aura in self.attached_auras(attacker_perm) if self.card(aura.uid).aura_keyword=="fear")
        fear_colors={self.changed_color_word(uid,"B") for uid in fear_sources}
        if "fear" in attacker_keywords and not (blocker.has_type("Artifact") or fear_colors & set(self.current_colors(blocker_perm))):
            return False,f"{blocker.name} cannot block a creature with fear."
        if set(self.current_colors(blocker_perm)) & self.current_protections(attacker_perm): return False,f"{attacker.name} has protection from {blocker.name}."
        if attacker.cant_be_blocked_by_subtype and self._has_subtype(blocker,attacker.cant_be_blocked_by_subtype):
            return False,f"{attacker.name} can't be blocked by {attacker.cant_be_blocked_by_subtype}s."
        if any(self.card(aura.uid).aura_blocked_except_wall for aura in self.attached_auras(attacker_perm)) and not self._has_subtype(blocker,"Wall"):
            return False,f"{attacker.name} can only be blocked by Walls."
        if "flying" in attacker_keywords and not ({"flying","reach"} & blocker_keywords):
            return False,f"{blocker.name} cannot block a creature with flying."
        for land_type in ("plains","island","swamp","mountain","forest"):
            if f"{land_type}walk" in attacker_keywords and any(self.has_current_land_type(x,land_type) for x in defender.battlefield):
                return False,f"{attacker.name} can't be blocked while the defender controls a {land_type.title()}."
        power=self.current_stats(attacker_perm)[0]
        if blocker.max_block_power is not None and power>blocker.max_block_power:
            return False,f"{blocker.name} can't block a creature with power {power}."
        return True,""

    def _combat_has_first_strike(self):
        combatants=set(self.attackers)|self.all_blocker_uids()
        return any(
            "first_strike" in self.current_keywords(x)
            for player in self.players.values() for x in player.battlefield if x.uid in combatants
        )

    def _damage_permanent(self,permanent,amount,source=None,colors=None,trigger_batch=None,source_uid=None):
        amount=max(0,int(amount))
        if source is not None and self._protected_from(permanent,source,colors): return 0
        hydra=self.card(permanent.uid).hydra_damage_replacement
        if hydra and permanent.hydra_counters_first:
            prevented=min(amount,permanent.plus_one_counters); permanent.plus_one_counters-=prevented; amount-=prevented
        if amount and source_uid is not None and source_uid in permanent.redirect_source_damage_to_player:
            recipient=permanent.redirect_source_damage_to_player.pop(source_uid); source_controller,source_permanent=self.find_permanent(source_uid)
            self._damage_player(recipient,amount,source_permanent,source_controller.user_id if source_controller is not None else None,source_uid); return 0
        redirected=min(amount,permanent.redirect_damage_to_owner)
        if redirected:
            permanent.redirect_damage_to_owner-=redirected; amount-=redirected; source_controller,source_permanent=self.find_permanent(source_uid) if source_uid is not None else (None,None)
            self._damage_player(self.permanent_owner(permanent).user_id,redirected,source_permanent,source_controller.user_id if source_controller is not None else None,source_uid)
        prevented=min(amount,permanent.damage_prevention); permanent.damage_prevention-=prevented; dealt=amount-prevented
        if hydra and not permanent.hydra_counters_first:
            counter_prevented=min(dealt,permanent.plus_one_counters); permanent.plus_one_counters-=counter_prevented; dealt-=counter_prevented
        permanent.damage+=dealt
        if dealt and source_uid is not None and source_uid not in permanent.damage_source_uids: permanent.damage_source_uids.append(source_uid)
        if dealt and self.card(permanent.uid).dealt_damage_plus_counter:
            controller,_=self.find_permanent(permanent.uid); uid=self.next_uid; self.next_uid+=1; self.cards[uid]=self.card(permanent.uid).key; batch=self.next_uid if trigger_batch is None else trigger_batch
            self.stack.append(Spell(controller.user_id,uid,self.card(permanent.uid).key,f"{controller.user_id}:{permanent.uid}",ability_effect="dealt_damage_counter",source_uid=permanent.uid,color_override=permanent.color_override,batch_id=batch))
            start=len(self.stack)-1
            while start and self.stack[start-1].ability_effect=="dealt_damage_counter" and self.stack[start-1].batch_id==batch: start-=1
            self.stack[start:]=sorted(self.stack[start:],key=lambda trigger:0 if trigger.owner==self.active_user else 1)
        return dealt

    def _discard_random(self,player,amount):
        chosen=random.SystemRandom().sample(player.hand,min(max(0,int(amount)),len(player.hand)))
        for uid in chosen: player.hand.remove(uid); player.graveyard.append(uid)
        return chosen

    def _damage_player(self,user,amount,source=None,source_controller=None,source_uid=None,combat=False):
        player=self.player(user); amount=max(0,int(amount)); identity=source_uid if source_uid is not None else getattr(source,"uid",None)
        if amount and identity in player.source_damage_lifegain:
            player.source_damage_lifegain.remove(identity); player.life+=amount; return 0
        if combat and amount and identity in player.source_damage_caps:
            cap=player.source_damage_caps.pop(identity); amount=min(amount,cap)
        if amount and identity in player.source_damage_prevention:
            player.source_damage_prevention.remove(identity); dealt=0
        else:
            prevented=min(amount,player.damage_prevention); player.damage_prevention-=prevented; dealt=amount-prevented; player.life-=dealt
        trigger_batch=self.next_uid if dealt else 0
        if dealt:
            player.damage_taken_this_turn+=dealt
        if dealt and source is not None and source_controller is not None and user==self.opponent(source_controller):
            source_card=self.card(source.uid)
            if source_card.opponent_damage_discard_random:
                uid=self.next_uid; self.next_uid+=1; self.cards[uid]=source_card.key
                self.stack.append(Spell(source_controller,uid,source_card.key,str(user),ability_effect="opponent_damage_discard_random",source_uid=source.uid,color_override=source.color_override,batch_id=trigger_batch))
        if dealt:
            for aura in self.player(user).battlefield:
                aura_card=self.card(aura.uid)
                if not aura_card.aura_damage_vitality: continue
                uid=self.next_uid; self.next_uid+=1; self.cards[uid]=aura_card.key
                self.stack.append(Spell(user,uid,aura_card.key,f"{user}:{aura.uid}",ability_effect="vitality_counter",source_uid=aura.uid,color_override=aura.color_override,choice_value=dealt,batch_id=trigger_batch))
            effects={"opponent_damage_discard_random","vitality_counter"}; start=len(self.stack)
            while start and self.stack[start-1].ability_effect in effects and self.stack[start-1].batch_id==trigger_batch: start-=1
            self.stack[start:]=sorted(self.stack[start:],key=lambda trigger:trigger.owner!=self.active_user)
        return dealt

    def _combat_damage(self,first_strike):
        atk=self.players[self.active_user]; dfn=self.players[self.opponent(self.active_user)]
        if self.prevent_combat_damage:
            self.log.append("Combat damage was prevented."); self._sba(); self._life(); return
        damage_batch=self.next_uid
        for uid in self.attackers:
            a=next((x for x in atk.battlefield if x.uid==uid),None)
            if a is None: continue
            blockers=[next((x for x in dfn.battlefield if x.uid==blocker_uid),None) for blocker_uid in self.blockers_for(uid)]
            blockers=[blocker for blocker in blockers if blocker is not None]
            attacker_strikes=("first_strike" in self.current_keywords(a))==first_strike
            if attacker_strikes:
                power=max(0,self.current_stats(a)[0]); trample="trample" in self.current_keywords(a)
                if not blockers:
                    if uid not in self.blocked_attackers or trample:
                        bodyguard=self._bodyguard_for(dfn.user_id) if uid not in self.blocked_attackers else None
                        if bodyguard is not None: self._damage_permanent(bodyguard,power,self.card(a.uid),self.current_colors(a),damage_batch,a.uid)
                        else: self._damage_player(dfn.user_id,power,a,atk.user_id,combat=True)
                elif len(blockers)==1:
                    blocker=blockers[0]; lethal=max(0,self.current_stats(blocker)[1]-blocker.damage); chosen=self.trample_assignments.get(uid,lethal); assigned=min(power,max(lethal,chosen)) if trample else power
                    self._damage_permanent(blocker,assigned,self.card(a.uid),self.current_colors(a),damage_batch,a.uid)
                    if trample: self._damage_player(dfn.user_id,max(0,power-assigned),a,atk.user_id,combat=True)
                else:
                    assignment={item.get("blocker"):item.get("damage",0) for item in self.attacker_damage_assignments.get(uid,[])}
                    for blocker in blockers: self._damage_permanent(blocker,assignment.get(blocker.uid,0),self.card(a.uid),self.current_colors(a),damage_batch,a.uid)
                    if trample: self._damage_player(dfn.user_id,max(0,power-sum(assignment.values())),a,atk.user_id,combat=True)
            for blocker in blockers:
                blocker_strikes=("first_strike" in self.current_keywords(blocker))==first_strike
                if blocker_strikes:
                    assigned=next((item.get("damage",0) for item in self.blocker_damage_assignments.get(blocker.uid,[]) if item.get("attacker")==a.uid),max(0,self.current_stats(blocker)[0]))
                    self._damage_permanent(a,assigned,self.card(blocker.uid),self.current_colors(blocker),damage_batch,blocker.uid)
        self.attacker_damage_assignments={}; self.blocker_damage_assignments={}
        self._sba(); self._life()

    def _end_combat(self):
        pending=self.end_combat_destroys; self.end_combat_destroys=[]
        participants=set(self.combat_participants)
        for controller_id in (self.active_user,self.opponent(self.active_user)):
            for permanent in self.player(controller_id).battlefield:
                card=self.card(permanent.uid)
                if permanent.uid in participants and card.end_combat_remove_power_counter:
                    uid=self.next_uid; self.next_uid+=1; self.cards[uid]=card.key
                    pending.append(Spell(controller_id,uid,card.key,f"{controller_id}:{permanent.uid}",ability_effect="end_combat_remove_power_counter",source_uid=permanent.uid,color_override=permanent.color_override))
        pending.sort(key=lambda trigger:0 if trigger.owner==self.active_user else 1)
        self.attackers=[]; self.attack_bands=[]; self.blocks={}; self.additional_blocks={}; self.blocked_attackers=[]; self.combat_participants=[]; self.trample_assignments={}; self.attacker_damage_assignments={}; self.blocker_damage_assignments={}
        for player in self.players.values(): player.bodyguard_choice=0
        for player in self.players.values():
            for permanent in player.battlefield: permanent.animated_until_end_combat=False
        self._sba(); self.stack.extend(pending)
        return bool(pending)

    def _death_trigger_sources(self):
        return [
            (controller.user_id,source.uid,self.card(source.uid).key,source.color_override,self.card(source.uid).death_life,self.card(source.uid).damaged_creature_death_counter,source.attached_to if self.card(source.uid).aura_death_toughness_damage else None)
            for controller in self.players.values() for source in controller.battlefield
            if self.card(source.uid).death_life or self.card(source.uid).damaged_creature_death_counter or self.card(source.uid).aura_death_toughness_damage
        ]

    def _death_triggers(self,dead,batch_id,sources=None,dead_controller=None):
        triggers=[]
        by_owner={owner:[] for owner in self.order}
        for owner,source_uid,key,color_override,death_life,damage_counter,bond_target in (sources if sources is not None else self._death_trigger_sources()):
            effects=[]
            if death_life: effects.append(("death_life",str(dead.uid)))
            if damage_counter and source_uid in dead.damage_source_uids: effects.append(("damaged_creature_death_counter",f"{owner}:{source_uid}",0))
            if bond_target==dead.uid: effects.append(("creature_bond_damage",str(dead_controller.user_id),max(0,dead.last_known_toughness)))
            effects=[item if len(item)==3 else (item[0],item[1],0) for item in effects]
            for effect,target,amount in effects:
                uid=self.next_uid; self.next_uid+=1; self.cards[uid]=key
                by_owner[owner].append(Spell(owner,uid,key,target,ability_effect=effect,source_uid=source_uid,color_override=color_override,batch_id=batch_id,choice_value=amount))
        for owner in (self.active_user,self.opponent(self.active_user)): triggers.extend(by_owner.get(owner,()))
        trigger_effects=("death_life","damaged_creature_death_counter","creature_bond_damage")
        start=len(self.stack)
        while start and self.stack[start-1].ability_effect in trigger_effects and self.stack[start-1].batch_id==batch_id: start-=1
        merged=self.stack[start:]+triggers
        merged.sort(key=lambda trigger:0 if trigger.owner==self.active_user else 1)
        self.stack[start:]=merged

    def _dies(self,controller,permanent,batch_id=None,death_sources=None):
        dies=not permanent.exile_on_death
        token=self.is_token(permanent.uid); owner=self.permanent_owner(permanent,controller)
        if not token: (owner.graveyard if dies else owner.exile).append(permanent.uid)
        if dies and self.card(permanent.uid).key=="lea:240":
            marker={"owner":controller.user_id,"source_uid":permanent.uid,"source_timestamp":permanent.layer_timestamp}
            if marker not in self.tomb_cleanup_sources: self.tomb_cleanup_sources.append(marker)
        if dies and self.card(permanent.uid).death_owner_half_life:
            owner.life-=max(0,(owner.life+1)//2)
        if dies and self.card(permanent.uid).aura_reanimate and permanent.attached_to is not None:
            uid=self.next_uid; self.next_uid+=1; self.cards[uid]=self.card(permanent.uid).key
            self.stack.append(Spell(controller.user_id,uid,self.card(permanent.uid).key,str(permanent.attached_to),ability_effect="animate_dead_sacrifice",source_uid=permanent.uid,color_override=permanent.color_override))
        if dies and self.is_creature(permanent):
            self.creatures_died_this_turn+=1
            self._death_triggers(permanent,self.next_uid if batch_id is None else batch_id,death_sources,controller)
        if token: self.cards.pop(permanent.uid,None)

    def _remove_from_combat(self,uid):
        if uid in self.attackers:
            self.attackers.remove(uid); self.attack_bands=[band for band in ([member for member in band if member!=uid] for band in self.attack_bands) if len(band)>1]; self.blocks.pop(uid,None); self.additional_blocks.pop(uid,None); self.trample_assignments.pop(uid,None); self.attacker_damage_assignments.pop(uid,None)
            if uid in self.blocked_attackers: self.blocked_attackers.remove(uid)
        for attacker in list(self.blocks):
            blockers=[blocker for blocker in self.blockers_for(attacker) if blocker!=uid]
            if blockers: self.blocks[attacker]=blockers[0]
            else: self.blocks.pop(attacker,None)
            if len(blockers)>1: self.additional_blocks[attacker]=blockers[1:]
            else: self.additional_blocks.pop(attacker,None)
        self.blocker_damage_assignments.pop(uid,None)

    def _destroy(self,controller,permanent,allow_regeneration=True,trigger_batch=None,death_sources=None):
        if self.is_indestructible(permanent):
            self.log.append(f"{self.card(permanent.uid).name} was indestructible."); return False
        if allow_regeneration and permanent.regeneration_shields:
            permanent.regeneration_shields-=1; permanent.tapped=True; permanent.damage=0
            self._remove_from_combat(permanent.uid)
            self.log.append(f"{self.card(permanent.uid).name} regenerated.")
            return False
        self._remove_from_combat(permanent.uid)
        was_land=self.card(permanent.uid).land
        if permanent in controller.battlefield:
            self._remember_source_power(permanent); controller.battlefield.remove(permanent)
        self._dies(controller,permanent,trigger_batch,death_sources)
        if was_land and permanent.uid in self.permanent_owner(permanent).graveyard: self.stack.extend(self._land_event_triggers(controller.user_id,"grave"))
        return True

    def _queue_state_triggers(self):
        pending={item.source_uid for item in self.stack if item.ability_effect=="no_land_sacrifice"}
        for controller_id in (self.active_user,self.opponent(self.active_user)):
            controller=self.player(controller_id)
            land_types={land_type for permanent in controller.battlefield for land_type in ("plains","island","swamp","mountain","forest") if self.has_current_land_type(permanent,land_type)}
            for source in controller.battlefield:
                card=self.card(source.uid); required=card.sacrifice_without_land_type
                if not required or required in land_types or source.uid in pending: continue
                uid=self.next_uid; self.next_uid+=1; self.cards[uid]=card.key
                self.stack.append(Spell(controller_id,uid,card.key,f"{controller_id}:{source.uid}",ability_effect="no_land_sacrifice",source_uid=source.uid,color_override=source.color_override))
                pending.add(source.uid)

    def _resolve_ability(self,s):
        card=CARDS[s.key]; effect=s.ability_effect
        def target_permanent():
            if not s.target or ":" not in s.target or s.target.upper().startswith(("S:","D:","J:")): return None,None
            user,uid=(int(x) for x in s.target.split(":")); controller=self.player(user)
            return controller,next((x for x in controller.battlefield if x.uid==uid),None)
        def fizzle(reason):
            self.cards.pop(s.uid,None); self.log.append(f"{card.name} ability fizzled because {reason}.")
        if effect=="animate_dead_sacrifice":
            try: target_uid=int(s.target)
            except (TypeError,ValueError): fizzle("its linked creature was invalid"); return
            target_controller,target=self.find_permanent(target_uid)
            if target is None: fizzle("its linked creature was gone"); return
            death_sources=self._death_trigger_sources(); self._remember_source_power(target); self._remove_from_combat(target.uid); target_controller.battlefield.remove(target); self._dies(target_controller,target,self.next_uid,death_sources)
            self.cards.pop(s.uid,None); self.log.append(f"{target_controller.user_id} sacrificed {self.card(target.uid).name} after Animate Dead left the battlefield."); self._sba(); self._life(); return
        controller,target=target_permanent()
        target_card=self.card(target.uid) if target is not None else None
        if target is not None and effect not in ("self","regenerate","corpse_regenerate","end_step_corpse_counters","empty_battlefield_sacrifice","earthbind_enter","end_step_sacrifice","end_step_destroy","forced_end_step_destroy","berserk_end_step_destroy","end_combat_destroy","end_combat_remove_power_counter","no_land_sacrifice","dealt_damage_counter","damaged_creature_death_counter","add_power_counters","redirect_one_to_owner","cap_unblocked_damage","hydra_prevent","hydra_counter") and self._protected_from(target,card,self.ability_source_colors(s)): fizzle("its target gained protection"); return
        if effect=="vesuvan_copy":
            _,source=self.find_permanent(s.source_uid)
            if source is None: fizzle("its source was gone"); return
            if target is None or not self.is_creature(target): fizzle("its target was gone or no longer a creature"); return
            kept_colors=list(source.copy_colors or CARDS[self.cards[source.uid]].colors)
            source.copy_key=target.copy_key or self.cards[target.uid]; source.copy_added_types=list(target.copy_added_types); source.copy_colors=kept_colors; source.copy_upkeep_creature=True
        elif effect=="hydra_prevent":
            if target is None or target.uid!=s.source_uid or not self.card(target.uid).hydra_damage_replacement: fizzle("its source was gone"); return
            target.damage_prevention+=1
        elif effect=="hydra_counter":
            if target is None or target.uid!=s.source_uid or not self.card(target.uid).hydra_damage_replacement: fizzle("its source was gone"); return
            target.plus_one_counters+=1
        elif effect=="prevent_source_damage":
            try: _,source_uid,_=s.target.split(":")
            except (AttributeError,ValueError): fizzle("its chosen source was invalid"); return
            self.player(s.owner).source_damage_prevention.append(int(source_uid))
        elif effect=="cap_unblocked_damage":
            if target is None or target.uid not in self.attackers or target.uid in self.blocks: fizzle("the chosen creature was no longer attacking unblocked"); return
            self.player(s.owner).source_damage_caps[target.uid]=1
        elif effect=="redirect_source_to_creature":
            try: _,source_uid,target_user,target_uid=s.target.split(":"); controller=self.player(int(target_user)); target=next((x for x in controller.battlefield if x.uid==int(target_uid)),None)
            except (AttributeError,ValueError): fizzle("its source or target was invalid"); return
            if target is None or not self.is_creature(target): fizzle("its target was gone or illegal"); return
            target.redirect_source_damage_to_player[int(source_uid)]=s.owner
        elif effect=="redirect_one_to_owner":
            if target is None or target.uid!=s.source_uid: fizzle("its source was gone"); return
            target.redirect_damage_to_owner+=1
        elif effect=="vitality_counter":
            if target is None or target.uid!=s.source_uid: fizzle("its source was gone"); return
            target.vitality_counters+=s.choice_value
        elif effect in ("dealt_damage_counter","damaged_creature_death_counter"):
            if target is None or target.uid!=s.source_uid: fizzle("its source was gone"); return
            target.plus_one_counters+=1
        elif effect=="end_step_corpse_counters":
            if target is None or target.uid!=s.source_uid: fizzle("its source was gone"); return
            target.corpse_counters+=self.creatures_died_this_turn
        elif effect=="empty_battlefield_sacrifice":
            if target is None or target.uid!=s.source_uid: fizzle("its source was gone"); return
            if any(self.is_creature(permanent) for player in self.players.values() for permanent in player.battlefield): fizzle("a creature was on the battlefield"); return
            trigger_batch=self.next_uid; death_sources=self._death_trigger_sources()
            self._remember_source_power(target); controller.battlefield.remove(target); self._dies(controller,target,trigger_batch,death_sources)
        elif effect=="end_step_sacrifice":
            if target is None or target.uid!=s.source_uid: fizzle("its source was gone"); return
            trigger_batch=self.next_uid; death_sources=self._death_trigger_sources()
            self._remember_source_power(target); self._remove_from_combat(target.uid); controller.battlefield.remove(target); self._dies(controller,target,trigger_batch,death_sources)
        elif effect=="no_land_sacrifice":
            if target is None or target.uid!=s.source_uid: fizzle("its source was gone"); return
            trigger_batch=self.next_uid; death_sources=self._death_trigger_sources()
            self._remember_source_power(target); self._remove_from_combat(target.uid); controller.battlefield.remove(target); self._dies(controller,target,trigger_batch,death_sources)
        elif effect=="forced_end_step_destroy":
            live_controller,live_target=self.find_permanent(int(s.target.split(":",1)[1]))
            if live_target is None or live_target.layer_timestamp!=s.choice_value: fizzle("its delayed target was gone"); return
            if live_target.uid in self.attacked_this_turn: fizzle("its target attacked this turn"); return
            self._destroy(live_controller,live_target)
        elif effect=="end_step_destroy":
            if target is None: fizzle("its delayed target was gone"); return
            self._destroy(controller,target)
        elif effect=="berserk_end_step_destroy":
            if target is None or target.layer_timestamp!=s.choice_value: fizzle("its delayed target was gone"); return
            if target.uid not in self.attacked_this_turn: fizzle("its target had not attacked this turn"); return
            self._destroy(controller,target)
        elif effect=="end_combat_destroy":
            if target is None: fizzle("its combatant was gone"); return
            self._destroy(controller,target)
        elif effect=="end_combat_remove_power_counter":
            if target is None or target.uid!=s.source_uid: fizzle("its source was gone"); return
            target.power_counters=max(0,target.power_counters-1)
        elif effect=="self":
            if target is None: fizzle("its source was gone"); return
            target.power_bonus+=card.activated_power; target.toughness_bonus+=card.activated_toughness
            if card.activated_keyword and card.activated_keyword not in target.temporary_keywords: target.temporary_keywords.append(card.activated_keyword)
        elif effect in ("regenerate","corpse_regenerate"):
            if target is None: fizzle("its source was gone"); return
            target.regeneration_shields+=1
        elif effect=="counter_color":
            uid=int(s.target.split(":",1)[1]); spell=next((item for item in self.stack if item.uid==uid and not item.ability_effect),None)
            if spell is None or card.target_color not in self.spell_colors(spell): fizzle("its target was gone or changed color"); return
            self.stack.remove(spell); self.player(spell.owner).graveyard.append(spell.uid)
            self.log.append(f"{card.name} countered {self.card(spell.uid).name}.")
        elif effect=="tap_damage":
            self._damage_player(int(s.target),card.land_tap_damage or card.aura_tap_damage,source_uid=s.source_uid)
        elif effect=="tap_life":
            self.player(int(s.target)).life+=card.opponent_forest_tap_life
        elif effect=="land_event_damage":
            self._damage_player(int(s.target),card.land_enter_damage or card.land_grave_damage or card.extra_land_damage,source_uid=s.source_uid)
        elif effect=="creature_bond_damage":
            self._damage_player(int(s.target),s.choice_value,source_uid=s.source_uid)
        elif effect=="earthbind_enter":
            _,aura=self.find_permanent(s.source_uid)
            if target is None or aura is None or aura.attached_to!=target.uid or "flying" not in self.current_keywords(target): fizzle("its intervening condition was no longer true"); return
            aura.aura_effect_enabled=True
            self._damage_permanent(target,card.aura_enter_flying_damage,card,self.ability_source_colors(s),source_uid=s.source_uid)
        elif effect=="upkeep_damage":
            self._damage_player(int(s.target),card.upkeep_each_damage,source_uid=s.source_uid)
        elif effect=="upkeep_untapped_land_damage":
            self._damage_player(int(s.target),s.choice_value,source_uid=s.source_uid)
        elif effect=="upkeep_land_type_damage":
            target_player=self.player(int(s.target)); amount=sum(self.has_current_land_type(permanent,card.upkeep_land_type_damage) for permanent in target_player.battlefield)
            self._damage_player(target_player.user_id,amount,source_uid=s.source_uid)
        elif effect=="aura_upkeep_damage":
            self._damage_player(int(s.target),card.aura_upkeep_damage,source_uid=s.source_uid)
        elif effect=="upkeep_sacrifice":
            self._damage_player(s.owner,card.upkeep_sacrifice_damage,source_uid=s.source_uid)
        elif effect=="upkeep_hand_damage":
            target_player=self.player(int(s.target)); self._damage_player(target_player.user_id,max(0,len(target_player.hand)-4),source_uid=s.source_uid)
        elif effect=="draw_step_draw":
            self._draw(self.player(int(s.target)),card.draw_step_extra)
        elif effect=="draw_tapped_damage":
            _,source=self.find_permanent(s.source_uid)
            if source is not None and source.tapped: self._damage_player(int(s.target),card.draw_tapped_damage,source_uid=s.source_uid)
        elif effect=="opponent_damage_discard_random":
            self._discard_random(self.player(int(s.target)),1)
        elif effect=="draw_self":
            self._draw(self.player(s.owner),1)
        elif effect=="take_extra_turn":
            self.extra_turns.insert(0,s.owner)
        elif effect in ("discard_choice","look_hand"):
            pass
        elif effect=="untap_self":
            if target is None: fizzle("its source was gone"); return
            target.tapped=False
        elif effect=="untap_attached":
            if target is None: fizzle("its enchanted creature was gone"); return
            target.tapped=False
        elif effect=="animate_self":
            if target is None: fizzle("its source was gone"); return
            target.animated_until_end_combat=True
        elif effect=="create_token":
            uid=self.next_uid; self.next_uid+=1; self.cards[uid]=card.creates_token
            self.player(s.owner).battlefield.append(self._make_permanent(uid,card.creates_token,owner=s.owner))
        elif effect=="add_power_counters":
            if target is None or target.uid!=s.source_uid: fizzle("its source was gone"); return
            target.power_counters+=min(s.choice_value,max(0,7-target.power_counters))
        elif effect=="prevent_player_damage":
            self.player(s.owner).damage_prevention+=card.activation_amount
        elif effect=="prevent_any_damage":
            if ":" in (s.target or ""):
                if target_card is None or not self.is_creature(target): fizzle("its target was gone or illegal"); return
                target.damage_prevention+=card.activation_amount
            else: self.player(int(s.target)).damage_prevention+=card.activation_amount
        elif effect=="destroy_all_nonland":
            trigger_batch=self.next_uid; death_sources=self._death_trigger_sources()
            for player in self.players.values():
                for permanent in list(player.battlefield):
                    if any(self.has_current_type(permanent,kind) for kind in ("Artifact","Creature","Enchantment")):
                        self._destroy(player,permanent,trigger_batch=trigger_batch,death_sources=death_sources)
        elif effect=="damage_all":
            damage_batch=self.next_uid; colors=self.ability_source_colors(s)
            for player in self.players.values():
                for permanent in list(player.battlefield):
                    if self.is_creature(permanent): self._damage_permanent(permanent,card.activation_amount,card,colors,damage_batch,s.source_uid)
            for user in self.order: self._damage_player(user,card.activation_amount,source_uid=s.source_uid)
        elif effect=="damage_any":
            if ":" in (s.target or ""):
                if target_card is None or not self.is_creature(target): fizzle("its target was gone or illegal"); return
                self._damage_permanent(target,card.activation_amount,card,self.ability_source_colors(s),source_uid=s.source_uid)
            else: self._damage_player(int(s.target),card.activation_amount,source_uid=s.source_uid)
            self._damage_player(s.owner,card.activation_self_damage,source_uid=s.source_uid)
        elif effect in ("destroy_black_permanent","destroy_tapped_creature","destroy_wall","destroy_land"):
            legal=target_card is not None
            if effect=="destroy_black_permanent": legal=legal and "B" in self.current_colors(target)
            elif effect=="destroy_tapped_creature": legal=legal and self.is_creature(target) and target.tapped
            elif effect=="destroy_land": legal=legal and target_card.land
            else: legal=legal and "Wall" in target_card.type_line.split(" — ",1)[-1].split()
            if not legal: fizzle("its target was gone or illegal"); return
            self._destroy(controller,target)
        elif effect=="grant_banding":
            if target_card is None or not self.is_creature(target): fizzle("its target was gone or illegal"); return
            if "banding" not in target.temporary_keywords: target.temporary_keywords.append("banding")
        elif effect=="unblockable":
            if target_card is None or not self.is_creature(target) or self.current_stats(target)[0]>2: fizzle("its target was gone or illegal"); return
            if "unblockable" not in target.temporary_keywords: target.temporary_keywords.append("unblockable")
        elif effect=="force_attack":
            if target is None or controller.user_id!=self.active_user or not self.is_creature(target) or self._has_subtype(target_card,"Wall") or target.sick: fizzle("its target was gone or no longer eligible"); return
            if target.uid not in self.forced_attackers: self.forced_attackers.append(target.uid)
            uid=self.next_uid; self.next_uid+=1; self.cards[uid]=card.key
            self.end_step_destroys.append(Spell(s.owner,uid,card.key,f"{controller.user_id}:{target.uid}",ability_effect="forced_end_step_destroy",source_uid=s.source_uid,color_override=s.color_override,choice_value=target.layer_timestamp))
        elif effect=="grant_flying_delayed_destroy":
            _,source=self.find_permanent(s.source_uid)
            source_power=self.current_stats(source)[0] if source is not None else s.source_power
            if target_card is None or controller.user_id!=s.owner or not self.is_creature(target) or self.current_stats(target)[1]>=source_power: fizzle("its target was gone or illegal"); return
            if "flying" not in target.temporary_keywords: target.temporary_keywords.append("flying")
            uid=self.next_uid; self.next_uid+=1; self.cards[uid]=card.key
            self.end_step_destroys.append(Spell(s.owner,uid,card.key,f"{controller.user_id}:{target.uid}",ability_effect="end_step_destroy",source_uid=s.source_uid,color_override=s.color_override))
        elif effect=="untap_land":
            if target_card is None or not target_card.land: fizzle("its target was gone or illegal"); return
            target.tapped=False
        elif effect=="add_mire_counter":
            land_type=self.changed_land_word(s.source_uid,"swamp")
            if target_card is None or not target_card.land or self.has_current_land_type(target,land_type): fizzle(f"its target was gone or became a {land_type.title()}"); return
            target.land_type_effects.append({"kind":"mire","source_uid":s.source_uid,"source_timestamp":s.choice_value,"effect_timestamp":self.next_layer_timestamp,"land_type":land_type}); self.next_layer_timestamp+=1
        elif effect=="set_land_forest":
            _,source=self.find_permanent(s.source_uid)
            if target_card is None or not target_card.land: fizzle("its target was gone or illegal"); return
            if source is None or source.layer_timestamp!=s.choice_value: fizzle("its source had left the battlefield"); return
            target.land_type_effects.append({"source_uid":s.source_uid,"source_timestamp":s.choice_value,"effect_timestamp":self.next_layer_timestamp,"land_type":self.changed_land_word(s.source_uid,"forest")}); self.next_layer_timestamp+=1
        elif effect=="chaos_orb_destroy":
            _,source=self.find_permanent(s.source_uid)
            if source is None: fizzle("Chaos Orb was no longer on the battlefield"); return
            if target is not None and not self.is_token(target.uid): self._destroy(self.find_permanent(target.uid)[0],target)
            source_controller,source=self.find_permanent(s.source_uid)
            if source is not None: self._destroy(source_controller,source,allow_regeneration=False)
        elif effect=="tap_permanent":
            if target_card is None or not any(self.has_current_type(target,kind) for kind in ("Artifact","Creature","Land")): fizzle("its target was gone or illegal"); return
            target.tapped=True
        else:
            fizzle("the effect is unsupported"); return
        self.cards.pop(s.uid,None); self.log.append(f"{card.name} ability resolved.")
        self._sba(); self._life()

    def _resolve(self,s):
        if s.ability_effect: self._resolve_ability(s); return
        p=self.players[s.owner]; c=self._text_changed_card(s.uid,CARDS[s.key],s.land_word_changes,s.color_word_changes)
        protected=None if c.enters_copy_types else self._stable_target_permanent(s.target)
        if protected is not None and self._protected_from(protected,c,self.spell_colors(s)):
            p.graveyard.append(s.uid); self.log.append(f"{c.name} fizzled because its target gained protection."); return
        if c.aura_reanimate:
            try: _,target_user_text,target_uid_text=s.target.split(":"); target_user,target_uid=int(target_user_text),int(target_uid_text)
            except (AttributeError,TypeError,ValueError): target_user=target_uid=0
            graveyard=self.player(target_user).graveyard if target_user in self.players else []
            if target_uid not in graveyard or not self.card(target_uid).creature:
                p.graveyard.append(s.uid); self.log.append(f"{c.name} fizzled because its target was gone or illegal."); return
            graveyard.remove(target_uid); creature=self._make_permanent(target_uid,self.cards[target_uid],owner=target_user,base_controller=s.owner); p.battlefield.append(creature)
            aura=self._make_permanent(s.uid,c.key,owner=s.owner,sick=False,attached_to=target_uid,color_override=s.color_override); p.battlefield.append(aura)
        elif c.aura_target_types:
            parts=s.target.split(":"); chosen_land_type=parts[0] if len(parts)==3 else ""; user,uid=(int(x) for x in parts[-2:]); target=next((x for x in self.player(user).battlefield if x.uid==uid),None)
            if target is None or not self._aura_can_attach(c,target,colors=self.spell_colors(s)):
                p.graveyard.append(s.uid); self.log.append(f"{c.name} fizzled because its target was gone or illegal."); return
            aura=self._make_permanent(s.uid,c.key,owner=s.owner,sick=False,attached_to=target.uid,color_override=s.color_override,chosen_land_type=chosen_land_type); p.battlefield.append(aura)
            if c.aura_control: self._reconcile_control()
            if c.aura_enters_tapped: self._tap_permanent(user,target)
            if c.aura_enter_flying_damage and "flying" in self.current_keywords(target):
                uid=self.next_uid; self.next_uid+=1; self.cards[uid]=c.key
                self.stack.append(Spell(s.owner,uid,c.key,f"{user}:{target.uid}",ability_effect="earthbind_enter",source_uid=aura.uid,color_override=aura.color_override))
        elif c.enters_copy_types:
            target=self._stable_target_permanent(s.target); copy_key=""; added_types=[]; copy_colors=[]; copy_upkeep=False
            if target is not None and any(self.has_current_type(target,kind) for kind in c.enters_copy_types):
                copy_key=target.copy_key or self.cards[target.uid]; added_types=list(target.copy_added_types); copy_colors=list(c.colors if c.copy_keep_colors else self.card(target.uid).colors); copy_upkeep=c.upkeep_copy_creature or self.card(target.uid).upkeep_copy_creature
            if copy_key and c.copy_add_type and c.copy_add_type not in added_types: added_types.append(c.copy_add_type)
            permanent=self._make_permanent(s.uid,c.key,owner=s.owner,copy_key=copy_key,copy_added_types=added_types,copy_colors=copy_colors,copy_upkeep_creature=copy_upkeep,color_override=s.color_override)
            effective=(CARDS.get(copy_key) or TOKENS.get(copy_key)) if copy_key else c; permanent.tapped=effective.enters_tapped
            p.battlefield.append(permanent)
        elif c.kind in ("Creature","Artifact","Enchantment"):
            counters=s.x_value if c.enters_x_plus_counters else 0
            p.battlefield.append(self._make_permanent(s.uid,c.key,owner=s.owner,tapped=c.enters_tapped,color_override=s.color_override,plus_one_counters=counters))
        elif c.effect=="blaze_of_glory":
            user,uid=(int(x) for x in s.target.split(":")); target=next((x for x in self.player(user).battlefield if x.uid==uid),None)
            if target is None or user!=self.opponent(self.active_user) or not self.is_creature(target): p.graveyard.append(s.uid); self.log.append(f"{c.name} fizzled because its target was gone or illegal."); return
            target.temporary_max_blocks=max(target.temporary_max_blocks,len(self.attackers)); target.must_block_all=True; p.graveyard.append(s.uid)
        elif c.effect=="false_orders":
            p.graveyard.append(s.uid); self.log.append(f"{c.name} fizzled because its target was gone or illegal.")
        elif c.effect=="power_sink":
            p.graveyard.append(s.uid); self.log.append(f"{c.name} fizzled because its target was gone.")
        elif c.effect in ("counter_spell","counter_mana_value_x","elemental_blast"):
            if s.target.startswith("S:"):
                target_uid=int(s.target.split(":",1)[1]); target=next((spell for spell in self.stack if spell.uid==target_uid),None)
                target_card=self.card(target.uid) if target is not None else None
                legal=target_card is not None and not target.ability_effect and (not c.target_color or c.target_color in self.spell_colors(target)) and (c.effect!="counter_mana_value_x" or self.spell_mana_value(target)==s.x_value)
                if not legal:
                    p.graveyard.append(s.uid); self.log.append(f"{c.name} fizzled because its target was gone or illegal."); return
                self.stack.remove(target); self.player(target.owner).graveyard.append(target.uid)
                self.log.append(f"{c.name} countered {target_card.name}.")
            else:
                user,uid=(int(x) for x in s.target.split(":")); controller=self.player(user)
                target=next((x for x in controller.battlefield if x.uid==uid),None); target_card=self.card(target.uid) if target is not None else None
                if target_card is None or c.target_color not in self.current_colors(target):
                    p.graveyard.append(s.uid); self.log.append(f"{c.name} fizzled because its target was gone or illegal."); return
                self._destroy(controller,target)
            p.graveyard.append(s.uid)
        elif c.effect=="set_color":
            if s.target.startswith("S:"):
                target_uid=int(s.target.split(":",1)[1]); target=next((spell for spell in self.stack if spell.uid==target_uid and not spell.ability_effect),None)
                if target is None:
                    p.graveyard.append(s.uid); self.log.append(f"{c.name} fizzled because its target was gone or illegal."); return
                target.color_override=c.color_change
            else:
                user,uid=(int(x) for x in s.target.split(":")); target=next((x for x in self.player(user).battlefield if x.uid==uid),None)
                if target is None:
                    p.graveyard.append(s.uid); self.log.append(f"{c.name} fizzled because its target was gone or illegal."); return
                target.color_override=c.color_change; target.color_timestamp=self.next_layer_timestamp; self.next_layer_timestamp+=1
            p.graveyard.append(s.uid)
        elif c.effect=="draw": self._draw(p,c.amount); p.graveyard.append(s.uid)
        elif c.effect in ("draw_target","draw_target_x"): self._draw(self.player(int(s.target)),s.x_value if c.effect=="draw_target_x" else c.amount); p.graveyard.append(s.uid)
        elif c.effect=="life_target_x": self.player(int(s.target)).life+=s.x_value; p.graveyard.append(s.uid)
        elif c.effect=="discard_random_x": self._discard_random(self.player(int(s.target)),s.x_value); p.graveyard.append(s.uid)
        elif c.effect=="wheel_seven":
            for player in self.players.values():
                player.graveyard.extend(player.hand); player.hand=[]
            self._draw_each(7); p.graveyard.append(s.uid)
        elif c.effect=="timetwister":
            for player in self.players.values():
                player.library.extend(player.hand); player.library.extend(player.graveyard); player.hand=[]; player.graveyard=[]
                random.SystemRandom().shuffle(player.library)
            self._draw_each(7); p.graveyard.append(s.uid)
        elif c.effect=="life": p.life+=c.amount; p.graveyard.append(s.uid)
        elif c.effect=="healing_salve":
            parts=s.target.split(":"); target_player=self.player(int(parts[1]))
            if parts[0]=="life": target_player.life+=c.amount
            elif len(parts)==2: target_player.damage_prevention+=c.amount
            else:
                target=next((permanent for permanent in target_player.battlefield if permanent.uid==int(parts[2])),None)
                if target is None or not self.is_creature(target):
                    p.graveyard.append(s.uid); self.log.append(f"{c.name} fizzled because its target was gone or illegal."); return
                target.damage_prevention+=c.amount
            p.graveyard.append(s.uid)
        elif c.effect=="simulacrum":
            user,uid=(int(x) for x in s.target.split(":")); target=next((x for x in self.player(user).battlefield if x.uid==uid),None)
            if target is None or user!=s.owner or not self.is_creature(target):
                p.graveyard.append(s.uid); self.log.append(f"{c.name} fizzled because its target was gone or illegal."); return
            amount=p.damage_taken_this_turn; p.life+=amount; self._damage_permanent(target,amount,c,self.spell_colors(s),source_uid=s.uid); p.graveyard.append(s.uid)
        elif c.effect=="guardian_angel":
            target=self._stable_target_permanent(s.target)
            if ":" in s.target and (target is None or not self.is_creature(target)):
                p.graveyard.append(s.uid); self.log.append(f"{c.name} fizzled because its target was gone or illegal."); return
            if target is not None: target.damage_prevention+=s.x_value
            else: self.player(int(s.target)).damage_prevention+=s.x_value
            p.guardian_angel_active=True; p.graveyard.append(s.uid)
        elif c.effect=="reverse_damage":
            try: source_uid=int(s.target.split(":",1)[1])
            except (AttributeError,ValueError): p.graveyard.append(s.uid); self.log.append(f"{c.name} fizzled because its source choice was invalid."); return
            p.source_damage_lifegain.append(source_uid); p.graveyard.append(s.uid)
        elif c.effect=="prevent_combat_damage": self.prevent_combat_damage=True; p.graveyard.append(s.uid)
        elif c.effect=="extra_turn": self.extra_turns.insert(0,s.owner); p.graveyard.append(s.uid)
        elif c.effect=="channel": p.channel_active=True; p.graveyard.append(s.uid)
        elif c.effect=="drain_power":
            target_player=self.player(int(s.target)); pending=[]
            for _,permanent,mana in self._drain_power_lands(s):
                symbol=s.mana_choices.get(str(permanent.uid))
                if symbol not in mana:
                    p.graveyard.append(s.uid); self.log.append(f"{c.name} fizzled because a mana choice was incomplete."); return
                self._tap_permanent(target_player.user_id,permanent,symbol,add_mana=True,pending_triggers=pending)
            transferred=dict(target_player.mana_pool); target_player.mana_pool.clear()
            for symbol,count in transferred.items(): p.mana_pool[symbol]=p.mana_pool.get(symbol,0)+count
            p.graveyard.append(s.uid); self.stack.extend(pending); self.log.append(f"{s.owner} received {sum(transferred.values())} mana from {target_player.user_id} with {c.name}.")
        elif c.effect=="mana_short":
            target_player=self.player(int(s.target)); pending=[]
            for permanent in target_player.battlefield:
                if self.card(permanent.uid).land: self._tap_permanent(target_player.user_id,permanent,pending_triggers=pending)
            target_player.mana_pool.clear(); p.graveyard.append(s.uid); self.stack.extend(pending)
        elif c.effect=="fireball":
            chosen=(s.target or "").split(",") if s.target else []
            legal=[]
            for item in chosen:
                if ":" not in item:
                    if int(item) in self.players: legal.append(("player",int(item),None))
                    continue
                target_user,target_uid=(int(value) for value in item.split(":")); permanent=next((x for x in self.player(target_user).battlefield if x.uid==target_uid),None)
                if permanent is not None and self.is_creature(permanent) and not self._protected_from(permanent,c,self.spell_colors(s)): legal.append(("creature",target_user,permanent))
            if chosen and not legal:
                p.graveyard.append(s.uid); self.log.append(f"{c.name} fizzled because all its targets were gone or illegal."); return
            amount=s.x_value//len(legal) if legal else 0; damage_batch=self.next_uid
            for kind,target_user,permanent in legal:
                if kind=="player": self._damage_player(target_user,amount,source_uid=s.uid)
                else: self._damage_permanent(permanent,amount,c,self.spell_colors(s),damage_batch,s.uid)
            p.graveyard.append(s.uid)
        elif c.effect=="volcanic_eruption":
            chosen=(s.target or "").split(",") if s.target else []; legal=[]
            for item in chosen:
                target_user,target_uid=(int(value) for value in item.split(":")); permanent=next((x for x in self.player(target_user).battlefield if x.uid==target_uid),None)
                if permanent is not None and self.has_current_land_type(permanent,"mountain") and not self._protected_from(permanent,c,self.spell_colors(s)): legal.append((self.player(target_user),permanent))
            if chosen and not legal:
                p.graveyard.append(s.uid); self.log.append(f"{c.name} fizzled because all its targets were gone or illegal."); return
            trigger_batch=self.next_uid; death_sources=self._death_trigger_sources(); destroyed=sum(self._destroy(controller,permanent,trigger_batch=trigger_batch,death_sources=death_sources) for controller,permanent in legal)
            damage_batch=self.next_uid
            for player in self.players.values():
                for permanent in list(player.battlefield):
                    if self.is_creature(permanent): self._damage_permanent(permanent,destroyed,c,self.spell_colors(s),damage_batch,s.uid)
            for target_user in self.order: self._damage_player(target_user,destroyed,source_uid=s.uid)
            p.graveyard.append(s.uid)
        elif c.effect in ("damage","damage_any","damage_x_exile","drain_life_x"):
            amount=s.x_value if c.effect in ("damage_x_exile","drain_life_x") else c.amount
            life_cap=dealt=0
            if ":" in (s.target or ""):
                user,uid=(int(x) for x in s.target.split(":")); target=next((x for x in self.player(user).battlefield if x.uid==uid),None)
                if target is None:
                    p.graveyard.append(s.uid); self.log.append(f"{c.name} fizzled because its target was gone."); return
                life_cap=max(0,self.current_stats(target)[1]); dealt=self._damage_permanent(target,amount,c,self.spell_colors(s),source_uid=s.uid)
                if c.effect=="damage_x_exile": target.exile_on_death=True; target.cant_regenerate=True
            else:
                target_player=self.player(int(s.target or self.opponent(s.owner))); life_cap=max(0,target_player.life); dealt=self._damage_player(target_player.user_id,amount,source_uid=s.uid)
            if c.effect=="drain_life_x": p.life+=min(dealt,life_cap)
            self._damage_player(s.owner,c.self_damage,source_uid=s.uid); p.graveyard.append(s.uid)
        elif c.effect in ("regenerate_target","grant_keyword","destroy_wall"):
            user,uid=(int(x) for x in s.target.split(":")); controller=self.player(user)
            target=next((x for x in controller.battlefield if x.uid==uid),None); target_card=self.card(target.uid) if target is not None else None
            legal=target_card is not None and self.is_creature(target) and (c.effect!="destroy_wall" or "Wall" in target_card.type_line.split(" — ",1)[-1].split())
            if not legal:
                p.graveyard.append(s.uid); self.log.append(f"{c.name} fizzled because its target was gone or illegal."); return
            if c.effect=="regenerate_target": target.regeneration_shields+=1
            elif c.effect=="grant_keyword":
                if c.temporary_keyword not in target.temporary_keywords: target.temporary_keywords.append(c.temporary_keyword)
            else: self._destroy(controller,target,allow_regeneration=False)
            p.graveyard.append(s.uid)
        elif c.effect=="tap_or_untap":
            mode,user_text,uid_text=s.target.split(":"); controller=self.player(int(user_text))
            target=next((x for x in controller.battlefield if x.uid==int(uid_text)),None); target_card=self.card(target.uid) if target is not None else None
            if target_card is None or not any(self.has_current_type(target,kind) for kind in c.target_types):
                p.graveyard.append(s.uid); self.log.append(f"{c.name} fizzled because its target was gone or illegal."); return
            if mode=="tap": self._tap_permanent(controller.user_id,target)
            else: target.tapped=False
            p.graveyard.append(s.uid)
        elif c.effect=="add_mana":
            p.mana_pool[c.mana_color]=p.mana_pool.get(c.mana_color,0)+c.mana_amount; p.graveyard.append(s.uid)
        elif c.effect=="sacrifice_mana":
            if s.choice_value: p.mana_pool[c.sacrifice_mana_color]=p.mana_pool.get(c.sacrifice_mana_color,0)+s.choice_value
            p.graveyard.append(s.uid)
        elif c.effect=="search_library":
            p.graveyard.append(s.uid)
        elif c.effect=="natural_selection":
            p.graveyard.append(s.uid)
        elif c.effect=="siren_call":
            active=self.player(self.active_user)
            for target in active.battlefield:
                target_card=self.card(target.uid)
                if not self.is_creature(target) or self._has_subtype(target_card,"Wall") or target.sick: continue
                if target.uid not in self.forced_attackers: self.forced_attackers.append(target.uid)
                trigger_uid=self.next_uid; self.next_uid+=1; self.cards[trigger_uid]=c.key
                self.end_step_destroys.append(Spell(s.owner,trigger_uid,c.key,f"{active.user_id}:{target.uid}",ability_effect="forced_end_step_destroy",source_uid=s.uid,color_override=s.color_override,choice_value=target.layer_timestamp))
            p.graveyard.append(s.uid)
        elif c.effect=="destroy_all_enchantments":
            for controller in self.players.values():
                for permanent in list(controller.battlefield):
                    if self.card(permanent.uid).has_type("Enchantment"): self._destroy(controller,permanent)
            p.graveyard.append(s.uid)
        elif c.effect in ("pump","pump_blocking","pump_power_x","berserk"):
            user,uid=(int(x) for x in s.target.split(":")); target=next((x for x in self.player(user).battlefield if x.uid==uid),None)
            if target is None or (c.effect=="berserk" and not self.is_creature(target)):
                p.graveyard.append(s.uid); self.log.append(f"{c.name} fizzled because its target was gone or illegal."); return
            if c.effect=="pump_power_x": target.power_bonus+=s.x_value
            elif c.effect=="berserk":
                target.power_bonus+=self.current_stats(target)[0]
                if "trample" not in target.temporary_keywords: target.temporary_keywords.append("trample")
                trigger_uid=self.next_uid; self.next_uid+=1; self.cards[trigger_uid]=c.key
                self.end_step_destroys.append(Spell(s.owner,trigger_uid,c.key,f"{user}:{target.uid}",ability_effect="berserk_end_step_destroy",source_uid=s.uid,color_override=s.color_override,choice_value=target.layer_timestamp))
            else: target.bonus+=c.amount
            p.graveyard.append(s.uid)
        elif c.effect=="return_creature_hand":
            user,uid=(int(x) for x in s.target.split(":")); controller=self.player(user)
            target=next((x for x in controller.battlefield if x.uid==uid),None)
            if target is None or not self.is_creature(target):
                p.graveyard.append(s.uid); self.log.append(f"{c.name} fizzled because its target was gone."); return
            self._remember_source_power(target); owner=self.permanent_owner(target,controller); controller.battlefield.remove(target)
            if self.is_token(target.uid): self.cards.pop(target.uid,None)
            else: owner.hand.append(target.uid)
            self._remove_from_combat(target.uid)
            p.graveyard.append(s.uid)
        elif c.effect in ("return_grave_creature_hand","return_grave_card_hand","reanimate_creature"):
            uid=int(s.target); creature_only=c.effect!="return_grave_card_hand"
            if uid not in p.graveyard or (creature_only and not self.card(uid).creature):
                p.graveyard.append(s.uid); self.log.append(f"{c.name} fizzled because its target was gone or illegal."); return
            p.graveyard.remove(uid)
            if c.effect=="reanimate_creature": p.battlefield.append(self._make_permanent(uid,self.cards[uid],owner=p.user_id))
            else: p.hand.append(uid)
            p.graveyard.append(s.uid)
        elif c.effect in ("destroy_creature","exile_creature_life"):
            user,uid=(int(x) for x in s.target.split(":")); controller=self.player(user)
            target=next((x for x in controller.battlefield if x.uid==uid),None)
            target_card=self.card(target.uid) if target is not None else None
            legal=target_card is not None and self.is_creature(target) and not (c.target_nonartifact and "Artifact" in target_card.type_line) and not (c.target_nonblack and s.color_word_changes.get("B","B") in self.current_colors(target))
            if not legal:
                p.graveyard.append(s.uid); self.log.append(f"{c.name} fizzled because its target was gone or illegal."); return
            life_gain=max(0,self.current_stats(target)[0]) if c.effect=="exile_creature_life" else 0
            if c.effect=="exile_creature_life":
                self._remember_source_power(target); self._remove_from_combat(target.uid); owner=self.permanent_owner(target,controller); controller.battlefield.remove(target)
                if self.is_token(target.uid): self.cards.pop(target.uid,None)
                else: owner.exile.append(target.uid)
                controller.life+=life_gain
            else: self._destroy(controller,target,allow_regeneration=False)
            p.graveyard.append(s.uid)
        elif c.effect in ("earthquake_x","hurricane_x"):
            damage_batch=self.next_uid
            for controller in self.players.values():
                self._damage_player(controller.user_id,s.x_value,source_uid=s.uid)
                for permanent in controller.battlefield:
                    card=self.card(permanent.uid)
                    keywords=self.current_keywords(permanent)
                    affected=self.is_creature(permanent) and ((c.effect=="earthquake_x" and "flying" not in keywords) or (c.effect=="hurricane_x" and "flying" in keywords))
                    if affected: self._damage_permanent(permanent,s.x_value,c,self.spell_colors(s),damage_batch,s.uid)
            p.graveyard.append(s.uid)
        elif c.effect=="destroy_all_creatures":
            trigger_batch=self.next_uid; death_sources=self._death_trigger_sources()
            for controller in self.players.values():
                for permanent in list(controller.battlefield):
                    if self.is_creature(permanent): self._destroy(controller,permanent,allow_regeneration=False,trigger_batch=trigger_batch,death_sources=death_sources)
            p.graveyard.append(s.uid)
        elif c.effect=="destroy_permanent":
            user,uid=(int(x) for x in s.target.split(":")); controller=self.player(user)
            target=next((x for x in controller.battlefield if x.uid==uid),None)
            if target is None or not any(self.has_current_type(target,kind) for kind in c.target_types):
                p.graveyard.append(s.uid); self.log.append(f"{c.name} fizzled because its target was gone."); return
            self._destroy(controller,target); p.graveyard.append(s.uid)
        elif c.effect=="destroy_land":
            user,uid=(int(x) for x in s.target.split(":")); controller=self.player(user)
            target=next((x for x in controller.battlefield if x.uid==uid),None)
            if target is None or not self.card(target.uid).land:
                p.graveyard.append(s.uid); self.log.append(f"{c.name} fizzled because its target was gone."); return
            self._destroy(controller,target,allow_regeneration=False); p.graveyard.append(s.uid)
        elif c.effect in ("destroy_all_lands","destroy_land_type"):
            for controller in self.players.values():
                destroyed=[x for x in controller.battlefield if self.card(x.uid).land and (c.effect=="destroy_all_lands" or self.has_current_land_type(x,c.land_type))]
                for permanent in destroyed: self._destroy(controller,permanent,allow_regeneration=False)
            p.graveyard.append(s.uid)
        self.log.append(f"{c.name} resolved."); self._sba(); self._life()

    def _perm(self,p,uid):
        return next(x for x in p.battlefield if x.uid==uid)
    def _sba(self):
        while True:
            self._reconcile_control()
            affected=False; trigger_batch=self.next_uid; death_sources=self._death_trigger_sources()
            all_permanents={permanent.uid:permanent for player in self.players.values() for permanent in player.battlefield}
            for controller in self.players.values():
                for permanent in list(controller.battlefield):
                    card=self.card(permanent.uid)
                    if card.aura_target_types:
                        target=all_permanents.get(permanent.attached_to)
                        if target is None or not self._aura_can_attach(card,target,permanent):
                            self._remember_source_power(permanent); controller.battlefield.remove(permanent); self._dies(controller,permanent,trigger_batch,death_sources); affected=True
                        continue
                    if not self.is_creature(permanent): continue
                    toughness=self.current_stats(permanent)[1]
                    if toughness<=0:
                        self._remember_source_power(permanent); self._remove_from_combat(permanent.uid); controller.battlefield.remove(permanent); self._dies(controller,permanent,trigger_batch,death_sources); affected=True
                    elif permanent.damage>=toughness:
                        affected=self._destroy(controller,permanent,allow_regeneration=not permanent.cant_regenerate,trigger_batch=trigger_batch,death_sources=death_sources) or affected
            if not affected:
                self._queue_state_triggers(); return
    def _cleanup(self):
        self.prevent_combat_damage=False; self.creatures_died_this_turn=0; self.attacked_this_turn=[]; self.forced_attackers=[]
        for p in self.players.values():
            p.channel_active=False; p.guardian_angel_active=False; p.damage_prevention=0; p.source_damage_prevention=[]; p.source_damage_lifegain=[]; p.source_damage_caps={}; p.damage_taken_this_turn=0; p.bodyguard_choice=0
            for x in p.battlefield:
                x.damage=x.bonus=x.power_bonus=x.toughness_bonus=x.activations_this_turn=0
                x.redirect_damage_to_owner=0
                x.redirect_source_damage_to_player={}
                x.exile_on_death=False; x.cant_regenerate=False; x.regeneration_shields=0; x.damage_prevention=0; x.temporary_keywords=[]; x.damage_source_uids=[]; x.temporary_max_blocks=0; x.must_block_all=False
    def _begin_end_step(self):
        pending_sacrifices=set(self.end_step_sacrifices)
        pending_destroys=self.end_step_destroys; self.end_step_destroys=[]
        pending_sacrifices.update(x.uid for p in self.players.values() for x in p.battlefield if x.sacrifice_at_end_step)
        self.end_step_sacrifices=[]; by_owner={owner:[] for owner in self.order}; batch=self.next_uid
        for trigger in pending_destroys: by_owner[trigger.owner].append(trigger)
        for controller_id in (self.active_user,self.opponent(self.active_user)):
            controller=self.player(controller_id)
            for source in controller.battlefield:
                card=self.card(source.uid)
                if card.end_step_corpse_counters:
                    uid=self.next_uid; self.next_uid+=1; self.cards[uid]=card.key
                    by_owner[controller_id].append(Spell(controller_id,uid,card.key,f"{controller_id}:{source.uid}",ability_effect="end_step_corpse_counters",source_uid=source.uid,color_override=source.color_override,batch_id=batch))
                if card.end_step_sacrifice_without_creatures and not any(self.is_creature(permanent) for player in self.players.values() for permanent in player.battlefield):
                    uid=self.next_uid; self.next_uid+=1; self.cards[uid]=card.key
                    by_owner[controller_id].append(Spell(controller_id,uid,card.key,f"{controller_id}:{source.uid}",ability_effect="empty_battlefield_sacrifice",source_uid=source.uid,color_override=source.color_override,batch_id=batch))
            for source in controller.battlefield:
                if source.uid not in pending_sacrifices: continue
                source.sacrifice_at_end_step=False; card=self.card(source.uid); uid=self.next_uid; self.next_uid+=1; self.cards[uid]=card.key
                by_owner[controller_id].append(Spell(controller_id,uid,card.key,f"{controller_id}:{source.uid}",ability_effect="end_step_sacrifice",source_uid=source.uid,color_override=source.color_override,batch_id=batch))
        for owner in (self.active_user,self.opponent(self.active_user)): self.stack.extend(by_owner[owner])
        if pending_sacrifices:
            names=", ".join(self.card(uid).name for uid in pending_sacrifices if uid in self.cards)
            self.log.append(f"End-step sacrifice trigger pending for {names}; players may respond.")
    def _resolve_end_step_sacrifices(self):
        pending=set(self.end_step_sacrifices); self.end_step_sacrifices=[]
        trigger_batch=self.next_uid; death_sources=self._death_trigger_sources()
        for p in self.players.values():
            sacrificed=[x for x in p.battlefield if x.uid in pending]
            for permanent in sacrificed: self._remember_source_power(permanent)
            p.battlefield=[x for x in p.battlefield if x not in sacrificed]
            for permanent in sacrificed:
                self._dies(p,permanent,trigger_batch,death_sources)
                self.log.append(f"{self.card(permanent.uid).name} was sacrificed by its end-step trigger.")
    def _life(self):
        losers=[p.user_id for p in self.players.values() if p.life<=0]
        if len(losers)==2: self._finish(None,"both players reached zero life")
        elif losers: self._finish(self.opponent(losers[0]),"zero life")
    def concede(self,user):
        self.player(user)
        if self.finished: raise GameError("Game is over.")
        self._finish(self.opponent(user),"concession")
    def _finish(self,winner,reason): self.winner=winner; self.finished_reason=reason; self.phase="finished"; self.priority_user=None
    def _priority(self,user):
        if self.finished: raise GameError("Game is over.")
        if self.turn_start_pending_user is not None:
            raise GameError("The incoming player must choose whether to skip their turn for Time Vault first.")
        if self.sanctuary_draw_pending:
            raise GameError("The active player must choose whether to skip their draw for Island Sanctuary first.")
        if self.stack and self.stack[-1].decision_pending:
            if self.stack[-1].ability_effect.startswith("balance_"): message="The pending Balance choice must be completed first."
            elif self.stack[-1].ability_effect=="vesuvan_copy": message="The pending Vesuvan target must be chosen first." if self.stack[-1].choice_value==0 else "The pending Vesuvan copy decision must be completed first."
            elif self.stack[-1].ability_effect=="kudzu_move": message="The destroyed lands controller must reattach Kudzu or decline first."
            elif not self.stack[-1].ability_effect and self.card(self.stack[-1].uid).effect=="false_orders": message="The resolving False Orders assignment must be chosen first."
            elif self.stack[-1].ability_effect: message="The pending trigger controller must pay or decline first."
            elif self.card(self.stack[-1].uid).enters_copy_types: message="The pending copy choice must be completed first."
            else: message="The pending private library search must be completed first."
            raise GameError(message)
        if self.priority_user!=user: raise GameError("You do not have priority.")
    def _active(self,user):
        if self.finished: raise GameError("Game is over.")
        if user!=self.active_user or self.priority_user!=user: raise GameError("It is not your action window.")

    def to_raw(self):
        return {"game_id":self.game_id,"order":self.order,"players":{str(k):{**asdict(v),"battlefield":[asdict(x) for x in v.battlefield]} for k,v in self.players.items()},"cards":self.cards,"next_uid":self.next_uid,"next_layer_timestamp":self.next_layer_timestamp,"active_index":self.active_index,"phase":self.phase,"phase_passes":self.phase_passes,"turn":self.turn,"stack":[asdict(x) for x in self.stack],"end_step_sacrifices":self.end_step_sacrifices,"end_step_destroys":[asdict(x) for x in self.end_step_destroys],"end_combat_destroys":[asdict(x) for x in self.end_combat_destroys],"tomb_cleanup_sources":self.tomb_cleanup_sources,"extra_turns":self.extra_turns,"turn_start_pending_user":self.turn_start_pending_user,"turn_start_pending_extra":self.turn_start_pending_extra,"untap_pending":self.untap_pending,"skip_draw_step":self.skip_draw_step,"sanctuary_draw_pending":self.sanctuary_draw_pending,"sanctuary_pending_draws":self.sanctuary_pending_draws,"sanctuary_resume_draw_step":self.sanctuary_resume_draw_step,"sanctuary_resume_mass_draw":self.sanctuary_resume_mass_draw,"sanctuary_mass_draw_failed":self.sanctuary_mass_draw_failed,"prevent_combat_damage":self.prevent_combat_damage,"creatures_died_this_turn":self.creatures_died_this_turn,"attackers":self.attackers,"attacked_this_turn":self.attacked_this_turn,"forced_attackers":self.forced_attackers,"blocks":self.blocks,"additional_blocks":self.additional_blocks,"attack_bands":self.attack_bands,"blocked_attackers":self.blocked_attackers,"combat_participants":self.combat_participants,"trample_assignments":self.trample_assignments,"attacker_damage_assignments":self.attacker_damage_assignments,"blocker_damage_assignments":self.blocker_damage_assignments,"priority_user":self.priority_user,"winner":self.winner,"finished_reason":self.finished_reason,"ai_user":self.ai_user,"ai_difficulty":self.ai_difficulty,"log":self.log[-100:],"history":self.history,"created_at":self.created_at,"updated_at":self.updated_at}
    @classmethod
    def from_raw(cls,r):
        g=cls.__new__(cls); g.game_id=int(r["game_id"]); g.order=[int(x) for x in r["order"]]
        g.players={}
        for k,v in r["players"].items():
            d=dict(v); d.setdefault("mana_pool",{}); d.setdefault("exile",[]); d.setdefault("damage_prevention",0); d.setdefault("source_damage_prevention",[]); d.setdefault("source_damage_lifegain",[]); d.setdefault("source_damage_caps",{}); d.setdefault("guardian_angel_active",False); d.setdefault("turn_start_untapped_lands",0); d.setdefault("channel_active",False); d.setdefault("damage_taken_this_turn",0); d.setdefault("bodyguard_choice",0); d.setdefault("island_sanctuary_active",False); d.setdefault("sanctuary_landwalk_type","island"); d["source_damage_prevention"]=[int(uid) for uid in d["source_damage_prevention"]]; d["source_damage_lifegain"]=[int(uid) for uid in d["source_damage_lifegain"]]; d["source_damage_caps"]={int(uid):int(cap) for uid,cap in d["source_damage_caps"].items()}; d.setdefault("lands_played_this_turn",int(bool(d.get("land_played",False)))); d["mana_pool"]={str(symbol):int(count) for symbol,count in d["mana_pool"].items()}; d["battlefield"]=[Permanent(**({**x,"owner":int(x.get("owner",k)),"base_controller":int(x.get("base_controller",0)),"damage_prevention":x.get("damage_prevention",0),"hydra_counters_first":x.get("hydra_counters_first",False),"redirect_damage_to_owner":x.get("redirect_damage_to_owner",0),"redirect_source_damage_to_player":{int(uid):int(user) for uid,user in x.get("redirect_source_damage_to_player",{}).items()},"plus_one_counters":x.get("plus_one_counters",0),"power_counters":x.get("power_counters",0),"corpse_counters":x.get("corpse_counters",0),"vitality_counters":x.get("vitality_counters",0),"damage_source_uids":[int(uid) for uid in x.get("damage_source_uids",[])],"chosen_land_type":x.get("chosen_land_type",""),"layer_timestamp":x.get("layer_timestamp",x.get("uid",0)),"color_timestamp":x.get("color_timestamp",x.get("layer_timestamp",x.get("uid",0))) if x.get("color_override") else 0,"aura_effect_enabled":x.get("aura_effect_enabled",False),"last_known_toughness":x.get("last_known_toughness",0),"land_type_effects":[dict(effect) for effect in x.get("land_type_effects",[])],"copy_key":x.get("copy_key",""),"copy_added_types":list(x.get("copy_added_types",[])),"copy_colors":list(x.get("copy_colors",[])),"copy_upkeep_creature":bool(x.get("copy_upkeep_creature",False)),"temporary_max_blocks":int(x.get("temporary_max_blocks",0)),"must_block_all":bool(x.get("must_block_all",False)),"land_word_changes":{str(k):str(v) for k,v in x.get("land_word_changes",{}).items()},"color_word_changes":{str(k):str(v) for k,v in x.get("color_word_changes",{}).items()}})) for x in d["battlefield"]]; g.players[int(k)]=Player(**d)
        g.cards={int(k):v for k,v in r["cards"].items()}; g.next_uid=int(r["next_uid"]); g.next_layer_timestamp=int(r.get("next_layer_timestamp",max((x.layer_timestamp for p in g.players.values() for x in p.battlefield),default=0)+1)); g.active_index=int(r["active_index"]); g.phase=r["phase"]; g.phase_passes=int(r.get("phase_passes",0)); g.turn=int(r["turn"]); g.stack=[Spell(**x) for x in r["stack"]]; g.end_step_sacrifices=[int(x) for x in r.get("end_step_sacrifices",[])]; g.end_step_destroys=[Spell(**x) for x in r.get("end_step_destroys",[])]; g.end_combat_destroys=[Spell(**x) for x in r.get("end_combat_destroys",[])]; g.tomb_cleanup_sources=[{"owner":int(x["owner"]),"source_uid":int(x["source_uid"]),"source_timestamp":int(x["source_timestamp"])} for x in r.get("tomb_cleanup_sources",[])]; g.extra_turns=[int(x) for x in r.get("extra_turns",[])]; g.turn_start_pending_user=int(r["turn_start_pending_user"]) if r.get("turn_start_pending_user") is not None else None; g.turn_start_pending_extra=bool(r.get("turn_start_pending_extra",False)); g.untap_pending=[int(x) for x in r.get("untap_pending",[])]; g.skip_draw_step=bool(r.get("skip_draw_step",False)); g.sanctuary_draw_pending=bool(r.get("sanctuary_draw_pending",False)); g.sanctuary_pending_draws=int(r.get("sanctuary_pending_draws",int(g.sanctuary_draw_pending))); g.sanctuary_resume_draw_step=bool(r.get("sanctuary_resume_draw_step",False)); g.sanctuary_resume_mass_draw=bool(r.get("sanctuary_resume_mass_draw",False)); g.sanctuary_mass_draw_failed=[int(user) for user in r.get("sanctuary_mass_draw_failed",[])]; g.prevent_combat_damage=bool(r.get("prevent_combat_damage",False)); g.creatures_died_this_turn=int(r.get("creatures_died_this_turn",0)); g.attackers=[int(x) for x in r["attackers"]]; g.attacked_this_turn=[int(x) for x in r.get("attacked_this_turn",g.attackers)]; g.forced_attackers=[int(x) for x in r.get("forced_attackers",[])]; g.blocks={int(k):int(v) for k,v in r["blocks"].items()}; g.additional_blocks={int(k):[int(uid) for uid in values] for k,values in r.get("additional_blocks",{}).items()}; g.attack_bands=[[int(uid) for uid in band] for band in r.get("attack_bands",[])]; g.blocked_attackers=[int(x) for x in r.get("blocked_attackers",g.blocks.keys())]; g.combat_participants=[int(x) for x in r.get("combat_participants",list(g.attackers)+list(g.blocks.values()))]; g.trample_assignments={int(k):int(v) for k,v in r.get("trample_assignments",{}).items()}; g.attacker_damage_assignments={int(uid):[{"blocker":int(item["blocker"]),"damage":int(item["damage"])} for item in items] for uid,items in r.get("attacker_damage_assignments",{}).items()}; g.blocker_damage_assignments={int(uid):[{"attacker":int(item["attacker"]),"damage":int(item["damage"])} for item in items] for uid,items in r.get("blocker_damage_assignments",{}).items()}; g.priority_user=r["priority_user"]; g.winner=r["winner"]; g.finished_reason=r["finished_reason"]; g.ai_user=int(r["ai_user"]) if r.get("ai_user") is not None else None; g.ai_difficulty=r.get("ai_difficulty"); g.log=list(r["log"]); g.history=list(r.get("history",[])); g.created_at=int(r.get("created_at",time.time())); g.updated_at=int(r.get("updated_at",g.created_at))
        return g
