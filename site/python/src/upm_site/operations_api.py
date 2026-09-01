"""HTTP boundary for Site-local room, media, device, and dashboard operations."""

from collections.abc import Callable, Iterator
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from upm_shared.enums import DeviceRole
from upm_site.persistence.models import (
    Device,
    DeviceAssignment,
    Event,
    LocalSiteIdentity,
    ProgramRoomMapping,
    Room,
    utc_now,
)
from upm_site.persistence.models import Session as ProgramSession
from upm_site.recovery_snapshots import touch_site_recovery_snapshot
from upm_site.room_operations import (
    list_devices,
    list_media,
    normalize_program_location,
    operational_dashboard,
    program_locations,
    reconcile_program_room_assignments,
    room_detail,
    room_summaries,
)


class RoomCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: Annotated[str, Field(min_length=1, max_length=255)]
    event_id: UUID | None = None


class RoomUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: Annotated[str | None, Field(min_length=1, max_length=255)] = None
    enabled: bool | None = None
    archived: bool | None = None
    revision: Annotated[int | None, Field(ge=1)] = None


class ProgramRoomMappingWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    imported_label: Annotated[str, Field(min_length=1, max_length=255)]
    room_id: UUID | None


class DeviceAssignmentWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    device_id: UUID | None


def register_operations_routes(
    app: FastAPI,
    read_db: Callable[[], Iterator[Session]],
    transaction_db: Callable[[], Iterator[Session]],
) -> None:
    ReadSession = Annotated[Session, Depends(read_db)]
    WriteSession = Annotated[Session, Depends(transaction_db)]

    def required_identity(session: Session) -> LocalSiteIdentity:
        identity = session.scalar(
            select(LocalSiteIdentity).where(LocalSiteIdentity.singleton_key == 1)
        )
        if identity is None:
            raise HTTPException(status.HTTP_409_CONFLICT, detail="Site identity is not configured")
        return identity

    def operational_event_id(session: Session, room: Room) -> UUID:
        event_ids = {room.event_id} if room.event_id else set()
        event_ids.update(
            session.scalars(
                select(ProgramRoomMapping.event_id).where(
                    ProgramRoomMapping.room_id == room.room_id
                )
            ).all()
        )
        if len(event_ids) != 1:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail="room must map to exactly one operational event before Agent assignment",
            )
        return next(iter(event_ids))

    def clear_device_assignment_state(session: Session, device_id: UUID) -> None:
        if session.scalar(
            select(DeviceAssignment.device_assignment_id).where(
                DeviceAssignment.device_id == device_id,
                DeviceAssignment.active.is_(True),
            )
        ) is not None:
            return
        device = session.get(Device, device_id)
        if device is not None:
            device.event_id = None
            device.enrollment_state = "unassigned"
            device.revision += 1

    def required_room(session: Session, room_id: UUID) -> Room:
        room = session.get(Room, room_id)
        if room is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="room not found")
        return room

    @app.get("/api/v1/rooms", tags=["rooms"])
    def rooms(session: ReadSession) -> list[dict[str, object]]:
        return room_summaries(session)

    @app.post("/api/v1/rooms", status_code=status.HTTP_201_CREATED, tags=["rooms"])
    def create_room(payload: RoomCreate, session: WriteSession) -> dict[str, object]:
        identity = required_identity(session)
        label = payload.label.strip()
        if payload.event_id is not None:
            event = session.get(Event, payload.event_id)
            if event is None or event.site_id != identity.site_id:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="invalid event")
        duplicate = session.scalar(
            select(Room).where(Room.site_id == identity.site_id, Room.label == label)
        )
        if duplicate:
            raise HTTPException(status.HTTP_409_CONFLICT, detail="room label already exists")
        room = Room(site_id=identity.site_id, label=label, event_id=payload.event_id)
        session.add(room)
        session.flush()
        if payload.event_id is not None:
            touch_site_recovery_snapshot(session, event)
        detail = room_detail(session, room.room_id)
        assert detail is not None
        return detail

    @app.get("/api/v1/rooms/{room_id}", tags=["rooms"])
    def get_room(room_id: UUID, session: ReadSession) -> dict[str, object]:
        detail = room_detail(session, room_id)
        if detail is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="room not found")
        return detail

    @app.patch("/api/v1/rooms/{room_id}", tags=["rooms"])
    def update_room(room_id: UUID, payload: RoomUpdate, session: WriteSession) -> dict[str, object]:
        room = required_room(session, room_id)
        if payload.revision is not None and payload.revision != room.revision:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail="room changed since it was loaded; refresh and retry",
            )
        changed = False
        if payload.label is not None:
            label = payload.label.strip()
            duplicate = session.scalar(
                select(Room).where(
                    Room.site_id == room.site_id,
                    Room.label == label,
                    Room.room_id != room.room_id,
                )
            )
            if duplicate:
                raise HTTPException(status.HTTP_409_CONFLICT, detail="room label already exists")
            if label != room.label:
                room.label = label
                changed = True
        if payload.enabled is not None and payload.enabled != room.enabled:
            room.enabled = payload.enabled
            changed = True
        if payload.archived is not None:
            archived = room.archived_at is not None
            if payload.archived != archived:
                room.archived_at = utc_now() if payload.archived else None
                room.enabled = not payload.archived
                changed = True
                if payload.archived:
                    active_assignments = session.scalars(
                        select(DeviceAssignment).where(
                            DeviceAssignment.room_id == room_id,
                            DeviceAssignment.active.is_(True),
                        )
                    ).all()
                    for assignment in active_assignments:
                        assignment.active = False
                        assignment.ends_at = utc_now()
                        assignment.revision += 1
        if changed:
            room.revision += 1
            session.flush()
            if room.event_id is not None:
                touch_site_recovery_snapshot(session, session.get(Event, room.event_id))
        detail = room_detail(session, room_id)
        assert detail is not None
        return detail

    @app.get("/api/v1/events/{event_id}/program-room-locations", tags=["rooms", "program"])
    def locations(event_id: UUID, session: ReadSession) -> list[dict[str, object]]:
        try:
            return program_locations(session, event_id)
        except ValueError as error:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(error)) from error

    @app.put("/api/v1/events/{event_id}/program-room-mappings", tags=["rooms", "program"])
    def save_mapping(
        event_id: UUID, payload: ProgramRoomMappingWrite, session: WriteSession
    ) -> dict[str, object]:
        identity = required_identity(session)
        event = session.get(Event, event_id)
        if event is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="event not found")
        if event.site_id != identity.site_id:
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="event belongs to another Site")
        normalized = normalize_program_location(payload.imported_label)
        matching_session = next(
            (
                item
                for item in session.scalars(
                    select(ProgramSession).where(
                        ProgramSession.event_id == event_id,
                        ProgramSession.active.is_(True),
                    )
                )
                if item.location_name
                and normalize_program_location(item.location_name) == normalized
            ),
            None,
        )
        if matching_session is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="imported location is not present in the deployed Site program",
            )
        room = None
        if payload.room_id is not None:
            room = session.get(Room, payload.room_id)
            if (
                room is None
                or room.site_id != identity.site_id
                or not room.enabled
                or room.archived_at is not None
            ):
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail="mapping requires an enabled, non-archived room at this Site",
                )
        mapping = session.scalar(
            select(ProgramRoomMapping)
            .where(
                ProgramRoomMapping.event_id == event_id,
                ProgramRoomMapping.normalized_imported_label == normalized,
            )
            .with_for_update()
        )
        if mapping is None:
            mapping = ProgramRoomMapping(
                site_id=identity.site_id,
                event_id=event_id,
                imported_label=matching_session.location_name,
                normalized_imported_label=normalized,
                room_id=room.room_id if room else None,
                confirmed_by="site-operator",
            )
            session.add(mapping)
        else:
            mapping.imported_label = matching_session.location_name
            mapping.room_id = room.room_id if room else None
            mapping.confirmed_by = "site-operator"
            mapping.revision += 1
        session.flush()
        counts = reconcile_program_room_assignments(session, event_id)
        session.flush()
        touch_site_recovery_snapshot(session, event)
        saved = next(
            item
            for item in program_locations(session, event_id)
            if item["normalized_imported_label"] == normalized
        )
        return {**saved, "reconciliation": counts}

    @app.get("/api/v1/devices", tags=["devices"])
    def devices(session: ReadSession) -> list[dict[str, object]]:
        return list_devices(session)

    @app.put("/api/v1/rooms/{room_id}/device-assignments/{role}", tags=["rooms", "devices"])
    def assign_device(
        room_id: UUID,
        role: DeviceRole,
        payload: DeviceAssignmentWrite,
        session: WriteSession,
    ) -> dict[str, object]:
        if role not in {DeviceRole.PRIMARY, DeviceRole.BACKUP}:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="room endpoint role must be primary or backup",
            )
        room = required_room(session, room_id)
        if not room.enabled or room.archived_at is not None:
            raise HTTPException(
                status.HTTP_409_CONFLICT, detail="cannot assign an endpoint to a disabled room"
            )
        event_id = operational_event_id(session, room)
        current = session.scalar(
            select(DeviceAssignment)
            .where(
                DeviceAssignment.room_id == room_id,
                DeviceAssignment.role == role,
                DeviceAssignment.active.is_(True),
            )
            .with_for_update()
        )
        if payload.device_id is None:
            if current is not None:
                current.active = False
                current.ends_at = utc_now()
                current.revision += 1
                session.flush()
                clear_device_assignment_state(session, current.device_id)
                session.flush()
            detail = room_detail(session, room_id)
            assert detail is not None
            return detail
        device = session.get(Device, payload.device_id)
        if (
            device is None
            or device.site_id != room.site_id
            or device.enrolled_at is None
            or device.revoked_at is not None
        ):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="device must be enrolled, active, and owned by this Site",
            )
        existing_assignment = session.scalar(
            select(DeviceAssignment)
            .where(
                DeviceAssignment.device_id == device.device_id,
                DeviceAssignment.active.is_(True),
            )
            .with_for_update()
        )
        if current is not None and current.device_id == device.device_id:
            if device.event_id != event_id or device.enrollment_state != "assigned":
                device.event_id = event_id
                device.enrollment_state = "assigned"
                device.revision += 1
            detail = room_detail(session, room_id)
            assert detail is not None
            return detail
        if existing_assignment is not None:
            existing_assignment.active = False
            existing_assignment.ends_at = utc_now()
            existing_assignment.revision += 1
        if current is not None:
            current.active = False
            current.ends_at = utc_now()
            current.revision += 1
            session.flush()
            clear_device_assignment_state(session, current.device_id)
        session.add(
            DeviceAssignment(
                device_id=device.device_id,
                room_id=room_id,
                role=role,
                starts_at=utc_now(),
                active=True,
            )
        )
        device.event_id = event_id
        device.enrollment_state = "assigned"
        device.revision += 1
        session.flush()
        detail = room_detail(session, room_id)
        assert detail is not None
        return detail

    @app.get("/api/v1/media", tags=["media", "storage"])
    def media(session: ReadSession) -> list[dict[str, object]]:
        return list_media(session)

    @app.get("/api/v1/operations/dashboard", tags=["operations"])
    def dashboard(session: ReadSession) -> dict[str, object]:
        return operational_dashboard(session)
