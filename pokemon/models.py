import random
import uuid
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from typing import Optional

from .catching import BALLS,attempt_catch
from .catalog_versions import CATALOG_VERSIONS
from .data import EVOLUTIONS, MOVES, NATURES, SPECIES, experience_to_next, moves_for_level
from .rulesets import resolve_ruleset


class BattleError(ValueError):
    pass


@dataclass
class OwnedPokemon:
    instance_id: str
    species_id: int
    level: int = 5
    experience: int = 0
    shiny: bool = False
    nickname: Optional[str] = None
    caught_guild_id: Optional[int] = None
    nature: str = "Hardy"
    ability: str = ""
    moves: tuple = ()
    move_pp: dict = field(default_factory=dict)
    ivs: dict = field(default_factory=dict)
    evs: dict = field(default_factory=dict)
    gender: str = "unknown"
    origin: str = "wild"
    caught_at: Optional[str] = None
    current_hp: Optional[int] = None
    status: str = ""
    status_turns: int = 0
    pending_moves: list = field(default_factory=list)
    catalog_version: str = "bundled-gen1-rby-v1"

    def __post_init__(self):
        if not self.moves:
            self.moves = moves_for_level(self.species_id, self.level)
        if not self.move_pp:
            self.move_pp = {key: MOVES[key].pp for key in self.moves}
        for key in ("hp", "attack", "defense", "special_attack", "special_defense", "speed"):
            self.evs.setdefault(key, 0)
            self.ivs.setdefault(key, 0)

    @classmethod
    def create(
        cls,
        instance_id: str,
        species_id: int,
        level: int = 5,
        *,
        seed: Optional[int] = None,
        shiny: bool = False,
        guild_id: Optional[int] = None,
        catalog_version: str = "bundled-gen1-rby-v1",
    ):
        rng = random.Random(seed)
        species=SPECIES[species_id]
        if species.gender_rate<0:
            gender="genderless"
        else:
            gender="female" if rng.randrange(8)<species.gender_rate else "male"
        return cls(
            instance_id,
            species_id,
            level,
            0,
            shiny,
            None,
            guild_id,
            rng.choice(NATURES),
            rng.choice(species.abilities) if species.abilities else "",
            moves_for_level(species_id, level),
            {key: MOVES[key].pp for key in moves_for_level(species_id, level)},
            {
                key: rng.randrange(32)
                for key in ("hp", "attack", "defense", "special_attack", "special_defense", "speed")
            },
            {key:0 for key in ("hp","attack","defense","special_attack","special_defense","speed")},
            gender,
            "wild" if guild_id is not None else "starter",
            datetime.now(timezone.utc).isoformat(),
            catalog_version=catalog_version,
        )

    def raw(self):
        result = asdict(self)
        result["moves"] = list(self.moves)
        return result

    @classmethod
    def from_raw(cls, raw):
        allowed = {item.name for item in fields(cls)}
        data = {key: value for key, value in dict(raw).items() if key in allowed}
        data["moves"] = tuple(data.get("moves") or moves_for_level(int(data["species_id"]), int(data.get("level",5))))
        data.setdefault("move_pp", {key: MOVES[key].pp for key in data["moves"]})
        data.setdefault("ivs", {})
        data.setdefault("evs",{})
        data.setdefault("gender","unknown")
        data.setdefault("origin","wild" if data.get("caught_guild_id") is not None else "starter")
        data.setdefault("current_hp",None)
        data.setdefault("status","")
        data.setdefault("status_turns",0)
        data.setdefault("pending_moves",[])
        data.setdefault("catalog_version","bundled-gen1-rby-v1")
        CATALOG_VERSIONS.get(data["catalog_version"])
        return cls(**data)

    def gain_experience(self, amount: int):
        self.experience += max(0, int(amount))
        levels = 0
        evolved_from = None
        learned = []
        while self.level < 100 and self.experience >= experience_to_next(self.species_id,self.level):
            self.experience -= experience_to_next(self.species_id,self.level)
            self.level += 1
            levels += 1
            evolution = EVOLUTIONS.get(self.species_id)
            if evolution and self.level >= evolution[1] and evolution[0] in SPECIES:
                evolved_from = self.species_id
                self.species_id = evolution[0]
            for learned_level,move in SPECIES[self.species_id].learnset:
                if learned_level!=self.level or move in self.moves or move in self.pending_moves:continue
                if len(self.moves)<4:
                    self.moves=tuple((*self.moves,move));self.move_pp[move]=MOVES[move].pp;learned.append(move)
                else:self.pending_moves.append(move)
        return levels, evolved_from, learned


def pokemon_max_hp(pokemon):
    species=SPECIES[pokemon.species_id]
    iv=int(pokemon.ivs.get("hp",0))
    return ((2*species.hp+iv)*pokemon.level)//100+pokemon.level+10


@dataclass
class Battle:
    encounter_id: int
    user_id: int
    guild_id: int
    channel_id: int
    message_id: int
    player: OwnedPokemon
    wild_species_id: int
    wild_level: int
    player_hp: int
    wild_hp: int
    turn: int = 1
    state: str = "active"
    result: Optional[str] = None
    seed: int = 0
    rolls: int = 0
    experience_award: int = 0
    wild_pp: dict = field(default_factory=dict)
    player_status: str = ""
    wild_status: str = ""
    last_action: str = ""
    party: list = field(default_factory=list)
    party_hp: dict = field(default_factory=dict)
    party_status: dict = field(default_factory=dict)
    party_status_turns: dict = field(default_factory=dict)
    action_history: list = field(default_factory=list)
    action_count: int = 0
    battle_kind: str = "wild"
    gym_key: str = ""
    wild_gender: str = "unknown"
    player_stages: dict = field(default_factory=dict)
    wild_stages: dict = field(default_factory=dict)
    levels_gained: int = 0
    evolved_from: Optional[int] = None
    learned_moves: list = field(default_factory=list)
    pending_moves: list = field(default_factory=list)
    ruleset: str = "standard"
    mechanics_generation: int = 9
    content_generation: int = 1
    catalog_version: str = "bundled-gen1-rby-v1"
    participants: list = field(default_factory=list)
    experience_awards: dict = field(default_factory=dict)
    progression_events: list = field(default_factory=list)
    player_status_turns: int = 0
    trainer_name: str = "Trainer"
    wild_status_turns: int = 0
    player_confusion_turns: int = 0
    wild_confusion_turns: int = 0
    last_ball: str = ""
    catch_shakes: int = 0

    def __post_init__(self):
        if not self.party:
            self.party = [self.player]
        self.party_hp.setdefault(self.player.instance_id, self.player_hp)
        self.party_status.setdefault(self.player.instance_id, self.player_status)
        self.party_status_turns.setdefault(self.player.instance_id,self.player_status_turns or self.player.status_turns)
        if self.player.current_hp is not None:self.player_hp=max(0,min(self.max_hp(self.player),int(self.player.current_hp)))
        if self.player.status:self.player_status=self.player.status
        self.party_status[self.player.instance_id]=self.player_status
        self.party_status_turns[self.player.instance_id]=self.player_status_turns or self.player.status_turns
        if self.player_status=="sleep" and self.player_status_turns<=0:self.player_status_turns=1
        if self.wild_status=="sleep" and self.wild_status_turns<=0:self.wild_status_turns=1
        if self.player.instance_id not in self.participants:self.participants.append(self.player.instance_id)

    def rules(self):
        return resolve_ruleset(self.ruleset)

    def initialize_party(self, party):
        self.party = list(party)
        self.party_hp = {
            pokemon.instance_id:max(0,min(self.max_hp(pokemon),int(pokemon.current_hp)))
            if pokemon.current_hp is not None else self.max_hp(pokemon)
            for pokemon in self.party
        }
        self.party_status = {pokemon.instance_id:pokemon.status for pokemon in self.party}
        self.party_status_turns = {pokemon.instance_id:pokemon.status_turns for pokemon in self.party}
        conscious=next((pokemon for pokemon in self.party if self.party_hp[pokemon.instance_id]>0),self.party[0])
        self.player = conscious
        self.player_hp = self.party_hp[conscious.instance_id]
        self.player_status=self.party_status.get(conscious.instance_id,"")
        self.player_status_turns=self.party_status_turns.get(conscious.instance_id,0)

    @property
    def needs_switch(self):
        return self.player_hp <= 0 and any(
            self.party_hp.get(pokemon.instance_id, 0) > 0
            for pokemon in self.party
            if pokemon.instance_id != self.player.instance_id
        )

    def switch_to(self, index):
        if self.state != "active":
            raise BattleError("This encounter is over.")
        if not 0 <= index < len(self.party):
            raise BattleError("That party slot is unavailable.")
        candidate=self.party[index]
        if candidate.instance_id==self.player.instance_id:
            raise BattleError("That Pokémon is already active.")
        if self.party_hp.get(candidate.instance_id,0)<=0:
            raise BattleError("That Pokémon has fainted.")
        self.party_hp[self.player.instance_id]=self.player_hp
        self.party_status[self.player.instance_id]=self.player_status
        self.party_status_turns[self.player.instance_id]=self.player_status_turns
        self.player_confusion_turns=0
        self.player=candidate
        if candidate.instance_id not in self.participants:self.participants.append(candidate.instance_id)
        self.player_hp=self.party_hp[candidate.instance_id]
        self.player_status=self.party_status.get(candidate.instance_id,"")
        self.player_status_turns=self.party_status_turns.get(candidate.instance_id,0)
        self.last_action=f"Go, {SPECIES[candidate.species_id].name}!"
        self.result=None
        self._record("switch")

    def switch_next(self):
        if self.state != "active":
            raise BattleError("This encounter is over.")
        self.party_hp[self.player.instance_id] = self.player_hp
        self.party_status[self.player.instance_id] = self.player_status
        self.party_status_turns[self.player.instance_id] = self.player_status_turns
        if not self.party:
            raise BattleError("No party Pokémon are available.")
        start = next(
            (index for index,item in enumerate(self.party) if item.instance_id==self.player.instance_id),
            0,
        )
        for offset in range(1, len(self.party) + 1):
            candidate = self.party[(start + offset) % len(self.party)]
            if self.party_hp.get(candidate.instance_id, 0) > 0:
                self.switch_to((start + offset) % len(self.party))
                return
        raise BattleError("No conscious party Pokémon remain.")

    def rng(self):
        self.rolls += 1
        return random.Random(self.seed + self.rolls * 7919)

    @staticmethod
    def stage_stat(value, stage):
        stage=max(-6,min(6,int(stage)))
        return max(1,value*(2+stage)//2) if stage>=0 else max(1,value*2//(2-stage))

    @staticmethod
    def scaled_stat(species, level, name, iv=0):
        base=getattr(species,name)
        if not base and name=="special_attack":base=species.attack
        if not base and name=="special_defense":base=species.defense
        if name=="hp":return ((2*base+int(iv))*level)//100+level+10
        return ((2*base+int(iv))*level)//100+5

    @staticmethod
    def stat(pokemon: OwnedPokemon, name: str):
        return Battle.scaled_stat(SPECIES[pokemon.species_id],pokemon.level,name,pokemon.ivs.get(name,0))

    def wild_stat(self,name):
        return self.scaled_stat(SPECIES[self.wild_species_id],self.wild_level,name)

    def max_hp(self, pokemon):
        return pokemon_max_hp(pokemon)

    @property
    def wild_max_hp(self):
        base = SPECIES[self.wild_species_id].hp
        return ((2 * base) * self.wild_level) // 100 + self.wild_level + 10

    def use_move(self, index):
        if self.state != "active":
            raise BattleError("This encounter is over.")
        if self.needs_switch:
            raise BattleError("Switch to another party Pokémon first.")
        move_keys = self.player.moves or SPECIES[self.player.species_id].moves
        if not 0 <= index < len(move_keys):
            raise BattleError("That move is unavailable.")
        player_key = move_keys[index]
        if self.player.move_pp.get(player_key, MOVES[player_key].pp) <= 0:
            raise BattleError("That move has no PP remaining.")
        self.player.move_pp[player_key] = self.player.move_pp.get(
            player_key, MOVES[player_key].pp
        ) - 1
        wild = SPECIES[self.wild_species_id]
        wild_moves = moves_for_level(self.wild_species_id, self.wild_level)
        if not self.wild_pp:
            self.wild_pp = {key: MOVES[key].pp for key in wild_moves}
        available = [key for key in wild_moves if self.wild_pp.get(key, 0) > 0]
        wild_key = available[self.rng().randrange(len(available))] if available else wild_moves[0]
        if available:
            self.wild_pp[wild_key] -= 1
        player_move = MOVES[player_key]
        wild_move = MOVES[wild_key]
        player_speed = self.combat_speed(True)
        wild_speed = self.combat_speed(False)
        player_order=(player_move.priority,player_speed)
        wild_order=(wild_move.priority,wild_speed)
        player_first=(self.rng().randrange(2)==0) if player_order==wild_order else player_order>wild_order
        self.player_acted=self.wild_acted=False
        self.player_flinched=self.wild_flinched=False
        actions = (
            (self._player_attack, player_move),
            (self._wild_attack, wild_move),
        ) if player_first else (
            (self._wild_attack, wild_move),
            (self._player_attack, player_move),
        )
        lines = []
        for action, move in actions:
            if self.state != "active" or self.player_hp <= 0 or self.wild_hp <= 0:
                break
            lines.append(action(move))
        self.turn += 1
        self._end_turn_status()
        self.last_action = " ".join(lines)
        self._finish_if_needed()
        self._record(f"move:{player_key}")

    def combat_speed(self,player):
        speed=self.stat(self.player,"speed") if player else self.wild_stat("speed")
        status=self.player_status if player else self.wild_status
        return self.rules().paralysis_speed(speed) if status=="paralysis" else speed

    def _apply_stat_changes(self, move, player):
        user_stages=self.player_stages if player else self.wild_stages
        target_stages=self.wild_stages if player else self.player_stages
        changed=[]
        for stat,change in move.stat_changes:
            stages=user_stages if change>0 else target_stages
            stages[stat]=max(-6,min(6,int(stages.get(stat,0))+change))
            changed.append(f"{stat.replace('_',' ')} {'rose' if change>0 else 'fell'}")
        return changed

    def _set_status(self,status,target_player,rng):
        species=SPECIES[self.player.species_id] if target_player else SPECIES[self.wild_species_id]
        target_name=species.name
        if status=="flinch":
            acted="player_acted" if target_player else "wild_acted"
            if getattr(self,acted,False):return ""
            setattr(self,"player_flinched" if target_player else "wild_flinched",True);return status
        if status=="confusion":
            attr="player_confusion_turns" if target_player else "wild_confusion_turns"
            if getattr(self,attr)>0:return ""
            setattr(self,attr,self.rules().status_duration(status,rng));return status
        current=self.player_status if target_player else self.wild_status
        if current or self.rules().status_immune(status,species.types):return ""
        if target_player:self.player_status=status
        else:self.wild_status=status
        if status=="sleep":
            turns=self.rules().status_duration(status,rng)
            if target_player:self.player_status_turns=turns
            else:self.wild_status_turns=turns
        return status

    def _status_action(self, move, player, rng):
        user_name=SPECIES[self.player.species_id].name if player else SPECIES[self.wild_species_id].name
        target_name=SPECIES[self.wild_species_id].name if player else SPECIES[self.player.species_id].name
        changes=self._apply_stat_changes(move,player)
        if move.healing:
            maximum=self.max_hp(self.player) if player else self.wild_max_hp
            amount=max(1,maximum*move.healing//100)
            if player:self.player_hp=min(maximum,self.player_hp+amount)
            else:self.wild_hp=min(maximum,self.wild_hp+amount)
            changes.append(f"restored {amount} HP")
        if move.name=="Haze":
            self.player_stages.clear();self.wild_stages.clear();changes.append("all stat changes were eliminated")
        if move.name=="Rest":
            if player:self.player_hp=self.max_hp(self.player);self.player_status="sleep";self.player_status_turns=2
            else:self.wild_hp=self.wild_max_hp;self.wild_status="sleep";self.wild_status_turns=2
            changes.append("fell asleep and restored its HP")
        if move.status in {"burn","poison","paralysis","sleep","freeze","confusion","flinch"}:
            applied=self._set_status(move.status,not player,rng)
            if applied:changes.append(f"{target_name} is {applied}")
        if move.name in {"Teleport","Roar","Whirlwind"} and self.battle_kind=="wild":
            self.state="ran";self.result=f"{user_name} used {move.name}. {SPECIES[self.wild_species_id].name} escaped!"
            changes.append(f"{SPECIES[self.wild_species_id].name} escaped")
        detail="; ".join(changes) if changes else "but nothing happened"
        return f"{user_name} used {move.name}; {detail}."

    def _confusion_damage(self,player,rng):
        pokemon=self.player if player else None;species=SPECIES[pokemon.species_id] if player else SPECIES[self.wild_species_id]
        level=pokemon.level if player else self.wild_level
        attack=self.stat(pokemon,"attack") if player else self.wild_stat("attack")
        defense=self.stat(pokemon,"defense") if player else self.wild_stat("defense")
        if (self.player_status if player else self.wild_status)=="burn":attack=self.rules().burned_attack(attack)
        base=max(1,(((2*level//5+2)*40*attack//max(1,defense))//50)+2)
        return max(1,base*(85+rng.randrange(16))//100)

    def _can_act(self, player, rng):
        flinch="player_flinched" if player else "wild_flinched"
        if getattr(self,flinch,False):
            setattr(self,flinch,False)
            name=SPECIES[self.player.species_id].name if player else f"Wild {SPECIES[self.wild_species_id].name}"
            return False,f"{name} flinched and could not move."
        status=self.player_status if player else self.wild_status
        name=SPECIES[self.player.species_id].name if player else f"Wild {SPECIES[self.wild_species_id].name}"
        if status=="paralysis" and rng.randrange(100)<25:return False,f"{name} is paralyzed."
        if status=="sleep":
            attr="player_status_turns" if player else "wild_status_turns";remaining=max(0,getattr(self,attr)-1);setattr(self,attr,remaining)
            if remaining:return False,f"{name} is asleep."
            if player:self.player_status=""
            else:self.wild_status=""
        if status=="freeze":
            if rng.randrange(100)>=20:return False,f"{name} is frozen solid."
            if player:self.player_status=""
            else:self.wild_status=""
        attr="player_confusion_turns" if player else "wild_confusion_turns";remaining=getattr(self,attr)
        if remaining:
            remaining-=1;setattr(self,attr,remaining)
            if remaining and rng.randrange(3)==0:
                damage=self._confusion_damage(player,rng)
                if player:self.player_hp=max(0,self.player_hp-damage)
                else:self.wild_hp=max(0,self.wild_hp-damage)
                return False,f"{name} hurt itself in confusion for {damage} damage."
        return True,""

    @staticmethod
    def effectiveness_line(move,target_id,ruleset="standard"):
        if move.power<=0:return ""
        value=resolve_ruleset(ruleset).effectiveness(move.type,SPECIES[target_id].types)
        if value==0:return f"It doesn't affect {SPECIES[target_id].name}..."
        if value>1:return "It's super effective!"
        if value<1:return "It's not very effective..."
        return ""

    @staticmethod
    def status_line(name,status):
        return {
            "burn":f"{name} was burned!",
            "poison":f"{name} was poisoned!",
            "paralysis":f"{name} is paralyzed! It may be unable to move!",
            "sleep":f"{name} fell asleep!",
            "freeze":f"{name} was frozen solid!",
            "confusion":f"{name} became confused!",
        }.get(status,"")

    @classmethod
    def attack_line(cls,attacker,target_id,move,damage,critical=False,status="",extra=(),ruleset="standard"):
        parts=[f"{attacker} used {move.name} and dealt {damage} damage."]
        if critical and damage>0:parts.append("A critical hit!")
        matchup=cls.effectiveness_line(move,target_id,ruleset)
        if matchup:parts.append(matchup)
        if status:parts.append(cls.status_line(SPECIES[target_id].name,status))
        parts.extend(f"{item}." for item in extra if item)
        return " ".join(parts)

    def _player_attack(self, move):
        self.player_acted=True
        rng=self.rng();allowed,message=self._can_act(True,rng)
        if not allowed:return message
        if rng.randrange(100)>=move.accuracy:return f"{SPECIES[self.player.species_id].name} used {move.name}, but it missed."
        if self.rules().move_category(move)=="status":return self._status_action(move,True,rng)
        critical=rng.randrange(self.rules().critical_denominator(move,self.combat_speed(True)))==0
        category=self.rules().move_category(move)
        attack_name,defense_name=self.rules().damage_stats(move)
        attack_stage=self.rules().effective_stage(self.player_stages.get(attack_name,0),critical=critical,offensive=True)
        defense_stage=self.rules().effective_stage(self.wild_stages.get(defense_name,0),critical=critical,offensive=False)
        attack=self.stage_stat(self.stat(self.player,attack_name),attack_stage)
        if category=="physical" and self.player_status=="burn":attack=self.rules().burned_attack(attack)
        defense=self.stage_stat(self.wild_stat(defense_name),defense_stage)
        damage=self._damage(attack,self.wild_species_id,self.player.level,move,rng,critical,self.wild_hp,SPECIES[self.player.species_id].types,defense)
        self.wild_hp=max(0,self.wild_hp-damage)
        if move.drain>0:self.player_hp=min(self.max_hp(self.player),self.player_hp+max(1,damage*move.drain//100))
        elif move.drain<0:self.player_hp=max(0,self.player_hp-max(1,damage*(-move.drain)//100))
        applied_status="";stat_changes=[]
        if move.status and rng.randrange(100)<move.status_chance:applied_status=self._set_status(move.status,False,rng)
        if move.stat_changes and rng.randrange(100)<move.stat_chance:stat_changes=self._apply_stat_changes(move,True)
        return self.attack_line(SPECIES[self.player.species_id].name,self.wild_species_id,move,damage,critical,applied_status,stat_changes,ruleset=self.ruleset)

    def _wild_attack(self, move):
        self.wild_acted=True
        rng=self.rng();allowed,message=self._can_act(False,rng)
        if not allowed:return message
        if rng.randrange(100)>=move.accuracy:return f"{SPECIES[self.wild_species_id].name} used {move.name}, but it missed."
        if self.rules().move_category(move)=="status":return self._status_action(move,False,rng)
        critical=rng.randrange(self.rules().critical_denominator(move,self.combat_speed(False)))==0
        wild=SPECIES[self.wild_species_id];category=self.rules().move_category(move);attack_name,defense_name=self.rules().damage_stats(move)
        attack_stage=self.rules().effective_stage(self.wild_stages.get(attack_name,0),critical=critical,offensive=True)
        defense_stage=self.rules().effective_stage(self.player_stages.get(defense_name,0),critical=critical,offensive=False)
        attack=self.stage_stat(self.wild_stat(attack_name),attack_stage)
        if category=="physical" and self.wild_status=="burn":attack=self.rules().burned_attack(attack)
        defense=self.stage_stat(self.stat(self.player,defense_name),defense_stage)
        damage=self._damage(attack,self.player.species_id,self.wild_level,move,rng,critical,self.player_hp,wild.types,defense)
        self.player_hp=max(0,self.player_hp-damage)
        if move.drain>0:self.wild_hp=min(self.wild_max_hp,self.wild_hp+max(1,damage*move.drain//100))
        elif move.drain<0:self.wild_hp=max(0,self.wild_hp-max(1,damage*(-move.drain)//100))
        applied_status="";stat_changes=[]
        if move.status and rng.randrange(100)<move.status_chance:applied_status=self._set_status(move.status,True,rng)
        if move.stat_changes and rng.randrange(100)<move.stat_chance:stat_changes=self._apply_stat_changes(move,False)
        return self.attack_line(wild.name,self.player.species_id,move,damage,critical,applied_status,stat_changes,ruleset=self.ruleset)

    def _end_turn_status(self):
        if self.player_status in {"poison","burn"} and self.player_hp>0:
            divisor=self.rules().residual_divisor(self.player_status);self.player_hp=max(0,self.player_hp-max(1,self.max_hp(self.player)//divisor))
        if self.wild_status in {"poison","burn"} and self.wild_hp>0:
            divisor=self.rules().residual_divisor(self.wild_status);self.wild_hp=max(0,self.wild_hp-max(1,self.wild_max_hp//divisor))

    def _award_experience(self,amount):
        self.experience_award=max(0,int(amount));self.experience_awards={};self.progression_events=[]
        eligible=[item for item in self.party if item.instance_id in self.participants and self.party_hp.get(item.instance_id,0)>0]
        if not eligible or not self.experience_award:return ""
        share=max(1,self.experience_award//len(eligible));details=[]
        for pokemon in eligible:
            previous_name=SPECIES[pokemon.species_id].name;previous_pending=set(pokemon.pending_moves)
            levels,evolved,learned=pokemon.gain_experience(share);current_name=SPECIES[pokemon.species_id].name
            self.experience_awards[pokemon.instance_id]=share
            event={"instance_id":pokemon.instance_id,"levels":levels,"evolved_from":evolved,"learned_moves":list(learned),"pending_moves":[move for move in pokemon.pending_moves if move not in previous_pending]}
            self.progression_events.append(event)
            detail=f" {current_name} gained {share} XP."
            if levels:detail+=f" {previous_name} grew to Lv. {pokemon.level}!"
            if evolved:detail+=f" What? {previous_name} evolved into {current_name}!"
            if learned:detail+=f" {current_name} learned "+", ".join(MOVES[key].name for key in learned)+"!"
            if event["pending_moves"]:detail+=f" {current_name} is trying to learn "+", ".join(MOVES[key].name for key in event["pending_moves"])+"!"
            details.append(detail)
            if pokemon.instance_id==self.player.instance_id:
                self.levels_gained=levels;self.evolved_from=evolved;self.learned_moves=list(learned);self.pending_moves=event["pending_moves"]
        return "".join(details)

    def _finish_if_needed(self):
        if self.wild_hp == 0:
            self.state = "won"
            detail=self._award_experience(self.rules().experience_reward(SPECIES[self.wild_species_id],self.wild_level,trainer=self.battle_kind=="gym"))
            self.result="The wild Pokémon fainted."+detail
        elif self.player_hp == 0:
            self.party_hp[self.player.instance_id]=0
            if self.needs_switch:
                self.result = f"{SPECIES[self.player.species_id].name} fainted. Switch Pokémon."
            else:
                self.state = "lost"
                self.result = f"{SPECIES[self.wild_species_id].name} escaped! Your party has no conscious Pokémon. Go to a Pokémon Center to heal."

    def _damage(self, attack, target_id, level, move, rng, critical=False, target_hp=None, attacker_types=(), defense=None):
        if move.effect.startswith("fixed-"):return int(move.effect.split("-",1)[1])
        if move.effect=="level":return level
        if move.effect=="half":return max(1,int(target_hp or 1)//2)
        if move.effect=="ohko":return int(target_hp or 0)
        if move.effect=="counter" or move.power<=0:return 0
        target=SPECIES[target_id]
        category=self.rules().move_category(move)
        if defense is None:defense=(target.special_defense or target.defense) if category=="special" else target.defense
        base=max(1,(((2*level//5+2)*move.power*attack//max(1,defense))//50)+2)
        modifier=self.rules().damage_modifier(move.type,attacker_types,target.types,critical,rng)
        if modifier==0:return 0
        per_hit=max(1,int(base*modifier))
        hits=move.min_hits if move.max_hits<=move.min_hits else move.min_hits+rng.randrange(move.max_hits-move.min_hits+1)
        return per_hit*hits

    def _wild_response(self):
        wild = SPECIES[self.wild_species_id]
        wild_moves = moves_for_level(self.wild_species_id, self.wild_level)
        if not self.wild_pp:
            self.wild_pp = {key: MOVES[key].pp for key in wild_moves}
        available = [key for key in wild_moves if self.wild_pp.get(key, 0) > 0]
        key = available[self.rng().randrange(len(available))] if available else wild_moves[0]
        if available:
            self.wild_pp[key] -= 1
        self.last_action = self._wild_attack(MOVES[key])
        self.turn += 1
        self._end_turn_status()
        self._finish_if_needed()

    def throw_ball(self,ball_key="poke_ball"):
        if self.state!="active":raise BattleError("This encounter is over.")
        if self.needs_switch:raise BattleError("Switch to another party Pokémon first.")
        if ball_key not in BALLS:raise BattleError("That Poké Ball is unavailable.")
        ball=BALLS[ball_key];result=attempt_catch(SPECIES[self.wild_species_id],self.wild_max_hp,self.wild_hp,self.wild_status,ball_key,self.rules(),self.rng())
        self.last_ball=ball_key;self.catch_shakes=result.shakes
        if result.caught:
            self.state="caught";detail=self._award_experience(self.rules().experience_reward(SPECIES[self.wild_species_id],self.wild_level,caught=True))
            self.result=f"You threw a {ball.name}. Caught {SPECIES[self.wild_species_id].name}!"+detail
            self._record(f"ball:{ball_key}:caught");return True
        self._wild_response();ball_line=f"You threw a {ball.name}, but {SPECIES[self.wild_species_id].name} broke free!"
        self.last_action=f"{ball_line} {self.last_action}"
        if self.result:self.result=f"{self.last_action} {self.result}"
        self._record(f"ball:{ball_key}:failed:{result.shakes}");return False

    def run(self):
        if self.state != "active":
            raise BattleError("This encounter is over.")
        self.state = "ran"
        self.result = "You forfeited the Gym challenge." if self.battle_kind=="gym" else "You got away safely."
        self._record("run")

    def caught(self, instance_id=None):
        if self.state != "caught":
            raise BattleError("The Pokémon was not caught.")
        identity = instance_id or uuid.uuid4().hex
        shiny = random.Random(self.seed + 4049).randrange(4096) == 0
        pokemon=OwnedPokemon.create(
            identity,self.wild_species_id,self.wild_level,seed=self.seed+991,
            shiny=shiny,guild_id=self.guild_id,catalog_version=self.catalog_version,
        )
        if self.wild_gender!="unknown":pokemon.gender=self.wild_gender
        return pokemon

    def _record(self, action):
        self.action_count += 1
        self.action_history.append({
            "sequence": self.action_count,
            "turn": self.turn,
            "action": str(action)[:80],
            "rolls": self.rolls,
            "player_hp": self.player_hp,
            "wild_hp": self.wild_hp,
            "state": self.state,
        })
        self.action_history = self.action_history[-100:]

    def raw(self):
        result = asdict(self)
        result["player"] = self.player.raw()
        return result

    @classmethod
    def from_raw(cls, raw):
        data = dict(raw)
        data["player"] = OwnedPokemon.from_raw(data["player"])
        data.setdefault("experience_award", 0)
        data.setdefault("wild_pp", {})
        data.setdefault("player_status", "")
        data.setdefault("wild_status", "")
        data.setdefault("last_action", "")
        data["party"]=[OwnedPokemon.from_raw(item) for item in data.get("party",[])]
        for index,item in enumerate(data["party"]):
            if item.instance_id==data["player"].instance_id:
                data["party"][index]=data["player"]
                break
        data.setdefault("party_hp", {})
        data.setdefault("party_status", {})
        data.setdefault("party_status_turns", {})
        data.setdefault("action_history", [])
        data.setdefault("action_count", len(data["action_history"]))
        data["action_history"]=list(data["action_history"])[-100:]
        data.setdefault("player_stages", {})
        data.setdefault("wild_stages", {})
        data.setdefault("levels_gained",0)
        data.setdefault("evolved_from",None)
        data.setdefault("learned_moves",[])
        data.setdefault("pending_moves",[])
        data.setdefault("ruleset","standard")
        data.setdefault("mechanics_generation",resolve_ruleset(data["ruleset"]).mechanics_generation)
        data.setdefault("content_generation",1)
        data.setdefault("catalog_version",CATALOG_VERSIONS.for_generation(data["content_generation"],bundled=data["content_generation"]==1).key)
        CATALOG_VERSIONS.get(data["catalog_version"])
        data.setdefault("participants",[data["player"].instance_id])
        data.setdefault("experience_awards",{})
        data.setdefault("progression_events",[])
        data.setdefault("player_status_turns",0)
        data.setdefault("trainer_name","Trainer")
        data.setdefault("wild_status_turns",0)
        data.setdefault("player_confusion_turns",0)
        data.setdefault("wild_confusion_turns",0)
        data.setdefault("last_ball","")
        data.setdefault("catch_shakes",0)
        battle=cls(**data)
        if not battle.party:battle.party=[battle.player]
        return battle
