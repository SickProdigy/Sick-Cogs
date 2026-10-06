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
    mana_activation_cost: str = ""
    sacrifice_for_mana: bool = False
    skip_untap: bool = False
    enters_tapped: bool = False
    target_nonartifact: bool = False
    target_nonblack: bool = False
    target_color: str = ""
    temporary_keyword: str = ""
    mana_color: str = ""
    color_change: str = ""
    aura_target_types: Tuple[str, ...] = ()
    aura_target_subtypes: Tuple[str, ...] = ()
    aura_power: int = 0
    aura_toughness: int = 0
    aura_forest_scaling: bool = False
    aura_keyword: str = ""
    aura_attack_override: bool = False
    aura_blocked_except_wall: bool = False
    aura_hostile: bool = False
    activation_attached: bool = False
    protection_colors: Tuple[str, ...] = ()
    aura_protection: str = ""
    protection_self_exception: bool = False
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
    lord_subtype: str = ""
    lord_power: int = 0
    lord_toughness: int = 0
    lord_keyword: str = ""
    lord_regeneration_cost: str = ""
    global_buff_color: str = ""
    global_power: int = 0
    global_toughness: int = 0
    global_controller_only: bool = False
    global_requires_untapped: bool = False
    global_requires_attacking: bool = False
    mana_flare: bool = False
    land_tap_damage: int = 0
    aura_extra_mana: str = ""
    aura_tap_damage: int = 0
    mountain_extra_red: bool = False
    opponent_forest_tap_life: int = 0
    land_enter_damage: int = 0
    land_grave_damage: int = 0
    upkeep_each_damage: int = 0
    upkeep_opponent_hand_damage: bool = False
    draw_step_extra: int = 0
    untap_power_limit: int = 0
    white_as_red: bool = False
    cast_life_color: str = ""
    upkeep_untap_cost: str = ""
    draw_tapped_damage: int = 0
    death_life: bool = False
    animate_combat: bool = False
    creates_token: str = ""
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
        if self.enters_tapped: abilities.append("Enters tapped")
        if self.max_block_power is not None: abilities.append(f"Blocks power ≤{self.max_block_power}")
        if self.activation_cost or self.activation_effect:
            effects=[]
            if (self.activated_power or self.activated_toughness) and not self.activation_attached:
                effects.append(f"{self.activated_power:+d}/{self.activated_toughness:+d} until end of turn")
            if self.activated_keyword:
                effects.append(f"Gains {self.activated_keyword.title()} until end of turn")
            if self.sacrifice_after_activations:
                effects.append(f"Sacrifice at the next end step after activation {self.sacrifice_after_activations}")
            if self.activation_text: effects.append(self.activation_text)
            costs=[self.activation_cost] if self.activation_cost else []
            if self.activation_tap: costs.append("{T}")
            abilities.append(", ".join(costs)+": "+"; ".join(effects))
        aura=[]
        if self.aura_power or self.aura_toughness: aura.append(f"Enchanted creature gets {self.aura_power:+d}/{self.aura_toughness:+d}")
        if self.aura_forest_scaling: aura.append("Enchanted creature gets +X/+Y for Forests you control")
        if self.aura_keyword: aura.append(f"Enchanted creature has {self.aura_keyword.replace('_',' ').title()}")
        if self.aura_protection: aura.append(f"Enchanted creature has Protection From {self.aura_protection}")
        if self.aura_attack_override: aura.append("Enchanted Wall can attack")
        if self.aura_blocked_except_wall: aura.append("Enchanted creature can be blocked only by Walls")
        abilities.extend(aura)
        if self.protection_colors: abilities.append("Protection From "+"/".join(self.protection_colors))
        if self.lord_subtype:
            granted=[]
            if self.lord_power or self.lord_toughness: granted.append(f"{self.lord_power:+d}/{self.lord_toughness:+d}")
            if self.lord_keyword: granted.append(self.lord_keyword.replace("_"," ").title())
            if self.lord_regeneration_cost: granted.append(f"{self.lord_regeneration_cost}: Regenerate")
            abilities.append(f"Other {self.lord_subtype} creatures have "+", ".join(granted))
        if self.global_power or self.global_toughness:
            color_name={"W":"White","U":"Blue","B":"Black","R":"Red","G":"Green"}.get(self.global_buff_color,self.global_buff_color)
            subject=(color_name+" creatures" if color_name else "Creatures")
            if self.global_requires_untapped: subject="Untapped creatures"
            elif self.global_requires_attacking: subject="Attacking creatures"
            if self.global_controller_only: subject+=" you control"
            abilities.append(f"{subject} get {self.global_power:+d}/{self.global_toughness:+d}")
        if self.mana_flare: abilities.append("Tapped lands produce one additional mana of the produced type")
        if self.land_tap_damage: abilities.append(f"Whenever a player taps a land for mana, deals {self.land_tap_damage} damage to that player")
        if self.aura_extra_mana: abilities.append(f"Enchanted land produces an additional {self.aura_extra_mana}")
        if self.aura_tap_damage: abilities.append(f"Whenever enchanted land becomes tapped, deals {self.aura_tap_damage} damage to its controller")
        if self.mountain_extra_red: abilities.append("Mountains tapped for mana add an additional R")
        if self.opponent_forest_tap_life: abilities.append(f"Whenever an opponent taps a Forest, you gain {self.opponent_forest_tap_life} life")
        if self.cast_life_color:
            color_name={"W":"white","U":"blue","B":"black","R":"red","G":"green"}[self.cast_life_color]
            abilities.append(f"Whenever a player casts a {color_name} spell, you may pay {{1}} to gain 1 life")
        if self.upkeep_untap_cost: abilities.append(f"At your upkeep, you may pay {self.upkeep_untap_cost} to untap this artifact")
        if self.draw_tapped_damage: abilities.append(f"At your draw step, if tapped, deals {self.draw_tapped_damage} damage to you")
        if self.death_life: abilities.append("Whenever a creature dies, you may pay {1} to gain 1 life")
        if self.animate_combat: abilities.append("{2}: Becomes a 3/6 Golem artifact creature until end of combat; activate only during combat")
        if self.land_enter_damage: abilities.append(f"Whenever a land enters, deals {self.land_enter_damage} damage to its controller")
        if self.land_grave_damage: abilities.append(f"Whenever a land goes from battlefield to graveyard, deals {self.land_grave_damage} damage to its controller")
        if self.upkeep_each_damage: abilities.append(f"At each player's upkeep, deals {self.upkeep_each_damage} damage to that player")
        if self.upkeep_opponent_hand_damage: abilities.append("At your opponent's upkeep, deals damage equal to cards in their hand minus 4")
        if self.draw_step_extra: abilities.append(f"At each draw step while untapped, that player draws {self.draw_step_extra} additional card"+("s" if self.draw_step_extra!=1 else ""))
        if self.untap_power_limit: abilities.append(f"Creatures with power {self.untap_power_limit} or greater don't untap")
        if self.white_as_red: abilities.append("You may spend white mana as though it were red mana")
        if self.skip_untap: abilities.append("Doesn't untap during your untap step")
        if self.produces:
            produced=(str(self.mana_amount)+" × " if self.mana_amount>1 else "")+"/".join(self.produces)
            if self.mana_activation_cost: abilities.append(f"{self.mana_activation_cost}, {{T}}: Add "+produced)
            else: abilities.append(("Sacrifice → " if self.sacrifice_for_mana else "Produces ")+produced)
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

ALPHA_GLOBAL_ENCHANTMENTS = {
    "lea:9": {"global_toughness":2, "global_controller_only":True, "global_requires_untapped":True},
    "lea:16": {"global_buff_color":"W", "global_power":1, "global_toughness":1},
    "lea:93": {"global_buff_color":"B", "global_power":1, "global_toughness":1},
    "lea:166": {"global_power":1, "global_controller_only":True, "global_requires_attacking":True},
}

ALPHA_TAP_ENCHANTMENTS = {
    "lea:75": {"aura_target_types":("Land",), "aura_tap_damage":2, "aura_hostile":True},
    "lea:162": {"mana_flare":True},
    "lea:163": {"land_tap_damage":1},
    "lea:229": {"aura_target_types":("Land",), "aura_extra_mana":"G"},
    "lea:61": {"opponent_forest_tap_life":1},
}

ALPHA_ENCHANTMENTS = {
    "lea:100": {"activation_cost":"{B}{B}", "activation_effect":"counter_color", "target_color":"G", "activation_text":"Counter target green spell"},
    "lea:206": {"activation_cost":"{G}{G}", "activation_effect":"counter_color", "target_color":"B", "activation_text":"Counter target black spell"},
    "lea:7": {"aura_target_types":("Creature",), "activation_cost":"{W}", "activated_power":1, "activated_toughness":1, "activation_attached":True, "activation_text":"Enchanted creature gets +1/+1 until end of turn"},
    "lea:108": {"aura_target_types":("Creature",), "aura_keyword":"fear"},
    "lea:184": {"aura_target_types":("Creature",), "aura_forest_scaling":True},
    "lea:5": {"aura_target_types":("Creature",), "aura_protection":"B", "protection_self_exception":True},
    "lea:8": {"aura_target_types":("Creature",), "aura_protection":"U", "protection_self_exception":True},
    "lea:20": {"aura_target_types":("Creature",), "aura_protection":"G", "protection_self_exception":True},
    "lea:33": {"aura_target_types":("Creature",), "aura_protection":"R", "protection_self_exception":True},
    "lea:44": {"aura_target_types":("Creature",), "aura_protection":"W", "protection_self_exception":True},
    "lea:1": {"aura_target_types":("Creature",), "aura_target_subtypes":("Wall",), "aura_attack_override":True},
    "lea:23": {"aura_target_types":("Creature",), "aura_toughness":2, "activation_cost":"{W}", "activated_toughness":1, "activation_attached":True, "activation_text":"Enchanted creature gets +0/+1 until end of turn"},
    "lea:24": {"aura_target_types":("Creature",), "aura_power":1, "aura_toughness":2},
    "lea:27": {"aura_target_types":("Creature",), "aura_keyword":"first_strike"},
    "lea:58": {"aura_target_types":("Creature",), "aura_keyword":"flying"},
    "lea:59": {"aura_target_types":("Creature",), "aura_blocked_except_wall":True},
    "lea:131": {"aura_target_types":("Creature",), "aura_power":2, "aura_toughness":1},
    "lea:134": {"aura_target_types":("Creature",), "aura_power":-2, "aura_toughness":-1, "aura_hostile":True},
    "lea:138": {"aura_target_types":("Creature",), "aura_keyword":"mountainwalk"},
    "lea:150": {"aura_target_types":("Creature",), "activation_cost":"{R}", "activated_power":1, "activation_attached":True, "activation_text":"Enchanted creature gets +1/+0 until end of turn"},
    "lea:213": {"aura_target_types":("Creature",), "activation_cost":"{G}", "activation_effect":"regenerate", "activation_attached":True, "activation_text":"Regenerate enchanted creature"},
    "lea:228": {"aura_target_types":("Creature",), "aura_toughness":2, "aura_keyword":"reach"},
}

ALPHA_ARTIFACTS = {
    "lea:230": {"land_enter_damage":2},
    "lea:241": {"land_grave_damage":2},
    "lea:231": {"produces":("C",), "mana_amount":3, "skip_untap":True, "activation_cost":"{3}", "activation_effect":"untap_self", "activation_text":"Untap this artifact"},
    "lea:234": {"produces":("W","U","B","R","G"), "mana_activation_cost":"{2}"},
    "lea:244": {"global_buff_color":"R", "global_power":1, "global_toughness":1, "mountain_extra_red":True},
    "lea:248": {"activation_cost":"{1}", "activation_tap":True, "activation_effect":"tap_permanent", "activation_text":"Tap target artifact, creature, or land"},
    "lea:254": {"activation_cost":"{4}", "activation_tap":True, "activation_effect":"draw_self", "activation_text":"Draw a card"},
    "lea:268": {"activation_cost":"{3}", "activation_tap":True, "activation_effect":"damage_any", "activation_amount":1, "activation_text":"Deals 1 damage to any target"},
    "lea:232": {"produces":("W","U","B","R","G"), "mana_amount":3, "sacrifice_for_mana":True},
    "lea:233": {"upkeep_opponent_hand_damage":True},
    "lea:238": {"upkeep_each_damage":1},
    "lea:247": {"draw_step_extra":1},
    "lea:259": {"produces":("C",), "mana_amount":3, "skip_untap":True, "upkeep_untap_cost":"{4}", "draw_tapped_damage":1},
    "lea:260": {"untap_power_limit":3},
    "lea:271": {"white_as_red":True},
    "lea:239": {"cast_life_color":"U"},
    "lea:250": {"cast_life_color":"R"},
    "lea:251": {"cast_life_color":"W"},
    "lea:273": {"cast_life_color":"B"},
    "lea:276": {"cast_life_color":"G"},
    "lea:261": {"produces":("G",)},
    "lea:262": {"produces":("B",)},
    "lea:263": {"produces":("W",)},
    "lea:264": {"produces":("R",)},
    "lea:265": {"produces":("U",)},
    "lea:266": {"enters_tapped":True, "activation_cost":"{1}", "activation_tap":True, "activation_effect":"destroy_all_nonland", "activation_text":"Destroy all artifacts, creatures, and enchantments"},
    "lea:269": {"produces":("C",), "mana_amount":2},
    "lea:270": {"death_life":True},
    "lea:253": {"activation_cost":"{2}", "activation_effect":"animate_self", "activation_text":"Becomes a 3/6 Golem artifact creature until end of combat", "animate_combat":True},
    "lea:272": {"activation_cost":"{5}", "activation_tap":True, "activation_effect":"create_token", "activation_text":"Create a 1/1 colorless Insect artifact creature token with flying named Wasp", "creates_token":"token:wasp"},
    "lea:237": {"activation_cost":"{3}", "activation_tap":True, "activation_effect":"prevent_player_damage", "activation_amount":2, "activation_text":"Prevent the next 2 damage that would be dealt to you this turn"},
}

ALPHA_MANA_CREATURES = {
    "lea:186": ("W","U","B","R","G"),
    "lea:210": ("G",),
}

ALPHA_ACTIVATED_CREATURES = {
    "lea:37": {"activation_tap":True, "activation_effect":"prevent_any_damage", "activation_amount":1, "activation_text":"Prevent the next 1 damage that would be dealt to any target this turn"},
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

ALPHA_PROTECTIONS = {
    "lea:43": ("B",),
    "lea:94": ("W",),
}

ALPHA_LORDS = {
    "lea:62": {"lord_subtype":"Merfolk", "lord_power":1, "lord_toughness":1, "lord_keyword":"islandwalk"},
    "lea:137": {"lord_subtype":"Zombie", "lord_keyword":"swampwalk", "lord_regeneration_cost":"{B}"},
    "lea:154": {"lord_subtype":"Goblin", "lord_power":1, "lord_toughness":1, "lord_keyword":"mountainwalk"},
}

ALPHA_CHARACTERISTIC_CREATURES = {
    "lea:118": "swamps",
    "lea:121": "plague_rats",
    "lea:160": "non_wall_creatures",
}

ALPHA_SPELLS = {
    "lea:32": {"effect":"set_color", "color_change":"W"},
    "lea:82": {"effect":"set_color", "color_change":"U"},
    "lea:83": {"effect":"extra_turn"},
    "lea:101": {"effect":"set_color", "color_change":"B"},
    "lea:139": {"effect":"set_color", "color_change":"R"},
    "lea:207": {"effect":"set_color", "color_change":"G"},
    "lea:17": {"effect":"regenerate_target"},
    "lea:22": {"effect":"healing_salve", "amount":3},
    "lea:60": {"effect":"grant_keyword", "temporary_keyword":"flying"},
    "lea:85": {"effect":"tap_or_untap", "target_types":("Artifact","Creature","Land")},
    "lea:65": {"effect":"mana_short"},
    "lea:98": {"effect":"add_mana", "mana_color":"B", "mana_amount":3},
    "lea:178": {"effect":"destroy_wall"},
    "lea:220": {"effect":"destroy_all_enchantments"},
    "lea:193": {"effect":"prevent_combat_damage"},
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
    "lea:43": ("first_strike",),
    "lea:46": ("flying",),
    "lea:64": ("flying",),
    "lea:69": ("flying",),
    "lea:89": ("defender", "flying"),
    "lea:90": ("defender",),
    "lea:94": ("first_strike",),
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
    "lea:227": ("trample",),
    "lea:186": ("flying",),
}

CARDS = dict(BASE_CARDS)
for reference in PLAYABLE_ALPHA:
    CARDS[reference.key] = Card(
        key=reference.key,
        name=reference.name,
        kind="Land" if reference.support_family == "land" else reference.kind if reference.support_family in {"spell","artifact","enchantment"} else "Creature",
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
        mana_amount=ALPHA_SPELLS.get(reference.key, {}).get("mana_amount",ALPHA_ARTIFACTS.get(reference.key, {}).get("mana_amount",1)),
        mana_activation_cost=ALPHA_ARTIFACTS.get(reference.key, {}).get("mana_activation_cost",""),
        sacrifice_for_mana=ALPHA_ARTIFACTS.get(reference.key, {}).get("sacrifice_for_mana",False),
        skip_untap=ALPHA_ARTIFACTS.get(reference.key, {}).get("skip_untap",False),
        target_nonartifact=ALPHA_SPELLS.get(reference.key, {}).get("target_nonartifact",False),
        target_nonblack=ALPHA_SPELLS.get(reference.key, {}).get("target_nonblack",False),
        target_color=ALPHA_SPELLS.get(reference.key, {}).get("target_color",ALPHA_ENCHANTMENTS.get(reference.key,{}).get("target_color","")),
        temporary_keyword=ALPHA_SPELLS.get(reference.key, {}).get("temporary_keyword",""),
        mana_color=ALPHA_SPELLS.get(reference.key, {}).get("mana_color",""),
        color_change=ALPHA_SPELLS.get(reference.key, {}).get("color_change",""),
        aura_target_types=ALPHA_ENCHANTMENTS.get(reference.key, ALPHA_TAP_ENCHANTMENTS.get(reference.key, {})).get("aura_target_types",()),
        aura_target_subtypes=ALPHA_ENCHANTMENTS.get(reference.key, {}).get("aura_target_subtypes",()),
        aura_power=ALPHA_ENCHANTMENTS.get(reference.key, {}).get("aura_power",0),
        aura_toughness=ALPHA_ENCHANTMENTS.get(reference.key, {}).get("aura_toughness",0),
        aura_forest_scaling=ALPHA_ENCHANTMENTS.get(reference.key, {}).get("aura_forest_scaling",False),
        aura_keyword=ALPHA_ENCHANTMENTS.get(reference.key, {}).get("aura_keyword",""),
        aura_attack_override=ALPHA_ENCHANTMENTS.get(reference.key, {}).get("aura_attack_override",False),
        aura_blocked_except_wall=ALPHA_ENCHANTMENTS.get(reference.key, {}).get("aura_blocked_except_wall",False),
        aura_hostile=ALPHA_ENCHANTMENTS.get(reference.key, ALPHA_TAP_ENCHANTMENTS.get(reference.key, {})).get("aura_hostile",False),
        aura_protection=ALPHA_ENCHANTMENTS.get(reference.key, {}).get("aura_protection",""),
        protection_self_exception=ALPHA_ENCHANTMENTS.get(reference.key, {}).get("protection_self_exception",False),
        protection_colors=ALPHA_PROTECTIONS.get(reference.key,()),
        keywords=ALPHA_KEYWORDS.get(reference.key, ()),
        max_block_power=1 if reference.key == "lea:159" else None,
        characteristic_pt=ALPHA_CHARACTERISTIC_CREATURES.get(reference.key),
        activation_cost=ALPHA_ENCHANTMENTS.get(reference.key,{}).get("activation_cost",ALPHA_ACTIVATED_CREATURES.get(reference.key,{}).get("activation_cost",ALPHA_ARTIFACTS.get(reference.key,{}).get("activation_cost",""))),
        activated_power=ALPHA_ENCHANTMENTS.get(reference.key,{}).get("activated_power",ALPHA_ACTIVATED_CREATURES.get(reference.key,{}).get("activated_power",0)),
        activated_toughness=ALPHA_ENCHANTMENTS.get(reference.key,{}).get("activated_toughness",ALPHA_ACTIVATED_CREATURES.get(reference.key,{}).get("activated_toughness",0)),
        activated_keyword=ALPHA_ACTIVATED_CREATURES.get(reference.key,{}).get("activated_keyword",""),
        sacrifice_after_activations=ALPHA_ACTIVATED_CREATURES.get(reference.key,{}).get("sacrifice_after_activations",0),
        activation_effect=ALPHA_ENCHANTMENTS.get(reference.key,{}).get("activation_effect",ALPHA_ACTIVATED_CREATURES.get(reference.key,{}).get("activation_effect",ALPHA_ARTIFACTS.get(reference.key,{}).get("activation_effect",""))),
        activation_tap=ALPHA_ACTIVATED_CREATURES.get(reference.key,{}).get("activation_tap",ALPHA_ARTIFACTS.get(reference.key,{}).get("activation_tap",False)),
        activation_text=ALPHA_ENCHANTMENTS.get(reference.key,{}).get("activation_text",ALPHA_ACTIVATED_CREATURES.get(reference.key,{}).get("activation_text",ALPHA_ARTIFACTS.get(reference.key,{}).get("activation_text",""))),
        activation_amount=ALPHA_ACTIVATED_CREATURES.get(reference.key,{}).get("activation_amount",ALPHA_ARTIFACTS.get(reference.key,{}).get("activation_amount",0)),
        activation_self_damage=ALPHA_ACTIVATED_CREATURES.get(reference.key,{}).get("activation_self_damage",0),
        enters_tapped=ALPHA_ARTIFACTS.get(reference.key,{}).get("enters_tapped",False),
        conditional_swamp_bonus=ALPHA_ACTIVATED_CREATURES.get(reference.key,{}).get("conditional_swamp_bonus",False),
        lord_subtype=ALPHA_LORDS.get(reference.key,{}).get("lord_subtype",""),
        lord_power=ALPHA_LORDS.get(reference.key,{}).get("lord_power",0),
        lord_toughness=ALPHA_LORDS.get(reference.key,{}).get("lord_toughness",0),
        lord_keyword=ALPHA_LORDS.get(reference.key,{}).get("lord_keyword",""),
        lord_regeneration_cost=ALPHA_LORDS.get(reference.key,{}).get("lord_regeneration_cost",""),
        global_buff_color=ALPHA_GLOBAL_ENCHANTMENTS.get(reference.key,{}).get("global_buff_color",ALPHA_ARTIFACTS.get(reference.key,{}).get("global_buff_color","")),
        global_power=ALPHA_GLOBAL_ENCHANTMENTS.get(reference.key,{}).get("global_power",ALPHA_ARTIFACTS.get(reference.key,{}).get("global_power",0)),
        global_toughness=ALPHA_GLOBAL_ENCHANTMENTS.get(reference.key,{}).get("global_toughness",ALPHA_ARTIFACTS.get(reference.key,{}).get("global_toughness",0)),
        global_controller_only=ALPHA_GLOBAL_ENCHANTMENTS.get(reference.key,{}).get("global_controller_only",False),
        global_requires_untapped=ALPHA_GLOBAL_ENCHANTMENTS.get(reference.key,{}).get("global_requires_untapped",False),
        global_requires_attacking=ALPHA_GLOBAL_ENCHANTMENTS.get(reference.key,{}).get("global_requires_attacking",False),
        mana_flare=ALPHA_TAP_ENCHANTMENTS.get(reference.key,{}).get("mana_flare",False),
        land_tap_damage=ALPHA_TAP_ENCHANTMENTS.get(reference.key,{}).get("land_tap_damage",0),
        aura_extra_mana=ALPHA_TAP_ENCHANTMENTS.get(reference.key,{}).get("aura_extra_mana",""),
        aura_tap_damage=ALPHA_TAP_ENCHANTMENTS.get(reference.key,{}).get("aura_tap_damage",0),
        mountain_extra_red=ALPHA_ARTIFACTS.get(reference.key,{}).get("mountain_extra_red",False),
        opponent_forest_tap_life=ALPHA_TAP_ENCHANTMENTS.get(reference.key,{}).get("opponent_forest_tap_life",0),
        land_enter_damage=ALPHA_ARTIFACTS.get(reference.key,{}).get("land_enter_damage",0),
        land_grave_damage=ALPHA_ARTIFACTS.get(reference.key,{}).get("land_grave_damage",0),
        upkeep_each_damage=ALPHA_ARTIFACTS.get(reference.key,{}).get("upkeep_each_damage",0),
        upkeep_opponent_hand_damage=ALPHA_ARTIFACTS.get(reference.key,{}).get("upkeep_opponent_hand_damage",False),
        draw_step_extra=ALPHA_ARTIFACTS.get(reference.key,{}).get("draw_step_extra",0),
        untap_power_limit=ALPHA_ARTIFACTS.get(reference.key,{}).get("untap_power_limit",0),
        white_as_red=ALPHA_ARTIFACTS.get(reference.key,{}).get("white_as_red",False),
        cast_life_color=ALPHA_ARTIFACTS.get(reference.key,{}).get("cast_life_color",""),
        upkeep_untap_cost=ALPHA_ARTIFACTS.get(reference.key,{}).get("upkeep_untap_cost",""),
        draw_tapped_damage=ALPHA_ARTIFACTS.get(reference.key,{}).get("draw_tapped_damage",0),
        death_life=ALPHA_ARTIFACTS.get(reference.key,{}).get("death_life",False),
        animate_combat=ALPHA_ARTIFACTS.get(reference.key,{}).get("animate_combat",False),
        creates_token=ALPHA_ARTIFACTS.get(reference.key,{}).get("creates_token",""),
        activation_attached=ALPHA_ENCHANTMENTS.get(reference.key,{}).get("activation_attached",False),
    )

TOKENS = {
    "token:wasp": Card(key="token:wasp",name="Wasp",kind="Creature",type_line="Token Artifact Creature  Insect",scryfall_id="",oracle_id="",power=1,toughness=1,keywords=("flying",),text="Flying"),
}

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

if {card.key for card in PLAYABLE_ALPHA if card.support_family == "creature_ability"} != set(ALPHA_KEYWORDS) | set(ALPHA_PROTECTIONS) | set(ALPHA_LORDS) | set(ALPHA_MANA_CREATURES) | set(ALPHA_CHARACTERISTIC_CREATURES) | set(ALPHA_ACTIVATED_CREATURES) | {"lea:159"}:
    raise RuntimeError("Playable Alpha creature abilities do not match the validated keyword map.")

if {card.key for card in PLAYABLE_ALPHA if card.support_family == "land"} != ALPHA_LAND_KEYS:
    raise RuntimeError("Playable Alpha lands do not match the validated land map.")

if {card.key for card in PLAYABLE_ALPHA if card.support_family == "spell"} != set(ALPHA_SPELLS):
    raise RuntimeError("Playable Alpha spells do not match the validated spell map.")

if {card.key for card in PLAYABLE_ALPHA if card.support_family == "enchantment"} != set(ALPHA_ENCHANTMENTS) | set(ALPHA_GLOBAL_ENCHANTMENTS) | set(ALPHA_TAP_ENCHANTMENTS):
    raise RuntimeError("Playable Alpha enchantments do not match the validated enchantment map.")

if {card.key for card in PLAYABLE_ALPHA if card.support_family == "artifact"} != set(ALPHA_ARTIFACTS):
    raise RuntimeError("Playable Alpha artifacts do not match the validated artifact map.")
