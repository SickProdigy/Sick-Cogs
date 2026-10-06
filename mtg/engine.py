import random
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional
from .cards import CARDS, TOKENS, starter

class GameError(ValueError): pass

@dataclass
class Permanent:
    uid: int
    key: str
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
    animated_until_end_combat: bool = False
    damage_prevention: int = 0

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
    kept: bool = False
    mulligans: int = 0
    damage_prevention: int = 0

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
        self.active_index, self.phase, self.turn = 0, "opening", 0
        self.stack, self.attackers, self.blocks = [], [], {}
        self.blocked_attackers=[]
        self.trample_assignments={}
        self.end_step_sacrifices=[]
        self.end_combat_destroys=[]
        self.extra_turns=[]
        self.skip_draw_step=False
        self.prevent_combat_damage=False
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
    def card(self,uid):
        key=self.cards[uid]
        return CARDS.get(key) or TOKENS[key]
    def is_token(self,uid): return self.cards.get(uid,"").startswith("token:")
    def is_creature(self,permanent): return self.card(permanent.uid).creature or permanent.animated_until_end_combat
    def has_current_type(self,permanent,card_type): return self.is_creature(permanent) if card_type=="Creature" else self.card(permanent.uid).has_type(card_type)
    def hand(self,user): return [self.card(x) for x in self.player(user).hand]
    def characteristic_stats(self,user,card,entering=False):
        player=self.player(user)
        if card.characteristic_pt=="swamps":
            value=sum(self.card(x.uid).has_land_type("swamp") for x in player.battlefield)
        elif card.characteristic_pt=="plague_rats":
            value=sum(self.card(x.uid).name=="Plague Rats" for p in self.players.values() for x in p.battlefield)+(1 if entering else 0)
        elif card.characteristic_pt=="non_wall_creatures":
            value=sum(self.is_creature(x) and "Wall" not in self.card(x.uid).type_line for x in player.battlefield)+(1 if entering else 0)
        else:
            return card.power,card.toughness
        return value,value
    def find_permanent(self,uid):
        for player in self.players.values():
            permanent=next((x for x in player.battlefield if x.uid==uid),None)
            if permanent is not None: return player,permanent
        return None,None
    def attached_auras(self,permanent):
        return [aura for player in self.players.values() for aura in player.battlefield if aura.attached_to==permanent.uid and self.card(aura.uid).aura_target_types]
    def aura_stats(self,aura):
        card=self.card(aura.uid)
        if not card.aura_forest_scaling: return card.aura_power,card.aura_toughness
        controller,_=self.find_permanent(aura.uid)
        forests=sum(self.card(x.uid).has_land_type("forest") for x in controller.battlefield)
        return forests//2,(forests+1)//2
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
        return (permanent.color_override,) if permanent.color_override else self.card(permanent.uid).colors
    def spell_colors(self,spell):
        return (spell.color_override,) if spell.color_override else self.card(spell.uid).colors
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
        excluded=aura.uid if aura is not None and aura_card.protection_self_exception else None
        aura_colors=self.current_colors(aura) if aura is not None else aura_card.colors if colors is None else colors
        return not bool(set(aura_colors) & self.current_protections(target,excluded))
    def _stable_target_permanent(self,target):
        if not target or target.upper().startswith(("S:","G:")): return None
        parts=target.split(":")
        try: uid=int(parts[-1])
        except (TypeError,ValueError): return None
        return self.find_permanent(uid)[1] if len(parts) in (2,3) else None
    def current_stats(self,permanent):
        owner=next((p.user_id for p in self.players.values() if permanent in p.battlefield),None)
        if owner is None: raise GameError("Permanent is not on the battlefield.")
        card=self.card(permanent.uid); power,toughness=((3,6) if permanent.animated_until_end_combat else self.characteristic_stats(owner,card))
        swamp_bonus=1 if card.conditional_swamp_bonus and any(self.card(x.uid).has_land_type("swamp") for x in self.player(owner).battlefield) else 0
        auras=self.attached_auras(permanent)
        lords=[self.card(source.uid) for source in self.continuous_lords(permanent)]
        globals_=[source for source_user,player in self.players.items() for source in player.battlefield if self.global_buff_applies(source,source_user,permanent,owner)]
        aura_bonuses=[self.aura_stats(aura) for aura in auras]
        return power+swamp_bonus+sum(bonus[0] for bonus in aura_bonuses)+sum(lord.lord_power for lord in lords)+sum(self.card(source.uid).global_power for source in globals_)+permanent.bonus+permanent.power_bonus,toughness+swamp_bonus+sum(bonus[1] for bonus in aura_bonuses)+sum(lord.lord_toughness for lord in lords)+sum(self.card(source.uid).global_toughness for source in globals_)+permanent.bonus+permanent.toughness_bonus
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
        aura_keywords={self.card(aura.uid).aura_keyword for aura in self.attached_auras(permanent) if self.card(aura.uid).aura_keyword}
        lord_keywords={self.card(source.uid).lord_keyword for source in self.continuous_lords(permanent) if self.card(source.uid).lord_keyword}
        return set(self.card(permanent.uid).keywords) | set(permanent.temporary_keywords) | aura_keywords | lord_keywords
    def can_attack_permanent(self,permanent):
        card=self.card(permanent.uid); keywords=self.current_keywords(permanent)
        defender_override=any(self.card(aura.uid).aura_attack_override for aura in self.attached_auras(permanent))
        return self.is_creature(permanent) and not permanent.tapped and (not permanent.sick or card.haste) and ("defender" not in keywords or defender_override)

    def _draw(self,p,n=1):
        for _ in range(n):
            if not p.library: self._finish(self.opponent(p.user_id),"empty library"); return
            p.hand.append(p.library.pop())

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

    def _turn_step_triggers(self,step):
        triggers=[]; active=self.active_user
        for controller_id in (active,self.opponent(active)):
            controller=self.player(controller_id)
            for source in controller.battlefield:
                card=self.card(source.uid); effect=""
                if step=="upkeep" and card.upkeep_untap_cost and active==controller.user_id: effect="upkeep_untap"
                elif step=="upkeep" and card.upkeep_cost and active==controller.user_id: effect="upkeep_cost"
                elif step=="upkeep" and card.upkeep_each_damage: effect="upkeep_damage"
                elif step=="upkeep" and card.upkeep_land_type_damage: effect="upkeep_land_type_damage"
                elif step=="upkeep" and card.aura_upkeep_damage:
                    attached_controller,attached=self.find_permanent(source.attached_to)
                    if attached is not None and active==attached_controller.user_id: effect="aura_upkeep_damage"
                elif step=="upkeep" and card.upkeep_opponent_hand_damage and active==self.opponent(controller.user_id): effect="upkeep_hand_damage"
                elif step=="draw" and card.draw_step_extra and not source.tapped: effect="draw_step_draw"
                elif step=="draw" and card.draw_tapped_damage and active==controller.user_id and source.tapped: effect="draw_tapped_damage"
                if not effect: continue
                uid=self.next_uid; self.next_uid+=1; self.cards[uid]=card.key
                triggers.append(Spell(controller.user_id,uid,card.key,str(active),ability_effect=effect,source_uid=source.uid,color_override=source.color_override))
        return triggers

    def _begin_draw_step(self):
        if self.skip_draw_step:
            self.skip_draw_step=False; self.phase="precombat_main"; self.priority_user=self.active_user; return
        self._draw(self.player(self.active_user))
        if self.finished: return
        triggers=self._turn_step_triggers("draw")
        if triggers:
            self.stack.extend(triggers); self.phase="draw"; self.priority_user=self.active_user
        else:
            self.phase="precombat_main"; self.priority_user=self.active_user

    def _start_turn(self,first=False):
        self.turn+=1; p=self.players[self.active_user]; p.land_played=False
        self._cleanup()
        limits=[self.card(source.uid).untap_power_limit for player in self.players.values() for source in player.battlefield if self.card(source.uid).untap_power_limit]
        for x in p.battlefield:
            restricted=self.is_creature(x) and any(self.current_stats(x)[0]>=limit for limit in limits)
            if not self.card(x.uid).skip_untap and not restricted: x.tapped=False
            x.sick=False
        self.skip_draw_step=bool(first and self.turn==1)
        self.phase_passes=0; triggers=self._turn_step_triggers("upkeep")
        if triggers:
            self.stack.extend(triggers); self.phase="upkeep"; self.priority_user=self.active_user
        else: self._begin_draw_step()
        self.log.append(f"Turn {self.turn}: {self.active_user}.")

    @staticmethod
    def _mana_requirements(card,x_value=0,mana_cost=None):
        symbols=re.findall(r"\{([^}]+)\}",card.mana_cost if mana_cost is None else mana_cost)
        generic=0; colored=[]
        for symbol in symbols:
            if symbol.isdigit(): generic+=int(symbol)
            elif symbol=="X": generic+=x_value
            elif symbol in {"W","U","B","R","G"}: colored.append(symbol)
            else: raise GameError(f"{card.name} uses an unsupported mana symbol: {{{symbol}}}.")
        return generic,colored

    def _mana_output(self,permanent,symbol):
        card=self.card(permanent.uid); output={symbol:card.mana_amount}
        if card.land:
            flares=sum(self.card(source.uid).mana_flare for player in self.players.values() for source in player.battlefield)
            output[symbol]+=flares
            if card.has_land_type("mountain"):
                gauntlets=sum(self.card(source.uid).mountain_extra_red for player in self.players.values() for source in player.battlefield)
                if gauntlets: output["R"]=output.get("R",0)+gauntlets
            for aura in self.attached_auras(permanent):
                extra=self.card(aura.uid).aura_extra_mana
                if extra: output[extra]=output.get(extra,0)+1
        return output

    def _land_event_triggers(self,user,event):
        field="land_enter_damage" if event=="enter" else "land_grave_damage"
        triggers=[]
        for controller in self.players.values():
            for source in controller.battlefield:
                if not getattr(self.card(source.uid),field): continue
                uid=self.next_uid; self.next_uid+=1; card=self.card(source.uid); self.cards[uid]=card.key
                triggers.append(Spell(controller.user_id,uid,card.key,str(user),ability_effect="land_event_damage",source_uid=source.uid,color_override=source.color_override))
        return triggers

    def _tap_triggers(self,user,permanent,mana_symbol):
        tapped_card=self.card(permanent.uid)
        if not tapped_card.land: return []
        sources=[]
        if mana_symbol:
            sources.extend((controller.user_id,source,"tap_damage",str(user)) for controller in self.players.values() for source in controller.battlefield if self.card(source.uid).land_tap_damage)
        sources.extend((controller.user_id,aura,"tap_damage",str(user)) for controller in self.players.values() for aura in controller.battlefield if aura.attached_to==permanent.uid and self.card(aura.uid).aura_tap_damage)
        if tapped_card.has_land_type("forest"):
            sources.extend((controller.user_id,source,"tap_life",str(controller.user_id)) for controller in self.players.values() if controller.user_id!=user for source in controller.battlefield if self.card(source.uid).opponent_forest_tap_life)
        triggers=[]
        for owner,source,effect,target in sources:
            uid=self.next_uid; self.next_uid+=1; card=self.card(source.uid); self.cards[uid]=card.key
            triggers.append(Spell(owner,uid,card.key,target,ability_effect=effect,source_uid=source.uid,color_override=source.color_override))
        return triggers

    def _spell_cast_triggers(self,spell):
        colors=self.spell_colors(spell); triggers=[]
        for controller_id in (self.active_user,self.opponent(self.active_user)):
            for source in self.player(controller_id).battlefield:
                card=self.card(source.uid)
                if not card.cast_life_color or card.cast_life_color not in colors: continue
                uid=self.next_uid; self.next_uid+=1; self.cards[uid]=card.key
                triggers.append(Spell(controller_id,uid,card.key,str(controller_id),ability_effect="cast_life",source_uid=source.uid,color_override=source.color_override))
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

    def _mana_payment(self,player,card,x_value=0,mana_cost=None,excluded_uids=()):
        generic,colored=self._mana_requirements(card,x_value,mana_cost)
        white_as_red=any(self.card(source.uid).white_as_red for source in player.battlefield)
        order=("W","U","B","R","G"); initial=tuple(colored.count(symbol) for symbol in order)+(generic,)
        items=[]
        for symbol,count in player.mana_pool.items():
            for number in range(count): items.append(("pool",f"{symbol}:{number}",None,((symbol,{symbol:1}),)))
        for permanent in player.battlefield:
            source=self.card(permanent.uid)
            if permanent.uid in excluded_uids: continue
            if source.produces and source.mana_amount==1 and not source.mana_activation_cost and not source.sacrifice_for_mana and not permanent.tapped and (not self.is_creature(permanent) or not permanent.sick or source.haste):
                options=tuple((symbol,self._mana_output(permanent,symbol)) for symbol in source.produces)
                items.append(("permanent",str(permanent.uid),permanent,options))

        def reduce_requirements(requirements,output):
            remaining=list(requirements); spare=0
            for index,symbol in enumerate(order):
                amount=output.get(symbol,0); used=min(remaining[index],amount); remaining[index]-=used; amount-=used
                if symbol=="W" and white_as_red:
                    red_used=min(remaining[3],amount); remaining[3]-=red_used; amount-=red_used
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
                paid_symbol=symbol if remaining.get(symbol,0) else ("W" if symbol=="R" and white_as_red and remaining.get("W",0) else symbol)
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

    def _target_for_activation(self,card,user,target,source):
        if card.activation_effect=="counter_color":
            spell=self._target_stack(target)
            if card.target_color not in self.spell_colors(spell): raise GameError(f"Target spell must be {card.target_color}.")
            return f"S:{spell.uid}"
        if card.activation_effect in ("draw_self","destroy_all_nonland","create_token","prevent_player_damage"): return str(user)
        if card.activation_effect in ("untap_self","animate_self"): return f"{user}:{source.uid}"
        if card.activation_attached:
            controller,attached=self.find_permanent(source.attached_to)
            if attached is None or not self._aura_can_attach(card,attached,source): raise GameError(f"{card.name} is not attached to a legal permanent.")
            return f"{controller.user_id}:{attached.uid}"
        if not card.activation_effect or card.activation_effect=="regenerate": return f"{user}:{source.uid}"
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
        if card.activation_effect=="unblockable" and (not self.is_creature(permanent) or self.current_stats(permanent)[0]>2): raise GameError("Target must be a creature with power 2 or less.")
        if card.activation_effect=="untap_land" and not target_card.land: raise GameError("Target must be a land.")
        if card.activation_effect=="tap_permanent" and not any(self.has_current_type(permanent,kind) for kind in ("Artifact","Creature","Land")): raise GameError("Target must be an artifact, creature, or land.")
        return f"{target_user}:{permanent.uid}"

    def can_activate(self,user,position,target=None):
        player=self.player(user)
        if not 1<=position<=len(player.battlefield): return False
        permanent=player.battlefield[position-1]; card=self.card(permanent.uid)
        activation_cost,activation_effect,activation_tap,_=self._activation_profile(permanent)
        if not (activation_cost or activation_effect): return False
        if card.animate_combat and self.phase not in ("after_attackers","after_blockers","after_first_strike"): return False
        if activation_tap and (permanent.tapped or (self.is_creature(permanent) and permanent.sick and not card.haste)): return False
        try:
            stable_target=self._target_for_activation(card,user,target,permanent)
            protected=self._stable_target_permanent(stable_target)
            if protected is not None and not card.activation_attached and activation_effect not in ("","regenerate") and self._protected_from(protected,card,self.current_colors(permanent)): return False
        except GameError: return False
        excluded=(permanent.uid,) if activation_tap else ()
        return self._mana_payment(player,card,mana_cost=activation_cost,excluded_uids=excluded) is not None

    def activate_ability(self,user,position,target=None):
        self._priority(user); player=self.player(user)
        if not 1<=position<=len(player.battlefield): raise GameError("No permanent at that battlefield position.")
        permanent=player.battlefield[position-1]; card=self.card(permanent.uid)
        activation_cost,activation_effect,activation_tap,native=self._activation_profile(permanent)
        if not (activation_cost or activation_effect): raise GameError("That permanent has no supported activated ability.")
        if card.animate_combat and self.phase not in ("after_attackers","after_blockers","after_first_strike"): raise GameError(f"{card.name} can be activated only during combat.")
        if activation_tap and permanent.tapped: raise GameError(f"{card.name} is already tapped.")
        if activation_tap and self.is_creature(permanent) and permanent.sick and not card.haste: raise GameError(f"{card.name} has summoning sickness.")
        stable_target=self._target_for_activation(card,user,target,permanent)
        protected=self._stable_target_permanent(stable_target)
        if protected is not None and not card.activation_attached and activation_effect not in ("","regenerate") and self._protected_from(protected,card,self.current_colors(permanent)): raise GameError(f"{card.name} cannot target a permanent with protection from its color.")
        excluded=(permanent.uid,) if activation_tap else ()
        payment=self._mana_payment(player,card,mana_cost=activation_cost,excluded_uids=excluded)
        if payment is None: raise GameError(f"You cannot pay {activation_cost or 'that cost'} for {card.name}.")
        sources,remaining,choices=payment; pending_triggers=[]
        for source in sources: self._tap_permanent(user,source,choices[source.uid],pending_triggers=pending_triggers)
        player.mana_pool=remaining
        if activation_tap: permanent.tapped=True
        permanent.activations_this_turn+=1
        if card.sacrifice_after_activations and permanent.activations_this_turn>=card.sacrifice_after_activations:
            permanent.sacrifice_at_end_step=True
        ability_uid=self.next_uid; self.next_uid+=1; self.cards[ability_uid]=card.key
        self.stack.append(Spell(user,ability_uid,card.key,stable_target,ability_effect=activation_effect or "self",source_uid=permanent.uid,color_override=permanent.color_override)); self.stack.extend(pending_triggers)
        self._sba(); self._life()
        self.phase_passes=0
        for item in self.stack[:-1]: item.passes=0
        if not self.finished: self.priority_user=self.opponent(user)
        ability_text=card.ability_text if native else f"{activation_cost}: Regenerate this creature (granted)"
        self.log.append(f"{user} activated {card.name}: {ability_text}.")

    def activate_mana(self,user,position,color=None):
        self._priority(user); player=self.player(user)
        if not 1<=position<=len(player.battlefield): raise GameError("No permanent at that battlefield position.")
        permanent=player.battlefield[position-1]; card=self.card(permanent.uid)
        if not card.produces: raise GameError("That permanent has no supported mana ability.")
        if permanent.tapped: raise GameError(f"{card.name} is already tapped.")
        if self.is_creature(permanent) and permanent.sick and not card.haste: raise GameError(f"{card.name} has summoning sickness.")
        symbol=(color or (card.produces[0] if len(card.produces)==1 else "")).upper()
        if symbol not in card.produces: raise GameError(f"Choose one of: {', '.join(card.produces)}.")
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
            player.battlefield.remove(permanent); self._dies(player,permanent)
        self._sba(); self._life()
        self.phase_passes=0
        for spell in self.stack: spell.passes=0
        produced=" ".join(f"{{{mana}}}"+(f"×{count}" if count>1 else "") for mana,count in output.items())
        self.log.append(f"{user} added {produced}.")

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
            if p.land_played: raise GameError("You already played a land.")
            p.hand.pop(index-1); p.battlefield.append(Permanent(uid,c.key,sick=False)); p.land_played=True; self.phase_passes=0
            self.stack.extend(self._land_event_triggers(user,"enter"))
            self.log.append(f"{user} played {c.name}."); return
        if c.kind!="Instant" and (user!=self.active_user or self.phase not in ("precombat_main","postcombat_main") or self.stack): raise GameError("Cast that during your main phase with an empty stack.")
        target=self._target_for_cast(c,user,target)
        protected=self._stable_target_permanent(target)
        if protected is not None and self._protected_from(protected,c): raise GameError(f"{c.name} cannot target a permanent with protection from its color.")
        payment=self._mana_payment(p,c,x_value)
        if payment is None: raise GameError(f"You cannot pay {c.mana_cost or c.cost} with your available mana.")
        sources,remaining,choices=payment; pending_triggers=[]
        for permanent in sources: self._tap_permanent(user,permanent,choices[permanent.uid],pending_triggers=pending_triggers)
        p.mana_pool=remaining
        p.hand.pop(index-1); self.phase_passes=0
        for spell in self.stack: spell.passes=0
        spell=Spell(user,uid,c.key,target,x_value=x_value)
        self.stack.append(spell); self.stack.extend(pending_triggers); self.stack.extend(self._spell_cast_triggers(spell)); self._sba(); self._life()
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

    def _target_for_cast(self,c,user,target):
        if c.aura_target_types:
            if not target or ":" not in target: raise GameError("Aura target must be USER_ID:POSITION.")
            try: target_user,pos=(int(x) for x in target.split(":"))
            except (TypeError,ValueError) as error: raise GameError("Aura target must be USER_ID:POSITION.") from error
            battlefield=self.player(target_user).battlefield
            if not 1<=pos<=len(battlefield): raise GameError("No permanent at that battlefield position.")
            permanent=battlefield[pos-1]
            if not self._aura_can_attach(c,permanent): raise GameError(f"{c.name} cannot enchant that permanent.")
            return f"{target_user}:{permanent.uid}"
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
        if c.effect in ("counter_spell","elemental_blast"):
            if target and target.upper().startswith("S:"):
                spell=self._target_stack(target); target_card=self.card(spell.uid)
                if c.target_color and c.target_color not in self.spell_colors(spell): raise GameError(f"Target spell must be {c.target_color}.")
                return f"S:{spell.uid}"
            if c.effect=="counter_spell": raise GameError("Counterspell requires an S:POSITION stack target.")
            if not target or ":" not in target: raise GameError("Elemental Blast target must be S:POSITION or USER_ID:POSITION.")
            try: target_user,pos=(int(x) for x in target.split(":"))
            except (TypeError,ValueError) as e: raise GameError("Elemental Blast target must be S:POSITION or USER_ID:POSITION.") from e
            battlefield=self.player(target_user).battlefield
            if not 1<=pos<=len(battlefield): raise GameError("No permanent at that battlefield position.")
            permanent=battlefield[pos-1]; target_card=self.card(permanent.uid)
            if c.target_color not in self.current_colors(permanent): raise GameError(f"Target permanent must be {c.target_color}.")
            return f"{target_user}:{permanent.uid}"
        if c.effect=="set_color":
            if target and target.upper().startswith("S:"):
                spell=self._target_stack(target); return f"S:{spell.uid}"
            if not target or ":" not in target: raise GameError("Color-change target must be S:POSITION or USER_ID:POSITION.")
            try: target_user,pos=(int(x) for x in target.split(":"))
            except (TypeError,ValueError) as e: raise GameError("Color-change target must be S:POSITION or USER_ID:POSITION.") from e
            battlefield=self.player(target_user).battlefield
            if not 1<=pos<=len(battlefield): raise GameError("No permanent at that battlefield position.")
            return f"{target_user}:{battlefield[pos-1].uid}"
        if c.effect=="damage":
            try: target_user=int(target) if target is not None else self.opponent(user)
            except (TypeError,ValueError) as e: raise GameError("Target must be a player ID.") from e
            self.player(target_user); return str(target_user)
        if c.effect in ("damage_any","damage_x_exile"):
            if target and ":" in target:
                target_user,permanent=self._target_creature(target,"Target must be a player ID or USER_ID:POSITION.")
                return f"{target_user}:{permanent.uid}"
            try: target_user=int(target)
            except (TypeError,ValueError) as e: raise GameError("Target must be a player ID or USER_ID:POSITION.") from e
            self.player(target_user); return str(target_user)
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
        if c.effect in ("pump","pump_blocking","pump_power_x"):
            target_user,permanent=self._target_creature(target)
            if c.effect=="pump_blocking" and permanent.uid not in self.blocks.values():
                raise GameError(f"{c.name} must target a blocking creature.")
            return f"{target_user}:{permanent.uid}"
        if c.effect in ("draw_target","draw_target_x","life_target_x","discard_random_x","mana_short"):
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
                if s.ability_effect in ("cast_life","death_life","upkeep_untap","upkeep_cost"):
                    s.decision_pending=True; self.priority_user=s.owner; return
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

    def trigger_cost(self,trigger):
        card=self.card(trigger.uid)
        if trigger.ability_effect=="upkeep_untap": return card.upkeep_untap_cost
        if trigger.ability_effect=="upkeep_cost": return card.upkeep_cost
        return "{1}"

    def choose_trigger(self,user,pay):
        if self.finished: raise GameError("Game is over.")
        if not self.stack or not self.stack[-1].decision_pending or self.stack[-1].owner!=user:
            raise GameError("You do not have a trigger choice to make.")
        trigger=self.stack.pop(); card=self.card(trigger.uid); pending=[]; cost=self.trigger_cost(trigger)
        if pay:
            player=self.player(user); payment=self._mana_payment(player,card,mana_cost=cost)
            if payment is None:
                self.stack.append(trigger); raise GameError(f"You cannot pay {cost} for this trigger.")
            sources,remaining,choices=payment
            for source in sources: self._tap_permanent(user,source,choices[source.uid],pending_triggers=pending)
            player.mana_pool=remaining
            if trigger.ability_effect in ("cast_life","death_life"):
                player.life+=1; result=" and gained 1 life"
            elif trigger.ability_effect=="upkeep_untap":
                _,source=self.find_permanent(trigger.source_uid)
                if source is not None: source.tapped=False
                result=" and untapped it" if source is not None else ""
            else: result=""
            self.log.append(f"{user} paid {cost} for {card.name}{result}.")
        else:
            self.log.append(f"{user} declined {card.name}.")
            if trigger.ability_effect=="upkeep_cost":
                source_controller,source=self.find_permanent(trigger.source_uid)
                if card.upkeep_unpaid_effect=="sacrifice" and source is not None:
                    self._remove_from_combat(source.uid); source_controller.battlefield.remove(source); self._dies(source_controller,source)
                elif card.upkeep_unpaid_effect=="damage": self._damage_player(user,card.upkeep_unpaid_damage)
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
                self._combat_damage(first_strike=True)
                if self.finished: return
                self.phase="after_first_strike"
            else:
                self._combat_damage(first_strike=False)
                if self.finished: return
                if self.stack: self.phase="after_combat_damage"
                else:
                    has_end_triggers=self._end_combat(); self.phase="end_combat" if has_end_triggers else "postcombat_main"
        elif self.phase=="after_first_strike":
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
            next_user=self.extra_turns.pop(0) if self.extra_turns else self.opponent(self.active_user)
            self.active_index=self.order.index(next_user); self._start_turn(); return
        else: raise GameError("Complete combat first.")
        self.priority_user=self.active_user

    def declare_attackers(self,user,positions):
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
        self.attackers=chosen; self.blocks={}; self.blocked_attackers=[]; self.trample_assignments={}; self.phase_passes=0
        for x in list(p.battlefield):
            if x.uid in chosen and "vigilance" not in self.current_keywords(x): x.tapped=True
        self._sba(); self._life()
        self.phase="after_attackers" if self.attackers else "postcombat_main"
        self.priority_user=user

    def declare_blockers(self,user,assignments):
        if self.phase!="blockers" or user!=self.opponent(self.active_user): raise GameError("You cannot block now.")
        p=self.player(user); used=set(); self.blocks={}; self.blocked_attackers=[]
        for a,b in assignments.items():
            if not 1<=a<=len(self.attackers) or not 1<=b<=len(p.battlefield): raise GameError("Bad combat position.")
            x=p.battlefield[b-1]; attacker_uid=self.attackers[a-1]
            if x.uid in used: raise GameError("Invalid blocker.")
            legal,reason=self.can_block(attacker_uid,x.uid)
            if not legal: raise GameError(reason)
            used.add(x.uid); self.blocks[attacker_uid]=x.uid; self.blocked_attackers.append(attacker_uid)
        self._schedule_combat_destroy_triggers()
        self.phase="after_blockers"; self.phase_passes=0; self.priority_user=self.active_user

    def _schedule_combat_destroy_triggers(self):
        active=self.player(self.active_user); defending=self.player(self.opponent(self.active_user)); pending=[]
        for attacker_uid,blocker_uid in self.blocks.items():
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

    def assign_trample(self,user,position,damage_to_blocker):
        self._priority(user)
        if user!=self.active_user or self.phase not in ("after_blockers","after_first_strike") or self.stack:
            raise GameError("Set trample assignment after blockers with an empty stack.")
        battlefield=self.player(user).battlefield
        if not 1<=position<=len(battlefield): raise GameError("No permanent at that battlefield position.")
        attacker=battlefield[position-1]
        if attacker.uid not in self.attackers or "trample" not in self.current_keywords(attacker): raise GameError("Choose an attacking creature with trample.")
        blocker_uid=self.blocks.get(attacker.uid); _,blocker=self.find_permanent(blocker_uid)
        if blocker is None: raise GameError("That attacker has no blocker to assign damage to.")
        power=max(0,self.current_stats(attacker)[0]); lethal=max(0,self.current_stats(blocker)[1]-blocker.damage)
        try: damage_to_blocker=int(damage_to_blocker)
        except (TypeError,ValueError) as e: raise GameError("Damage to blocker must be a whole number.") from e
        minimum=min(power,lethal)
        if not minimum<=damage_to_blocker<=power: raise GameError(f"Assign between {minimum} and {power} damage to the blocker.")
        self.trample_assignments[attacker.uid]=damage_to_blocker; self.phase_passes=0
        self.log.append(f"{user} assigned {damage_to_blocker} damage from {self.card(attacker.uid).name} to its blocker.")

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
        if "fear" in attacker_keywords and not (blocker.has_type("Artifact") or "B" in self.current_colors(blocker_perm)):
            return False,f"{blocker.name} cannot block a creature with fear."
        if set(self.current_colors(blocker_perm)) & self.current_protections(attacker_perm): return False,f"{attacker.name} has protection from {blocker.name}."
        if any(self.card(aura.uid).aura_blocked_except_wall for aura in self.attached_auras(attacker_perm)) and not self._has_subtype(blocker,"Wall"):
            return False,f"{attacker.name} can only be blocked by Walls."
        if "flying" in attacker_keywords and not ({"flying","reach"} & blocker_keywords):
            return False,f"{blocker.name} cannot block a creature with flying."
        for land_type in ("plains","island","swamp","mountain","forest"):
            if f"{land_type}walk" in attacker_keywords and any(self.card(x.uid).has_land_type(land_type) for x in defender.battlefield):
                return False,f"{attacker.name} can't be blocked while the defender controls a {land_type.title()}."
        power=self.current_stats(attacker_perm)[0]
        if blocker.max_block_power is not None and power>blocker.max_block_power:
            return False,f"{blocker.name} can't block a creature with power {power}."
        return True,""

    def _combat_has_first_strike(self):
        combatants=set(self.attackers)|set(self.blocks.values())
        return any(
            "first_strike" in self.current_keywords(x)
            for player in self.players.values() for x in player.battlefield if x.uid in combatants
        )

    def _damage_permanent(self,permanent,amount,source=None,colors=None):
        amount=max(0,int(amount))
        if source is not None and self._protected_from(permanent,source,colors): return 0
        prevented=min(amount,permanent.damage_prevention); permanent.damage_prevention-=prevented; permanent.damage+=amount-prevented
        return amount-prevented

    def _discard_random(self,player,amount):
        chosen=random.SystemRandom().sample(player.hand,min(max(0,int(amount)),len(player.hand)))
        for uid in chosen: player.hand.remove(uid); player.graveyard.append(uid)
        return chosen

    def _damage_player(self,user,amount,source=None,source_controller=None):
        player=self.player(user); amount=max(0,int(amount)); prevented=min(amount,player.damage_prevention)
        player.damage_prevention-=prevented; dealt=amount-prevented; player.life-=dealt
        if dealt and source is not None and source_controller is not None and user==self.opponent(source_controller):
            source_card=self.card(source.uid)
            if source_card.opponent_damage_discard_random:
                uid=self.next_uid; self.next_uid+=1; self.cards[uid]=source_card.key
                self.stack.append(Spell(source_controller,uid,source_card.key,str(user),ability_effect="opponent_damage_discard_random",source_uid=source.uid,color_override=source.color_override))
        return dealt

    def _combat_damage(self,first_strike):
        atk=self.players[self.active_user]; dfn=self.players[self.opponent(self.active_user)]
        if self.prevent_combat_damage:
            self.log.append("Combat damage was prevented."); self._sba(); self._life(); return
        for uid in self.attackers:
            a=next((x for x in atk.battlefield if x.uid==uid),None)
            if a is None: continue
            block_uid=self.blocks.get(uid); b=next((x for x in dfn.battlefield if x.uid==block_uid),None)
            attacker_strikes=("first_strike" in self.current_keywords(a))==first_strike
            blocker_strikes=b is not None and (("first_strike" in self.current_keywords(b))==first_strike)
            if attacker_strikes:
                power=max(0,self.current_stats(a)[0])
                trample="trample" in self.current_keywords(a)
                if b is None:
                    if uid not in self.blocked_attackers or trample: self._damage_player(dfn.user_id,power,a,atk.user_id)
                else:
                    lethal=max(0,self.current_stats(b)[1]-b.damage)
                    chosen=self.trample_assignments.get(uid,lethal)
                    assigned=min(power,max(lethal,chosen)) if trample else power
                    self._damage_permanent(b,assigned,self.card(a.uid),self.current_colors(a))
                    if trample: self._damage_player(dfn.user_id,max(0,power-assigned),a,atk.user_id)
            if blocker_strikes: self._damage_permanent(a,max(0,self.current_stats(b)[0]),self.card(b.uid),self.current_colors(b))
        self._sba(); self._life()

    def _end_combat(self):
        pending=self.end_combat_destroys; self.end_combat_destroys=[]
        self.attackers=[]; self.blocks={}; self.blocked_attackers=[]; self.trample_assignments={}
        for player in self.players.values():
            for permanent in player.battlefield: permanent.animated_until_end_combat=False
        self._sba(); self.stack.extend(pending)
        return bool(pending)

    def _death_trigger_sources(self):
        return [(controller.user_id,source.uid,self.card(source.uid).key,source.color_override) for controller in self.players.values() for source in controller.battlefield if self.card(source.uid).death_life]

    def _death_triggers(self,dead_uid,batch_id,sources=None):
        triggers=[]
        by_owner={owner:[] for owner in self.order}
        for owner,source_uid,key,color_override in (sources if sources is not None else self._death_trigger_sources()):
            uid=self.next_uid; self.next_uid+=1; self.cards[uid]=key
            by_owner[owner].append(Spell(owner,uid,key,str(dead_uid),ability_effect="death_life",source_uid=source_uid,color_override=color_override,batch_id=batch_id))
        for owner in (self.active_user,self.opponent(self.active_user)): triggers.extend(by_owner.get(owner,()))
        start=len(self.stack)
        while start and self.stack[start-1].ability_effect=="death_life" and self.stack[start-1].batch_id==batch_id: start-=1
        merged=self.stack[start:]+triggers
        merged.sort(key=lambda trigger:0 if trigger.owner==self.active_user else 1)
        self.stack[start:]=merged

    def _dies(self,controller,permanent,batch_id=None,death_sources=None):
        dies=not permanent.exile_on_death
        token=self.is_token(permanent.uid)
        if not token: (controller.graveyard if dies else controller.exile).append(permanent.uid)
        if dies and self.is_creature(permanent):
            self._death_triggers(permanent.uid,self.next_uid if batch_id is None else batch_id,death_sources)
        if token: self.cards.pop(permanent.uid,None)

    def _remove_from_combat(self,uid):
        if uid in self.attackers:
            self.attackers.remove(uid); self.blocks.pop(uid,None); self.trample_assignments.pop(uid,None)
            if uid in self.blocked_attackers: self.blocked_attackers.remove(uid)
        for attacker,blocker in list(self.blocks.items()):
            if blocker==uid: self.blocks.pop(attacker)

    def _destroy(self,controller,permanent,allow_regeneration=True,trigger_batch=None,death_sources=None):
        if allow_regeneration and permanent.regeneration_shields:
            permanent.regeneration_shields-=1; permanent.tapped=True; permanent.damage=0
            self._remove_from_combat(permanent.uid)
            self.log.append(f"{self.card(permanent.uid).name} regenerated.")
            return False
        self._remove_from_combat(permanent.uid)
        was_land=self.card(permanent.uid).land
        if permanent in controller.battlefield: controller.battlefield.remove(permanent)
        self._dies(controller,permanent,trigger_batch,death_sources)
        if was_land and permanent.uid in controller.graveyard: self.stack.extend(self._land_event_triggers(controller.user_id,"grave"))
        return True

    def _resolve_ability(self,s):
        card=CARDS[s.key]; effect=s.ability_effect
        def target_permanent():
            if not s.target or ":" not in s.target or s.target.upper().startswith("S:"): return None,None
            user,uid=(int(x) for x in s.target.split(":")); controller=self.player(user)
            return controller,next((x for x in controller.battlefield if x.uid==uid),None)
        def fizzle(reason):
            self.cards.pop(s.uid,None); self.log.append(f"{card.name} ability fizzled because {reason}.")
        controller,target=target_permanent()
        target_card=self.card(target.uid) if target is not None else None
        if target is not None and effect not in ("self","regenerate","end_combat_destroy") and self._protected_from(target,card,self.ability_source_colors(s)): fizzle("its target gained protection"); return
        if effect=="end_combat_destroy":
            if target is None: fizzle("its combatant was gone"); return
            self._destroy(controller,target)
        elif effect=="self":
            if target is None: fizzle("its source was gone"); return
            target.power_bonus+=card.activated_power; target.toughness_bonus+=card.activated_toughness
            if card.activated_keyword and card.activated_keyword not in target.temporary_keywords: target.temporary_keywords.append(card.activated_keyword)
        elif effect=="regenerate":
            if target is None: fizzle("its source was gone"); return
            target.regeneration_shields+=1
        elif effect=="counter_color":
            uid=int(s.target.split(":",1)[1]); spell=next((item for item in self.stack if item.uid==uid and not item.ability_effect),None)
            if spell is None or card.target_color not in self.spell_colors(spell): fizzle("its target was gone or changed color"); return
            self.stack.remove(spell); self.player(spell.owner).graveyard.append(spell.uid)
            self.log.append(f"{card.name} countered {self.card(spell.uid).name}.")
        elif effect=="tap_damage":
            self._damage_player(int(s.target),card.land_tap_damage or card.aura_tap_damage)
        elif effect=="tap_life":
            self.player(int(s.target)).life+=card.opponent_forest_tap_life
        elif effect=="land_event_damage":
            self._damage_player(int(s.target),card.land_enter_damage or card.land_grave_damage)
        elif effect=="upkeep_damage":
            self._damage_player(int(s.target),card.upkeep_each_damage)
        elif effect=="upkeep_land_type_damage":
            target_player=self.player(int(s.target)); amount=sum(self.card(permanent.uid).has_land_type(card.upkeep_land_type_damage) for permanent in target_player.battlefield)
            self._damage_player(target_player.user_id,amount)
        elif effect=="aura_upkeep_damage":
            self._damage_player(int(s.target),card.aura_upkeep_damage)
        elif effect=="upkeep_hand_damage":
            target_player=self.player(int(s.target)); self._damage_player(target_player.user_id,max(0,len(target_player.hand)-4))
        elif effect=="draw_step_draw":
            self._draw(self.player(int(s.target)),card.draw_step_extra)
        elif effect=="draw_tapped_damage":
            _,source=self.find_permanent(s.source_uid)
            if source is not None and source.tapped: self._damage_player(int(s.target),card.draw_tapped_damage)
        elif effect=="opponent_damage_discard_random":
            self._discard_random(self.player(int(s.target)),1)
        elif effect=="draw_self":
            self._draw(self.player(s.owner),1)
        elif effect=="untap_self":
            if target is None: fizzle("its source was gone"); return
            target.tapped=False
        elif effect=="animate_self":
            if target is None: fizzle("its source was gone"); return
            target.animated_until_end_combat=True
        elif effect=="create_token":
            uid=self.next_uid; self.next_uid+=1; self.cards[uid]=card.creates_token
            self.player(s.owner).battlefield.append(Permanent(uid,card.creates_token))
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
        elif effect=="damage_any":
            if ":" in (s.target or ""):
                if target_card is None or not self.is_creature(target): fizzle("its target was gone or illegal"); return
                self._damage_permanent(target,card.activation_amount)
            else: self._damage_player(int(s.target),card.activation_amount)
            self._damage_player(s.owner,card.activation_self_damage)
        elif effect in ("destroy_black_permanent","destroy_tapped_creature","destroy_wall"):
            legal=target_card is not None
            if effect=="destroy_black_permanent": legal=legal and "B" in self.current_colors(target)
            elif effect=="destroy_tapped_creature": legal=legal and self.is_creature(target) and target.tapped
            else: legal=legal and "Wall" in target_card.type_line.split(" — ",1)[-1].split()
            if not legal: fizzle("its target was gone or illegal"); return
            self._destroy(controller,target)
        elif effect=="unblockable":
            if target_card is None or not self.is_creature(target) or self.current_stats(target)[0]>2: fizzle("its target was gone or illegal"); return
            if "unblockable" not in target.temporary_keywords: target.temporary_keywords.append("unblockable")
        elif effect=="untap_land":
            if target_card is None or not target_card.land: fizzle("its target was gone or illegal"); return
            target.tapped=False
        elif effect=="tap_permanent":
            if target_card is None or not any(self.has_current_type(target,kind) for kind in ("Artifact","Creature","Land")): fizzle("its target was gone or illegal"); return
            target.tapped=True
        else:
            fizzle("the effect is unsupported"); return
        self.cards.pop(s.uid,None); self.log.append(f"{card.name} ability resolved.")
        self._sba(); self._life()

    def _resolve(self,s):
        if s.ability_effect: self._resolve_ability(s); return
        p=self.players[s.owner]; c=CARDS[s.key]
        protected=self._stable_target_permanent(s.target)
        if protected is not None and self._protected_from(protected,c,self.spell_colors(s)):
            p.graveyard.append(s.uid); self.log.append(f"{c.name} fizzled because its target gained protection."); return
        if c.aura_target_types:
            user,uid=(int(x) for x in s.target.split(":")); target=next((x for x in self.player(user).battlefield if x.uid==uid),None)
            if target is None or not self._aura_can_attach(c,target,colors=self.spell_colors(s)):
                p.graveyard.append(s.uid); self.log.append(f"{c.name} fizzled because its target was gone or illegal."); return
            p.battlefield.append(Permanent(s.uid,c.key,sick=False,attached_to=target.uid,color_override=s.color_override))
        elif c.kind in ("Creature","Artifact","Enchantment"): p.battlefield.append(Permanent(s.uid,c.key,tapped=c.enters_tapped,color_override=s.color_override))
        elif c.effect in ("counter_spell","elemental_blast"):
            if s.target.startswith("S:"):
                target_uid=int(s.target.split(":",1)[1]); target=next((spell for spell in self.stack if spell.uid==target_uid),None)
                target_card=self.card(target.uid) if target is not None else None
                legal=target_card is not None and not target.ability_effect and (not c.target_color or c.target_color in self.spell_colors(target))
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
                target.color_override=c.color_change
            p.graveyard.append(s.uid)
        elif c.effect=="draw": self._draw(p,c.amount); p.graveyard.append(s.uid)
        elif c.effect in ("draw_target","draw_target_x"): self._draw(self.player(int(s.target)),s.x_value if c.effect=="draw_target_x" else c.amount); p.graveyard.append(s.uid)
        elif c.effect=="life_target_x": self.player(int(s.target)).life+=s.x_value; p.graveyard.append(s.uid)
        elif c.effect=="discard_random_x": self._discard_random(self.player(int(s.target)),s.x_value); p.graveyard.append(s.uid)
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
        elif c.effect=="prevent_combat_damage": self.prevent_combat_damage=True; p.graveyard.append(s.uid)
        elif c.effect=="extra_turn": self.extra_turns.insert(0,s.owner); p.graveyard.append(s.uid)
        elif c.effect=="mana_short":
            target_player=self.player(int(s.target)); pending=[]
            for permanent in target_player.battlefield:
                if self.card(permanent.uid).land: self._tap_permanent(target_player.user_id,permanent,pending_triggers=pending)
            target_player.mana_pool.clear(); p.graveyard.append(s.uid); self.stack.extend(pending)
        elif c.effect in ("damage","damage_any","damage_x_exile"):
            amount=s.x_value if c.effect=="damage_x_exile" else c.amount
            if ":" in (s.target or ""):
                user,uid=(int(x) for x in s.target.split(":")); target=next((x for x in self.player(user).battlefield if x.uid==uid),None)
                if target is None:
                    p.graveyard.append(s.uid); self.log.append(f"{c.name} fizzled because its target was gone."); return
                self._damage_permanent(target,amount)
                if c.effect=="damage_x_exile": target.exile_on_death=True; target.cant_regenerate=True
            else: self._damage_player(int(s.target or self.opponent(s.owner)),amount)
            self._damage_player(s.owner,c.self_damage); p.graveyard.append(s.uid)
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
        elif c.effect=="destroy_all_enchantments":
            for controller in self.players.values():
                for permanent in list(controller.battlefield):
                    if self.card(permanent.uid).has_type("Enchantment"): self._destroy(controller,permanent)
            p.graveyard.append(s.uid)
        elif c.effect in ("pump","pump_blocking","pump_power_x"):
            user,uid=(int(x) for x in s.target.split(":")); target=next((x for x in self.player(user).battlefield if x.uid==uid),None)
            if target is None:
                p.graveyard.append(s.uid); self.log.append(f"{c.name} fizzled because its target was gone."); return
            if c.effect=="pump_power_x": target.power_bonus+=s.x_value
            else: target.bonus+=c.amount
            p.graveyard.append(s.uid)
        elif c.effect=="return_creature_hand":
            user,uid=(int(x) for x in s.target.split(":")); controller=self.player(user)
            target=next((x for x in controller.battlefield if x.uid==uid),None)
            if target is None or not self.is_creature(target):
                p.graveyard.append(s.uid); self.log.append(f"{c.name} fizzled because its target was gone."); return
            controller.battlefield.remove(target)
            if self.is_token(target.uid): self.cards.pop(target.uid,None)
            else: controller.hand.append(target.uid)
            self._remove_from_combat(target.uid)
            p.graveyard.append(s.uid)
        elif c.effect in ("return_grave_creature_hand","return_grave_card_hand","reanimate_creature"):
            uid=int(s.target); creature_only=c.effect!="return_grave_card_hand"
            if uid not in p.graveyard or (creature_only and not self.card(uid).creature):
                p.graveyard.append(s.uid); self.log.append(f"{c.name} fizzled because its target was gone or illegal."); return
            p.graveyard.remove(uid)
            if c.effect=="reanimate_creature": p.battlefield.append(Permanent(uid,self.cards[uid]))
            else: p.hand.append(uid)
            p.graveyard.append(s.uid)
        elif c.effect in ("destroy_creature","exile_creature_life"):
            user,uid=(int(x) for x in s.target.split(":")); controller=self.player(user)
            target=next((x for x in controller.battlefield if x.uid==uid),None)
            target_card=self.card(target.uid) if target is not None else None
            legal=target_card is not None and self.is_creature(target) and not (c.target_nonartifact and "Artifact" in target_card.type_line) and not (c.target_nonblack and "B" in self.current_colors(target))
            if not legal:
                p.graveyard.append(s.uid); self.log.append(f"{c.name} fizzled because its target was gone or illegal."); return
            life_gain=max(0,self.current_stats(target)[0]) if c.effect=="exile_creature_life" else 0
            if c.effect=="exile_creature_life":
                self._remove_from_combat(target.uid); controller.battlefield.remove(target)
                if self.is_token(target.uid): self.cards.pop(target.uid,None)
                else: controller.exile.append(target.uid)
                controller.life+=life_gain
            else: self._destroy(controller,target,allow_regeneration=False)
            p.graveyard.append(s.uid)
        elif c.effect in ("earthquake_x","hurricane_x"):
            for controller in self.players.values():
                self._damage_player(controller.user_id,s.x_value)
                for permanent in controller.battlefield:
                    card=self.card(permanent.uid)
                    keywords=self.current_keywords(permanent)
                    affected=self.is_creature(permanent) and ((c.effect=="earthquake_x" and "flying" not in keywords) or (c.effect=="hurricane_x" and "flying" in keywords))
                    if affected: self._damage_permanent(permanent,s.x_value,c,self.spell_colors(s))
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
                destroyed=[x for x in controller.battlefield if self.card(x.uid).land and (c.effect=="destroy_all_lands" or self.card(x.uid).has_land_type(c.land_type))]
                for permanent in destroyed: self._destroy(controller,permanent,allow_regeneration=False)
            p.graveyard.append(s.uid)
        self.log.append(f"{c.name} resolved."); self._sba(); self._life()

    def _perm(self,p,uid):
        return next(x for x in p.battlefield if x.uid==uid)
    def _sba(self):
        while True:
            affected=False; trigger_batch=self.next_uid; death_sources=self._death_trigger_sources()
            all_permanents={permanent.uid:permanent for player in self.players.values() for permanent in player.battlefield}
            for controller in self.players.values():
                for permanent in list(controller.battlefield):
                    card=self.card(permanent.uid)
                    if card.aura_target_types:
                        target=all_permanents.get(permanent.attached_to)
                        if target is None or not self._aura_can_attach(card,target,permanent):
                            controller.battlefield.remove(permanent); controller.graveyard.append(permanent.uid); affected=True
                        continue
                    if not self.is_creature(permanent): continue
                    toughness=self.current_stats(permanent)[1]
                    if toughness<=0:
                        self._remove_from_combat(permanent.uid); controller.battlefield.remove(permanent); self._dies(controller,permanent,trigger_batch,death_sources); affected=True
                    elif permanent.damage>=toughness:
                        self._destroy(controller,permanent,allow_regeneration=not permanent.cant_regenerate,trigger_batch=trigger_batch,death_sources=death_sources); affected=True
            if not affected: return
    def _cleanup(self):
        self.prevent_combat_damage=False
        for p in self.players.values():
            p.damage_prevention=0
            for x in p.battlefield:
                x.damage=x.bonus=x.power_bonus=x.toughness_bonus=x.activations_this_turn=0
                x.exile_on_death=False; x.cant_regenerate=False; x.regeneration_shields=0; x.damage_prevention=0; x.temporary_keywords=[]
    def _begin_end_step(self):
        self.end_step_sacrifices=[x.uid for p in self.players.values() for x in p.battlefield if x.sacrifice_at_end_step]
        if self.end_step_sacrifices:
            names=", ".join(self.card(uid).name for uid in self.end_step_sacrifices)
            self.log.append(f"End-step sacrifice trigger pending for {names}; players may respond.")
        for p in self.players.values():
            for permanent in p.battlefield:
                if permanent.uid in self.end_step_sacrifices: permanent.sacrifice_at_end_step=False
    def _resolve_end_step_sacrifices(self):
        pending=set(self.end_step_sacrifices); self.end_step_sacrifices=[]
        trigger_batch=self.next_uid; death_sources=self._death_trigger_sources()
        for p in self.players.values():
            sacrificed=[x for x in p.battlefield if x.uid in pending]
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
        if self.stack and self.stack[-1].decision_pending: raise GameError("The pending trigger controller must pay or decline first.")
        if self.priority_user!=user: raise GameError("You do not have priority.")
    def _active(self,user):
        if self.finished: raise GameError("Game is over.")
        if user!=self.active_user or self.priority_user!=user: raise GameError("It is not your action window.")

    def to_raw(self):
        return {"game_id":self.game_id,"order":self.order,"players":{str(k):{**asdict(v),"battlefield":[asdict(x) for x in v.battlefield]} for k,v in self.players.items()},"cards":self.cards,"next_uid":self.next_uid,"active_index":self.active_index,"phase":self.phase,"phase_passes":self.phase_passes,"turn":self.turn,"stack":[asdict(x) for x in self.stack],"end_step_sacrifices":self.end_step_sacrifices,"end_combat_destroys":[asdict(x) for x in self.end_combat_destroys],"extra_turns":self.extra_turns,"skip_draw_step":self.skip_draw_step,"prevent_combat_damage":self.prevent_combat_damage,"attackers":self.attackers,"blocks":self.blocks,"blocked_attackers":self.blocked_attackers,"trample_assignments":self.trample_assignments,"priority_user":self.priority_user,"winner":self.winner,"finished_reason":self.finished_reason,"ai_user":self.ai_user,"ai_difficulty":self.ai_difficulty,"log":self.log[-100:],"history":self.history,"created_at":self.created_at,"updated_at":self.updated_at}
    @classmethod
    def from_raw(cls,r):
        g=cls.__new__(cls); g.game_id=int(r["game_id"]); g.order=[int(x) for x in r["order"]]
        g.players={}
        for k,v in r["players"].items():
            d=dict(v); d.setdefault("mana_pool",{}); d.setdefault("exile",[]); d.setdefault("damage_prevention",0); d["mana_pool"]={str(symbol):int(count) for symbol,count in d["mana_pool"].items()}; d["battlefield"]=[Permanent(**({**x,"damage_prevention":x.get("damage_prevention",0)})) for x in d["battlefield"]]; g.players[int(k)]=Player(**d)
        g.cards={int(k):v for k,v in r["cards"].items()}; g.next_uid=int(r["next_uid"]); g.active_index=int(r["active_index"]); g.phase=r["phase"]; g.phase_passes=int(r.get("phase_passes",0)); g.turn=int(r["turn"]); g.stack=[Spell(**x) for x in r["stack"]]; g.end_step_sacrifices=[int(x) for x in r.get("end_step_sacrifices",[])]; g.end_combat_destroys=[Spell(**x) for x in r.get("end_combat_destroys",[])]; g.extra_turns=[int(x) for x in r.get("extra_turns",[])]; g.skip_draw_step=bool(r.get("skip_draw_step",False)); g.prevent_combat_damage=bool(r.get("prevent_combat_damage",False)); g.attackers=[int(x) for x in r["attackers"]]; g.blocks={int(k):int(v) for k,v in r["blocks"].items()}; g.blocked_attackers=[int(x) for x in r.get("blocked_attackers",g.blocks.keys())]; g.trample_assignments={int(k):int(v) for k,v in r.get("trample_assignments",{}).items()}; g.priority_user=r["priority_user"]; g.winner=r["winner"]; g.finished_reason=r["finished_reason"]; g.ai_user=int(r["ai_user"]) if r.get("ai_user") is not None else None; g.ai_difficulty=r.get("ai_difficulty"); g.log=list(r["log"]); g.history=list(r.get("history",[])); g.created_at=int(r.get("created_at",time.time())); g.updated_at=int(r.get("updated_at",g.created_at))
        return g
