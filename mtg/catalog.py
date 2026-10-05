import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple


@dataclass(frozen=True)
class ReferenceCard:
    key: str
    name: str
    scryfall_id: str
    oracle_id: str
    collector_number: str
    mana_cost: str
    mana_value: float
    type_line: str
    colors: Tuple[str, ...]
    color_identity: Tuple[str, ...]
    rarity: str
    oracle_text: str
    power: Optional[str]
    toughness: Optional[str]
    layout: str
    support_family: str
    engine_status: str

    @property
    def kind(self):
        return self.type_line.split(" — ", 1)[0]


_ALPHA_PATH = Path(__file__).with_name("data") / "alpha.json"
_ALPHA_DATA = json.loads(_ALPHA_PATH.read_text(encoding="utf-8"))
if _ALPHA_DATA.get("schema") != 1 or _ALPHA_DATA.get("set", {}).get("code") != "lea":
    raise RuntimeError("Unsupported MTG Alpha catalog schema.")

ALPHA_SET = dict(_ALPHA_DATA["set"])
ALPHA_CARDS = tuple(
    ReferenceCard(
        **{
            **raw,
            "colors": tuple(raw.get("colors", ())),
            "color_identity": tuple(raw.get("color_identity", ())),
        }
    )
    for raw in _ALPHA_DATA["cards"]
)
ALPHA_BY_KEY = {card.key: card for card in ALPHA_CARDS}
if len(ALPHA_CARDS) != ALPHA_SET.get("printing_count") or len(ALPHA_BY_KEY) != len(ALPHA_CARDS):
    raise RuntimeError("Alpha catalog printing count or keys are invalid.")
if len({card.scryfall_id for card in ALPHA_CARDS}) != len(ALPHA_CARDS):
    raise RuntimeError("Alpha catalog printing identifiers are not unique.")
if len({card.oracle_id for card in ALPHA_CARDS}) != ALPHA_SET.get("distinct_card_count"):
    raise RuntimeError("Alpha catalog distinct-card count is invalid.")
SUPPORT_FAMILIES = {"vanilla_creature","creature_ability","spell","artifact","enchantment","land","excluded_ante","digital_adaptation_required"}
if any(card.support_family not in SUPPORT_FAMILIES for card in ALPHA_CARDS):
    raise RuntimeError("Alpha catalog contains an unknown support family.")
if any(card.engine_status not in {"playable", "reference_only"} for card in ALPHA_CARDS):
    raise RuntimeError("Alpha catalog contains an unknown engine status.")
PLAYABLE_ALPHA = tuple(card for card in ALPHA_CARDS if card.engine_status == "playable")
REFERENCE_ALPHA = tuple(card for card in ALPHA_CARDS if card.engine_status == "reference_only")


def search_alpha(query):
    query = query.casefold().strip()
    exact = [
        card for card in ALPHA_CARDS
        if query in {card.key.casefold(), card.name.casefold(), card.collector_number.casefold()}
    ]
    if exact:
        return exact
    return [card for card in ALPHA_CARDS if query in card.name.casefold()]
