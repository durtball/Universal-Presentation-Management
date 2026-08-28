"""Authority-aware effective rotating-slide resolution shared by Central and Site."""

from dataclasses import dataclass
from datetime import date
from uuid import UUID


@dataclass(frozen=True)
class RotationCandidate:
    assignment_id: UUID
    event_id: UUID
    event_day: date
    scope: str
    presentation_version_id: UUID | None
    room_id: UUID | None = None
    session_id: UUID | None = None
    source_authority: str = "central"
    active: bool = True


def effective_rotation(
    assignments: list[RotationCandidate],
    *,
    event_id: UUID,
    event_day: date,
    room_id: UUID | None,
    session_id: UUID | None,
) -> RotationCandidate | None:
    """Resolve Site overrides before Central defaults and Session before room/global scope."""
    applicable = [
        item
        for item in assignments
        if item.active
        and item.event_id == event_id
        and item.event_day == event_day
        and (
            item.scope == "event_day"
            or item.scope == "room_day"
            and item.room_id == room_id
            or item.scope == "session"
            and item.session_id == session_id
        )
    ]
    rank = {"event_day": 1, "room_day": 2, "session": 3}
    authority = {"central": 0, "site": 1}
    return max(
        applicable,
        key=lambda item: (rank[item.scope], authority.get(item.source_authority, -1)),
        default=None,
    )
