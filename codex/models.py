from dataclasses import dataclass
from typing import Iterable, List, Tuple


@dataclass(frozen=True)
class UsageSnapshot:
    remaining_percent: int
    updated_at: int
    source: str = "manual"


def validate_percent(value: int) -> int:
    value = int(value)
    if not 0 <= value <= 100:
        raise ValueError("Remaining allowance must be from 0 through 100 percent.")
    return value


def normalize_offsets(hours: Iterable[int]) -> List[int]:
    values = sorted({int(hour) * 3600 for hour in hours}, reverse=True)
    if not values or any(value < 3600 or value > 30 * 86400 for value in values):
        raise ValueError("Reminder offsets must be from 1 hour through 30 days.")
    return values


def due_notifications(
    now: int, reset_at: int, offsets: Iterable[int], sent: Iterable[str]
) -> List[Tuple[str, int]]:
    sent = set(sent)
    due = []
    for offset in sorted({int(value) for value in offsets}, reverse=True):
        key = f"{reset_at}:{offset}"
        if key not in sent and reset_at - offset <= now < reset_at:
            due.append((key, offset))
    return due


def roll_cycle(now: int, reset_at: int, cycle_seconds: int):
    if cycle_seconds <= 0:
        raise ValueError("Cycle length must be positive.")
    rolls = 0
    while reset_at <= now:
        reset_at += cycle_seconds
        rolls += 1
    return reset_at, rolls
