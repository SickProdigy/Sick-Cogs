from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class Card:
    key: str
    name: str
    kind: str
    scryfall_id: str
    oracle_id: str
    mana_cost: str = ""
    colors: Tuple[str, ...] = ()
    rarity: str = "common"
    cost: int = 0
    power: int = 0
    toughness: int = 0
    text: str = ""
    effect: Optional[str] = None
    amount: int = 0

    @property
    def land(self):
        return self.kind == "Land"

    @property
    def creature(self):
        return self.kind == "Creature"


CARDS = {
    "mountain": Card("mountain","Mountain","Land","2a844b96-6616-4c39-8f4f-5d14a3b2bd55","a3fb7228-e76b-4e96-a40e-20b5fed75685",colors=("R",),text="Tap: Add one red mana."),
    "forest": Card("forest","Forest","Land","dce15387-4114-4b3e-91aa-5b42b45c44ac","b34bb2dc-c1af-4d77-b0b3-a0fb342a5fc6",colors=("G",),text="Tap: Add one green mana."),
    "goblin": Card("goblin","Raging Goblin","Creature","3480927c-10da-4817-9954-10aea2bc7100","30997b43-fc13-41d3-8064-1ccc2cb6fd2b","{R}",("R",),"common",1,1,1,"Haste."),
    "bear": Card("bear","Bear Cub","Creature","d8662ebb-068b-41d2-b504-4b5854e4d4aa","ed206651-3d9b-4546-8d45-0682817192fd","{1}{G}",("G",),"common",2,2,2),
    "giant": Card("giant","Hill Giant","Creature","14c2be6a-9ca6-4d3a-8dd0-db4ea40799f8","342199e0-15b6-4824-83da-25caef2592b3","{3}{R}",("R",),"common",4,3,3),
    "centaur": Card("centaur","Centaur Courser","Creature","e8b67ee8-3189-4426-8b1a-b540267768fd","2f5bf099-2e01-4e1c-9ebf-0ce0ac66939e","{2}{G}",("G",),"common",3,3,3),
    "shock": Card("shock","Shock","Instant","b23900fb-efe9-43ab-9f67-4545dd01fb9c","a9d288b8-cdc1-4e55-a0c9-d6edfc95e65d","{R}",("R",),"common",1,text="Deal 2 damage to a player.",effect="damage",amount=2),
    "strike": Card("strike","Lightning Strike","Instant","88b13bc0-da54-4c3b-917c-7c8345a329f5","f34b9bc4-7bfe-47fd-ba23-4eeeb46026eb","{1}{R}",("R",),"uncommon",2,text="Deal 3 damage to a player.",effect="damage",amount=3),
    "growth": Card("growth","Giant Growth","Instant","fd1f95bf-48ea-455a-8a6c-0249b11c8900","5748ebf1-24e3-499d-ab7c-c2cebd462a24","{G}",("G",),"common",1,text="A creature gets +3/+3 until end of turn.",effect="pump",amount=3),
    "renew": Card("renew","Natural Spring","Sorcery","983874b1-3179-4ac6-a4f3-efa133331c6f","f7571a2e-aaf3-4148-ab76-2a2e35273c70","{3}{G}{G}",("G",),"common",5,text="You gain 8 life.",effect="life",amount=8),
    "inspire": Card("inspire","Harmonize","Sorcery","bd7138fb-6aa7-455e-b1f7-ca08d747277d","7eff84f1-f772-497a-b350-bbc93d0230f7","{2}{G}{G}",("G",),"uncommon",4,text="Draw three cards.",effect="draw",amount=3),
}


def starter(color):
    if color == "red":
        return ["mountain"]*24 + ["goblin"]*12 + ["giant"]*8 + ["shock"]*8 + ["strike"]*8
    if color == "green":
        return ["forest"]*24 + ["bear"]*12 + ["centaur"]*8 + ["growth"]*8 + ["renew"]*4 + ["inspire"]*4
    raise ValueError("Unknown deck.")
