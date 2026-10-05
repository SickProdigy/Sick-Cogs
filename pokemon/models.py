import random
import uuid
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from typing import Optional

from .data import EVOLUTIONS, MOVES, NATURES, SPECIES, effectiveness


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
    caught_at: Optional[str] = None

    def __post_init__(self):
        if not self.moves:
            self.moves = tuple(SPECIES[self.species_id].moves[:4])
        if not self.move_pp:
            self.move_pp = {key: MOVES[key].pp for key in self.moves}

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
        return cls(
            instance_id,
            species_id,
            level,
            0,
            shiny,
            None,
            guild_id,
            rng.choice(NATURES),
            "",
            tuple(SPECIES[species_id].moves[:4]),
            {key: MOVES[key].pp for key in SPECIES[species_id].moves[:4]},
            {
                key: rng.randrange(32)
                for key in ("hp", "attack", "defense", "speed")
            },
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
        data["moves"] = tuple(data.get("moves") or SPECIES[int(data["species_id"])].moves[:4])
        data.setdefault("move_pp", {key: MOVES[key].pp for key in data["moves"]})
        data.setdefault("ivs", {})
        return cls(**data)

    def gain_experience(self, amount: int):
        self.experience += max(0, int(amount))
        levels = 0
        evolved_from = None
        while self.level < 100 and self.experience >= self.level * self.level * 10:
            self.experience -= self.level * self.level * 10
            self.level += 1
            levels += 1
            evolution = EVOLUTIONS.get(self.species_id)
            if evolution and self.level >= evolution[1] and evolution[0] in SPECIES:
                evolved_from = self.species_id
                self.species_id = evolution[0]
                known = list(self.moves)
                for move in SPECIES[self.species_id].moves:
                    if move not in known and len(known) < 4:
                        known.append(move)
                self.moves = tuple(known)
                for move in known:self.move_pp.setdefault(move,MOVES[move].pp)
        return levels, evolved_from


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

    def __post_init__(self):
        if not self.party:
            self.party = [self.player]
        self.party_hp.setdefault(self.player.instance_id, self.player_hp)
        self.party_status.setdefault(self.player.instance_id, self.player_status)

    def initialize_party(self, party):
        self.party = list(party)
        self.party_hp = {
            pokemon.instance_id: self.max_hp(pokemon) for pokemon in self.party
        }
        self.party_status = {pokemon.instance_id: "" for pokemon in self.party}
        self.player = self.party[0]
        self.player_hp = self.party_hp[self.player.instance_id]

    @property
    def needs_switch(self):
        return self.player_hp <= 0 and any(
            self.party_hp.get(pokemon.instance_id, 0) > 0
            for pokemon in self.party
            if pokemon.instance_id != self.player.instance_id
        )

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
                self.player = candidate
                self.player_hp = self.party_hp[candidate.instance_id]
                self.player_status = self.party_status.get(candidate.instance_id, "")
                self.last_action = f"Go, {SPECIES[candidate.species_id].name}!"
                self.result = None
                return
        raise BattleError("No conscious party Pokémon remain.")

    def rng(self):
        self.rolls += 1
        return random.Random(self.seed + self.rolls * 7919)

    @staticmethod
    def stat(pokemon: OwnedPokemon, name: str):
        base = getattr(SPECIES[pokemon.species_id], name)
        iv = int(pokemon.ivs.get(name, 0))
        if name == "hp":
            return ((2 * base + iv) * pokemon.level) // 100 + pokemon.level + 10
        return ((2 * base + iv) * pokemon.level) // 100 + 5

    def max_hp(self, pokemon):
        return self.stat(pokemon, "hp")

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
        if not self.wild_pp:
            self.wild_pp = {key: MOVES[key].pp for key in wild.moves}
        available = [key for key in wild.moves if self.wild_pp.get(key, 0) > 0]
        wild_key = available[self.rng().randrange(len(available))] if available else wild.moves[0]
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
        player_first = (player_move.priority, player_speed) >= (
            wild_move.priority,
            wild_speed,
        )
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

    def _player_attack(self, move):
        if self.player_status == "paralysis" and self.rng().randrange(100) < 25:
            return f"{SPECIES[self.player.species_id].name} is paralyzed."
        rng = self.rng()
        if rng.randrange(100) >= move.accuracy:
            return f"{move.name} missed."
        critical = rng.randrange(24) == 0
        damage = self._damage(
            self.stat(self.player, "attack"),
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
        damage = self._damage(
            SPECIES[self.wild_species_id].attack,
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
            levels, evolved = self.player.gain_experience(self.experience_award)
            detail = f" Gained {self.experience_award} XP."
            if levels:
                detail += f" Reached level {self.player.level}."
            if evolved:
                detail += f" Evolved into {SPECIES[self.player.species_id].name}."
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
        defense = SPECIES[target_id].defense
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
        if not self.wild_pp:
            self.wild_pp = {key: MOVES[key].pp for key in wild.moves}
        available = [key for key in wild.moves if self.wild_pp.get(key, 0) > 0]
        key = available[self.rng().randrange(len(available))] if available else wild.moves[0]
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
            self.result = "Caught!"
            return True
        self._wild_response()
        return False

    def run(self):
        if self.state != "active":
            raise BattleError("This encounter is over.")
        self.state = "ran"
        self.result = "You got away safely."

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
        data.setdefault("party_hp", {})
        data.setdefault("party_status", {})
        battle=cls(**data)
        if not battle.party:battle.party=[battle.player]
        return battle
