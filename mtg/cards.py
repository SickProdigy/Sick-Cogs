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
    x_mana_color: str = ""
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
    additional_sacrifice_creature: bool = False
    sacrifice_mana_color: str = ""
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
    aura_attack_haste: bool = False
    aura_attack_override: bool = False
    aura_blocked_except_wall: bool = False
    aura_lure: bool = False
    aura_kudzu: bool = False
    aura_hostile: bool = False
    aura_control: bool = False
    aura_reanimate: bool = False
    aura_animate_mana_value: bool = False
    animate_land_type: str = ""
    animate_land_color: str = ""
    activation_attached: bool = False
    protection_colors: Tuple[str, ...] = ()
    aura_protection: str = ""
    protection_self_exception: bool = False
    haste: bool = False
    keywords: Tuple[str, ...] = ()
    max_block_power: Optional[int] = None
    max_blocks: int = 1
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
    enters_power_counters: int = 0
    end_combat_remove_power_counter: bool = False
    activation_upkeep_only: bool = False
    activation_controller_turn_only: bool = False
    activation_owner_only: bool = False
    activation_once_per_turn: bool = False
    activation_x_choice: bool = False
    prevent_source_color: str = ""
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
    extra_land_damage: int = 0
    land_grave_damage: int = 0
    upkeep_each_damage: int = 0
    upkeep_land_type_damage: str = ""
    aura_upkeep_damage: int = 0
    aura_power_leak: int = 0
    enters_copy_types: tuple = ()
    copy_add_type: str = ""
    copy_keep_colors: bool = False
    upkeep_copy_creature: bool = False
    aura_enters_tapped: bool = False
    aura_skip_untap: bool = False
    aura_upkeep_untap_cost: str = ""
    aura_controller_upkeep_cost: str = ""
    aura_controller_upkeep_life: int = 0
    aura_indestructible: bool = False
    aura_excludes_other_auras: bool = False
    aura_set_land_type: str = ""
    aura_choose_land_type: bool = False
    aura_enter_flying_damage: int = 0
    aura_death_toughness_damage: bool = False
    global_land_from_type: str = ""
    global_land_to_type: str = ""
    upkeep_turn_start_untapped_damage: bool = False
    end_step_sacrifice_without_creatures: bool = False
    tax_white_spells: int = 0
    tax_white_enchantment_abilities: int = 0
    upkeep_opponent_hand_damage: bool = False
    draw_step_extra: int = 0
    draw_step_sanctuary: bool = False
    untap_power_limit: int = 0
    untap_creature_limit: int = 0
    untap_land_limit: int = 0
    untap_limit_requires_untapped: bool = False
    skip_all_untap: bool = False
    white_as_red: bool = False
    cast_life_color: str = ""
    upkeep_untap_cost: str = ""
    upkeep_cost: str = ""
    upkeep_unpaid_effect: str = ""
    upkeep_unpaid_damage: int = 0
    upkeep_sacrifice_other: bool = False
    upkeep_sacrifice_damage: int = 0
    draw_tapped_damage: int = 0
    death_life: bool = False
    death_owner_half_life: bool = False
    animate_combat: bool = False
    creates_token: str = ""
    opponent_damage_discard_random: bool = False
    no_maximum_hand: bool = False
    discard_to_library: bool = False
    combat_destroy_nonwall: bool = False
    raging_river: bool = False
    lich: bool = False
    illusionary_mask: bool = False
    enchantment_cast_draw: bool = False
    dealt_damage_plus_counter: bool = False
    damaged_creature_death_counter: bool = False
    end_step_corpse_counters: bool = False
    graveyard_upkeep_return: bool = False
    aura_damage_vitality: bool = False
    attack_requires_defender_land_type: str = ""
    sacrifice_without_land_type: str = ""
    attacks_each_combat: bool = False
    redirects_unblocked_combat_damage: bool = False
    enters_x_plus_counters: bool = False
    hydra_damage_replacement: bool = False
    cant_be_blocked_by_subtype: str = ""
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
        if self.enters_power_counters: abilities.append(f"Enters with {self.enters_power_counters} +1/+0 counters")
        if self.enters_x_plus_counters: abilities.append("Enters with X +1/+1 counters")
        if self.hydra_damage_replacement: abilities.extend(("Remove a +1/+1 counter to prevent each 1 damage", "{R}: Prevent the next 1 damage", "{R}{R}{R}: Add a +1/+1 counter during your upkeep"))
        if self.end_combat_remove_power_counter: abilities.append("At end of combat, remove a +1/+0 counter if this creature attacked or blocked")
        if self.max_block_power is not None: abilities.append(f"Blocks power ≤{self.max_block_power}")
        if self.max_blocks>1: abilities.append(f"Can block {self.max_blocks} creatures each combat")
        if self.activation_cost or self.activation_effect:
            effects=[]
            if (self.activated_power or self.activated_toughness) and not self.activation_attached:
                effects.append(f"{self.activated_power:+d}/{self.activated_toughness:+d} until end of turn")
            if self.activated_keyword:
                effects.append(f"Gains {self.activated_keyword.title()} until end of turn")
            if self.sacrifice_after_activations:
                effects.append(f"Sacrifice at the next end step after activation {self.sacrifice_after_activations}")
            if self.activation_text: effects.append(self.activation_text)
            if self.activation_effect=="corpse_regenerate": effects.append("Regenerate this creature")
            costs=[self.activation_cost] if self.activation_cost else []
            if self.activation_effect=="corpse_regenerate": costs.append("Remove a corpse counter")
            if self.activation_tap: costs.append("{T}")
            abilities.append(", ".join(costs)+": "+"; ".join(effects))
        aura=[]
        if self.aura_power or self.aura_toughness: aura.append(f"Enchanted creature gets {self.aura_power:+d}/{self.aura_toughness:+d}")
        if self.aura_forest_scaling: aura.append("Enchanted creature gets +X/+Y for Forests you control")
        if self.aura_animate_mana_value: aura.append("Enchanted noncreature artifact is a creature with power and toughness equal to its mana value")
        if self.aura_control: aura.append("You control enchanted permanent")
        if self.aura_reanimate: aura.append("Reanimates enchanted creature card; enchanted creature gets -1/-0; sacrifice it when this Aura leaves")
        if self.aura_keyword: aura.append(f"Enchanted creature has {self.aura_keyword.replace('_',' ').title()}")
        if self.aura_attack_haste: aura.append("Enchanted creature can attack as though it had haste")
        if self.aura_protection: aura.append(f"Enchanted creature has Protection From {self.aura_protection}")
        if self.aura_attack_override: aura.append("Enchanted Wall can attack")
        if self.aura_blocked_except_wall: aura.append("Enchanted creature can be blocked only by Walls")
        if self.aura_lure: aura.append("All creatures able to block enchanted creature do so")
        if self.aura_kudzu: aura.append("When enchanted land becomes tapped, destroy it; its controller may attach this Aura to a land")
        if self.aura_damage_vitality: aura.append("Damage dealt to you adds vitality counters; at your upkeep, you may remove one to gain 1 life")
        abilities.extend(aura)
        if self.protection_colors: abilities.append("Protection From "+"/".join(self.protection_colors))
        if self.lord_subtype:
            granted=[]
            if self.lord_power or self.lord_toughness: granted.append(f"{self.lord_power:+d}/{self.lord_toughness:+d}")
            if self.lord_keyword: granted.append(self.lord_keyword.replace("_"," ").title())
            if self.lord_regeneration_cost: granted.append(f"{self.lord_regeneration_cost}: Regenerate")
            abilities.append(f"Other {self.lord_subtype} creatures have "+", ".join(granted))
        if self.animate_land_type:
            color_name={"W":"white","U":"blue","B":"black","R":"red","G":"green"}.get(self.animate_land_color,"")
            abilities.append(f"All {self.animate_land_type.title()}s are 1/1{(' '+color_name) if color_name else ''} creatures that are still lands")
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
        if self.upkeep_cost and self.upkeep_unpaid_effect=="sacrifice": abilities.append(f"At your upkeep, sacrifice this permanent unless you pay {self.upkeep_cost}")
        if self.upkeep_cost and self.upkeep_unpaid_effect=="damage": abilities.append(f"At your upkeep, this creature deals {self.upkeep_unpaid_damage} damage to you unless you pay {self.upkeep_cost}")
        if self.upkeep_cost and self.upkeep_unpaid_effect=="tap_opponent_land_sacrifice": abilities.append(f"At your upkeep, tap this creature and sacrifice a land an opponent chooses unless you pay {self.upkeep_cost}")
        if self.upkeep_sacrifice_other: abilities.append(f"At your upkeep, sacrifice another creature or this creature deals {self.upkeep_sacrifice_damage} damage to you")
        if self.draw_step_sanctuary: abilities.append("At your draw step, you may skip your draw; until your next turn, only creatures with flying or islandwalk can attack you")
        if self.draw_tapped_damage: abilities.append(f"At your draw step, if tapped, deals {self.draw_tapped_damage} damage to you")
        if self.opponent_damage_discard_random: abilities.append("Whenever this creature deals damage to an opponent, that player discards a card at random")
        if self.no_maximum_hand: abilities.append("You have no maximum hand size")
        if self.discard_to_library: abilities.append("Cards discarded by effects may be put on top of your library")
        if self.combat_destroy_nonwall: abilities.append("Whenever this creature blocks or becomes blocked by a non-Wall creature, destroy that creature at end of combat")
        if self.enchantment_cast_draw: abilities.append("Whenever you cast an enchantment spell, you may draw a card")
        if self.dealt_damage_plus_counter: abilities.append("Whenever dealt damage, put a +1/+1 counter on this creature")
        if self.damaged_creature_death_counter: abilities.append("Whenever a creature dealt damage by this creature this turn dies, put a +1/+1 counter on this creature")
        if self.graveyard_upkeep_return: abilities.append("At your upkeep, if this card has three creature cards above it in your graveyard, you may return it to the battlefield")
        if self.end_step_corpse_counters: abilities.append("At each end step, put a corpse counter on this creature for each creature that died this turn")
        if self.attack_requires_defender_land_type: abilities.append(f"Can't attack unless defending player controls a {self.attack_requires_defender_land_type.title()}")
        if self.sacrifice_without_land_type: abilities.append(f"When you control no {self.sacrifice_without_land_type.title()}s, sacrifice this creature")
        if self.attacks_each_combat: abilities.append("Attacks each combat if able")
        if self.redirects_unblocked_combat_damage: abilities.append("While untapped, unblocked-creature damage to you is dealt to this creature instead")
        if self.cant_be_blocked_by_subtype: abilities.append(f"Can't be blocked by {self.cant_be_blocked_by_subtype}s")
        if self.death_life: abilities.append("Whenever a creature dies, you may pay {1} to gain 1 life")
        if self.animate_combat: abilities.append("{2}: Becomes a 3/6 Golem artifact creature until end of combat; activate only during combat")
        if self.land_enter_damage: abilities.append(f"Whenever a land enters, deals {self.land_enter_damage} damage to its controller")
        if self.extra_land_damage: abilities.append(f"You may play any number of lands; each after your first each turn deals {self.extra_land_damage} damage to you")
        if self.land_grave_damage: abilities.append(f"Whenever a land goes from battlefield to graveyard, deals {self.land_grave_damage} damage to its controller")
        if self.upkeep_each_damage: abilities.append(f"At each player's upkeep, deals {self.upkeep_each_damage} damage to that player")
        if self.upkeep_land_type_damage: abilities.append(f"At each player's upkeep, deals damage equal to that player's {self.upkeep_land_type_damage.title()}s")
        if self.aura_power_leak: abilities.append(f"At enchanted enchantment controller's upkeep, they may pay any amount; deals {self.aura_power_leak} damage minus the amount paid")
        if self.enters_copy_types: abilities.append("May enter as a copy of " + " or ".join(self.enters_copy_types).lower())
        if self.upkeep_copy_creature: abilities.append("At your upkeep, may become a copy of target creature while retaining this ability")
        if self.aura_upkeep_damage: abilities.append(f"At enchanted permanent controller's upkeep, deals {self.aura_upkeep_damage} damage to that player")
        if self.aura_enters_tapped: abilities.append("When this Aura enters, tap enchanted creature")
        if self.aura_skip_untap: abilities.append("Enchanted creature does not untap during its controller's untap step")
        if self.aura_upkeep_untap_cost: abilities.append(f"At enchanted creature controller's upkeep, that player may pay {self.aura_upkeep_untap_cost} to untap it")
        if self.aura_controller_upkeep_cost: abilities.append(f"Enchanted land has \"At your upkeep, you may pay {self.aura_controller_upkeep_cost} to gain {self.aura_controller_upkeep_life} life\"")
        if self.aura_indestructible: abilities.append("Enchanted land has indestructible")
        if self.aura_excludes_other_auras: abilities.append("Enchanted land cannot be enchanted by other Auras")
        if self.aura_set_land_type: abilities.append(f"Enchanted land is a {self.aura_set_land_type.title()}")
        if self.aura_choose_land_type: abilities.append("Choose a basic land type; enchanted land is that type")
        if self.aura_enter_flying_damage: abilities.append(f"When this Aura enters, if enchanted creature has flying, deal {self.aura_enter_flying_damage} damage to it and it loses flying")
        if self.aura_death_toughness_damage: abilities.append("When enchanted creature dies, deal damage equal to its toughness to its controller")
        if self.global_land_from_type: abilities.append(f"All {self.global_land_from_type.title()}s are {self.global_land_to_type.title()}s")
        if self.upkeep_turn_start_untapped_damage: abilities.append("At each player’s upkeep, deals damage equal to lands they controlled untapped at the beginning of the turn")
        if self.end_step_sacrifice_without_creatures: abilities.append("At each end step, sacrifice this enchantment if no creatures are on the battlefield")
        if self.tax_white_spells: abilities.append(f"White spells cost {{{self.tax_white_spells}}} more to cast")
        if self.tax_white_enchantment_abilities: abilities.append(f"Activated abilities of white enchantments cost {{{self.tax_white_enchantment_abilities}}} more")
        if self.upkeep_opponent_hand_damage: abilities.append("At your opponent's upkeep, deals damage equal to cards in their hand minus 4")
        if self.draw_step_extra: abilities.append(f"At each draw step while untapped, that player draws {self.draw_step_extra} additional card"+("s" if self.draw_step_extra!=1 else ""))
        if self.untap_power_limit: abilities.append(f"Creatures with power {self.untap_power_limit} or greater don't untap")
        if self.untap_creature_limit: abilities.append(f"Players can untap no more than {self.untap_creature_limit} creature during their untap steps")
        if self.untap_land_limit: abilities.append(f"While untapped, players can untap no more than {self.untap_land_limit} land during their untap steps")
        if self.skip_all_untap: abilities.append("Players skip their untap steps")
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

ALPHA_COPY_PERMANENTS = {
    "lea:51": {"enters_copy_types": ("Creature",)},
    "lea:53": {"enters_copy_types": ("Artifact",), "copy_add_type": "Enchantment"},
    "lea:87": {"enters_copy_types": ("Creature",), "copy_keep_colors": True, "upkeep_copy_creature": True},
}

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
    "lea:113": {"lich":True},
    "lea:168": {"raging_river":True},
    "lea:25": {"draw_step_sanctuary":True},
    "lea:100": {"activation_cost":"{B}{B}", "activation_effect":"counter_color", "target_color":"G", "activation_text":"Counter target green spell"},
    "lea:10": {"activation_cost":"{1}", "activation_effect":"prevent_source_damage", "prevent_source_color":"U", "activation_text":"Prevent the next damage a chosen blue source would deal to you this turn"},
    "lea:11": {"activation_cost":"{1}", "activation_effect":"prevent_source_damage", "prevent_source_color":"G", "activation_text":"Prevent the next damage a chosen green source would deal to you this turn"},
    "lea:12": {"activation_cost":"{1}", "activation_effect":"prevent_source_damage", "prevent_source_color":"R", "activation_text":"Prevent the next damage a chosen red source would deal to you this turn"},
    "lea:13": {"activation_cost":"{1}", "activation_effect":"prevent_source_damage", "prevent_source_color":"W", "activation_text":"Prevent the next damage a chosen white source would deal to you this turn"},
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
    "lea:211": {"aura_target_types":("Creature",), "aura_lure":True},
    "lea:204": {"aura_target_types":("Land",), "aura_hostile":True, "aura_kudzu":True},
    "lea:57": {"aura_target_types":("Enchantment",), "aura_upkeep_damage":1, "aura_hostile":True},
    "lea:71": {"aura_target_types":("Enchantment",), "aura_power_leak":2, "aura_hostile":True},
    "lea:97": {"aura_target_types":("Land",), "aura_upkeep_damage":1, "aura_hostile":True},
    "lea:133": {"aura_target_types":("Artifact",), "aura_upkeep_damage":1, "aura_hostile":True},
    "lea:226": {"aura_target_types":("Creature",), "aura_upkeep_damage":1, "aura_hostile":True},
    "lea:48": {"aura_target_types":("Artifact",), "aura_animate_mana_value":True},
    "lea:92": {"aura_target_types":("Creature",), "aura_power":-1, "aura_reanimate":True},
    "lea:52": {"aura_target_types":("Creature",), "aura_hostile":True, "aura_control":True},
    "lea:81": {"aura_target_types":("Artifact",), "aura_hostile":True, "aura_control":True},
    "lea:209": {"animate_land_type":"forest"},
    "lea:26": {"upkeep_land_type_damage":"swamp"},
    "lea:192": {"extra_land_damage":1},
    "lea:80": {"skip_all_untap":True, "upkeep_cost":"{U}", "upkeep_unpaid_effect":"sacrifice"},
    "lea:175": {"untap_creature_limit":1},
    "lea:119": {"aura_target_types":("Creature",), "aura_hostile":True, "aura_enters_tapped":True, "aura_skip_untap":True, "aura_upkeep_untap_cost":"{4}"},
    "lea:202": {"aura_target_types":("Creature",), "aura_attack_haste":True, "activation_cost":"{0}", "activation_effect":"untap_attached", "activation_attached":True, "activation_controller_turn_only":True, "activation_once_per_turn":True, "activation_text":"Untap enchanted creature; activate only during your turn and only once each turn"},
    "lea:14": {"aura_target_types":("Land",), "aura_indestructible":True, "aura_excludes_other_auras":True},
    "lea:19": {"aura_target_types":("Land",), "aura_controller_upkeep_cost":"{W}{W}", "aura_controller_upkeep_life":1},
    "lea:15": {"global_land_from_type":"mountain", "global_land_to_type":"plains", "upkeep_cost":"{W}{W}", "upkeep_unpaid_effect":"sacrifice"},
    "lea:68": {"aura_target_types":("Land",), "aura_choose_land_type":True},
    "lea:107": {"aura_target_types":("Land",), "aura_hostile":True, "aura_set_land_type":"swamp"},
    "lea:120": {"activation_cost":"{B}", "activation_effect":"damage_all", "activation_amount":1, "activation_text":"Deals 1 damage to each creature and each player", "end_step_sacrifice_without_creatures":True},
    "lea:167": {"upkeep_turn_start_untapped_damage":True},
    "lea:55": {"aura_target_types":("Creature",), "aura_hostile":True, "aura_death_toughness_damage":True},
    "lea:145": {"aura_target_types":("Creature",), "aura_hostile":True, "aura_enter_flying_damage":2},
    "lea:208": {"aura_target_types":("Artifact",), "aura_damage_vitality":True},
    "lea:110": {"tax_white_spells":3, "tax_white_enchantment_abilities":3},
}

ALPHA_ARTIFACTS = {
    "lea:249": {"illusionary_mask":True},
    "lea:257": {"no_maximum_hand":True, "discard_to_library":True},
    "lea:235": {"activation_cost":"{1}", "activation_tap":True, "activation_effect":"chaos_orb_destroy", "activation_text":"Digital adaptation: destroy target nontoken permanent, then destroy this artifact"},
    "lea:246": {"activation_cost":"{1}", "activation_tap":True, "activation_effect":"grant_banding", "activation_text":"Target creature gains banding until end of turn"},
    "lea:230": {"land_enter_damage":2},
    "lea:240": {"activation_cost":"{2}", "activation_tap":True, "activation_effect":"add_mire_counter", "activation_upkeep_only":True, "activation_text":"Put a mire counter on target non-Swamp land; it is a Swamp while it has one"},
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
    "lea:275": {"untap_land_limit":1, "untap_limit_requires_untapped":True},
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
    "lea:256": {"animate_land_type":"swamp", "animate_land_color":"B"},
    "lea:274": {"enters_tapped":True, "skip_untap":True, "activation_tap":True, "activation_effect":"take_extra_turn", "activation_text":"Take an extra turn after this one"},
    "lea:272": {"activation_cost":"{5}", "activation_tap":True, "activation_effect":"create_token", "activation_text":"Create a 1/1 colorless Insect artifact creature token with flying named Wasp", "creates_token":"token:wasp"},
    "lea:237": {"activation_cost":"{3}", "activation_tap":True, "activation_effect":"prevent_player_damage", "activation_amount":2, "activation_text":"Prevent the next 2 damage that would be dealt to you this turn"},
    "lea:243": {"activation_cost":"{1}", "activation_effect":"cap_unblocked_damage", "activation_text":"Prevent all but 1 combat damage from a chosen unblocked creature"},
    "lea:252": {"activation_cost":"{1}", "activation_effect":"redirect_source_to_creature", "activation_text":"Choose a source and target creature; its next damage to that creature is dealt to you instead"},
    "lea:242": {"activation_cost":"{3}", "activation_tap":True, "activation_effect":"discard_choice", "activation_text":"Target player discards a card", "activation_controller_turn_only":True},
    "lea:245": {"activation_tap":True, "activation_effect":"look_hand", "activation_text":"Look at target player’s hand"},
}

ALPHA_MANA_CREATURES = {
    "lea:186": ("W","U","B","R","G"),
    "lea:210": ("G",),
}

ALPHA_ACTIVATED_CREATURES = {
    "lea:41": {"redirects_unblocked_combat_damage":True},
    "lea:117": {"activation_tap":True, "activation_effect":"force_attack", "activation_text":"Target eligible non-Wall creature the active player controls attacks this turn if able; destroy it at the next end step if it did not attack"},
    "lea:171": {"enters_x_plus_counters":True, "hydra_damage_replacement":True},
    "lea:31": {"activation_cost":"{0}", "activation_effect":"redirect_one_to_owner", "activation_owner_only":True, "death_owner_half_life":True, "activation_text":"The next 1 damage to this creature is dealt to its owner instead"},
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
    "lea:103": {"activation_tap":True, "activation_effect":"destroy_land", "activation_text":"Destroy target land"},
    "lea:123": {"activation_tap":True, "activation_effect":"destroy_tapped_creature", "activation_text":"Destroy target tapped creature"},
    "lea:142": {"activation_tap":True, "activation_effect":"destroy_wall", "activation_text":"Destroy target Wall"},
    "lea:143": {"activation_tap":True, "activation_effect":"unblockable", "activation_text":"Target creature with power 2 or less can't be blocked this turn"},
    "lea:165": {"activation_tap":True, "activation_effect":"damage_any", "activation_amount":2, "activation_self_damage":3, "activation_text":"Deals 2 damage to any target and 3 damage to you"},
    "lea:176": {"activation_tap":True, "activation_effect":"grant_flying_delayed_destroy", "activation_text":"Target creature you control with toughness less than this creature’s power gains flying until end of turn; destroy it at the beginning of the next end step"},
    "lea:196": {"activation_tap":True, "activation_effect":"set_land_forest", "activation_text":"Target land becomes a Forest until this creature leaves the battlefield"},
    "lea:205": {"activation_tap":True, "activation_effect":"untap_land", "activation_text":"Untap target land"},
    "lea:236": {"activation_cost":"{X}", "activation_tap":True, "activation_effect":"add_power_counters", "activation_text":"Put up to X +1/+0 counters on this creature (maximum seven)", "enters_power_counters":7, "end_combat_remove_power_counter":True, "activation_upkeep_only":True, "activation_x_choice":True},
    "lea:141": {"activation_cost":"{R}", "activated_power":1, "sacrifice_after_activations":4},
    "lea:153": {"activation_cost":"{R}", "activated_keyword":"flying"},
    "lea:90": {"activation_cost":"{U}", "activated_power":1},
    "lea:109": {"activation_cost":"{B}", "activated_power":1, "activated_toughness":1},
    "lea:155": {"activation_cost":"{R}", "activated_toughness":1},
    "lea:174": {"activation_cost":"{R}", "activated_power":1},
    "lea:181": {"activation_cost":"{R}", "activated_power":1},
}

ALPHA_DAMAGE_COUNTER_CREATURES = {
    "lea:127": {"damaged_creature_death_counter":True},
    "lea:195": {"dealt_damage_plus_counter":True},
}

ALPHA_DEATH_COUNTER_CREATURES = {
    "lea:126": {"end_step_corpse_counters":True, "activation_effect":"corpse_regenerate"},
}

ALPHA_GRAVEYARD_CREATURES = {
    "lea:116": {"graveyard_upkeep_return":True},
}

ALPHA_OPTIONAL_TRIGGERS = {
    "lea:222": {"enchantment_cast_draw":True},
}

ALPHA_COMBAT_REQUIREMENTS = {
    "lea:255": {"attacks_each_combat":True, "cant_be_blocked_by_subtype":"Wall"},
}

ALPHA_ISLAND_DEPENDENT_CREATURES = {
    "lea:70": {"attack_requires_defender_land_type":"island", "sacrifice_without_land_type":"island", "activation_tap":True, "activation_effect":"damage_any", "activation_amount":1, "activation_text":"Deals 1 damage to any target"},
    "lea:76": {"attack_requires_defender_land_type":"island", "sacrifice_without_land_type":"island"},
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
    "lea:196": "gaea_liege",
    "lea:121": "plague_rats",
    "lea:160": "non_wall_creatures",
}

ALPHA_SPELLS = {
    "lea:136": {"effect":"word_of_command"},
    "lea:152": {"effect":"fork"},
    "lea:187": {"effect":"camouflage"},
    "lea:3": {"effect":"balance"},
    "lea:63": {"effect":"text_change_land"},
    "lea:78": {"effect":"text_change_color"},
    "lea:32": {"effect":"set_color", "color_change":"W"},
    "lea:82": {"effect":"set_color", "color_change":"U"},
    "lea:83": {"effect":"extra_turn"},
    "lea:115": {"effect":"discard_random_x"},
    "lea:105": {"effect":"drain_life_x", "x_mana_color":"B"},
    "lea:104": {"effect":"search_library"},
    "lea:212": {"effect":"natural_selection"},
    "lea:56": {"effect":"drain_power"},
    "lea:72": {"effect":"power_sink"},
    "lea:84": {"effect":"timetwister"},
    "lea:183": {"effect":"wheel_seven"},
    "lea:101": {"effect":"set_color", "color_change":"B"},
    "lea:139": {"effect":"set_color", "color_change":"R"},
    "lea:207": {"effect":"set_color", "color_change":"G"},
    "lea:17": {"effect":"regenerate_target"},
    "lea:22": {"effect":"healing_salve", "amount":3},
    "lea:21": {"effect":"guardian_angel"},
    "lea:35": {"effect":"reverse_damage"},
    "lea:60": {"effect":"grant_keyword", "temporary_keyword":"flying"},
    "lea:85": {"effect":"tap_or_untap", "target_types":("Artifact","Creature","Land")},
    "lea:65": {"effect":"mana_short"},
    "lea:98": {"effect":"add_mana", "mana_color":"B", "mana_amount":3},
    "lea:178": {"effect":"destroy_wall"},
    "lea:6": {"effect":"blaze_of_glory"},
    "lea:147": {"effect":"false_orders"},
    "lea:149": {"effect":"fireball"},
    "lea:88": {"effect":"volcanic_eruption"},
    "lea:220": {"effect":"destroy_all_enchantments"},
    "lea:193": {"effect":"prevent_combat_damage"},
    "lea:50": {"effect":"draw_target_x"},
    "lea:111": {"effect":"pump_power_x"},
    "lea:185": {"effect":"berserk"},
    "lea:188": {"effect":"channel"},
    "lea:140": {"effect":"damage_x_exile"},
    "lea:146": {"effect":"earthquake_x"},
    "lea:200": {"effect":"hurricane_x"},
    "lea:217": {"effect":"life_target_x"},
    "lea:49": {"effect":"elemental_blast", "target_color":"R"},
    "lea:54": {"effect":"counter_spell"},
    "lea:79": {"effect":"counter_mana_value_x"},
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
    "lea:124": {"effect":"sacrifice_mana", "additional_sacrifice_creature":True, "sacrifice_mana_color":"B"},
    "lea:128": {"effect":"simulacrum"},
    "lea:214": {"effect":"return_grave_card_hand"},
    "lea:77": {"effect":"siren_call"},
}

ALPHA_DAMAGE_TRIGGERS = {
    "lea:112": {"opponent_damage_discard_random":True},
}

ALPHA_COMBAT_TRIGGERS = {
    "lea:189": {"combat_destroy_nonwall":True},
    "lea:218": {"combat_destroy_nonwall":True},
}

ALPHA_UPKEEP_CREATURES = {
    "lea:103": {"upkeep_cost":"{B}{B}{B}", "upkeep_unpaid_effect":"tap_opponent_land_sacrifice"},
    "lea:114": {"upkeep_sacrifice_other":True, "upkeep_sacrifice_damage":7},
    "lea:67": {"upkeep_cost":"{U}", "upkeep_unpaid_effect":"sacrifice"},
    "lea:194": {"upkeep_cost":"{G}{G}{G}{G}", "upkeep_unpaid_effect":"damage", "upkeep_unpaid_damage":8},
}

ALPHA_KEYWORDS = {
    "lea:4": ("banding",),
    "lea:28": ("flying", "banding"),
    "lea:219": ("banding",),
    "lea:39": ("flying", "vigilance"),
    "lea:42": ("defender", "flying"),
    "lea:43": ("first_strike",),
    "lea:46": ("flying",),
    "lea:64": ("flying",),
    "lea:67": ("flying",),
    "lea:69": ("flying",),
    "lea:89": ("defender", "flying"),
    "lea:90": ("defender",),
    "lea:94": ("first_strike",),
    "lea:141": ("flying",),
    "lea:95": ("swampwalk",),
    "lea:112": ("flying",),
    "lea:114": ("flying", "trample"),
    "lea:116": ("haste",),
    "lea:118": ("flying",),
    "lea:155": ("flying",),
    "lea:170": ("flying",),
    "lea:174": ("flying",),
    "lea:179": ("trample",),
    "lea:181": ("defender",),
    "lea:132": ("defender",),
    "lea:135": ("flying",),
    "lea:223": ("defender",),
    "lea:258": ("defender",),
    "lea:182": ("defender",),
    "lea:191": ("first_strike",),
    "lea:194": ("trample",),
    "lea:198": ("reach",),
    "lea:215": ("flying",),
    "lea:216": ("forestwalk",),
    "lea:224": ("defender",),
    "lea:225": ("defender",),
    "lea:227": ("trample",),
    "lea:186": ("flying",),
    "lea:189": ("flying",),
}

CARDS = dict(BASE_CARDS)
for reference in PLAYABLE_ALPHA:
    CARDS[reference.key] = Card(
        key=reference.key,
        name=reference.name,
        kind="Land" if reference.support_family == "land" else reference.kind if reference.support_family in {"spell","artifact","enchantment","digital_adaptation_required"} else "Creature",
        type_line=reference.type_line,
        set_code="lea",
        scryfall_id=reference.scryfall_id,
        oracle_id=reference.oracle_id,
        mana_cost=reference.mana_cost,
        x_mana_color=ALPHA_SPELLS.get(reference.key,{}).get("x_mana_color",""),
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
        additional_sacrifice_creature=ALPHA_SPELLS.get(reference.key, {}).get("additional_sacrifice_creature",False),
        sacrifice_mana_color=ALPHA_SPELLS.get(reference.key, {}).get("sacrifice_mana_color",""),
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
        aura_attack_haste=ALPHA_ENCHANTMENTS.get(reference.key, {}).get("aura_attack_haste",False),
        aura_attack_override=ALPHA_ENCHANTMENTS.get(reference.key, {}).get("aura_attack_override",False),
        aura_blocked_except_wall=ALPHA_ENCHANTMENTS.get(reference.key, {}).get("aura_blocked_except_wall",False),
        aura_lure=ALPHA_ENCHANTMENTS.get(reference.key, {}).get("aura_lure",False),
        aura_kudzu=ALPHA_ENCHANTMENTS.get(reference.key, {}).get("aura_kudzu",False),
        aura_hostile=ALPHA_ENCHANTMENTS.get(reference.key, ALPHA_TAP_ENCHANTMENTS.get(reference.key, {})).get("aura_hostile",False),
        aura_control=ALPHA_ENCHANTMENTS.get(reference.key,{}).get("aura_control",False),
        aura_reanimate=ALPHA_ENCHANTMENTS.get(reference.key,{}).get("aura_reanimate",False),
        aura_animate_mana_value=ALPHA_ENCHANTMENTS.get(reference.key,{}).get("aura_animate_mana_value",False),
        animate_land_type=ALPHA_ENCHANTMENTS.get(reference.key,{}).get("animate_land_type",ALPHA_ARTIFACTS.get(reference.key,{}).get("animate_land_type","")),
        animate_land_color=ALPHA_ENCHANTMENTS.get(reference.key,{}).get("animate_land_color",ALPHA_ARTIFACTS.get(reference.key,{}).get("animate_land_color","")),
        aura_protection=ALPHA_ENCHANTMENTS.get(reference.key, {}).get("aura_protection",""),
        protection_self_exception=ALPHA_ENCHANTMENTS.get(reference.key, {}).get("protection_self_exception",False),
        protection_colors=ALPHA_PROTECTIONS.get(reference.key,()),
        keywords=ALPHA_KEYWORDS.get(reference.key, ()),
        max_block_power=1 if reference.key == "lea:159" else None,
        max_blocks=2 if reference.key=="lea:179" else 1,
        characteristic_pt=ALPHA_CHARACTERISTIC_CREATURES.get(reference.key),
        activation_cost=ALPHA_ENCHANTMENTS.get(reference.key,{}).get("activation_cost",ALPHA_ACTIVATED_CREATURES.get(reference.key,ALPHA_ISLAND_DEPENDENT_CREATURES.get(reference.key,{})).get("activation_cost",ALPHA_ARTIFACTS.get(reference.key,{}).get("activation_cost",""))),
        activated_power=ALPHA_ENCHANTMENTS.get(reference.key,{}).get("activated_power",ALPHA_ACTIVATED_CREATURES.get(reference.key,{}).get("activated_power",0)),
        activated_toughness=ALPHA_ENCHANTMENTS.get(reference.key,{}).get("activated_toughness",ALPHA_ACTIVATED_CREATURES.get(reference.key,{}).get("activated_toughness",0)),
        activated_keyword=ALPHA_ACTIVATED_CREATURES.get(reference.key,{}).get("activated_keyword",""),
        sacrifice_after_activations=ALPHA_ACTIVATED_CREATURES.get(reference.key,{}).get("sacrifice_after_activations",0),
        activation_effect=ALPHA_DEATH_COUNTER_CREATURES.get(reference.key,{}).get("activation_effect",ALPHA_ENCHANTMENTS.get(reference.key,{}).get("activation_effect",ALPHA_ACTIVATED_CREATURES.get(reference.key,ALPHA_ISLAND_DEPENDENT_CREATURES.get(reference.key,{})).get("activation_effect",ALPHA_ARTIFACTS.get(reference.key,{}).get("activation_effect","")))),
        activation_tap=ALPHA_ACTIVATED_CREATURES.get(reference.key,ALPHA_ISLAND_DEPENDENT_CREATURES.get(reference.key,{})).get("activation_tap",ALPHA_ARTIFACTS.get(reference.key,{}).get("activation_tap",False)),
        activation_text=ALPHA_ENCHANTMENTS.get(reference.key,{}).get("activation_text",ALPHA_ACTIVATED_CREATURES.get(reference.key,ALPHA_ISLAND_DEPENDENT_CREATURES.get(reference.key,{})).get("activation_text",ALPHA_ARTIFACTS.get(reference.key,{}).get("activation_text",""))),
        activation_amount=ALPHA_ENCHANTMENTS.get(reference.key,{}).get("activation_amount",ALPHA_ACTIVATED_CREATURES.get(reference.key,ALPHA_ISLAND_DEPENDENT_CREATURES.get(reference.key,{})).get("activation_amount",ALPHA_ARTIFACTS.get(reference.key,{}).get("activation_amount",0))),
        activation_self_damage=ALPHA_ACTIVATED_CREATURES.get(reference.key,{}).get("activation_self_damage",0),
        enters_power_counters=ALPHA_ACTIVATED_CREATURES.get(reference.key,{}).get("enters_power_counters",0),
        end_combat_remove_power_counter=ALPHA_ACTIVATED_CREATURES.get(reference.key,{}).get("end_combat_remove_power_counter",False),
        activation_upkeep_only=ALPHA_ACTIVATED_CREATURES.get(reference.key,{}).get("activation_upkeep_only",ALPHA_ARTIFACTS.get(reference.key,{}).get("activation_upkeep_only",False)),
        activation_controller_turn_only=ALPHA_ENCHANTMENTS.get(reference.key,{}).get("activation_controller_turn_only",ALPHA_ARTIFACTS.get(reference.key,{}).get("activation_controller_turn_only",False)),
        activation_owner_only=ALPHA_ACTIVATED_CREATURES.get(reference.key,{}).get("activation_owner_only",False),
        redirects_unblocked_combat_damage=ALPHA_ACTIVATED_CREATURES.get(reference.key,{}).get("redirects_unblocked_combat_damage",False),
        enters_x_plus_counters=ALPHA_ACTIVATED_CREATURES.get(reference.key,{}).get("enters_x_plus_counters",False),
        hydra_damage_replacement=ALPHA_ACTIVATED_CREATURES.get(reference.key,{}).get("hydra_damage_replacement",False),
        activation_once_per_turn=ALPHA_ENCHANTMENTS.get(reference.key,{}).get("activation_once_per_turn",False),
        activation_x_choice=ALPHA_ACTIVATED_CREATURES.get(reference.key,{}).get("activation_x_choice",False),
        prevent_source_color=ALPHA_ENCHANTMENTS.get(reference.key,{}).get("prevent_source_color",""),
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
        extra_land_damage=ALPHA_ENCHANTMENTS.get(reference.key,{}).get("extra_land_damage",0),
        land_grave_damage=ALPHA_ARTIFACTS.get(reference.key,{}).get("land_grave_damage",0),
        upkeep_each_damage=ALPHA_ARTIFACTS.get(reference.key,{}).get("upkeep_each_damage",0),
        upkeep_land_type_damage=ALPHA_ENCHANTMENTS.get(reference.key,{}).get("upkeep_land_type_damage",""),
        aura_upkeep_damage=ALPHA_ENCHANTMENTS.get(reference.key,{}).get("aura_upkeep_damage",0),
        aura_power_leak=ALPHA_ENCHANTMENTS.get(reference.key,{}).get("aura_power_leak",0),
        enters_copy_types=ALPHA_COPY_PERMANENTS.get(reference.key,{}).get("enters_copy_types",()),
        copy_add_type=ALPHA_COPY_PERMANENTS.get(reference.key,{}).get("copy_add_type",""),
        copy_keep_colors=ALPHA_COPY_PERMANENTS.get(reference.key,{}).get("copy_keep_colors",False),
        upkeep_copy_creature=ALPHA_COPY_PERMANENTS.get(reference.key,{}).get("upkeep_copy_creature",False),
        aura_enters_tapped=ALPHA_ENCHANTMENTS.get(reference.key,{}).get("aura_enters_tapped",False),
        aura_skip_untap=ALPHA_ENCHANTMENTS.get(reference.key,{}).get("aura_skip_untap",False),
        aura_upkeep_untap_cost=ALPHA_ENCHANTMENTS.get(reference.key,{}).get("aura_upkeep_untap_cost",""),
        aura_controller_upkeep_cost=ALPHA_ENCHANTMENTS.get(reference.key,{}).get("aura_controller_upkeep_cost",""),
        aura_controller_upkeep_life=ALPHA_ENCHANTMENTS.get(reference.key,{}).get("aura_controller_upkeep_life",0),
        aura_indestructible=ALPHA_ENCHANTMENTS.get(reference.key,{}).get("aura_indestructible",False),
        aura_excludes_other_auras=ALPHA_ENCHANTMENTS.get(reference.key,{}).get("aura_excludes_other_auras",False),
        aura_set_land_type=ALPHA_ENCHANTMENTS.get(reference.key,{}).get("aura_set_land_type",""),
        aura_choose_land_type=ALPHA_ENCHANTMENTS.get(reference.key,{}).get("aura_choose_land_type",False),
        aura_enter_flying_damage=ALPHA_ENCHANTMENTS.get(reference.key,{}).get("aura_enter_flying_damage",0),
        aura_death_toughness_damage=ALPHA_ENCHANTMENTS.get(reference.key,{}).get("aura_death_toughness_damage",False),
        global_land_from_type=ALPHA_ENCHANTMENTS.get(reference.key,{}).get("global_land_from_type",""),
        global_land_to_type=ALPHA_ENCHANTMENTS.get(reference.key,{}).get("global_land_to_type",""),
        upkeep_turn_start_untapped_damage=ALPHA_ENCHANTMENTS.get(reference.key,{}).get("upkeep_turn_start_untapped_damage",False),
        end_step_sacrifice_without_creatures=ALPHA_ENCHANTMENTS.get(reference.key,{}).get("end_step_sacrifice_without_creatures",False),
        tax_white_spells=ALPHA_ENCHANTMENTS.get(reference.key,{}).get("tax_white_spells",0),
        tax_white_enchantment_abilities=ALPHA_ENCHANTMENTS.get(reference.key,{}).get("tax_white_enchantment_abilities",0),
        upkeep_opponent_hand_damage=ALPHA_ARTIFACTS.get(reference.key,{}).get("upkeep_opponent_hand_damage",False),
        draw_step_extra=ALPHA_ARTIFACTS.get(reference.key,{}).get("draw_step_extra",0),
        draw_step_sanctuary=ALPHA_ENCHANTMENTS.get(reference.key,{}).get("draw_step_sanctuary",False),
        untap_power_limit=ALPHA_ARTIFACTS.get(reference.key,{}).get("untap_power_limit",0),
        untap_creature_limit=ALPHA_ENCHANTMENTS.get(reference.key,{}).get("untap_creature_limit",0),
        untap_land_limit=ALPHA_ARTIFACTS.get(reference.key,{}).get("untap_land_limit",0),
        untap_limit_requires_untapped=ALPHA_ARTIFACTS.get(reference.key,{}).get("untap_limit_requires_untapped",False),
        skip_all_untap=ALPHA_ENCHANTMENTS.get(reference.key,{}).get("skip_all_untap",False),
        white_as_red=ALPHA_ARTIFACTS.get(reference.key,{}).get("white_as_red",False),
        cast_life_color=ALPHA_ARTIFACTS.get(reference.key,{}).get("cast_life_color",""),
        upkeep_untap_cost=ALPHA_ARTIFACTS.get(reference.key,{}).get("upkeep_untap_cost",""),
        upkeep_cost=ALPHA_UPKEEP_CREATURES.get(reference.key,{}).get("upkeep_cost",ALPHA_ENCHANTMENTS.get(reference.key,{}).get("upkeep_cost","")),
        upkeep_unpaid_effect=ALPHA_UPKEEP_CREATURES.get(reference.key,{}).get("upkeep_unpaid_effect",ALPHA_ENCHANTMENTS.get(reference.key,{}).get("upkeep_unpaid_effect","")),
        upkeep_unpaid_damage=ALPHA_UPKEEP_CREATURES.get(reference.key,{}).get("upkeep_unpaid_damage",0),
        upkeep_sacrifice_other=ALPHA_UPKEEP_CREATURES.get(reference.key,{}).get("upkeep_sacrifice_other",False),
        upkeep_sacrifice_damage=ALPHA_UPKEEP_CREATURES.get(reference.key,{}).get("upkeep_sacrifice_damage",0),
        draw_tapped_damage=ALPHA_ARTIFACTS.get(reference.key,{}).get("draw_tapped_damage",0),
        death_life=ALPHA_ARTIFACTS.get(reference.key,{}).get("death_life",False),
        death_owner_half_life=ALPHA_ACTIVATED_CREATURES.get(reference.key,{}).get("death_owner_half_life",False),
        animate_combat=ALPHA_ARTIFACTS.get(reference.key,{}).get("animate_combat",False),
        creates_token=ALPHA_ARTIFACTS.get(reference.key,{}).get("creates_token",""),
        opponent_damage_discard_random=ALPHA_DAMAGE_TRIGGERS.get(reference.key,{}).get("opponent_damage_discard_random",False),
        no_maximum_hand=ALPHA_ARTIFACTS.get(reference.key,{}).get("no_maximum_hand",False),
        discard_to_library=ALPHA_ARTIFACTS.get(reference.key,{}).get("discard_to_library",False),
        combat_destroy_nonwall=ALPHA_COMBAT_TRIGGERS.get(reference.key,{}).get("combat_destroy_nonwall",False),
        raging_river=ALPHA_ENCHANTMENTS.get(reference.key,{}).get("raging_river",False),
        lich=ALPHA_ENCHANTMENTS.get(reference.key,{}).get("lich",False),
        illusionary_mask=ALPHA_ARTIFACTS.get(reference.key,{}).get("illusionary_mask",False),
        enchantment_cast_draw=ALPHA_OPTIONAL_TRIGGERS.get(reference.key,{}).get("enchantment_cast_draw",False),
        dealt_damage_plus_counter=ALPHA_DAMAGE_COUNTER_CREATURES.get(reference.key,{}).get("dealt_damage_plus_counter",False),
        damaged_creature_death_counter=ALPHA_DAMAGE_COUNTER_CREATURES.get(reference.key,{}).get("damaged_creature_death_counter",False),
        end_step_corpse_counters=ALPHA_DEATH_COUNTER_CREATURES.get(reference.key,{}).get("end_step_corpse_counters",False),
        graveyard_upkeep_return=ALPHA_GRAVEYARD_CREATURES.get(reference.key,{}).get("graveyard_upkeep_return",False),
        aura_damage_vitality=ALPHA_ENCHANTMENTS.get(reference.key,{}).get("aura_damage_vitality",False),
        attack_requires_defender_land_type=ALPHA_ISLAND_DEPENDENT_CREATURES.get(reference.key,{}).get("attack_requires_defender_land_type",""),
        sacrifice_without_land_type=ALPHA_ISLAND_DEPENDENT_CREATURES.get(reference.key,{}).get("sacrifice_without_land_type",""),
        attacks_each_combat=ALPHA_COMBAT_REQUIREMENTS.get(reference.key,{}).get("attacks_each_combat",False),
        cant_be_blocked_by_subtype=ALPHA_COMBAT_REQUIREMENTS.get(reference.key,{}).get("cant_be_blocked_by_subtype",""),
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

if {card.key for card in PLAYABLE_ALPHA if card.support_family == "creature_ability"} != set(ALPHA_KEYWORDS) | set(ALPHA_PROTECTIONS) | set(ALPHA_LORDS) | set(ALPHA_MANA_CREATURES) | set(ALPHA_CHARACTERISTIC_CREATURES) | set(ALPHA_ACTIVATED_CREATURES) | set(ALPHA_COMBAT_TRIGGERS) | set(ALPHA_UPKEEP_CREATURES) | set(ALPHA_ISLAND_DEPENDENT_CREATURES) | set(ALPHA_COMBAT_REQUIREMENTS) | set(ALPHA_OPTIONAL_TRIGGERS) | {"lea:51","lea:87"} | set(ALPHA_DAMAGE_COUNTER_CREATURES) | set(ALPHA_DEATH_COUNTER_CREATURES) | set(ALPHA_GRAVEYARD_CREATURES) | {"lea:159"}:
    raise RuntimeError("Playable Alpha creature abilities do not match the validated keyword map.")

if {card.key for card in PLAYABLE_ALPHA if card.support_family == "land"} != ALPHA_LAND_KEYS:
    raise RuntimeError("Playable Alpha lands do not match the validated land map.")

if {card.key for card in PLAYABLE_ALPHA if card.support_family == "spell"} != set(ALPHA_SPELLS):
    raise RuntimeError("Playable Alpha spells do not match the validated spell map.")

if {card.key for card in PLAYABLE_ALPHA if card.support_family == "enchantment"} != set(ALPHA_ENCHANTMENTS) | set(ALPHA_GLOBAL_ENCHANTMENTS) | set(ALPHA_TAP_ENCHANTMENTS) | {"lea:53"}:
    raise RuntimeError("Playable Alpha enchantments do not match the validated enchantment map.")

if {card.key for card in PLAYABLE_ALPHA if card.support_family in ("artifact","digital_adaptation_required")} != set(ALPHA_ARTIFACTS):
    raise RuntimeError("Playable Alpha artifacts do not match the validated artifact map.")
