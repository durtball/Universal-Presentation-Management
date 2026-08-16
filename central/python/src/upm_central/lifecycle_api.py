"""Production administrator lifecycle deletion endpoints."""

from collections.abc import Callable, Iterator
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from upm_central.lifecycle import (
    bulk_people_impact,
    event_impact,
    person_deletion_impact,
    request_bulk_people_deletion,
    request_deletion,
)
from upm_central.persistence.models import DeletionOperation, Event, Person


class Confirmation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmation: str


def _view(item: DeletionOperation) -> dict[str, object]:
    return {
        "deletion_operation_id": item.deletion_operation_id,
        "target_type": item.target_type,
        "target_id": item.target_id,
        "target_display_name": item.target_display_name,
        "status": item.status,
        "stage": item.stage,
        "dependency_counts": item.dependency_counts,
        "site_statuses": item.site_statuses,
        "media_results": item.media_results,
        "attempt_count": item.attempt_count,
        "last_error": item.last_error,
        "created_at": item.created_at,
        "completed_at": item.completed_at,
    }


def register_lifecycle_routes(
    app: FastAPI, db: Callable[[], Iterator[Session]], require_admin: Callable
) -> None:
    admin = [Depends(require_admin)]
    Db = Annotated[Session, Depends(db)]

    @app.get("/api/v1/admin/people-bulk-deletion/impact", dependencies=admin)
    def bulk_people_preview(session: Db):
        return {
            "confirmation": "delete all",
            "impact": bulk_people_impact(session),
        }

    @app.post("/api/v1/admin/people-bulk-deletion", status_code=202, dependencies=admin)
    def delete_all_people(payload: Confirmation, request: Request, session: Db):
        return _view(
            request_bulk_people_deletion(
                session,
                confirmation=payload.confirmation,
                actor=getattr(request.state, "admin_actor", "central-admin"),
            )
        )

    @app.get("/api/v1/admin/people-bulk-deletion/current", dependencies=admin)
    def current_bulk_people_deletion(session: Db):
        item = session.scalar(
            select(DeletionOperation)
            .where(DeletionOperation.target_type == "people_bulk")
            .order_by(DeletionOperation.created_at.desc())
            .limit(1)
        )
        return _view(item) if item is not None else None

    @app.get("/api/v1/admin/events/{event_id}/deletion-impact", dependencies=admin)
    def event_preview(event_id: UUID, session: Db):
        event = session.get(Event, event_id)
        if event is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="event not found")
        return {
            "target_id": event_id,
            "confirmation": event.name,
            "impact": event_impact(session, event_id),
        }

    @app.delete("/api/v1/admin/events/{event_id}", status_code=202, dependencies=admin)
    def delete_event(event_id: UUID, payload: Confirmation, request: Request, session: Db):
        return _view(
            request_deletion(
                session,
                "event",
                event_id,
                payload.confirmation,
                getattr(request.state, "admin_actor", "central-admin"),
            )
        )

    @app.get("/api/v1/admin/people/{person_id}/lifecycle-deletion-impact", dependencies=admin)
    def person_preview(person_id: UUID, session: Db):
        person = session.get(Person, person_id)
        if person is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="person not found")
        return {
            "target_id": person_id,
            "confirmation": person.display_name,
            "impact": person_deletion_impact(session, person_id),
        }

    @app.delete("/api/v1/admin/people/{person_id}/lifecycle", status_code=202, dependencies=admin)
    def delete_person(person_id: UUID, payload: Confirmation, request: Request, session: Db):
        return _view(
            request_deletion(
                session,
                "person",
                person_id,
                payload.confirmation,
                getattr(request.state, "admin_actor", "central-admin"),
            )
        )

    @app.get("/api/v1/admin/deletions/{operation_id}", dependencies=admin)
    def deletion_status(operation_id: UUID, session: Db):
        item = session.get(DeletionOperation, operation_id)
        if item is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="deletion not found")
        return _view(item)

    @app.get("/api/v1/admin/deletions", dependencies=admin)
    def deletions(session: Db):
        return [
            _view(x)
            for x in session.scalars(
                select(DeletionOperation).order_by(DeletionOperation.created_at.desc()).limit(100)
            )
        ]
