from datetime import date
from uuid import uuid4

from upm_shared.rotating_slides import RotationCandidate, effective_rotation


def candidate(scope, *, event, day, room=None, session=None, authority="central"):
    return RotationCandidate(uuid4(), event, day, scope, uuid4(), room, session, authority)


def test_rotation_inheritance_and_clearing_falls_back():
    event, room, session, day = uuid4(), uuid4(), uuid4(), date(2027, 4, 10)
    global_deck = candidate("event_day", event=event, day=day)
    room_deck = candidate("room_day", event=event, day=day, room=room)
    session_deck = candidate("session", event=event, day=day, session=session)
    values = [global_deck, room_deck, session_deck]

    assert (
        effective_rotation(values, event_id=event, event_day=day, room_id=room, session_id=session)
        == session_deck
    )
    assert (
        effective_rotation(
            values[:-1], event_id=event, event_day=day, room_id=room, session_id=session
        )
        == room_deck
    )
    assert (
        effective_rotation(
            values[:1], event_id=event, event_day=day, room_id=room, session_id=session
        )
        == global_deck
    )


def test_site_override_wins_same_scope_without_affecting_other_rooms():
    event, room, other, day = uuid4(), uuid4(), uuid4(), date(2027, 4, 10)
    central = candidate("room_day", event=event, day=day, room=room)
    local = candidate("room_day", event=event, day=day, room=room, authority="site")

    assert (
        effective_rotation(
            [central, local], event_id=event, event_day=day, room_id=room, session_id=None
        )
        == local
    )
    assert (
        effective_rotation(
            [central, local], event_id=event, event_day=day, room_id=other, session_id=None
        )
        is None
    )


def test_rotations_are_not_presentation_entries_or_readiness_inputs():
    event, day = uuid4(), date(2027, 4, 10)
    presentations = [uuid4(), uuid4(), uuid4()]
    rotation = candidate("event_day", event=event, day=day)

    expected = len(presentations)
    ready = len(presentations[:2])

    assert expected == 3
    assert ready == 2
    assert rotation.presentation_version_id not in presentations
