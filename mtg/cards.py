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
    target_color: str = ""
    haste: bool = False
    keywords: Tuple[str, ...] = ()
    max_block_power: Optional[int] = None
    characteristic_pt: Optional[str] = None
    activation_cost: str = ""
    activated_power: int = 0
    activated_toughness: int = 0
    activated_keyword: str = ""
    sacrifice_after_activations: int = 0
    activation_effect: str = ""
    activation_tap: bool = False
    activation_text: str = ""
    activation_amount: int = 0
    activation_self_damage: int = 0
    conditional_swamp_bonus: bool = False
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
        if self.activation_cost or self.activation_effect:
            effects=[]
            if self.activated_power or self.activated_toughness:
                effects.append(f"{self.activated_power:+d}/{self.activated_toughness:+d} until end of turn")
            if self.activated_keyword:
                effects.append(f"Gains {self.activated_keyword.title()} until end of turn")
            if self.sacrifice_after_activations:
                effects.append(f"Sacrifice at the next end step after activation {self.sacrifice_after_activations}")
            if self.activation_text: effects.append(self.activation_text)
            costs=[self.activation_cost] if self.activation_cost else []
            if self.activation_tap: costs.append("{T}")
            abilities.append(", ".join(costs)+": "+"; ".join(effects))
        if self.produces:
            produced=(str(self.mana_amount)+" × " if self.mana_amount>1 else "")+"/".join(self.produces)
            abilities.append(("Sacrifice → " if self.sacrifice_for_mana else "Produces ")+produced)
        return ", ".join(abilities)

    def has_type(self,card_type):
        return self.kind==card_type or card_type in self.type_line.split(" — ",1)[0].split()

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

ALPHA_MANA_CREATURES = {
    "lea:186": ("W","U","B","R","G"),
    "lea:210": ("G",),
}

ALPHA_ACTIVATED_CREATURES = {
    "lea:106": {"activation_cost":"{B}", "activation_effect":"regenerate", "activation_text":"Regenerate this creature"},
    "lea:132": {"activation_cost":"{B}", "activation_effect":"regenerate", "activation_text":"Regenerate this creature"},
    "lea:135": {"activation_cost":"{B}", "activation_effect":"regenerate", "activation_text":"Regenerate this creature"},
    "lea:172": {"activation_cost":"{B}", "activation_effect":"regenerate", "activation_text":"Regenerate this creature", "conditional_swamp_bonus":True},
    "lea:180": {"activation_cost":"{R}", "activation_effect":"regenerate", "activation_text":"Regenerate this creature"},
    "lea:223": {"activation_cost":"{G}", "activation_effect":"regenerate", "activation_text":"Regenerate this creature"},
    "lea:258": {"activation_cost":"{1}", "activation_effect":"regenerate", "activation_text":"Regenerate this creature"},
    "lea:29": {"activation_cost":"{W}{W}", "activation_tap":True, "activation_effect":"destroy_black_permanent", "activation_text":"Destroy target black permanent"},
    "lea:73": {"activation_tap":True, "activation_effect":"damage_any", "activation_amount":1, "activation_text":"Deals 1 damage to any target"},
    "lea:123": {"activation_tap":True, "activation_effect":"destroy_tapped_creature", "activation_text":"Destroy target tapped creature"},
    "lea:142": {"activation_tap":True, "activation_effect":"destroy_wall", "activation_text":"Destroy target Wall"},
    "lea:143": {"activation_tap":True, "activation_effect":"unblockable", "activation_text":"Target creature with power 2 or less can't be blocked this turn"},
    "lea:165": {"activation_tap":True, "activation_effect":"damage_any", "activation_amount":2, "activation_self_damage":3, "activation_text":"Deals 2 damage to any target and 3 damage to you"},
    "lea:205": {"activation_tap":True, "activation_effect":"untap_land", "activation_text":"Untap target land"},
    "lea:141": {"activation_cost":"{R}", "activated_power":1, "sacrifice_after_activations":4},
    "lea:153": {"activation_cost":"{R}", "activated_keyword":"flying"},
    "lea:90": {"activation_cost":"{U}", "activated_power":1},
    "lea:109": {"activation_cost":"{B}", "activated_power":1, "activated_toughness":1},
    "lea:155": {"activation_cost":"{R}", "activated_toughness":1},
    "lea:174": {"activation_cost":"{R}", "activated_power":1},
    "lea:181": {"activation_cost":"{R}", "activated_power":1},
}

ALPHA_CHARACTERISTIC_CREATURES = {
    "lea:118": "swamps",
    "lea:121": "plague_rats",
    "lea:160": "non_wall_creatures",
}

ALPHA_SPELLS = {
    "lea:50": {"effect":"draw_target_x"},
    "lea:111": {"effect":"pump_power_x"},
    "lea:140": {"effect":"damage_x_exile"},
    "lea:146": {"effect":"earthquake_x"},
    "lea:200": {"effect":"hurricane_x"},
    "lea:217": {"effect":"life_target_x"},
    "lea:49": {"effect":"elemental_blast", "target_color":"R"},
    "lea:54": {"effect":"counter_spell"},
    "lea:169": {"effect":"elemental_blast", "target_color":"U"},
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
    "lea:34": {"effect":"reanimate_creature"},
    "lea:86": {"effect":"return_creature_hand"},
    "lea:122": {"effect":"return_grave_creature_hand"},
    "lea:214": {"effect":"return_grave_card_hand"},
}

ALPHA_KEYWORDS = {
    "lea:39": ("flying", "vigilance"),
    "lea:42": ("defender", "flying"),
    "lea:46": ("flying",),
    "lea:64": ("flying",),
    "lea:69": ("flying",),
    "lea:89": ("defender", "flying"),
    "lea:90": ("defender",),
    "lea:141": ("flying",),
    "lea:95": ("swampwalk",),
    "lea:118": ("flying",),
    "lea:155": ("flying",),
    "lea:170": ("flying",),
    "lea:174": ("flying",),
    "lea:181": ("defender",),
    "lea:132": ("defender",),
    "lea:135": ("flying",),
    "lea:223": ("defender",),
    "lea:258": ("defender",),
    "lea:182": ("defender",),
    "lea:191": ("first_strike",),
    "lea:198": ("reach",),
    "lea:215": ("flying",),
    "lea:216": ("forestwalk",),
    "lea:224": ("defender",),
    "lea:225": ("defender",),
    "lea:186": ("flying",),
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
        produces=ALPHA_ARTIFACTS.get(reference.key, {}).get("produces", ALPHA_MANA_CREATURES.get(reference.key, reference.color_identity if reference.support_family == "land" else ())),
        rarity=reference.rarity,
        pack_slot="alpha",
        cost=int(reference.mana_value),
        power=int(reference.power) if str(reference.power).isdigit() else 0,
        toughness=int(reference.toughness) if str(reference.toughness).isdigit() else 0,
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
        target_color=ALPHA_SPELLS.get(reference.key, {}).get("target_color",""),
        keywords=ALPHA_KEYWORDS.get(reference.key, ()),
        max_block_power=1 if reference.key == "lea:159" else None,
        characteristic_pt=ALPHA_CHARACTERISTIC_CREATURES.get(reference.key),
        activation_cost=ALPHA_ACTIVATED_CREATURES.get(reference.key,{}).get("activation_cost",""),
        activated_power=ALPHA_ACTIVATED_CREATURES.get(reference.key,{}).get("activated_power",0),
        activated_toughness=ALPHA_ACTIVATED_CREATURES.get(reference.key,{}).get("activated_toughness",0),
        activated_keyword=ALPHA_ACTIVATED_CREATURES.get(reference.key,{}).get("activated_keyword",""),
        sacrifice_after_activations=ALPHA_ACTIVATED_CREATURES.get(reference.key,{}).get("sacrifice_after_activations",0),
        activation_effect=ALPHA_ACTIVATED_CREATURES.get(reference.key,{}).get("activation_effect",""),
        activation_tap=ALPHA_ACTIVATED_CREATURES.get(reference.key,{}).get("activation_tap",False),
        activation_text=ALPHA_ACTIVATED_CREATURES.get(reference.key,{}).get("activation_text",""),
        activation_amount=ALPHA_ACTIVATED_CREATURES.get(reference.key,{}).get("activation_amount",0),
        activation_self_damage=ALPHA_ACTIVATED_CREATURES.get(reference.key,{}).get("activation_self_damage",0),
        conditional_swamp_bonus=ALPHA_ACTIVATED_CREATURES.get(reference.key,{}).get("conditional_swamp_bonus",False),
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

if {card.key for card in PLAYABLE_ALPHA if card.support_family == "creature_ability"} != set(ALPHA_KEYWORDS) | set(ALPHA_MANA_CREATURES) | set(ALPHA_CHARACTERISTIC_CREATURES) | set(ALPHA_ACTIVATED_CREATURES) | {"lea:159"}:
    raise RuntimeError("Playable Alpha creature abilities do not match the validated keyword map.")

if {card.key for card in PLAYABLE_ALPHA if card.support_family == "land"} != ALPHA_LAND_KEYS:
    raise RuntimeError("Playable Alpha lands do not match the validated land map.")

if {card.key for card in PLAYABLE_ALPHA if card.support_family == "spell"} != set(ALPHA_SPELLS):
    raise RuntimeError("Playable Alpha spells do not match the validated spell map.")

if {card.key for card in PLAYABLE_ALPHA if card.support_family == "artifact"} != set(ALPHA_ARTIFACTS):
    raise RuntimeError("Playable Alpha artifacts do not match the validated artifact map.")
