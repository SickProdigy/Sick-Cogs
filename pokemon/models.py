import random
import uuid
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from typing import Optional

from .data import EVOLUTIONS, MOVES, NATURES, SPECIES, effectiveness, moves_for_level


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
        return cls(**data)

    def gain_experience(self, amount: int):
        self.experience += max(0, int(amount))
        levels = 0
        evolved_from = None
        learned = []
        while self.level < 100 and self.experience >= self.level * self.level * 10:
            self.experience -= self.level * self.level * 10
            self.level += 1
            levels += 1
            evolution = EVOLUTIONS.get(self.species_id)
            if evolution and self.level >= evolution[1] and evolution[0] in SPECIES:
                evolved_from = self.species_id
                self.species_id = evolution[0]
            for move in moves_for_level(self.species_id, self.level):
                if move in self.moves:
                    continue
                known=list(self.moves)+[move]
                if len(known)>4:
                    forgotten=known.pop(0);self.move_pp.pop(forgotten,None)
                self.moves=tuple(known);self.move_pp[move]=MOVES[move].pp;learned.append(move)
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
    action_history: list = field(default_factory=list)
    action_count: int = 0
    battle_kind: str = "wild"
    gym_key: str = ""

    def __post_init__(self):
        if not self.party:
            self.party = [self.player]
        self.party_hp.setdefault(self.player.instance_id, self.player_hp)
        self.party_status.setdefault(self.player.instance_id, self.player_status)
        if self.player.current_hp is not None:self.player_hp=max(0,min(self.max_hp(self.player),int(self.player.current_hp)))
        if self.player.status:self.player_status=self.player.status

    def initialize_party(self, party):
        self.party = list(party)
        self.party_hp = {
            pokemon.instance_id:max(0,min(self.max_hp(pokemon),int(pokemon.current_hp)))
            if pokemon.current_hp is not None else self.max_hp(pokemon)
            for pokemon in self.party
        }
        self.party_status = {pokemon.instance_id:pokemon.status for pokemon in self.party}
        conscious=next((pokemon for pokemon in self.party if self.party_hp[pokemon.instance_id]>0),self.party[0])
        self.player = conscious
        self.player_hp = self.party_hp[conscious.instance_id]
        self.player_status=self.party_status.get(conscious.instance_id,"")

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
        self.player=candidate
        self.player_hp=self.party_hp[candidate.instance_id]
        self.player_status=self.party_status.get(candidate.instance_id,"")
        self.last_action=f"Go, {SPECIES[candidate.species_id].name}!"
        self.result=None
        self._record("switch")

    def switch_next(self):
        if self.state != "active":
            raise BattleError("This encounter is over.")
        self.party_hp[self.player.instance_id] = self.player_hp
        self.party_status[self.player.instance_id] = self.player_status
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
    def stat(pokemon: OwnedPokemon, name: str):
        species=SPECIES[pokemon.species_id]
        base = getattr(species, name)
        if not base and name=="special_attack":base=species.attack
        if not base and name=="special_defense":base=species.defense
        iv = int(pokemon.ivs.get(name, 0))
        if name == "hp":
            return ((2 * base + iv) * pokemon.level) // 100 + pokemon.level + 10
        return ((2 * base + iv) * pokemon.level) // 100 + 5

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
        player_speed = self.stat(self.player, "speed")
        if self.player_status == "paralysis":
            player_speed //= 2
        wild_speed = wild.speed
        if self.wild_status == "paralysis":
            wild_speed //= 2
        player_order=(player_move.priority,player_speed)
        wild_order=(wild_move.priority,wild_speed)
        player_first=(self.rng().randrange(2)==0) if player_order==wild_order else player_order>wild_order
        actions = (
            (self._player_attack, player_move),
            (self._wild_attack, wild_move),
        ) if player_first else (
            (self._wild_attack, wild_move),
            (self._player_attack, player_move),
        )
        lines = []
        for action, move in actions:
            if self.player_hp <= 0 or self.wild_hp <= 0:
                break
            lines.append(action(move))
        self.turn += 1
        self._end_turn_status()
        self.last_action = " ".join(lines)
        self._finish_if_needed()
        self._record(f"move:{player_key}")

    def _player_attack(self, move):
        if self.player_status == "paralysis" and self.rng().randrange(100) < 25:
            return f"{SPECIES[self.player.species_id].name} is paralyzed."
        rng = self.rng()
        if rng.randrange(100) >= move.accuracy:
            return f"{move.name} missed."
        critical = rng.randrange(24) == 0
        attack_stat = "special_attack" if move.category == "special" else "attack"
        damage = self._damage(
            self.stat(self.player, attack_stat),
            self.wild_species_id,
            self.player.level,
            move,
            rng,
            critical,
        )
        self.wild_hp = max(0, self.wild_hp - damage)
        if move.status and not self.wild_status and rng.randrange(100) < move.status_chance:
            self.wild_status = move.status
        return f"{move.name} dealt {damage} damage" + (" (critical)." if critical else ".")

    def _wild_attack(self, move):
        if self.wild_status == "paralysis" and self.rng().randrange(100) < 25:
            return f"Wild {SPECIES[self.wild_species_id].name} is paralyzed."
        rng = self.rng()
        if rng.randrange(100) >= move.accuracy:
            return f"Wild {move.name} missed."
        critical = rng.randrange(24) == 0
        wild=SPECIES[self.wild_species_id]
        attack = (wild.special_attack or wild.attack) if move.category == "special" else wild.attack
        damage = self._damage(
            attack,
            self.player.species_id,
            self.wild_level,
            move,
            rng,
            critical,
        )
        self.player_hp = max(0, self.player_hp - damage)
        if move.status and not self.player_status and rng.randrange(100) < move.status_chance:
            self.player_status = move.status
        return f"Wild {move.name} dealt {damage} damage" + (" (critical)." if critical else ".")

    def _end_turn_status(self):
        if self.player_status in {"poison", "burn"} and self.player_hp > 0:
            self.player_hp = max(0, self.player_hp - max(1, self.max_hp(self.player) // 8))
        if self.wild_status in {"poison", "burn"} and self.wild_hp > 0:
            self.wild_hp = max(0, self.wild_hp - max(1, self.wild_max_hp // 8))

    def _finish_if_needed(self):
        if self.wild_hp == 0:
            self.state = "won"
            self.experience_award = self.wild_level * 20
            levels, evolved, learned = self.player.gain_experience(self.experience_award)
            detail = f" Gained {self.experience_award} XP."
            if levels:
                detail += f" Reached level {self.player.level}."
            if evolved:
                detail += f" Evolved into {SPECIES[self.player.species_id].name}."
            if learned:
                detail += " Learned " + ", ".join(MOVES[key].name for key in learned) + "."
            self.result = "The wild Pokémon fainted." + detail
            for index,item in enumerate(self.party):
                if item.instance_id==self.player.instance_id:self.party[index]=self.player
        elif self.player_hp == 0:
            self.party_hp[self.player.instance_id]=0
            if self.needs_switch:
                self.result = f"{SPECIES[self.player.species_id].name} fainted. Switch Pokémon."
            else:
                self.state = "lost"
                self.result = "Your party has no conscious Pokémon."

    def _damage(self, attack, target_id, level, move, rng, critical=False):
        target=SPECIES[target_id]
        defense = (target.special_defense or target.defense) if move.category=="special" else target.defense
        base = max(
            1,
            (((2 * level // 5 + 2) * move.power * attack // max(1, defense)) // 50)
            + 2,
        )
        modifier = effectiveness(move.type, SPECIES[target_id].types)
        if critical:
            modifier *= 1.5
        return max(1, int(base * modifier * (85 + rng.randrange(16)) / 100))

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

    def throw_ball(self):
        if self.state != "active":
            raise BattleError("This encounter is over.")
        if self.needs_switch:
            raise BattleError("Switch to another party Pokémon first.")
        rate = SPECIES[self.wild_species_id].catch_rate
        chance = min(
            95,
            max(
                5,
                int(
                    rate / 255 * 55
                    + (1 - self.wild_hp / self.wild_max_hp) * 40
                    + (10 if self.wild_status else 0)
                ),
            ),
        )
        if self.rng().randrange(100) < chance:
            self.state = "caught"
            self.result = f"Caught {SPECIES[self.wild_species_id].name}! Catching does not award battle XP."
            self._record("ball:caught")
            return True
        self._wild_response()
        self._record("ball:failed")
        return False

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
        return OwnedPokemon.create(
            identity,
            self.wild_species_id,
            self.wild_level,
            seed=self.seed + 991,
            shiny=shiny,
            guild_id=self.guild_id,
        )

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
        data.setdefault("action_history", [])
        data.setdefault("action_count", len(data["action_history"]))
        data["action_history"]=list(data["action_history"])[-100:]
        battle=cls(**data)
        if not battle.party:battle.party=[battle.player]
        return battle
