"""Central-managed rotating-slide defaults deployed with the event snapshot."""

from collections.abc import Callable, Iterator
from datetime import date
from typing import Annotated, Literal
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from upm_central.persistence.models import (
    Event,
    Presentation,
    PresentationVersion,
    RotationAssignment,
)
from upm_central.program import audit, touch_event_program


class RotationWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_day: date
    scope: Literal["event_day", "room_day", "session"]
    room_id: UUID | None = None
    session_id: UUID | None = None
    presentation_version_id: UUID | None = None


def register_rotation_routes(
    app: FastAPI, db: Callable[[], Iterator[Session]], require_admin
) -> None:
    Db = Annotated[Session, Depends(db)]
    admin = [Depends(require_admin)]

    @app.get(
        "/api/v1/admin/events/{event_id}/rotating-slides",
        dependencies=admin,
        tags=["rotating-slides"],
    )
    def list_rotations(event_id: UUID, session: Db):
        return [
            _view(item)
            for item in session.scalars(
                select(RotationAssignment)
                .where(RotationAssignment.event_id == event_id, RotationAssignment.active.is_(True))
                .order_by(RotationAssignment.event_day, RotationAssignment.scope)
            )
        ]

    @app.post(
        "/api/v1/admin/events/{event_id}/rotating-slides",
        status_code=201,
        dependencies=admin,
        tags=["rotating-slides"],
    )
    def set_rotation(event_id: UUID, payload: RotationWrite, request: Request, session: Db):
        event = session.get(Event, event_id)
        if event is None:
            raise HTTPException(404, "event not found")
        _validate(payload)
        if payload.presentation_version_id:
            version = session.get(PresentationVersion, payload.presentation_version_id)
            presentation = session.get(Presentation, version.presentation_id) if version else None
            if presentation is None or presentation.event_id != event_id:
                raise HTTPException(422, "rotation media version is not in the event")
        for prior in session.scalars(
            select(RotationAssignment).where(
                RotationAssignment.event_id == event_id,
                RotationAssignment.event_day == payload.event_day,
                RotationAssignment.scope == payload.scope,
                RotationAssignment.room_id.is_(payload.room_id)
                if payload.room_id is None
                else RotationAssignment.room_id == payload.room_id,
                RotationAssignment.session_id.is_(payload.session_id)
                if payload.session_id is None
                else RotationAssignment.session_id == payload.session_id,
                RotationAssignment.active.is_(True),
            )
        ):
            prior.active = False
        item = RotationAssignment(
            event_id=event_id, source_authority="central", active=True, **payload.model_dump()
        )
        session.add(item)
        session.flush()
        touch_event_program(session, event)
        audit(
            session,
            action="central.rotation_assignment.configured",
            target_type="rotation_assignment",
            target_id=item.rotation_assignment_id,
            event_id=event_id,
            after=payload.model_dump(mode="json"),
            actor=getattr(request.state, "admin_actor", "central-admin"),
        )
        return _view(item)

    @app.delete(
        "/api/v1/admin/rotating-slides/{assignment_id}",
        dependencies=admin,
        tags=["rotating-slides"],
    )
    def clear_rotation(assignment_id: UUID, request: Request, session: Db):
        item = session.get(RotationAssignment, assignment_id)
        if item is None:
            raise HTTPException(404, "rotation assignment not found")
        item.active = False
        item.revision += 1
        event = session.get(Event, item.event_id)
        touch_event_program(session, event)
        audit(
            session,
            action="central.rotation_assignment.cleared",
            target_type="rotation_assignment",
            target_id=item.rotation_assignment_id,
            event_id=item.event_id,
            actor=getattr(request.state, "admin_actor", "central-admin"),
        )
        return {"rotation_assignment_id": assignment_id, "active": False}


def _validate(payload: RotationWrite):
    valid = (
        payload.scope == "event_day"
        and payload.room_id is None
        and payload.session_id is None
        or payload.scope == "room_day"
        and payload.room_id is not None
        and payload.session_id is None
        or payload.scope == "session"
        and payload.room_id is None
        and payload.session_id is not None
    )
    if not valid:
        raise HTTPException(422, "invalid rotating-slide scope")


def _view(item):
    return {
        name: getattr(item, name)
        for name in (
            "rotation_assignment_id",
            "event_id",
            "event_day",
            "scope",
            "room_id",
            "session_id",
            "presentation_version_id",
            "source_authority",
            "active",
            "revision",
        )
    }
