from dataclasses import dataclass
from typing import Optional

@dataclass(frozen=True)
class Card:
    key: str
    name: str
    kind: str
    cost: int = 0
    power: int = 0
    toughness: int = 0
    text: str = ""
    effect: Optional[str] = None
    amount: int = 0

    @property
    def land(self): return self.kind == "Land"
    @property
    def creature(self): return self.kind == "Creature"

CARDS = {
    "mountain": Card("mountain", "Mountain", "Land", text="Tap: Add one mana."),
    "forest": Card("forest", "Forest", "Land", text="Tap: Add one mana."),
    "goblin": Card("goblin", "Raging Goblin", "Creature", 1, 1, 1, "Haste."),
    "bear": Card("bear", "Bear Cub", "Creature", 2, 2, 2),
    "giant": Card("giant", "Hill Giant", "Creature", 4, 3, 3),
    "centaur": Card("centaur", "Centaur Courser", "Creature", 3, 3, 3),
    "shock": Card("shock", "Shock", "Instant", 1, text="Deal 2 damage to a player.", effect="damage", amount=2),
    "strike": Card("strike", "Lightning Strike", "Instant", 2, text="Deal 3 damage to a player.", effect="damage", amount=3),
    "growth": Card("growth", "Giant Growth", "Instant", 1, text="A creature gets +3/+3 this turn.", effect="pump", amount=3),
    "renew": Card("renew", "Natural Renewal", "Sorcery", 2, text="Gain 4 life.", effect="life", amount=4),
    "inspire": Card("inspire", "Inspiration", "Sorcery", 3, text="Draw two cards.", effect="draw", amount=2),
}

def starter(color):
    if color == "red":
        return ["mountain"]*24 + ["goblin"]*12 + ["giant"]*8 + ["shock"]*8 + ["strike"]*8
    if color == "green":
        return ["forest"]*24 + ["bear"]*12 + ["centaur"]*8 + ["growth"]*8 + ["renew"]*4 + ["inspire"]*4
    raise ValueError("Unknown deck.")
