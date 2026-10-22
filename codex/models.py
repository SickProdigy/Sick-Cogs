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


def validate_alert_levels(values: Iterable[int]) -> Tuple[int, ...]:
    levels = tuple(sorted({validate_percent(value) for value in values}))
    if not levels:
        raise ValueError("Provide at least one alert percentage.")
    if len(levels) > 10:
        raise ValueError("Configure no more than 10 alert percentages.")
    if levels[0] == 0:
        raise ValueError("Alert percentages must be from 1 through 100.")
    return levels


def due_alerts(
    payload: Dict, levels: Iterable[int], sent_keys: Iterable[str]
) -> Tuple[Tuple[LimitWindow, int], ...]:
    sent = set(sent_keys)
    return tuple(
        (window, level)
        for window in iter_limit_windows(payload)
        for level in validate_alert_levels(levels)
        if window.used_percent >= level
        and alert_key(window, level) not in sent
    )


def alert_key(window: LimitWindow, level: int) -> str:
    return (
        f"{window.limit_id}:{window.window_name}:"
        f"{window.resets_at or 0}:{level}"
    )


def available_resets(payload):
    summary = payload.get("rateLimitResetCredits")
    value = summary.get("availableCount") if isinstance(summary, dict) else None
    return value if type(value) is int and value >= 0 else None


def reset_observations(payload, previous, sent, now):
    """Read-only observations; never infer a Monday schedule or consume a credit."""
    count = available_resets(payload)
    prior_count = previous.get("available_resets")
    messages, suggestion_keys = [], []
    if count is not None and type(prior_count) is int and count > prior_count:
        messages.append(f"Banked Codex resets increased from **{prior_count}** to **{count}**.")
    windows = dict(previous.get("windows", {}))
    for window in list(iter_limit_windows(payload))[:10]:
        key = f"{window.limit_id}:{window.window_name}"
        old = windows.get(key)
        if old and window.used_percent < old["used_percent"]:
            reset = f" Next reported reset: <t:{window.resets_at}:F> (<t:{window.resets_at}:R>)." if window.resets_at else ""
            messages.append(
                f"Observed allowance increase for **{window.limit_name} {window.window_name}**: "
                f"{100-old['used_percent']}% → **{window.remaining_percent}% remaining**.{reset} "
                "This is an observation between checks, not confirmation of a scheduled or banked reset."
            )
        windows[key] = {"used_percent": window.used_percent, "resets_at": window.resets_at}
        suggestion = f"reset-suggestion:{key}:{window.resets_at}"
        if (window.limit_id == "codex" and window.duration_minutes == 10080
                and window.remaining_percent <= 5 and count is not None and count > 0
                and window.resets_at and window.resets_at-now > 4*86400 and suggestion not in sent):
            messages.append(
                f"Your weekly Codex allowance has **{window.remaining_percent}% remaining**, "
                f"with its next reported reset <t:{window.resets_at}:F> (<t:{window.resets_at}:R>). "
                f"You have **{count} banked reset(s)**; consider using one in Codex if you need to continue. "
                "This bot will not use it for you."
            )
            suggestion_keys.append(suggestion)
    snapshot = {"windows": dict(list(windows.items())[-20:]),
                "available_resets": count if count is not None else prior_count}
    return messages, snapshot, suggestion_keys
