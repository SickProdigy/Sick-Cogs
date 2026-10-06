from dataclasses import dataclass
import json
from pathlib import Path

@dataclass(frozen=True)
class Species:
    id: int
    name: str
    types: tuple
    hp: int
    attack: int
    defense: int
    speed: int
    catch_rate: int
    moves: tuple
    learnset: tuple = ()
    abilities: tuple = ()
    gender_rate: int = -1
    special_attack: int = 0
    special_defense: int = 0

@dataclass(frozen=True)
class Move:
    name: str
    type: str
    power: int
    accuracy: int = 100
    pp: int = 35
    priority: int = 0
    status: str = ""
    status_chance: int = 0
    category: str = "physical"
    effect: str = "damage"
    stat_changes: tuple = ()
    stat_chance: int = 0
    min_hits: int = 1
    max_hits: int = 1
    drain: int = 0
    healing: int = 0
    crit_rate: int = 0


def _load_moves():
    raw=json.loads((Path(__file__).with_name("gen1_moves.json")).read_text(encoding="utf-8"))
    result={}
    for key,value in raw["moves"].items():
        value=dict(value)
        value["stat_changes"]=tuple(tuple(item) for item in value.get("stat_changes",()))
        result[key]=Move(**value)
    return result


MOVES=_load_moves()
SPECIES={
1:Species(1,"Bulbasaur",("grass","poison"),45,49,49,45,45,("tackle","vine_whip")),
4:Species(4,"Charmander",("fire",),39,52,43,65,45,("scratch","ember")),
7:Species(7,"Squirtle",("water",),44,48,65,43,45,("tackle","water_gun")),
10:Species(10,"Caterpie",("bug",),45,30,35,45,255,("tackle",)),
16:Species(16,"Pidgey",("normal","flying"),40,45,40,56,255,("tackle","gust")),
19:Species(19,"Rattata",("normal",),30,56,35,72,255,("tackle","quick_attack")),
23:Species(23,"Ekans",("poison",),35,60,44,55,255,("tackle","poison_sting")),
25:Species(25,"Pikachu",("electric",),35,55,40,90,190,("quick_attack","thunder_shock")),
29:Species(29,"Nidoran♀",("poison",),55,47,52,41,235,("tackle","poison_sting")),
32:Species(32,"Nidoran♂",("poison",),46,57,40,50,235,("tackle","poison_sting")),
41:Species(41,"Zubat",("poison","flying"),40,45,35,55,255,("bite","gust")),
52:Species(52,"Meowth",("normal",),40,45,35,90,255,("scratch","bite")),
}
def _load_bundled_species():
    raw=json.loads((Path(__file__).with_name("gen1.json")).read_text(encoding="utf-8"))
    for value in raw["species"]:
        item=Species(
            int(value["id"]),str(value["name"]),tuple(value["types"]),int(value["hp"]),
            int(value["attack"]),int(value["defense"]),int(value["speed"]),int(value["catch_rate"]),
            tuple(value["moves"]),tuple((int(level),str(move)) for level,move in value["learnset"]),
            tuple(value.get("abilities",())),int(value.get("gender_rate",-1)),
            int(value.get("special_attack",value["attack"])),int(value.get("special_defense",value["defense"])),
        )
        SPECIES[item.id]=item


_load_bundled_species()
SPAWN_IDS=(10,16,19,23,25,29,32,41,52)
TYPE={
("normal","rock"):.5,("normal","ghost"):0,("normal","steel"):.5,
("fire","fire"):.5,("fire","water"):.5,("fire","grass"):2,("fire","ice"):2,("fire","bug"):2,("fire","rock"):.5,("fire","dragon"):.5,("fire","steel"):2,
("water","fire"):2,("water","water"):.5,("water","grass"):.5,("water","ground"):2,("water","rock"):2,("water","dragon"):.5,
("electric","water"):2,("electric","electric"):.5,("electric","grass"):.5,("electric","ground"):0,("electric","flying"):2,("electric","dragon"):.5,
("grass","fire"):.5,("grass","water"):2,("grass","grass"):.5,("grass","poison"):.5,("grass","ground"):2,("grass","flying"):.5,("grass","bug"):.5,("grass","rock"):2,("grass","dragon"):.5,("grass","steel"):.5,
("ice","fire"):.5,("ice","water"):.5,("ice","grass"):2,("ice","ice"):.5,("ice","ground"):2,("ice","flying"):2,("ice","dragon"):2,("ice","steel"):.5,
("fighting","normal"):2,("fighting","ice"):2,("fighting","poison"):.5,("fighting","flying"):.5,("fighting","psychic"):.5,("fighting","bug"):.5,("fighting","rock"):2,("fighting","ghost"):0,("fighting","dark"):2,("fighting","steel"):2,("fighting","fairy"):.5,
("poison","grass"):2,("poison","poison"):.5,("poison","ground"):.5,("poison","rock"):.5,("poison","ghost"):.5,("poison","steel"):0,("poison","fairy"):2,
("ground","fire"):2,("ground","electric"):2,("ground","grass"):.5,("ground","poison"):2,("ground","flying"):0,("ground","bug"):.5,("ground","rock"):2,("ground","steel"):2,
("flying","electric"):.5,("flying","grass"):2,("flying","fighting"):2,("flying","bug"):2,("flying","rock"):.5,("flying","steel"):.5,
("psychic","fighting"):2,("psychic","poison"):2,("psychic","psychic"):.5,("psychic","dark"):0,("psychic","steel"):.5,
("bug","fire"):.5,("bug","grass"):2,("bug","fighting"):.5,("bug","poison"):.5,("bug","flying"):.5,("bug","psychic"):2,("bug","ghost"):.5,("bug","dark"):2,("bug","steel"):.5,("bug","fairy"):.5,
("rock","fire"):2,("rock","ice"):2,("rock","fighting"):.5,("rock","ground"):.5,("rock","flying"):2,("rock","bug"):2,("rock","steel"):.5,
("ghost","normal"):0,("ghost","psychic"):2,("ghost","ghost"):2,("ghost","dark"):.5,
("dragon","dragon"):2,("dragon","steel"):.5,("dragon","fairy"):0,
("dark","fighting"):.5,("dark","psychic"):2,("dark","ghost"):2,("dark","dark"):.5,("dark","fairy"):.5,
("steel","fire"):.5,("steel","water"):.5,("steel","electric"):.5,("steel","ice"):2,("steel","rock"):2,("steel","steel"):.5,("steel","fairy"):2,
("fairy","fire"):.5,("fairy","fighting"):2,("fairy","poison"):.5,("fairy","dragon"):2,("fairy","dark"):2,("fairy","steel"):.5,
}
def effectiveness(move_type,defender_types):
    value=1.0
    for kind in defender_types:value*=TYPE.get((move_type,kind),1.0)
    return value
def moves_for_level(species_id,level):
    species=SPECIES[species_id]
    learned=[move for learned_level,move in species.learnset if learned_level<=level]
    return tuple((learned or list(species.moves))[-4:])

def sprite(species_id,back=False,shiny=False):
    folder="back/" if back else ""
    if shiny: folder+="shiny/"
    return f"https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/{folder}{species_id}.png"

GENERATION_MAX=(151,251,386,493,649,721,809,905,1025)
def generation_for(species_id):
    for index,maximum in enumerate(GENERATION_MAX,1):
        if species_id<=maximum:return index
    return 9

NATURES=("Hardy","Lonely","Brave","Adamant","Naughty","Bold","Docile","Relaxed","Impish","Lax","Timid","Hasty","Serious","Jolly","Naive","Modest","Mild","Quiet","Bashful","Rash","Calm","Gentle","Sassy","Careful","Quirky")
EVOLUTIONS={1:(2,16),2:(3,32),4:(5,16),5:(6,36),7:(8,16),8:(9,36),10:(11,7),11:(12,10),13:(14,7),14:(15,10),16:(17,18),17:(18,36),19:(20,20),21:(22,20),23:(24,22),27:(28,22),29:(30,16),32:(33,16),41:(42,22),43:(44,21),46:(47,24),48:(49,31),50:(51,26),52:(53,28),54:(55,33),56:(57,28),60:(61,25),63:(64,16),66:(67,28),69:(70,21),72:(73,30),74:(75,25),77:(78,40),79:(80,37),81:(82,30),84:(85,31),86:(87,34),88:(89,38),92:(93,25),96:(97,26),98:(99,28),100:(101,30),104:(105,28),109:(110,35),111:(112,42),116:(117,32),118:(119,33),129:(130,20),138:(139,40),140:(141,40),147:(148,30),148:(149,55)}
