"""Durable server-local prediction market records."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional


@dataclass
class PredictionMarket:
    market_id: int
    guild_id: int
    creator_id: int
    question: str
    outcomes: List[str]
    closes_at: datetime
    created_at: datetime
    votes: Dict[str, int] = field(default_factory=dict)
    resolved_outcome: Optional[int] = None
    stake_mode: str = "none"
    stake_min: int = 0
    stake_max: int = 0
    house_cut_bps: int = 0
    treasury_user_id: Optional[int] = None
    entries: Dict[str, dict] = field(default_factory=dict)
    state: str = "open"
    settlement: dict = field(default_factory=dict)
    review: dict = field(default_factory=dict)
    audit: List[dict] = field(default_factory=list)

    def __post_init__(self):
        if self.market_id < 1 or self.guild_id < 1 or self.creator_id < 1:
            raise ValueError("Prediction market identity is invalid.")
        if not self.question.strip() or not 2 <= len(self.outcomes) <= 5:
            raise ValueError("A market needs a question and between two and five outcomes.")
        if len({outcome.casefold() for outcome in self.outcomes}) != len(self.outcomes):
            raise ValueError("Prediction outcomes must be distinct.")
        if self.closes_at.tzinfo is None or self.created_at.tzinfo is None or self.closes_at <= self.created_at:
            raise ValueError("Prediction market dates must be ordered and timezone-aware.")
        if self.resolved_outcome is not None and not 0 <= self.resolved_outcome < len(self.outcomes):
            raise ValueError("Resolved outcome is invalid.")
        if self.stake_mode not in {"none", "fixed", "range"}:
            raise ValueError("Prediction stake mode is invalid.")
        if self.stake_mode == "none":
            self.stake_min = self.stake_max = 0
        elif self.stake_min < 1 or self.stake_max < self.stake_min:
            raise ValueError("Prediction stake limits are invalid.")
        if not 0 <= self.house_cut_bps <= 2500:
            raise ValueError("Prediction house cut must be between zero and 25 percent.")
        if self.house_cut_bps and not self.treasury_user_id:
            raise ValueError("A treasury member is required for a house cut.")
        if self.state not in {"open", "pending_review", "resolved", "cancelled", "frozen"}:
            raise ValueError("Prediction state is invalid.")

    @property
    def is_resolved(self) -> bool:
        return self.resolved_outcome is not None

    def is_open(self, now: Optional[datetime] = None) -> bool:
        now = now or datetime.now(timezone.utc)
        return self.state == "open" and not self.is_resolved and now < self.closes_at

    @property
    def uses_bank(self) -> bool:
        return self.stake_mode != "none"

    def vote_counts(self) -> List[int]:
        return [sum(choice == index for choice in self.votes.values()) for index in range(len(self.outcomes))]

    def funded_entries(self) -> Dict[str, dict]:
        return {
            user_id: entry for user_id, entry in self.entries.items()
            if entry.get("state") == "funded" and int(entry.get("stake", 0)) > 0
        }

    def pool_totals(self) -> List[int]:
        funded = self.funded_entries().values()
        return [
            sum(int(entry["stake"]) for entry in funded if int(entry.get("choice", -1)) == index)
            for index in range(len(self.outcomes))
        ]

    def estimated_return(self, user_id: int, choice: int, amount: int) -> int:
        """Estimate gross payout using the pool after this member proposal."""
        entries = {
            entry_user: dict(entry) for entry_user, entry in self.funded_entries().items()
            if entry_user != str(user_id)
        }
        entries[str(user_id)] = {"choice": choice, "stake": amount, "state": "funded"}
        payouts, _, _ = calculate_payouts(entries, choice, self.house_cut_bps)
        return int(payouts.get(str(user_id), amount))

    def to_raw(self) -> dict:
        return {
            "market_id": self.market_id, "guild_id": self.guild_id, "creator_id": self.creator_id,
            "question": self.question, "outcomes": self.outcomes, "closes_at": self.closes_at.isoformat(),
            "created_at": self.created_at.isoformat(), "votes": self.votes,
            "resolved_outcome": self.resolved_outcome, "stake_mode": self.stake_mode,
            "stake_min": self.stake_min, "stake_max": self.stake_max,
            "house_cut_bps": self.house_cut_bps, "treasury_user_id": self.treasury_user_id,
            "entries": self.entries, "state": self.state, "settlement": self.settlement,
            "review": self.review, "audit": self.audit[-100:],
        }

    @classmethod
    def from_raw(cls, raw: object) -> "PredictionMarket":
        if not isinstance(raw, dict):
            raise ValueError("Prediction market data must be an object.")
        try:
            outcomes = raw["outcomes"]
            votes = raw.get("votes", {})
            if not isinstance(outcomes, list) or not isinstance(votes, dict):
                raise ValueError
            return cls(
                int(raw["market_id"]), int(raw["guild_id"]), int(raw["creator_id"]), str(raw["question"]),
                [str(value) for value in outcomes], datetime.fromisoformat(str(raw["closes_at"])),
                datetime.fromisoformat(str(raw["created_at"])), {str(key): int(value) for key, value in votes.items()},
                None if raw.get("resolved_outcome") is None else int(raw["resolved_outcome"]),
                str(raw.get("stake_mode", "none")), int(raw.get("stake_min", 0)),
                int(raw.get("stake_max", 0)), int(raw.get("house_cut_bps", 0)),
                None if raw.get("treasury_user_id") is None else int(raw["treasury_user_id"]),
                {str(key): dict(value) for key, value in raw.get("entries", {}).items()},
                str(raw.get("state", "resolved" if raw.get("resolved_outcome") is not None else "open")),
                dict(raw.get("settlement", {})), dict(raw.get("review", {})),
                list(raw.get("audit", []))[-100:],
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Prediction market data is invalid.") from exc


def calculate_payouts(entries: Dict[str, dict], winning_choice: Optional[int], house_cut_bps: int = 0):
    """Return exact integer payouts, treasury cut, and whether the pool was refunded."""
    funded = {
        str(user_id): entry for user_id, entry in entries.items()
        if entry.get("state") == "funded" and int(entry.get("stake", 0)) > 0
    }
    stakes = {user_id: int(entry["stake"]) for user_id, entry in funded.items()}
    if winning_choice is None:
        return stakes, 0, True
    winners = {
        user_id: stake for user_id, stake in stakes.items()
        if int(funded[user_id].get("choice", -1)) == winning_choice
    }
    if not winners:
        return stakes, 0, True
    loser_pool = sum(stakes.values()) - sum(winners.values())
    cut = loser_pool * max(0, min(2500, int(house_cut_bps))) // 10000
    distributable = loser_pool - cut
    winning_stakes = sum(winners.values())
    payouts = {
        user_id: stake + (distributable * stake // winning_stakes)
        for user_id, stake in winners.items()
    }
    remainder = sum(stakes.values()) - cut - sum(payouts.values())
    for user_id in sorted(payouts, key=int)[:remainder]:
        payouts[user_id] += 1
    return payouts, cut, False
