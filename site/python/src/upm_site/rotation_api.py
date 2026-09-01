"""Site-local rotating-slide overrides and effective inheritance projection."""

from collections.abc import Callable, Iterator
from datetime import date
from typing import Annotated, Literal
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from upm_shared.rotating_slides import RotationCandidate, effective_rotation
from upm_site.persistence.models import (
    AuditRecord,
    Event,
    LocalSiteIdentity,
    MediaObject,
    Presentation,
    PresentationAsset,
    PresentationVersion,
    RotationAssignment,
)
from upm_site.recovery_snapshots import touch_site_recovery_snapshot


class RotationOverrideWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_day: date
    scope: Literal["event_day", "room_day", "session"]
    room_id: UUID | None = None
    session_id: UUID | None = None
    presentation_version_id: UUID | None = None


def register_rotation_routes(
    app: FastAPI,
    read_db: Callable[[], Iterator[Session]],
    write_db: Callable[[], Iterator[Session]],
) -> None:
    Read = Annotated[Session, Depends(read_db)]
    Write = Annotated[Session, Depends(write_db)]

    @app.get("/api/v1/events/{event_id}/rotating-slides", tags=["rotating-slides"])
    def rotations(
        event_id: UUID,
        session: Read,
        event_day: date,
        room_id: UUID | None = None,
        session_id: UUID | None = None,
    ):
        rows = session.scalars(
            select(RotationAssignment).where(
                RotationAssignment.event_id == event_id, RotationAssignment.active.is_(True)
            )
        ).all()
        effective = effective_rotation(
            [_candidate(item) for item in rows],
            event_id=event_id,
            event_day=event_day,
            room_id=room_id,
            session_id=session_id,
        )
        return {
            "central_defaults": [
                _view(session, item) for item in rows if item.source_authority == "central"
            ],
            "site_overrides": [
                _view(session, item) for item in rows if item.source_authority == "site"
            ],
            "effective": _view(
                session,
                next(
                    item for item in rows if item.rotation_assignment_id == effective.assignment_id
                )
            )
            if effective
            else None,
            "effective_source": f"{effective.source_authority}:{effective.scope}"
            if effective
            else None,
        }

    @app.post(
        "/api/v1/events/{event_id}/rotating-slides/overrides",
        status_code=201,
        tags=["rotating-slides"],
    )
    def override(event_id: UUID, payload: RotationOverrideWrite, request: Request, session: Write):
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
                RotationAssignment.source_authority == "site",
                RotationAssignment.active.is_(True),
            )
        ):
            if prior.room_id == payload.room_id and prior.session_id == payload.session_id:
                prior.active = False
                prior.override_state = "superseded"
        item = RotationAssignment(
            event_id=event_id,
            source_authority="site",
            override_state="configured",
            active=True,
            **payload.model_dump(),
        )
        session.add(item)
        session.flush()
        _audit(session, request, item, "site.rotation_override.configured")
        touch_site_recovery_snapshot(session, event)
        return _view(session, item)

    @app.delete("/api/v1/rotating-slides/overrides/{assignment_id}", tags=["rotating-slides"])
    def clear_override(assignment_id: UUID, request: Request, session: Write):
        item = session.get(RotationAssignment, assignment_id)
        if item is None or item.source_authority != "site":
            raise HTTPException(404, "Site rotation override not found")
        item.active = False
        item.override_state = "cleared"
        item.revision += 1
        _audit(session, request, item, "site.rotation_override.cleared")
        touch_site_recovery_snapshot(session, session.get(Event, item.event_id))
        return {
            "rotation_assignment_id": assignment_id,
            "active": False,
            "effective_recalculation_required": True,
        }


def _validate(payload):
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


def _candidate(item):
    return RotationCandidate(
        item.rotation_assignment_id,
        item.event_id,
        item.event_day,
        item.scope,
        item.presentation_version_id,
        item.room_id,
        item.session_id,
        item.source_authority,
        item.active,
    )


def _view(session, item):
    view = {
        name: getattr(item, name)
        for name in (
            "rotation_assignment_id",
            "central_assignment_id",
            "event_id",
            "event_day",
            "scope",
            "room_id",
            "session_id",
            "presentation_version_id",
            "source_authority",
            "override_state",
            "active",
            "revision",
        )
    }
    version = (
        session.get(PresentationVersion, item.presentation_version_id)
        if item.presentation_version_id
        else None
    )
    asset = session.scalar(
        select(PresentationAsset).where(
            PresentationAsset.presentation_version_id == item.presentation_version_id
        ).order_by(PresentationAsset.created_at.desc())
    ) if version else None
    media = session.get(MediaObject, asset.media_object_id) if asset else None
    view.update({
        "presentation_id": version.presentation_id if version else None,
        "version_number": version.version_number if version else None,
        "filename": asset.original_filename if asset else None,
        "availability": media.availability if media else None,
        "authority": item.source_authority,
    })
    return view


def _audit(session, request, item, action):
    identity = session.scalar(select(LocalSiteIdentity).where(LocalSiteIdentity.singleton_key == 1))
    session.add(
        AuditRecord(
            site_id=identity.site_id,
            event_id=item.event_id,
            actor_id=getattr(request.state, "site_user_id", None),
            action=action,
            target_type="rotation_assignment",
            target_id=item.rotation_assignment_id,
            after_context=_view(session, item),
        )
    )
