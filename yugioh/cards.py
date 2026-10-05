from dataclasses import dataclass
@dataclass(frozen=True)
class Card:
    key:str; passcode:int; name:str; kind:str; level:int=0; attack:int=0; defense:int=0; text:str=""; effect:str=""; amount:int=0
    @property
    def monster(self): return self.kind=="Monster"
    @property
    def spell(self): return self.kind=="Spell"
    @property
    def trap(self): return self.kind=="Trap"
CARDS={
"mystical_elf":Card("mystical_elf",15025844,"Mystical Elf","Monster",4,800,2000),
"battle_ox":Card("battle_ox",5053103,"Battle Ox","Monster",4,1700,1000),
"celtic_guardian":Card("celtic_guardian",91152256,"Celtic Guardian","Monster",4,1400,1200),
"silver_fang":Card("silver_fang",90357090,"Silver Fang","Monster",3,1200,800),
"summoned_skull":Card("summoned_skull",70781052,"Summoned Skull","Monster",6,2500,1200),
"curse_of_dragon":Card("curse_of_dragon",28279543,"Curse of Dragon","Monster",5,2000,1500),
"dark_magician":Card("dark_magician",46986414,"Dark Magician","Monster",7,2500,2100),
"gaia":Card("gaia",6368038,"Gaia The Fierce Knight","Monster",7,2300,2100),
"pot_of_greed":Card("pot_of_greed",55144522,"Pot of Greed","Spell",text="Draw 2 cards.",effect="draw",amount=2),
"dark_hole":Card("dark_hole",53129443,"Dark Hole","Spell",text="Destroy all monsters.",effect="destroy_all"),
"fissure":Card("fissure",66788016,"Fissure","Spell",text="Destroy the opposing face-up monster with the lowest ATK.",effect="destroy_lowest"),
"sakuretsu_armor":Card("sakuretsu_armor",56120475,"Sakuretsu Armor","Trap",text="When an opponent attacks: destroy the attacking monster.",effect="destroy_attacker"),
}
def starter(name):
    deck=["mystical_elf"]*5+["battle_ox"]*5+["celtic_guardian"]*4+["silver_fang"]*4+["summoned_skull"]*4+["curse_of_dragon"]*4
    deck+=(["dark_magician"]*2 if name=="arcane" else ["gaia"]*2)
    return deck+["pot_of_greed"]*3+["fissure"]*3+["dark_hole"]*2+["sakuretsu_armor"]*4
SUPPORTED_MECHANICS=("40-card fixed decks; 8,000 LP; five-card hands; first player skips draw","Six legacy phases","Normal summons, sets, and tributes","Battle positions, attacks, damage, destruction","Three Normal Spells and one attack-response Trap")
