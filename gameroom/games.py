import re
import secrets
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple


SUITS = ("♠", "♥", "♦", "♣")
RANKS = ("2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A")
DICE_RE = re.compile(
    r"^(?:(?P<count>\d{1,2})?d(?P<sides>\d{1,4})|(?P<sides_only>\d{1,4}))"
    r"(?P<modifier>[+-]\d{1,5})?$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Card:
    rank: str
    suit: str

    @property
    def label(self) -> str:
        return f"{self.rank}{self.suit}"

    @property
    def high_value(self) -> int:
        if self.rank == "A":
            return 14
        if self.rank in {"J", "Q", "K"}:
            return {"J": 11, "Q": 12, "K": 13}[self.rank]
        return int(self.rank)


@dataclass(frozen=True)
class DiceSpec:
    count: int
    sides: int
    modifier: int = 0

    @property
    def notation(self) -> str:
        suffix = ""
        if self.modifier > 0:
            suffix = f"+{self.modifier}"
        elif self.modifier < 0:
            suffix = str(self.modifier)
        return f"{self.count}d{self.sides}{suffix}"

    def roll(self) -> Tuple[List[int], int]:
        rolls = [secrets.randbelow(self.sides) + 1 for _ in range(self.count)]
        return rolls, sum(rolls) + self.modifier


def parse_dice(notation: str) -> DiceSpec:
    cleaned = notation.strip().replace(" ", "")
    match = DICE_RE.fullmatch(cleaned)
    if not match:
        raise ValueError("Use dice notation such as d20, 2d6, or 2d8+3.")

    if match.group("sides_only"):
        count = 1
        sides = int(match.group("sides_only"))
    else:
        count = int(match.group("count") or 1)
        sides = int(match.group("sides"))

    modifier = int(match.group("modifier") or 0)
    if not 1 <= count <= 20:
        raise ValueError("Roll between 1 and 20 dice at a time.")
    if not 2 <= sides <= 1000:
        raise ValueError("Dice must have between 2 and 1,000 sides.")
    if not -10000 <= modifier <= 10000:
        raise ValueError("The modifier must be between -10,000 and 10,000.")
    return DiceSpec(count, sides, modifier)


def new_deck() -> List[Card]:
    deck = [Card(rank, suit) for suit in SUITS for rank in RANKS]
    secrets.SystemRandom().shuffle(deck)
    return deck


def hand_value(hand: Sequence[Card]) -> int:
    value = 0
    aces = 0
    for card in hand:
        if card.rank == "A":
            value += 11
            aces += 1
        elif card.rank in {"J", "Q", "K"}:
            value += 10
        else:
            value += int(card.rank)
    while value > 21 and aces:
        value -= 10
        aces -= 1
    return value


def format_hand(hand: Sequence[Card], hide_second: bool = False) -> str:
    labels = [card.label for card in hand]
    if hide_second and len(labels) > 1:
        labels[1] = "❓"
    return " ".join(labels)


class BlackjackGame:
    def __init__(self, deck: Optional[List[Card]] = None):
        self.deck = list(deck) if deck is not None else new_deck()
        self.player = [self._draw(), self._draw()]
        self.dealer = [self._draw(), self._draw()]
        self.finished = False
        self.result: Optional[str] = None
        self._resolve_naturals()

    def _draw(self) -> Card:
        if not self.deck:
            raise RuntimeError("The deck is empty.")
        return self.deck.pop()

    def _resolve_naturals(self) -> None:
        player_blackjack = hand_value(self.player) == 21
        dealer_blackjack = hand_value(self.dealer) == 21
        if player_blackjack and dealer_blackjack:
            self.finished = True
            self.result = "Both have blackjack — push."
        elif player_blackjack:
            self.finished = True
            self.result = "Blackjack! You win."
        elif dealer_blackjack:
            self.finished = True
            self.result = "Dealer blackjack."

    def hit(self) -> None:
        if self.finished:
            return
        self.player.append(self._draw())
        value = hand_value(self.player)
        if value > 21:
            self.finished = True
            self.result = "Bust — dealer wins."
        elif value == 21:
            self.stand()

    def double(self) -> None:
        if self.finished or len(self.player) != 2:
            return
        self.player.append(self._draw())
        if hand_value(self.player) > 21:
            self.finished = True
            self.result = "Bust — dealer wins."
            return
        self.stand()

    def stand(self) -> None:
        if self.finished:
            return
        while hand_value(self.dealer) < 17:
            self.dealer.append(self._draw())
        player_value = hand_value(self.player)
        dealer_value = hand_value(self.dealer)
        self.finished = True
        if dealer_value > 21:
            self.result = "Dealer busts — you win!"
        elif player_value > dealer_value:
            self.result = "You win!"
        elif player_value < dealer_value:
            self.result = "Dealer wins."
        else:
            self.result = "Push — tied hand."


class HigherLowerGame:
    def __init__(self, deck: Optional[List[Card]] = None):
        self.deck = list(deck) if deck is not None else new_deck()
        self.current = self._draw()
        self.score = 0
        self.finished = False
        self.last_result = "Will the next card be higher or lower?"

    def _draw(self) -> Card:
        if not self.deck:
            raise RuntimeError("The deck is empty.")
        return self.deck.pop()

    def guess(self, higher: bool) -> Card:
        if self.finished:
            return self.current
        next_card = self._draw()
        if next_card.high_value == self.current.high_value:
            self.last_result = f"{next_card.label} ties. Keep going!"
            self.current = next_card
            return next_card

        correct = (
            next_card.high_value > self.current.high_value
            if higher
            else next_card.high_value < self.current.high_value
        )
        if correct:
            self.score += 1
            self.last_result = f"Correct — it was {next_card.label}."
            self.current = next_card
            if self.score >= 10:
                self.finished = True
                self.last_result = "Ten correct guesses — you win!"
        else:
            direction = "higher" if higher else "lower"
            self.finished = True
            self.last_result = (
                f"{next_card.label} was not {direction} than {self.current.label}. Game over."
            )
        return next_card
