from dataclasses import dataclass

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

MOVES={
"tackle":Move("Tackle","normal",40),
"scratch":Move("Scratch","normal",40),
"vine_whip":Move("Vine Whip","grass",45,category="special"),
"ember":Move("Ember","fire",40,status="burn",status_chance=10,category="special"),
"water_gun":Move("Water Gun","water",40,category="special"),
"thunder_shock":Move("Thunder Shock","electric",40,status="paralysis",status_chance=10,category="special"),
"gust":Move("Gust","flying",40),
"quick_attack":Move("Quick Attack","normal",40,priority=1),
"poison_sting":Move("Poison Sting","poison",35,status="poison",status_chance=30),
"bite":Move("Bite","dark",60,category="special"),
}
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
SPAWN_IDS=(10,16,19,23,25,29,32,41,52)
TYPE={("fire","grass"):2,("water","fire"):2,("grass","water"):2,("electric","water"):2,
("grass","fire"):0.5,("fire","water"):0.5,("water","grass"):0.5,("electric","grass"):0.5}
def effectiveness(move_type,defender_types):
    value=1.0
    for kind in defender_types:value*=TYPE.get((move_type,kind),1.0)
    return value
def moves_for_level(species_id,level):
    species=SPECIES[species_id]
    learned=[move for learned_level,move in species.learnset if learned_level<=level]
    return tuple((learned or list(species.moves) or ["tackle"])[-4:])

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
