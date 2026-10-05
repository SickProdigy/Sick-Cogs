import random
import uuid
from dataclasses import asdict,dataclass
from typing import Optional
from .data import MOVES,SPECIES,effectiveness

class BattleError(ValueError):pass

@dataclass
class OwnedPokemon:
    instance_id:str
    species_id:int
    level:int=5
    experience:int=0
    shiny:bool=False
    nickname:Optional[str]=None
    caught_guild_id:Optional[int]=None
    def raw(self):return asdict(self)
    @classmethod
    def from_raw(cls,r):return cls(**r)

@dataclass
class Battle:
    encounter_id:int
    user_id:int
    guild_id:int
    channel_id:int
    message_id:int
    player:OwnedPokemon
    wild_species_id:int
    wild_level:int
    player_hp:int
    wild_hp:int
    turn:int=1
    state:str="active"
    result:Optional[str]=None
    seed:int=0
    rolls:int=0
    def rng(self):
        self.rolls+=1
        return random.Random(self.seed+self.rolls*7919)
    def max_hp(self,pokemon):
        return SPECIES[pokemon.species_id].hp+pokemon.level*2
    @property
    def wild_max_hp(self):return SPECIES[self.wild_species_id].hp+self.wild_level*2
    def use_move(self,index):
        if self.state!="active":raise BattleError("This encounter is over.")
        species=SPECIES[self.player.species_id]
        if not 0<=index<len(species.moves):raise BattleError("That move is unavailable.")
        move=MOVES[species.moves[index]]; rng=self.rng()
        if rng.randrange(100)<move.accuracy:
            damage=self._damage(species.attack,self.wild_species_id,self.player.level,move,rng)
            self.wild_hp=max(0,self.wild_hp-damage)
        if self.wild_hp==0:
            self.state="won";self.result="The wild Pokémon fainted.";return
        self._wild_turn()
    def _wild_turn(self):
        wild=SPECIES[self.wild_species_id]; move=MOVES[wild.moves[self.rng().randrange(len(wild.moves))]]
        rng=self.rng()
        if rng.randrange(100)<move.accuracy:
            damage=self._damage(wild.attack,self.player.species_id,self.wild_level,move,rng)
            self.player_hp=max(0,self.player_hp-damage)
        self.turn+=1
        if self.player_hp==0:self.state="lost";self.result="Your Pokémon fainted."
    def _damage(self,attack,target_id,level,move,rng):
        defense=SPECIES[target_id].defense
        base=max(1,(((2*level//5+2)*move.power*attack//max(1,defense))//50)+2)
        return max(1,int(base*effectiveness(move.type,SPECIES[target_id].types)*(85+rng.randrange(16))/100))
    def throw_ball(self):
        if self.state!="active":raise BattleError("This encounter is over.")
        rate=SPECIES[self.wild_species_id].catch_rate
        chance=min(95,max(5,int(rate/255*55+(1-self.wild_hp/self.wild_max_hp)*40)))
        if self.rng().randrange(100)<chance:
            self.state="caught";self.result="Caught!";return True
        self._wild_turn();return False
    def run(self):
        if self.state!="active":raise BattleError("This encounter is over.")
        self.state="ran";self.result="You got away safely."
    def caught(self):
        if self.state!="caught":raise BattleError("The Pokémon was not caught.")
        return OwnedPokemon(uuid.uuid4().hex,self.wild_species_id,self.wild_level,0,self.rng().randrange(4096)==0,None,self.guild_id)
    def raw(self):
        r=asdict(self);r["player"]=self.player.raw();return r
    @classmethod
    def from_raw(cls,r):
        d=dict(r);d["player"]=OwnedPokemon.from_raw(d["player"]);return cls(**d)
