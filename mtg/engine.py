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

@dataclass
class Player:
    user_id: int
    deck: str
    life: int = 20
    library: List[int] = field(default_factory=list)
    hand: List[int] = field(default_factory=list)
    graveyard: List[int] = field(default_factory=list)
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
    def _mana_requirements(card):
        symbols=re.findall(r"\{([^}]+)\}",card.mana_cost or "")
        generic=0; colored=[]
        for symbol in symbols:
            if symbol.isdigit(): generic+=int(symbol)
            elif symbol in {"W","U","B","R","G"}: colored.append(symbol)
            else: raise GameError(f"{card.name} uses an unsupported mana symbol: {{{symbol}}}.")
        return generic,colored

    def _mana_payment(self,player,card):
        generic,colored=self._mana_requirements(card)
        available=[permanent for permanent in player.battlefield if self.card(permanent.uid).land and not permanent.tapped]

        def assign(index,remaining,selected):
            if index==len(colored): return selected,remaining
            symbol=colored[index]
            choices=sorted(
                (permanent for permanent in remaining if symbol in self.card(permanent.uid).produces),
                key=lambda item:len(self.card(item.uid).produces),
            )
            for permanent in choices:
                rest=[item for item in remaining if item.uid!=permanent.uid]
                result=assign(index+1,rest,selected+[permanent])
                if result is not None: return result
            return None

        result=assign(0,available,[])
        if result is None: return None
        selected,remaining=result
        if len(remaining)<generic: return None
        return selected+remaining[:generic]

    def can_pay(self,user,card):
        return self._mana_payment(self.player(user),card) is not None

    def play(self,user,index,target=None):
        self._priority(user); p=self.player(user)
        if not 1<=index<=len(p.hand): raise GameError("No card at that hand position.")
        uid=p.hand[index-1]; c=self.card(uid)
        if c.land:
            if user!=self.active_user: raise GameError("Only the active player can play a land.")
            if self.phase not in ("precombat_main","postcombat_main") or self.stack: raise GameError("Land requires an empty-stack main phase.")
            if p.land_played: raise GameError("You already played a land.")
            p.hand.pop(index-1); p.battlefield.append(Permanent(uid,c.key,sick=False)); p.land_played=True; self.phase_passes=0
            self.log.append(f"{user} played {c.name}."); return
        if c.kind!="Instant" and (user!=self.active_user or self.phase not in ("precombat_main","postcombat_main") or self.stack): raise GameError("Cast that during your main phase with an empty stack.")
        target=self._target_for_cast(c,user,target)
        payment=self._mana_payment(p,c)
        if payment is None: raise GameError(f"You cannot pay {c.mana_cost or c.cost} with your untapped lands.")
        for permanent in payment: permanent.tapped=True
        p.hand.pop(index-1); self.phase_passes=0
        for spell in self.stack: spell.passes=0
        self.stack.append(Spell(user,uid,c.key,target)); self.priority_user=self.opponent(user)
        self.log.append(f"{user} cast {c.name}.")

    def _target_creature(self,target,message="Target must be USER_ID:POSITION."):
        if not target or ":" not in target: raise GameError(message)
        try: target_user,pos=(int(x) for x in target.split(":"))
        except (TypeError,ValueError) as e: raise GameError(message) from e
        battlefield=self.player(target_user).battlefield
        if not 1<=pos<=len(battlefield): raise GameError("No permanent at that battlefield position.")
        permanent=battlefield[pos-1]
        if not self.card(permanent.uid).creature: raise GameError("Target is not a creature.")
        return target_user,permanent

    def _target_for_cast(self,c,user,target):
        if c.effect=="damage":
            try: target_user=int(target) if target is not None else self.opponent(user)
            except (TypeError,ValueError) as e: raise GameError("Target must be a player ID.") from e
            self.player(target_user); return str(target_user)
        if c.effect=="damage_any":
            if target and ":" in target:
                target_user,permanent=self._target_creature(target,"Target must be a player ID or USER_ID:POSITION.")
                return f"{target_user}:{permanent.uid}"
            try: target_user=int(target)
            except (TypeError,ValueError) as e: raise GameError("Target must be a player ID or USER_ID:POSITION.") from e
            self.player(target_user); return str(target_user)
        if c.effect in ("pump","pump_blocking"):
            target_user,permanent=self._target_creature(target)
            if c.effect=="pump_blocking" and permanent.uid not in self.blocks.values():
                raise GameError(f"{c.name} must target a blocking creature.")
            return f"{target_user}:{permanent.uid}"
        if c.effect=="draw_target":
            try: target_user=int(target)
            except (TypeError,ValueError) as e: raise GameError("Target must be a player ID.") from e
            self.player(target_user); return str(target_user)
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
            else: self.phase_passes=0; self._advance()

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
        power=attacker.power+next((x.bonus for x in self.players[self.active_user].battlefield if x.uid==attacker_uid),0)
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
                if block_uid is None: dfn.life-=self.card(a.uid).power+a.bonus
                elif b is not None: b.damage+=self.card(a.uid).power+a.bonus
            if blocker_strikes: a.damage+=self.card(b.uid).power+b.bonus
        self._sba(); self._life()

    def _end_combat(self):
        self.attackers=[]; self.blocks={}

    def _resolve(self,s):
        p=self.players[s.owner]; c=CARDS[s.key]
        if c.creature: p.battlefield.append(Permanent(s.uid,c.key))
        elif c.effect=="draw": self._draw(p,c.amount); p.graveyard.append(s.uid)
        elif c.effect=="draw_target": self._draw(self.player(int(s.target)),c.amount); p.graveyard.append(s.uid)
        elif c.effect=="life": p.life+=c.amount; p.graveyard.append(s.uid)
        elif c.effect in ("damage","damage_any"):
            if ":" in (s.target or ""):
                user,uid=(int(x) for x in s.target.split(":")); target=next((x for x in self.player(user).battlefield if x.uid==uid),None)
                if target is None:
                    p.graveyard.append(s.uid); self.log.append(f"{c.name} fizzled because its target was gone."); return
                target.damage+=c.amount
            else: self.player(int(s.target or self.opponent(s.owner))).life-=c.amount
            p.life-=c.self_damage; p.graveyard.append(s.uid)
        elif c.effect in ("pump","pump_blocking"):
            user,uid=(int(x) for x in s.target.split(":")); target=next((x for x in self.player(user).battlefield if x.uid==uid),None)
            if target is None:
                p.graveyard.append(s.uid); self.log.append(f"{c.name} fizzled because its target was gone."); return
            target.bonus+=c.amount; p.graveyard.append(s.uid)
        self.log.append(f"{c.name} resolved."); self._sba(); self._life()

    def _perm(self,p,uid):
        return next(x for x in p.battlefield if x.uid==uid)
    def _sba(self):
        for p in self.players.values():
            alive=[]
            for x in p.battlefield:
                c=self.card(x.uid)
                if c.creature and x.damage>=c.toughness+x.bonus: p.graveyard.append(x.uid)
                else: alive.append(x)
            p.battlefield=alive
    def _cleanup(self):
        for p in self.players.values():
            for x in p.battlefield: x.damage=x.bonus=0
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
            d=dict(v); d["battlefield"]=[Permanent(**x) for x in d["battlefield"]]; g.players[int(k)]=Player(**d)
        g.cards={int(k):v for k,v in r["cards"].items()}; g.next_uid=int(r["next_uid"]); g.active_index=int(r["active_index"]); g.phase=r["phase"]; g.phase_passes=int(r.get("phase_passes",0)); g.turn=int(r["turn"]); g.stack=[Spell(**x) for x in r["stack"]]; g.attackers=[int(x) for x in r["attackers"]]; g.blocks={int(k):int(v) for k,v in r["blocks"].items()}; g.priority_user=r["priority_user"]; g.winner=r["winner"]; g.finished_reason=r["finished_reason"]; g.ai_user=int(r["ai_user"]) if r.get("ai_user") is not None else None; g.ai_difficulty=r.get("ai_difficulty"); g.log=list(r["log"]); g.history=list(r.get("history",[])); g.created_at=int(r.get("created_at",time.time())); g.updated_at=int(r.get("updated_at",g.created_at))
        return g
