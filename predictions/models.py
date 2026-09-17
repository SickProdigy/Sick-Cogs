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

    @property
    def is_resolved(self) -> bool:
        return self.resolved_outcome is not None

    def is_open(self, now: Optional[datetime] = None) -> bool:
        now = now or datetime.now(timezone.utc)
        return not self.is_resolved and now < self.closes_at

    def vote_counts(self) -> List[int]:
        return [sum(choice == index for choice in self.votes.values()) for index in range(len(self.outcomes))]

    def to_raw(self) -> dict:
        return {
            "market_id": self.market_id, "guild_id": self.guild_id, "creator_id": self.creator_id,
            "question": self.question, "outcomes": self.outcomes, "closes_at": self.closes_at.isoformat(),
            "created_at": self.created_at.isoformat(), "votes": self.votes,
            "resolved_outcome": self.resolved_outcome,
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
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Prediction market data is invalid.") from exc
