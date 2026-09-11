from datetime import UTC, datetime

from upm_signage.scheduler import effective_override, room_door


def test_offline_scheduler_advances_and_ignores_cancelled_sessions():
    rows = [
        {
            "session_id": "one",
            "room_id": "room",
            "title": "Opening",
            "starts_at": "2026-09-10T10:00:00+00:00",
            "ends_at": "2026-09-10T11:00:00+00:00",
            "cancelled": False,
        },
        {
            "session_id": "cancelled",
            "room_id": "room",
            "title": "Cancelled",
            "starts_at": "2026-09-10T11:00:00+00:00",
            "ends_at": "2026-09-10T12:00:00+00:00",
            "cancelled": True,
        },
        {
            "session_id": "two",
            "room_id": "room",
            "title": "Closing",
            "starts_at": "2026-09-10T12:00:00+00:00",
            "ends_at": "2026-09-10T13:00:00+00:00",
            "cancelled": False,
        },
    ]
    first = room_door(rows, datetime(2026, 9, 10, 10, 30, tzinfo=UTC), "room")
    later = room_door(rows, datetime(2026, 9, 10, 12, 30, tzinfo=UTC), "room")
    assert first["current"]["session_id"] == "one" and first["next"]["session_id"] == "two"
    assert later["current"]["session_id"] == "two"


def test_override_expiry_is_deterministic():
    rows = [
        {
            "override_id": "a",
            "starts_at": "2026-09-10T10:00:00+00:00",
            "expires_at": "2026-09-10T11:00:00+00:00",
        }
    ]
    assert effective_override(rows, datetime(2026, 9, 10, 10, 59, tzinfo=UTC))["override_id"] == "a"
    assert effective_override(rows, datetime(2026, 9, 10, 11, 0, tzinfo=UTC)) is None
