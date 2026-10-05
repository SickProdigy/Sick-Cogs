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

@dataclass(frozen=True)
class Move:
    name: str
    type: str
    power: int
    accuracy: int = 100

MOVES={
"tackle":Move("Tackle","normal",40),
"scratch":Move("Scratch","normal",40),
"vine_whip":Move("Vine Whip","grass",45),
"ember":Move("Ember","fire",40),
"water_gun":Move("Water Gun","water",40),
"thunder_shock":Move("Thunder Shock","electric",40),
"gust":Move("Gust","flying",40),
"quick_attack":Move("Quick Attack","normal",40),
"poison_sting":Move("Poison Sting","poison",35),
"bite":Move("Bite","dark",60),
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
def sprite(species_id,back=False,shiny=False):
    folder="back/" if back else ""
    if shiny: folder+="shiny/"
    return f"https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/{folder}{species_id}.png"
