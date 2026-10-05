import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

from .catalog import PLAYABLE_ALPHA


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
    pack_slot: str = "common"
    cost: int = 0
    power: int = 0
    toughness: int = 0
    text: str = ""
    effect: Optional[str] = None
    amount: int = 0
    haste: bool = False
    keywords: Tuple[str, ...] = ()
    type_line: str = ""
    set_code: str = ""

    @property
    def land(self):
        return self.kind == "Land"

    @property
    def creature(self):
        return self.kind == "Creature"

    @property
    def keyword_text(self):
        return ", ".join(keyword.title() for keyword in self.keywords)


_CATALOG_PATH = Path(__file__).with_name("data") / "cards.json"
_CATALOG = json.loads(_CATALOG_PATH.read_text(encoding="utf-8"))
if _CATALOG.get("schema") != 1:
    raise RuntimeError("Unsupported MTG card catalog schema.")

BASE_CARDS = {
    raw["key"]: Card(
        **{
            **raw,
            "colors": tuple(raw.get("colors", ())),
        }
    )
    for raw in _CATALOG["cards"]
}

ALPHA_KEYWORDS = {
    "lea:39": ("flying", "vigilance"),
    "lea:42": ("defender", "flying"),
    "lea:46": ("flying",),
    "lea:64": ("flying",),
    "lea:69": ("flying",),
    "lea:89": ("defender", "flying"),
    "lea:170": ("flying",),
    "lea:182": ("defender",),
    "lea:198": ("reach",),
    "lea:215": ("flying",),
    "lea:224": ("defender",),
    "lea:225": ("defender",),
}

CARDS = dict(BASE_CARDS)
for reference in PLAYABLE_ALPHA:
    CARDS[reference.key] = Card(
        key=reference.key,
        name=reference.name,
        kind="Creature",
        type_line=reference.type_line,
        set_code="lea",
        scryfall_id=reference.scryfall_id,
        oracle_id=reference.oracle_id,
        mana_cost=reference.mana_cost,
        colors=reference.colors,
        rarity=reference.rarity,
        pack_slot="alpha",
        cost=int(reference.mana_value),
        power=int(reference.power),
        toughness=int(reference.toughness),
        text=reference.oracle_text,
        keywords=ALPHA_KEYWORDS.get(reference.key, ()),
    )

PACK_POOLS = {
    slot: tuple(card.key for card in BASE_CARDS.values() if card.pack_slot == slot)
    for slot in ("basic", "common", "uncommon", "rare", "mythic")
}


def starter(color):
    if color == "red":
        return ["mountain"]*24 + ["goblin"]*12 + ["giant"]*8 + ["shock"]*8 + ["strike"]*8
    if color == "green":
        return ["forest"]*24 + ["bear"]*12 + ["centaur"]*8 + ["growth"]*8 + ["renew"]*4 + ["inspire"]*4
    raise ValueError("Unknown deck.")

if {card.key for card in PLAYABLE_ALPHA if card.support_family == "creature_ability"} != set(ALPHA_KEYWORDS):
    raise RuntimeError("Playable Alpha creature abilities do not match the validated keyword map.")
