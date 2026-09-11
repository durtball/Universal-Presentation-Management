from datetime import datetime


def room_door(snapshot: list[dict], now: datetime, room_id: str) -> dict:
    rows = [
        x
        for x in snapshot
        if not x.get("cancelled")
        and x.get("room_id") == room_id
        and x.get("starts_at")
        and x.get("ends_at")
    ]
    rows.sort(key=lambda x: (x["starts_at"], x["session_id"]))
    current = next(
        (
            x
            for x in rows
            if datetime.fromisoformat(x["starts_at"]) <= now < datetime.fromisoformat(x["ends_at"])
        ),
        None,
    )
    future = [x for x in rows if datetime.fromisoformat(x["starts_at"]) > now]
    return {"current": current, "next": future[0] if future else None}


def effective_override(rows: list[dict], now: datetime) -> dict | None:
    active = [
        x
        for x in rows
        if datetime.fromisoformat(x["starts_at"]) <= now < datetime.fromisoformat(x["expires_at"])
    ]
    return (
        sorted(active, key=lambda x: (x["starts_at"], x["override_id"]), reverse=True)[0]
        if active
        else None
    )
