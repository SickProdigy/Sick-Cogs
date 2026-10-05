import random
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional
from .cards import CARDS, starter

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
    exile_on_death: bool = False

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

@dataclass
class Spell:
    owner: int
    uid: int
    key: str
    target: Optional[str] = None
    passes: int = 0
    x_value: int = 0

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
    def card(self,uid): return CARDS[self.cards[uid]]
    def hand(self,user): return [self.card(x) for x in self.player(user).hand]
    def characteristic_stats(self,user,card,entering=False):
        player=self.player(user)
        if card.characteristic_pt=="swamps":
            value=sum(self.card(x.uid).has_land_type("swamp") for x in player.battlefield)
        elif card.characteristic_pt=="plague_rats":
            value=sum(self.card(x.uid).name=="Plague Rats" for p in self.players.values() for x in p.battlefield)+(1 if entering else 0)
        elif card.characteristic_pt=="non_wall_creatures":
            value=sum(self.card(x.uid).creature and "Wall" not in self.card(x.uid).type_line for x in player.battlefield)+(1 if entering else 0)
        else:
            return card.power,card.toughness
        return value,value
    def current_stats(self,permanent):
        owner=next((p.user_id for p in self.players.values() if permanent in p.battlefield),None)
        if owner is None: raise GameError("Permanent is not on the battlefield.")
        power,toughness=self.characteristic_stats(owner,self.card(permanent.uid))
        return power+permanent.bonus+permanent.power_bonus,toughness+permanent.bonus
    def projected_stats(self,user,card):
        return self.characteristic_stats(user,card,entering=bool(card.characteristic_pt))

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

    def _start_turn(self,first=False):
        self.turn+=1; p=self.players[self.active_user]; p.land_played=False
        for x in p.battlefield: x.tapped=False; x.sick=False
        self._cleanup()
        if not(first and self.turn==1):
            self._draw(p)
            if self.finished: return
        self.phase_passes=0; self.phase="precombat_main"; self.priority_user=self.active_user
        self.log.append(f"Turn {self.turn}: {self.active_user}.")

    @staticmethod
    def _mana_requirements(card,x_value=0):
        symbols=re.findall(r"\{([^}]+)\}",card.mana_cost or "")
        generic=0; colored=[]
        for symbol in symbols:
            if symbol.isdigit(): generic+=int(symbol)
            elif symbol=="X": generic+=x_value
            elif symbol in {"W","U","B","R","G"}: colored.append(symbol)
            else: raise GameError(f"{card.name} uses an unsupported mana symbol: {{{symbol}}}.")
        return generic,colored

    def _mana_payment(self,player,card,x_value=0):
        generic,colored=self._mana_requirements(card,x_value)
        sources=[]
        for symbol,count in player.mana_pool.items():
            sources.extend(("pool",f"{symbol}:{number}",(symbol,),None) for number in range(count))
        for permanent in player.battlefield:
            source=self.card(permanent.uid)
            if source.produces and source.mana_amount==1 and not source.sacrifice_for_mana and not permanent.tapped and (not source.creature or not permanent.sick or source.haste):
                sources.append(("permanent",str(permanent.uid),source.produces,permanent))

        def assign(symbols,remaining,selected):
            if not symbols: return selected,remaining
            symbol=min(symbols,key=lambda item:sum(item in source[2] for source in remaining))
            rest_symbols=list(symbols); rest_symbols.remove(symbol)
            choices=sorted((source for source in remaining if symbol in source[2]),key=lambda source:(len(source[2]),source[0]!="pool"))
            for source in choices:
                rest=[item for item in remaining if item[0:2]!=source[0:2]]
                result=assign(rest_symbols,rest,selected+[source])
                if result is not None: return result
            return None

        result=assign(colored,sources,[])
        if result is None: return None
        selected,remaining=result
        if len(remaining)<generic: return None
        selected+=sorted(remaining,key=lambda source:source[0]!="pool")[:generic]
        lands=[]; pool={}
        for kind,identifier,options,permanent in selected:
            if kind=="permanent": lands.append(permanent)
            else:
                symbol=options[0]; pool[symbol]=pool.get(symbol,0)+1
        return lands,pool

    def can_pay(self,user,card,x_value=0):
        return self._mana_payment(self.player(user),card,x_value) is not None
    def max_payable_x(self,user,card):
        if "{X}" not in card.mana_cost: return 0
        value=0
        while self.can_pay(user,card,value+1): value+=1
        return value

    def activate_mana(self,user,position,color=None):
        self._priority(user); player=self.player(user)
        if not 1<=position<=len(player.battlefield): raise GameError("No permanent at that battlefield position.")
        permanent=player.battlefield[position-1]; card=self.card(permanent.uid)
        if not card.produces: raise GameError("That permanent has no supported mana ability.")
        if permanent.tapped: raise GameError(f"{card.name} is already tapped.")
        if card.creature and permanent.sick and not card.haste: raise GameError(f"{card.name} has summoning sickness.")
        symbol=(color or (card.produces[0] if len(card.produces)==1 else "")).upper()
        if symbol not in card.produces: raise GameError(f"Choose one of: {', '.join(card.produces)}.")
        permanent.tapped=True; player.mana_pool[symbol]=player.mana_pool.get(symbol,0)+card.mana_amount
        if card.sacrifice_for_mana:
            player.battlefield.remove(permanent); player.graveyard.append(permanent.uid)
        self.phase_passes=0
        for spell in self.stack: spell.passes=0
        amount=f" ×{card.mana_amount}" if card.mana_amount>1 else ""
        self.log.append(f"{user} added {{{symbol}}}{amount}.")

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
            self.log.append(f"{user} played {c.name}."); return
        if c.kind!="Instant" and (user!=self.active_user or self.phase not in ("precombat_main","postcombat_main") or self.stack): raise GameError("Cast that during your main phase with an empty stack.")
        target=self._target_for_cast(c,user,target)
        payment=self._mana_payment(p,c,x_value)
        if payment is None: raise GameError(f"You cannot pay {c.mana_cost or c.cost} with your available mana.")
        lands,pool=payment
        for permanent in lands: permanent.tapped=True
        for symbol,count in pool.items():
            p.mana_pool[symbol]-=count
            if not p.mana_pool[symbol]: p.mana_pool.pop(symbol)
        p.hand.pop(index-1); self.phase_passes=0
        for spell in self.stack: spell.passes=0
        self.stack.append(Spell(user,uid,c.key,target,x_value=x_value)); self.priority_user=self.opponent(user)
        suffix=f" with X={x_value}" if uses_x else ""
        self.log.append(f"{user} cast {c.name}{suffix}.")

    def _target_creature(self,target,message="Target must be USER_ID:POSITION."):
        if not target or ":" not in target: raise GameError(message)
        try: target_user,pos=(int(x) for x in target.split(":"))
        except (TypeError,ValueError) as e: raise GameError(message) from e
        battlefield=self.player(target_user).battlefield
        if not 1<=pos<=len(battlefield): raise GameError("No permanent at that battlefield position.")
        permanent=battlefield[pos-1]
        if not self.card(permanent.uid).creature: raise GameError("Target is not a creature.")
        return target_user,permanent

    def _target_stack(self,target):
        if not target or not target.upper().startswith("S:"): raise GameError("Stack target must be S:POSITION.")
        try: position=int(target.split(":",1)[1])
        except (TypeError,ValueError) as e: raise GameError("Stack target must be S:POSITION.") from e
        visible=list(reversed(self.stack))
        if not 1<=position<=len(visible): raise GameError("No spell at that stack position.")
        return visible[position-1]

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
        if c.effect in ("counter_spell","elemental_blast"):
            if target and target.upper().startswith("S:"):
                spell=self._target_stack(target); target_card=self.card(spell.uid)
                if c.target_color and c.target_color not in target_card.colors: raise GameError(f"Target spell must be {c.target_color}.")
                return f"S:{spell.uid}"
            if c.effect=="counter_spell": raise GameError("Counterspell requires an S:POSITION stack target.")
            if not target or ":" not in target: raise GameError("Elemental Blast target must be S:POSITION or USER_ID:POSITION.")
            try: target_user,pos=(int(x) for x in target.split(":"))
            except (TypeError,ValueError) as e: raise GameError("Elemental Blast target must be S:POSITION or USER_ID:POSITION.") from e
            battlefield=self.player(target_user).battlefield
            if not 1<=pos<=len(battlefield): raise GameError("No permanent at that battlefield position.")
            permanent=battlefield[pos-1]; target_card=self.card(permanent.uid)
            if c.target_color not in target_card.colors: raise GameError(f"Target permanent must be {c.target_color}.")
            return f"{target_user}:{permanent.uid}"
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
        if c.effect in ("destroy_creature","exile_creature_life"):
            target_user,permanent=self._target_creature(target)
            target_card=self.card(permanent.uid)
            if c.target_nonartifact and "Artifact" in target_card.type_line: raise GameError("Target must be a nonartifact creature.")
            if c.target_nonblack and "B" in target_card.colors: raise GameError("Target must be a nonblack creature.")
            return f"{target_user}:{permanent.uid}"
        if c.effect in ("pump","pump_blocking","pump_power_x"):
            target_user,permanent=self._target_creature(target)
            if c.effect=="pump_blocking" and permanent.uid not in self.blocks.values():
                raise GameError(f"{c.name} must target a blocking creature.")
            return f"{target_user}:{permanent.uid}"
        if c.effect in ("draw_target","draw_target_x","life_target_x"):
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
            if not any(target_card.has_type(kind) for kind in c.target_types): raise GameError("Target has an unsupported permanent type.")
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
                self.stack.pop(); self._resolve(s)
                if self.stack: self.stack[-1].passes=0
                if not self.finished: self.priority_user=self.active_user
            else: self.priority_user=self.opponent(user)
        else:
            if self.phase in ("attackers","blockers"): raise GameError("Complete the required combat declaration.")
            self.phase_passes+=1
            if self.phase_passes<2: self.priority_user=self.opponent(user)
            else: self.phase_passes=0; self._empty_mana(); self._advance()

    def _advance(self):
        if self.phase=="precombat_main": self.phase="attackers"; self.priority_user=None; return
        elif self.phase=="after_attackers": self.phase="blockers"; self.priority_user=None; return
        elif self.phase=="after_blockers":
            if self._combat_has_first_strike():
                self._combat_damage(first_strike=True)
                if self.finished: return
                self.phase="after_first_strike"
            else:
                self._combat_damage(first_strike=False); self._end_combat()
                if self.finished: return
                self.phase="postcombat_main"
        elif self.phase=="after_first_strike":
            self._combat_damage(first_strike=False); self._end_combat()
            if self.finished: return
            self.phase="postcombat_main"
        elif self.phase=="postcombat_main": self.phase="ending"
        elif self.phase=="ending": self.active_index=1-self.active_index; self._start_turn(); return
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
            if not c.creature or x.tapped or (x.sick and not c.haste) or "defender" in c.keywords: raise GameError(f"{c.name} cannot attack.")
            if x.uid in chosen: raise GameError("Duplicate attacker.")
            chosen.append(x.uid)
        for x in p.battlefield:
            if x.uid in chosen and "vigilance" not in self.card(x.uid).keywords: x.tapped=True
        self.attackers=chosen; self.blocks={}; self.phase_passes=0
        self.phase="after_attackers" if chosen else "postcombat_main"
        self.priority_user=user

    def declare_blockers(self,user,assignments):
        if self.phase!="blockers" or user!=self.opponent(self.active_user): raise GameError("You cannot block now.")
        p=self.player(user); used=set(); self.blocks={}
        for a,b in assignments.items():
            if not 1<=a<=len(self.attackers) or not 1<=b<=len(p.battlefield): raise GameError("Bad combat position.")
            x=p.battlefield[b-1]; attacker_uid=self.attackers[a-1]
            if x.uid in used: raise GameError("Invalid blocker.")
            legal,reason=self.can_block(attacker_uid,x.uid)
            if not legal: raise GameError(reason)
            used.add(x.uid); self.blocks[attacker_uid]=x.uid
        self.phase="after_blockers"; self.phase_passes=0; self.priority_user=self.active_user

    def can_block(self,attacker_uid,blocker_uid):
        defender=self.players[self.opponent(self.active_user)]
        attacker=self.card(attacker_uid)
        blocker_perm=next((x for x in defender.battlefield if x.uid==blocker_uid),None)
        if blocker_perm is None: return False,"That blocker is no longer on the battlefield."
        blocker=self.card(blocker_uid)
        if not blocker.creature or blocker_perm.tapped: return False,"Invalid blocker."
        if "flying" in attacker.keywords and not ({"flying","reach"} & set(blocker.keywords)):
            return False,f"{blocker.name} cannot block a creature with flying."
        for land_type in ("plains","island","swamp","mountain","forest"):
            if f"{land_type}walk" in attacker.keywords and any(self.card(x.uid).has_land_type(land_type) for x in defender.battlefield):
                return False,f"{attacker.name} can't be blocked while the defender controls a {land_type.title()}."
        attacker_perm=next(x for x in self.players[self.active_user].battlefield if x.uid==attacker_uid)
        power=self.current_stats(attacker_perm)[0]
        if blocker.max_block_power is not None and power>blocker.max_block_power:
            return False,f"{blocker.name} can't block a creature with power {power}."
        return True,""

    def _combat_has_first_strike(self):
        combatants=set(self.attackers)|set(self.blocks.values())
        return any(
            "first_strike" in self.card(x.uid).keywords
            for player in self.players.values() for x in player.battlefield if x.uid in combatants
        )

    def _combat_damage(self,first_strike):
        atk=self.players[self.active_user]; dfn=self.players[self.opponent(self.active_user)]
        for uid in self.attackers:
            a=next((x for x in atk.battlefield if x.uid==uid),None)
            if a is None: continue
            block_uid=self.blocks.get(uid); b=next((x for x in dfn.battlefield if x.uid==block_uid),None)
            attacker_strikes=("first_strike" in self.card(a.uid).keywords)==first_strike
            blocker_strikes=b is not None and (("first_strike" in self.card(b.uid).keywords)==first_strike)
            if attacker_strikes:
                if block_uid is None: dfn.life-=self.current_stats(a)[0]
                elif b is not None: b.damage+=self.current_stats(a)[0]
            if blocker_strikes: a.damage+=self.current_stats(b)[0]
        self._sba(); self._life()

    def _end_combat(self):
        self.attackers=[]; self.blocks={}

    @staticmethod
    def _dies(controller,permanent):
        (controller.exile if permanent.exile_on_death else controller.graveyard).append(permanent.uid)

    def _resolve(self,s):
        p=self.players[s.owner]; c=CARDS[s.key]
        if c.kind in ("Creature","Artifact","Enchantment"): p.battlefield.append(Permanent(s.uid,c.key))
        elif c.effect in ("counter_spell","elemental_blast"):
            if s.target.startswith("S:"):
                target_uid=int(s.target.split(":",1)[1]); target=next((spell for spell in self.stack if spell.uid==target_uid),None)
                target_card=self.card(target.uid) if target is not None else None
                legal=target_card is not None and (not c.target_color or c.target_color in target_card.colors)
                if not legal:
                    p.graveyard.append(s.uid); self.log.append(f"{c.name} fizzled because its target was gone or illegal."); return
                self.stack.remove(target); self.player(target.owner).graveyard.append(target.uid)
                self.log.append(f"{c.name} countered {target_card.name}.")
            else:
                user,uid=(int(x) for x in s.target.split(":")); controller=self.player(user)
                target=next((x for x in controller.battlefield if x.uid==uid),None); target_card=self.card(target.uid) if target is not None else None
                if target_card is None or c.target_color not in target_card.colors:
                    p.graveyard.append(s.uid); self.log.append(f"{c.name} fizzled because its target was gone or illegal."); return
                controller.battlefield.remove(target); self._dies(controller,target)
            p.graveyard.append(s.uid)
        elif c.effect=="draw": self._draw(p,c.amount); p.graveyard.append(s.uid)
        elif c.effect in ("draw_target","draw_target_x"): self._draw(self.player(int(s.target)),s.x_value if c.effect=="draw_target_x" else c.amount); p.graveyard.append(s.uid)
        elif c.effect=="life_target_x": self.player(int(s.target)).life+=s.x_value; p.graveyard.append(s.uid)
        elif c.effect=="life": p.life+=c.amount; p.graveyard.append(s.uid)
        elif c.effect in ("damage","damage_any","damage_x_exile"):
            amount=s.x_value if c.effect=="damage_x_exile" else c.amount
            if ":" in (s.target or ""):
                user,uid=(int(x) for x in s.target.split(":")); target=next((x for x in self.player(user).battlefield if x.uid==uid),None)
                if target is None:
                    p.graveyard.append(s.uid); self.log.append(f"{c.name} fizzled because its target was gone."); return
                target.damage+=amount
                if c.effect=="damage_x_exile": target.exile_on_death=True
            else: self.player(int(s.target or self.opponent(s.owner))).life-=amount
            p.life-=c.self_damage; p.graveyard.append(s.uid)
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
            if target is None or not self.card(target.uid).creature:
                p.graveyard.append(s.uid); self.log.append(f"{c.name} fizzled because its target was gone."); return
            controller.battlefield.remove(target); controller.hand.append(target.uid)
            if target.uid in self.attackers:
                self.attackers.remove(target.uid); self.blocks.pop(target.uid,None)
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
            legal=target_card is not None and target_card.creature and not (c.target_nonartifact and "Artifact" in target_card.type_line) and not (c.target_nonblack and "B" in target_card.colors)
            if not legal:
                p.graveyard.append(s.uid); self.log.append(f"{c.name} fizzled because its target was gone or illegal."); return
            life_gain=max(0,self.current_stats(target)[0]) if c.effect=="exile_creature_life" else 0
            controller.battlefield.remove(target)
            if c.effect=="exile_creature_life":
                controller.exile.append(target.uid); controller.life+=life_gain
            else: self._dies(controller,target)
            p.graveyard.append(s.uid)
        elif c.effect in ("earthquake_x","hurricane_x"):
            for controller in self.players.values():
                controller.life-=s.x_value
                for permanent in controller.battlefield:
                    card=self.card(permanent.uid)
                    affected=card.creature and ((c.effect=="earthquake_x" and "flying" not in card.keywords) or (c.effect=="hurricane_x" and "flying" in card.keywords))
                    if affected: permanent.damage+=s.x_value
            p.graveyard.append(s.uid)
        elif c.effect=="destroy_all_creatures":
            for controller in self.players.values():
                destroyed=[x for x in controller.battlefield if self.card(x.uid).creature]
                controller.battlefield=[x for x in controller.battlefield if x not in destroyed]
                for permanent in destroyed: self._dies(controller,permanent)
            p.graveyard.append(s.uid)
        elif c.effect=="destroy_permanent":
            user,uid=(int(x) for x in s.target.split(":")); controller=self.player(user)
            target=next((x for x in controller.battlefield if x.uid==uid),None)
            if target is None or not any(self.card(target.uid).has_type(kind) for kind in c.target_types):
                p.graveyard.append(s.uid); self.log.append(f"{c.name} fizzled because its target was gone."); return
            controller.battlefield.remove(target); self._dies(controller,target); p.graveyard.append(s.uid)
        elif c.effect=="destroy_land":
            user,uid=(int(x) for x in s.target.split(":")); controller=self.player(user)
            target=next((x for x in controller.battlefield if x.uid==uid),None)
            if target is None or not self.card(target.uid).land:
                p.graveyard.append(s.uid); self.log.append(f"{c.name} fizzled because its target was gone."); return
            controller.battlefield.remove(target); controller.graveyard.append(target.uid); p.graveyard.append(s.uid)
        elif c.effect in ("destroy_all_lands","destroy_land_type"):
            for controller in self.players.values():
                destroyed=[x for x in controller.battlefield if self.card(x.uid).land and (c.effect=="destroy_all_lands" or self.card(x.uid).has_land_type(c.land_type))]
                controller.battlefield=[x for x in controller.battlefield if x not in destroyed]
                controller.graveyard.extend(x.uid for x in destroyed)
            p.graveyard.append(s.uid)
        self.log.append(f"{c.name} resolved."); self._sba(); self._life()

    def _perm(self,p,uid):
        return next(x for x in p.battlefield if x.uid==uid)
    def _sba(self):
        while True:
            doomed={p.user_id:[x for x in p.battlefield if self.card(x.uid).creature and x.damage>=self.current_stats(x)[1]] for p in self.players.values()}
            if not any(doomed.values()): return
            for p in self.players.values():
                deaths=doomed[p.user_id]
                p.battlefield=[x for x in p.battlefield if x not in deaths]
                p.exile.extend(x.uid for x in deaths if x.exile_on_death)
                p.graveyard.extend(x.uid for x in deaths if not x.exile_on_death)
    def _cleanup(self):
        for p in self.players.values():
            for x in p.battlefield: x.damage=x.bonus=x.power_bonus=0; x.exile_on_death=False
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
        if self.priority_user!=user: raise GameError("You do not have priority.")
    def _active(self,user):
        if self.finished: raise GameError("Game is over.")
        if user!=self.active_user or self.priority_user!=user: raise GameError("It is not your action window.")

    def to_raw(self):
        return {"game_id":self.game_id,"order":self.order,"players":{str(k):{**asdict(v),"battlefield":[asdict(x) for x in v.battlefield]} for k,v in self.players.items()},"cards":self.cards,"next_uid":self.next_uid,"active_index":self.active_index,"phase":self.phase,"phase_passes":self.phase_passes,"turn":self.turn,"stack":[asdict(x) for x in self.stack],"attackers":self.attackers,"blocks":self.blocks,"priority_user":self.priority_user,"winner":self.winner,"finished_reason":self.finished_reason,"ai_user":self.ai_user,"ai_difficulty":self.ai_difficulty,"log":self.log[-100:],"history":self.history,"created_at":self.created_at,"updated_at":self.updated_at}
    @classmethod
    def from_raw(cls,r):
        g=cls.__new__(cls); g.game_id=int(r["game_id"]); g.order=[int(x) for x in r["order"]]
        g.players={}
        for k,v in r["players"].items():
            d=dict(v); d.setdefault("mana_pool",{}); d.setdefault("exile",[]); d["mana_pool"]={str(symbol):int(count) for symbol,count in d["mana_pool"].items()}; d["battlefield"]=[Permanent(**x) for x in d["battlefield"]]; g.players[int(k)]=Player(**d)
        g.cards={int(k):v for k,v in r["cards"].items()}; g.next_uid=int(r["next_uid"]); g.active_index=int(r["active_index"]); g.phase=r["phase"]; g.phase_passes=int(r.get("phase_passes",0)); g.turn=int(r["turn"]); g.stack=[Spell(**x) for x in r["stack"]]; g.attackers=[int(x) for x in r["attackers"]]; g.blocks={int(k):int(v) for k,v in r["blocks"].items()}; g.priority_user=r["priority_user"]; g.winner=r["winner"]; g.finished_reason=r["finished_reason"]; g.ai_user=int(r["ai_user"]) if r.get("ai_user") is not None else None; g.ai_difficulty=r.get("ai_difficulty"); g.log=list(r["log"]); g.history=list(r.get("history",[])); g.created_at=int(r.get("created_at",time.time())); g.updated_at=int(r.get("updated_at",g.created_at))
        return g
