from dataclasses import dataclass
from typing import Dict, Iterable, Iterator, Tuple


@dataclass(frozen=True)
class LimitWindow:
    limit_id: str
    limit_name: str
    window_name: str
    used_percent: int
    resets_at: int | None
    duration_minutes: int | None

    @property
    def remaining_percent(self):
        return max(0, min(100, 100 - self.used_percent))

    @property
    def alert_key(self):
        return (
            f"{self.limit_id}:{self.window_name}:"
            f"{self.resets_at or 0}:{self.used_percent}"
        )


def validate_percent(value: int) -> int:
    value = int(value)
    if not 0 <= value <= 100:
        raise ValueError("The threshold must be from 0 through 100 percent.")
    return value


def limit_snapshots(payload: Dict) -> Dict[str, Dict]:
    snapshots = payload.get("rateLimitsByLimitId") or {}
    if snapshots:
        return {
            str(key): value
            for key, value in snapshots.items()
            if isinstance(value, dict)
        }
    legacy = payload.get("rateLimits")
    if isinstance(legacy, dict):
        key = str(legacy.get("limitId") or "codex")
        return {key: legacy}
    return {}


def iter_limit_windows(payload: Dict) -> Iterator[LimitWindow]:
    for limit_id, snapshot in limit_snapshots(payload).items():
        label = str(snapshot.get("limitName") or limit_id)
        for window_name in ("primary", "secondary"):
            window = snapshot.get(window_name)
            if not isinstance(window, dict):
                continue
            used = window.get("usedPercent")
            if not isinstance(used, (int, float)):
                continue
            reset = window.get("resetsAt")
            duration = window.get("windowDurationMins")
            yield LimitWindow(
                limit_id=limit_id,
                limit_name=label,
                window_name=window_name,
                used_percent=max(0, min(100, int(used))),
                resets_at=int(reset) if isinstance(reset, (int, float)) else None,
                duration_minutes=(
                    int(duration)
                    if isinstance(duration, (int, float))
                    else None
                ),
            )


def due_low_alerts(
    payload: Dict, threshold: int, sent_keys: Iterable[str]
) -> Tuple[LimitWindow, ...]:
    sent = set(sent_keys)
    return tuple(
        window
        for window in iter_limit_windows(payload)
        if window.remaining_percent <= threshold
        and (
            f"{window.limit_id}:{window.window_name}:"
            f"{window.resets_at or 0}:{threshold}"
        )
        not in sent
    )


def alert_key(window: LimitWindow, threshold: int) -> str:
    return (
        f"{window.limit_id}:{window.window_name}:"
        f"{window.resets_at or 0}:{threshold}"
    )
