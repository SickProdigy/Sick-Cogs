import random,time
from dataclasses import asdict,dataclass,field
from .cards import CARDS,starter
class GameError(ValueError): pass
PHASES=("draw","standby","main1","battle","main2","end")
@dataclass
class FieldCard:
    uid:int; key:str; position:str="attack"; face_up:bool=True; attacked:bool=False; summoned_turn:int=0; changed_turn:int=0
@dataclass
class Player:
    user_id:int; deck_name:str; life:int=8000; deck:list=field(default_factory=list); hand:list=field(default_factory=list); graveyard:list=field(default_factory=list); banished:list=field(default_factory=list); monsters:list=field(default_factory=lambda:[None]*5); spells:list=field(default_factory=lambda:[None]*5); normal_summoned:bool=False
class Game:
    """Serializable bounded rules engine; Discord is only a view."""
    def __init__(self,game_id,users,seed=None):
        if len(users)!=2 or int(users[0])==int(users[1]): raise GameError("Two different players are required.")
        self.game_id=int(game_id);self.order=[int(x) for x in users];self.players={self.order[0]:Player(self.order[0],"arcane"),self.order[1]:Player(self.order[1],"knight")}
        self.cards={};self.next_uid=1;self.active_index=0;self.turn=1;self.phase="draw";self.winner=self.finished_reason=self.pending_attack=None;self.chain=[];self.history=[];self.state_version=0;self.created_at=self.updated_at=int(time.time())
        rng=random.Random(seed)
        for user in self.order:
            p=self.players[user]
            for key in starter(p.deck_name):self.cards[self.next_uid]=key;p.deck.append(self.next_uid);self.next_uid+=1
            rng.shuffle(p.deck);self._draw(p,5)
        self._event(None,"game_created")
    @property
    def active_user(self):return self.order[self.active_index]
    @property
    def finished(self):return self.phase=="finished"
    def player(self,user):
        try:return self.players[int(user)]
        except (KeyError,ValueError) as e:raise GameError("You are not in this duel.") from e
    def opponent(self,user):self.player(user);return self.order[1] if int(user)==self.order[0] else self.order[0]
    def card(self,uid):return CARDS[self.cards[int(uid)]]
    def hand(self,user):return [self.card(x) for x in self.player(user).hand]
    def _event(self,user,action,detail=""):
        self.updated_at=int(time.time());self.state_version+=1;e={"seq":len(self.history)+1,"version":self.state_version,"at":self.updated_at,"user":user,"action":action}
        if detail:e["detail"]=detail
        self.history.append(e)
    def record(self,user,action,detail=""):self._event(user,action,detail)
    def _live(self):
        if self.finished:raise GameError("This duel is over.")
    def _turn(self,user,phases=None):
        self._live();self.player(user)
        if int(user)!=self.active_user:raise GameError("It is not your turn.")
        if self.pending_attack:raise GameError("Resolve the attack response first.")
        if phases and self.phase not in phases:raise GameError(f"That action is not legal during {self.phase}.")
    def _draw(self,p,n=1):
        for _ in range(n):
            if not p.deck:self._finish(self.opponent(p.user_id),"could not draw");return
            p.hand.append(p.deck.pop())
    def advance(self,user):
        self._turn(user);old=self.phase
        if old=="draw" and not(self.turn==1 and self.active_index==0):self._draw(self.player(user))
        if self.finished:return
        if old=="end":
            self.active_index=1-self.active_index;self.turn+=1;self.phase="draw";p=self.player(self.active_user);p.normal_summoned=False
            for m in p.monsters:
                if m:m.attacked=False
        else:self.phase=PHASES[PHASES.index(old)+1]
        self._event(user,"advance",self.phase)
    def summon(self,user,hand_pos,zone,position="attack",face_down=False,tributes=()):
        self._turn(user,("main1","main2"));p=self.player(user)
        if p.normal_summoned:raise GameError("You already normal summoned or set this turn.")
        if not 1<=hand_pos<=len(p.hand):raise GameError("No card at that hand position.")
        if not 1<=zone<=5 or p.monsters[zone-1]:raise GameError("Choose an empty Monster Zone 1-5.")
        uid=p.hand[hand_pos-1];c=self.card(uid)
        if not c.monster:raise GameError("That is not a monster.")
        need=0 if c.level<=4 else 1 if c.level<=6 else 2;chosen=[int(x) for x in tributes]
        if len(chosen)!=need or len(set(chosen))!=len(chosen):raise GameError(f"{c.name} requires exactly {need} tribute(s).")
        if zone in chosen:raise GameError("The destination cannot be a tribute.")
        for z in chosen:m=self._monster(p,z);p.graveyard.append(m.uid);p.monsters[z-1]=None
        if position not in ("attack","defense"):raise GameError("Position must be attack or defense.")
        p.hand.pop(hand_pos-1);p.monsters[zone-1]=FieldCard(uid,c.key,"defense" if face_down else position,not face_down,False,self.turn,self.turn);p.normal_summoned=True;self._event(user,"set" if face_down else "summon",c.name)
    def set_spell(self,user,hand_pos,zone):
        self._turn(user,("main1","main2"));p=self.player(user)
        if not 1<=hand_pos<=len(p.hand):raise GameError("No card at that hand position.")
        if not 1<=zone<=5 or p.spells[zone-1]:raise GameError("Choose an empty Spell/Trap Zone 1-5.")
        uid=p.hand[hand_pos-1];c=self.card(uid)
        if not(c.spell or c.trap):raise GameError("Only a Spell or Trap can be set.")
        p.hand.pop(hand_pos-1);p.spells[zone-1]=FieldCard(uid,c.key,"set",False,summoned_turn=self.turn);self._event(user,"set_backrow",c.kind)
    def activate(self,user,hand_pos):
        self._turn(user,("main1","main2"));p=self.player(user)
        if not 1<=hand_pos<=len(p.hand):raise GameError("No card at that hand position.")
        uid=p.hand[hand_pos-1];c=self.card(uid)
        if not c.spell:raise GameError("Only a supported Normal Spell can activate from hand.")
        q=self.player(self.opponent(user))
        if c.effect=="destroy_lowest" and not any(m and m.face_up for m in q.monsters):raise GameError("There is no legal Fissure target.")
        p.hand.pop(hand_pos-1);self.chain=[{"user":int(user),"key":c.key}]
        if c.effect=="draw":self._draw(p,c.amount)
        elif c.effect=="destroy_all":
            for x in self.players.values():
                for i,m in enumerate(x.monsters):
                    if m:x.graveyard.append(m.uid);x.monsters[i]=None
        elif c.effect=="destroy_lowest":
            _,i,m=min((self.card(m.uid).attack,i,m) for i,m in enumerate(q.monsters) if m and m.face_up);q.graveyard.append(m.uid);q.monsters[i]=None
        else:raise GameError("Unsupported effect.")
        p.graveyard.append(uid);self.chain=[];self._event(user,"activate_spell",c.name)
    def flip_summon(self,user,zone):
        self._turn(user,("main1","main2"));m=self._monster(self.player(user),zone)
        if m.face_up:raise GameError("That monster is already face-up.")
        if m.summoned_turn==self.turn:raise GameError("It cannot be Flip Summoned this turn.")
        m.face_up=True;m.position="attack";m.changed_turn=self.turn;self._event(user,"flip_summon",self.card(m.uid).name)
    def change_position(self,user,zone):
        self._turn(user,("main1","main2"));m=self._monster(self.player(user),zone)
        if not m.face_up:raise GameError("Use Flip Summon for a face-down monster.")
        if m.summoned_turn==self.turn or m.changed_turn==self.turn or m.attacked:raise GameError("That monster cannot change position this turn.")
        m.position="defense" if m.position=="attack" else "attack";m.changed_turn=self.turn;self._event(user,"change_position")
    def attack(self,user,attacker_zone,target_zone=None):
        self._turn(user,("battle",));m=self._monster(self.player(user),attacker_zone)
        if not m.face_up or m.position!="attack" or m.attacked:raise GameError("That monster cannot attack.")
        q=self.player(self.opponent(user));occupied=[i+1 for i,x in enumerate(q.monsters) if x]
        if target_zone is None and occupied:raise GameError("Choose an opponent Monster Zone.")
        if target_zone is not None:self._monster(q,int(target_zone))
        m.attacked=True;self.pending_attack={"attacker_user":int(user),"attacker_zone":int(attacker_zone),"target_zone":int(target_zone) if target_zone is not None else None};self._event(user,"declare_attack",self.card(m.uid).name)
    def respond_attack(self,user,trap_zone=None):
        self._live();a=self.pending_attack
        if not a or int(user)!=self.opponent(a["attacker_user"]):raise GameError("You do not have an attack response.")
        if trap_zone is not None:
            p=self.player(user);t=self._spell(p,int(trap_zone));c=self.card(t.uid)
            if c.effect!="destroy_attacker":raise GameError("That is not a supported attack-response Trap.")
            if t.summoned_turn==self.turn:raise GameError("A Trap cannot activate in the turn it was set.")
            p.spells[int(trap_zone)-1]=None;p.graveyard.append(t.uid);q=self.player(a["attacker_user"]);m=self._monster(q,a["attacker_zone"]);q.graveyard.append(m.uid);q.monsters[a["attacker_zone"]-1]=None;self.pending_attack=None;self._event(user,"activate_trap",c.name);return
        self._resolve_attack();self._event(user,"resolve_attack")
    def _resolve_attack(self):
        a=self.pending_attack;p=self.player(a["attacker_user"]);q=self.player(self.opponent(a["attacker_user"]));m=self._monster(p,a["attacker_zone"]);atk=self.card(m.uid).attack;z=a["target_zone"]
        if z is None:q.life-=atk
        else:
            d=self._monster(q,z);c=self.card(d.uid);d.face_up=True;value=c.attack if d.position=="attack" else c.defense
            if d.position=="attack":
                if atk>=value:q.graveyard.append(d.uid);q.monsters[z-1]=None
                if atk<=value:p.graveyard.append(m.uid);p.monsters[a["attacker_zone"]-1]=None
                if atk>value:q.life-=atk-value
                elif atk<value:p.life-=value-atk
            elif atk>value:q.graveyard.append(d.uid);q.monsters[z-1]=None
            elif atk<value:p.life-=value-atk
        self.pending_attack=None;self._check_life()
    def _monster(self,p,z):
        if not 1<=int(z)<=5 or p.monsters[int(z)-1] is None:raise GameError("No monster exists in that zone.")
        return p.monsters[int(z)-1]
    def _spell(self,p,z):
        if not 1<=int(z)<=5 or p.spells[int(z)-1] is None:raise GameError("No set card exists in that zone.")
        return p.spells[int(z)-1]
    def _check_life(self):
        for p in self.players.values():
            if p.life<=0:self._finish(self.opponent(p.user_id),"life points reached zero");return
    def concede(self,user):self._live();self.player(user);self._finish(self.opponent(user),"concession");self._event(user,"concede")
    def _finish(self,winner,reason):self.winner=winner;self.finished_reason=reason;self.phase="finished";self.pending_attack=None;self.chain=[]
    def expire(self):
        if self.finished:return False
        self._finish(None,"inactivity timeout");self._event(None,"duel_expired");return True
    def is_expired(self,now,timeout):return not self.finished and int(now)-self.updated_at>=int(timeout)
    def public_view(self):
        def one(p):
            def mon(m):
                if not m:return None
                if not m.face_up:return {"face_up":False,"position":m.position}
                c=self.card(m.uid);return {"face_up":True,"name":c.name,"position":m.position,"attack":c.attack,"defense":c.defense}
            return {"user_id":p.user_id,"life":p.life,"deck_count":len(p.deck),"hand_count":len(p.hand),"graveyard":[self.card(x).name for x in p.graveyard],"banished_count":len(p.banished),"monsters":[mon(x) for x in p.monsters],"spells":[{"face_up":x.face_up} if x else None for x in p.spells]}
        return {"game_id":self.game_id,"turn":self.turn,"phase":self.phase,"active_user":None if self.finished else self.active_user,"players":[one(self.players[x]) for x in self.order],"chain":[x["key"] for x in self.chain],"state_version":self.state_version,"winner":self.winner,"finished_reason":self.finished_reason}
    def to_raw(self):return {"game_id":self.game_id,"order":self.order,"players":{str(k):{**asdict(v),"monsters":[asdict(x) if x else None for x in v.monsters],"spells":[asdict(x) if x else None for x in v.spells]} for k,v in self.players.items()},"cards":self.cards,"next_uid":self.next_uid,"active_index":self.active_index,"turn":self.turn,"phase":self.phase,"winner":self.winner,"finished_reason":self.finished_reason,"pending_attack":self.pending_attack,"chain":self.chain,"history":self.history[-250:],"state_version":self.state_version,"created_at":self.created_at,"updated_at":self.updated_at}
    @classmethod
    def from_raw(cls,r):
        g=cls.__new__(cls);g.game_id=int(r["game_id"]);g.order=[int(x) for x in r["order"]];g.players={}
        for k,v in r["players"].items():d=dict(v);d["monsters"]=[FieldCard(**x) if x else None for x in d["monsters"]];d["spells"]=[FieldCard(**x) if x else None for x in d["spells"]];g.players[int(k)]=Player(**d)
        g.cards={int(k):v for k,v in r["cards"].items()};g.next_uid=int(r["next_uid"]);g.active_index=int(r["active_index"]);g.turn=int(r["turn"]);g.phase=r["phase"];g.winner=r.get("winner");g.finished_reason=r.get("finished_reason");g.pending_attack=r.get("pending_attack");g.chain=list(r.get("chain",[]));g.history=list(r.get("history",[]));g.state_version=int(r.get("state_version",len(g.history)));g.created_at=int(r.get("created_at",time.time()));g.updated_at=int(r.get("updated_at",g.created_at));return g
