"""Small storage helpers for Tickets."""

import time

OPEN_STATUSES = {"open", "waiting_member", "waiting_staff"}
ALL_STATUSES = OPEN_STATUSES | {"closed", "deleted"}


def new_ticket_record(
    number,
    channel_id,
    owner_id,
    control_message_id=0,
    mode="text",
    claims_enabled=False,
    statuses_enabled=False,
    close_behavior="delete",
):
    now = int(time.time())
    return {
        "number": int(number),
        "channel_id": int(channel_id),
        "owner_id": int(owner_id),
        "mode": mode if mode in {"text", "voice", "thread"} else "text",
        "claims_enabled": bool(claims_enabled),
        "statuses_enabled": bool(statuses_enabled),
        "close_behavior": close_behavior if close_behavior in {"delete", "review"} else "delete",
        "status": "open",
        "claimed_by_id": 0,
        "created_at": now,
        "updated_at": now,
        "closed_at": 0,
        "closed_by_id": 0,
        "control_message_id": int(control_message_id),
    }


def is_open(record):
    return record.get("status", "open") in OPEN_STATUSES


def ticket_channel_name(number):
    return f"ticket-{int(number):04d}"


def safe_display(value, limit=100):
    return str(value).replace("@", "@\u200b")[:limit]


def normalized_record(value):
    if not isinstance(value, dict):
        return None
    try:
        status = str(value.get("status", "open"))
        if status not in ALL_STATUSES:
            status = "open"
        return {
            "number": int(value["number"]),
            "channel_id": int(value["channel_id"]),
            "owner_id": int(value.get("owner_id", 0) or 0),
            "mode": value.get("mode") if value.get("mode") in {"text", "voice", "thread"} else "text",
            "claims_enabled": bool(value.get("claims_enabled", True)),
            "statuses_enabled": bool(value.get("statuses_enabled", True)),
            "close_behavior": value.get("close_behavior") if value.get("close_behavior") in {"delete", "review"} else "review",
            "status": status,
            "claimed_by_id": int(value.get("claimed_by_id", 0) or 0),
            "created_at": int(value.get("created_at", 0) or 0),
            "updated_at": int(value.get("updated_at", 0) or 0),
            "closed_at": int(value.get("closed_at", 0) or 0),
            "closed_by_id": int(value.get("closed_by_id", 0) or 0),
            "control_message_id": int(value.get("control_message_id", 0) or 0),
        }
    except (KeyError, TypeError, ValueError):
        return None
