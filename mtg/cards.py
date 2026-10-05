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
    produces: Tuple[str, ...] = ()
    rarity: str = "common"
    pack_slot: str = "common"
    cost: int = 0
    power: int = 0
    toughness: int = 0
    text: str = ""
    effect: Optional[str] = None
    amount: int = 0
    self_damage: int = 0
    land_type: str = ""
    target_types: Tuple[str, ...] = ()
    mana_amount: int = 1
    sacrifice_for_mana: bool = False
    target_nonartifact: bool = False
    target_nonblack: bool = False
    haste: bool = False
    keywords: Tuple[str, ...] = ()
    max_block_power: Optional[int] = None
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
        return ", ".join(keyword.replace("_", " ").title() for keyword in self.keywords)

    @property
    def ability_text(self):
        abilities=[self.keyword_text] if self.keyword_text else []
        if self.max_block_power is not None: abilities.append(f"Blocks power ≤{self.max_block_power}")
        if self.produces:
            produced=(str(self.mana_amount)+" × " if self.mana_amount>1 else "")+"/".join(self.produces)
            abilities.append(("Sacrifice → " if self.sacrifice_for_mana else "Produces ")+produced)
        return ", ".join(abilities)

    def has_land_type(self,land_type):
        return self.land and (self.key == land_type.casefold() or land_type.casefold() in self.type_line.casefold().split())


_CATALOG_PATH = Path(__file__).with_name("data") / "cards.json"
_CATALOG = json.loads(_CATALOG_PATH.read_text(encoding="utf-8"))
if _CATALOG.get("schema") != 1:
    raise RuntimeError("Unsupported MTG card catalog schema.")

BASE_CARDS = {
    raw["key"]: Card(
        **{
            **raw,
            "colors": tuple(raw.get("colors", ())),
            "produces": tuple(raw.get("produces", raw.get("colors", ()))) if raw.get("kind") == "Land" else tuple(raw.get("produces", ())),
        }
    )
    for raw in _CATALOG["cards"]
}

ALPHA_LAND_KEYS = {f"lea:{number}" for number in range(277,296)}

ALPHA_ARTIFACTS = {
    "lea:232": {"produces":("W","U","B","R","G"), "mana_amount":3, "sacrifice_for_mana":True},
    "lea:261": {"produces":("G",)},
    "lea:262": {"produces":("B",)},
    "lea:263": {"produces":("W",)},
    "lea:264": {"produces":("R",)},
    "lea:265": {"produces":("U",)},
    "lea:269": {"produces":("C",), "mana_amount":2},
}

ALPHA_SPELLS = {
    "lea:36": {"effect":"pump_blocking", "amount":7},
    "lea:47": {"effect":"draw_target", "amount":3},
    "lea:74": {"effect":"damage_any", "amount":4, "self_damage":2},
    "lea:161": {"effect":"damage_any", "amount":3},
    "lea:197": {"effect":"pump", "amount":3},
    "lea:2": {"effect":"destroy_all_lands"},
    "lea:129": {"effect":"destroy_land"},
    "lea:151": {"effect":"destroy_land_type", "land_type":"plains"},
    "lea:177": {"effect":"destroy_land"},
    "lea:201": {"effect":"destroy_land"},
    "lea:221": {"effect":"destroy_land_type", "land_type":"island"},
    "lea:18": {"effect":"destroy_permanent", "target_types":("Artifact","Enchantment")},
    "lea:173": {"effect":"destroy_permanent", "target_types":("Artifact",)},
    "lea:40": {"effect":"exile_creature_life"},
    "lea:45": {"effect":"destroy_all_creatures"},
    "lea:130": {"effect":"destroy_creature", "target_nonartifact":True, "target_nonblack":True},
}

ALPHA_KEYWORDS = {
    "lea:39": ("flying", "vigilance"),
    "lea:42": ("defender", "flying"),
    "lea:46": ("flying",),
    "lea:64": ("flying",),
    "lea:69": ("flying",),
    "lea:89": ("defender", "flying"),
    "lea:95": ("swampwalk",),
    "lea:170": ("flying",),
    "lea:182": ("defender",),
    "lea:191": ("first_strike",),
    "lea:198": ("reach",),
    "lea:215": ("flying",),
    "lea:216": ("forestwalk",),
    "lea:224": ("defender",),
    "lea:225": ("defender",),
}

CARDS = dict(BASE_CARDS)
for reference in PLAYABLE_ALPHA:
    CARDS[reference.key] = Card(
        key=reference.key,
        name=reference.name,
        kind="Land" if reference.support_family == "land" else reference.kind if reference.support_family in {"spell","artifact"} else "Creature",
        type_line=reference.type_line,
        set_code="lea",
        scryfall_id=reference.scryfall_id,
        oracle_id=reference.oracle_id,
        mana_cost=reference.mana_cost,
        colors=reference.colors,
        produces=ALPHA_ARTIFACTS.get(reference.key, {}).get("produces", reference.color_identity if reference.support_family == "land" else ()),
        rarity=reference.rarity,
        pack_slot="alpha",
        cost=int(reference.mana_value),
        power=int(reference.power) if reference.power is not None else 0,
        toughness=int(reference.toughness) if reference.toughness is not None else 0,
        text=reference.oracle_text,
        effect=ALPHA_SPELLS.get(reference.key, {}).get("effect"),
        amount=ALPHA_SPELLS.get(reference.key, {}).get("amount",0),
        self_damage=ALPHA_SPELLS.get(reference.key, {}).get("self_damage",0),
        land_type=ALPHA_SPELLS.get(reference.key, {}).get("land_type",""),
        target_types=ALPHA_SPELLS.get(reference.key, {}).get("target_types",()),
        mana_amount=ALPHA_ARTIFACTS.get(reference.key, {}).get("mana_amount",1),
        sacrifice_for_mana=ALPHA_ARTIFACTS.get(reference.key, {}).get("sacrifice_for_mana",False),
        target_nonartifact=ALPHA_SPELLS.get(reference.key, {}).get("target_nonartifact",False),
        target_nonblack=ALPHA_SPELLS.get(reference.key, {}).get("target_nonblack",False),
        keywords=ALPHA_KEYWORDS.get(reference.key, ()),
        max_block_power=1 if reference.key == "lea:159" else None,
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

if {card.key for card in PLAYABLE_ALPHA if card.support_family == "creature_ability"} != set(ALPHA_KEYWORDS) | {"lea:159"}:
    raise RuntimeError("Playable Alpha creature abilities do not match the validated keyword map.")

if {card.key for card in PLAYABLE_ALPHA if card.support_family == "land"} != ALPHA_LAND_KEYS:
    raise RuntimeError("Playable Alpha lands do not match the validated land map.")

if {card.key for card in PLAYABLE_ALPHA if card.support_family == "spell"} != set(ALPHA_SPELLS):
    raise RuntimeError("Playable Alpha spells do not match the validated spell map.")

if {card.key for card in PLAYABLE_ALPHA if card.support_family == "artifact"} != set(ALPHA_ARTIFACTS):
    raise RuntimeError("Playable Alpha artifacts do not match the validated artifact map.")
