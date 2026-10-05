import random
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
    def __init__(self, game_id, users, seed=None):
        if len(users) != 2 or users[0] == users[1]: raise GameError("Two different players are required.")
        self.game_id, self.order = int(game_id), [int(x) for x in users]
        self.players = {self.order[0]: Player(self.order[0],"red"), self.order[1]: Player(self.order[1],"green")}
        self.cards, self.next_uid = {}, 1
        self.active_index, self.phase, self.turn = 0, "opening", 0
        self.stack, self.attackers, self.blocks = [], [], {}
        self.priority_user = self.winner = self.finished_reason = None
        self.log, self.history = [], []
        self.created_at = self.updated_at = int(time.time())
        rng = random.Random(seed)
        for user, color in zip(self.order, ("red","green")):
            p=self.players[user]
            for key in starter(color):
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
        if not(first and self.turn==1): self._draw(p)
        self.phase="precombat_main"; self.priority_user=self.active_user
        self.log.append(f"Turn {self.turn}: {self.active_user}.")

    def play(self,user,index,target=None):
        self._priority(user); p=self.player(user)
        if not 1<=index<=len(p.hand): raise GameError("No card at that hand position.")
        uid=p.hand[index-1]; c=self.card(uid)
        if c.land:
            if user!=self.active_user: raise GameError("Only the active player can play a land.")
            if self.phase not in ("precombat_main","postcombat_main") or self.stack: raise GameError("Land requires an empty-stack main phase.")
            if p.land_played: raise GameError("You already played a land.")
            p.hand.pop(index-1); p.battlefield.append(Permanent(uid,c.key,sick=False)); p.land_played=True
            self.log.append(f"{user} played {c.name}."); return
        if c.kind!="Instant" and (user!=self.active_user or self.phase not in ("precombat_main","postcombat_main") or self.stack): raise GameError("Cast that during your main phase with an empty stack.")
        target=self._target_for_cast(c,user,target)
        lands=[x for x in p.battlefield if self.card(x.uid).land and not x.tapped]
        if len(lands)<c.cost: raise GameError(f"You need {c.cost} untapped lands.")
        for x in lands[:c.cost]: x.tapped=True
        p.hand.pop(index-1)
        for spell in self.stack: spell.passes=0
        self.stack.append(Spell(user,uid,c.key,target)); self.priority_user=self.opponent(user)
        self.log.append(f"{user} cast {c.name}.")

    def _target_for_cast(self,c,user,target):
        if c.effect=="damage":
            try: target_user=int(target) if target is not None else self.opponent(user)
            except (TypeError,ValueError) as e: raise GameError("Target must be a player ID.") from e
            self.player(target_user); return str(target_user)
        if c.effect=="pump":
            if not target or ":" not in target: raise GameError("Target must be USER_ID:POSITION.")
            try: target_user,pos=(int(x) for x in target.split(":"))
            except (TypeError,ValueError) as e: raise GameError("Target must be USER_ID:POSITION.") from e
            battlefield=self.player(target_user).battlefield
            if not 1<=pos<=len(battlefield): raise GameError("No permanent at that battlefield position.")
            permanent=battlefield[pos-1]
            if not self.card(permanent.uid).creature: raise GameError("Target is not a creature.")
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
        elif user==self.active_user: self._advance()
        else: self.priority_user=self.active_user

    def _advance(self):
        if self.phase=="precombat_main": self.phase="attackers"
        elif self.phase=="attackers": self.phase="postcombat_main"
        elif self.phase=="postcombat_main": self.phase="ending"
        elif self.phase=="ending": self.active_index=1-self.active_index; self._start_turn(); return
        else: raise GameError("Complete combat first.")
        self.priority_user=self.active_user

    def declare_attackers(self,user,positions):
        self._active(user)
        if self.phase!="attackers": raise GameError("Not the attack step.")
        p=self.player(user); chosen=[]
        for pos in positions:
            if not 1<=pos<=len(p.battlefield): raise GameError("Bad attacker position.")
            x=p.battlefield[pos-1]; c=self.card(x.uid)
            if not c.creature or x.tapped or (x.sick and c.key!="goblin"): raise GameError(f"{c.name} cannot attack.")
            if x.uid in chosen: raise GameError("Duplicate attacker.")
            chosen.append(x.uid)
        for x in p.battlefield:
            if x.uid in chosen: x.tapped=True
        self.attackers=chosen; self.blocks={}
        self.phase="blockers" if chosen else "postcombat_main"
        self.priority_user=self.opponent(user) if chosen else user

    def declare_blockers(self,user,assignments):
        if self.phase!="blockers" or user!=self.opponent(self.active_user): raise GameError("You cannot block now.")
        p=self.player(user); used=set(); self.blocks={}
        for a,b in assignments.items():
            if not 1<=a<=len(self.attackers) or not 1<=b<=len(p.battlefield): raise GameError("Bad combat position.")
            x=p.battlefield[b-1]
            if not self.card(x.uid).creature or x.tapped or x.uid in used: raise GameError("Invalid blocker.")
            used.add(x.uid); self.blocks[self.attackers[a-1]]=x.uid
        self._combat(); self.phase="postcombat_main"; self.priority_user=self.active_user

    def _combat(self):
        atk=self.players[self.active_user]; dfn=self.players[self.opponent(self.active_user)]
        for uid in self.attackers:
            a=self._perm(atk,uid); block=self.blocks.get(uid)
            if block is None: dfn.life-=self.card(a.uid).power+a.bonus
            else:
                b=self._perm(dfn,block); a.damage+=self.card(b.uid).power+b.bonus; b.damage+=self.card(a.uid).power+a.bonus
        self.attackers=[]; self.blocks={}; self._sba(); self._life()

    def _resolve(self,s):
        p=self.players[s.owner]; c=CARDS[s.key]
        if c.creature: p.battlefield.append(Permanent(s.uid,c.key))
        elif c.effect=="draw": self._draw(p,c.amount); p.graveyard.append(s.uid)
        elif c.effect=="life": p.life+=c.amount; p.graveyard.append(s.uid)
        elif c.effect=="damage":
            target=self.player(int(s.target or self.opponent(s.owner))); target.life-=c.amount; p.graveyard.append(s.uid)
        elif c.effect=="pump":
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
        for p in self.players.values():
            if p.life<=0: self._finish(self.opponent(p.user_id),"zero life"); break
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
        return {"game_id":self.game_id,"order":self.order,"players":{str(k):{**asdict(v),"battlefield":[asdict(x) for x in v.battlefield]} for k,v in self.players.items()},"cards":self.cards,"next_uid":self.next_uid,"active_index":self.active_index,"phase":self.phase,"turn":self.turn,"stack":[asdict(x) for x in self.stack],"attackers":self.attackers,"blocks":self.blocks,"priority_user":self.priority_user,"winner":self.winner,"finished_reason":self.finished_reason,"log":self.log[-100:],"history":self.history,"created_at":self.created_at,"updated_at":self.updated_at}
    @classmethod
    def from_raw(cls,r):
        g=cls.__new__(cls); g.game_id=int(r["game_id"]); g.order=[int(x) for x in r["order"]]
        g.players={}
        for k,v in r["players"].items():
            d=dict(v); d["battlefield"]=[Permanent(**x) for x in d["battlefield"]]; g.players[int(k)]=Player(**d)
        g.cards={int(k):v for k,v in r["cards"].items()}; g.next_uid=int(r["next_uid"]); g.active_index=int(r["active_index"]); g.phase=r["phase"]; g.turn=int(r["turn"]); g.stack=[Spell(**x) for x in r["stack"]]; g.attackers=[int(x) for x in r["attackers"]]; g.blocks={int(k):int(v) for k,v in r["blocks"].items()}; g.priority_user=r["priority_user"]; g.winner=r["winner"]; g.finished_reason=r["finished_reason"]; g.log=list(r["log"]); g.history=list(r.get("history",[])); g.created_at=int(r.get("created_at",time.time())); g.updated_at=int(r.get("updated_at",g.created_at))
        return g
