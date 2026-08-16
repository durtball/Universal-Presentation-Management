"""Versioned Central administration APIs for people, programs, and imports."""
# ruff: noqa: E501

from collections.abc import Callable, Iterator
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from upm_central.imports import commit_batch, create_batch, decide, detect_columns
from upm_central.persistence.models import (
    Event,
    EventParticipation,
    ExternalIdentifier,
    ImportBatch,
    ImportRow,
    Person,
    Presentation,
    PresentationPresenter,
    PresentationSession,
    ReconciliationDecision,
    SessionParticipant,
)
from upm_central.persistence.models import Session as ProgramSession
from upm_central.program import (
    audit,
    delete_person,
    entity_event_ids,
    external_identifier,
    normalize_email,
    normalize_text,
    person_impact,
    require_aware,
    touch_event_program,
)
from upm_shared.enums import (
    ExternalEntityType,
    ImportEntityType,
    ImportValidationState,
    ParticipantStatus,
    PresentationProcessingStatus,
    PresentationWorkflowStatus,
    ReconciliationAction,
    SessionStatus,
    ValidationSeverity,
)


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PersonWrite(ApiModel):
    given_name: Annotated[str | None, Field(max_length=255)] = None
    middle_name: Annotated[str | None, Field(max_length=255)] = None
    family_name: Annotated[str | None, Field(max_length=255)] = None
    prefix: Annotated[str | None, Field(max_length=64)] = None
    suffix: Annotated[str | None, Field(max_length=64)] = None
    preferred_name: Annotated[str | None, Field(max_length=255)] = None
    display_name: Annotated[str, Field(min_length=1, max_length=255)]
    professional_title: Annotated[str | None, Field(max_length=255)] = None
    organization: Annotated[str | None, Field(max_length=255)] = None
    primary_email: Annotated[str | None, Field(max_length=320)] = None
    phone: Annotated[str | None, Field(max_length=64)] = None


class PersonUpdate(PersonWrite):
    expected_revision: Annotated[int, Field(ge=1)]


class DeleteConfirmation(ApiModel):
    confirmation: str


class ParticipantWrite(ApiModel):
    person_id: UUID
    display_name: Annotated[str | None, Field(max_length=255)] = None
    professional_title: Annotated[str | None, Field(max_length=255)] = None
    organization: Annotated[str | None, Field(max_length=255)] = None
    registration_status: Annotated[str | None, Field(max_length=100)] = None
    participant_status: ParticipantStatus = ParticipantStatus.ACTIVE
    is_presenter: bool = False
    notes: str | None = None


class ParticipantUpdate(ParticipantWrite):
    expected_revision: Annotated[int, Field(ge=1)]


class SessionWrite(ApiModel):
    title: Annotated[str, Field(min_length=1, max_length=255)]
    subtitle: Annotated[str | None, Field(max_length=255)] = None
    description: str | None = None
    session_code: Annotated[str | None, Field(max_length=255)] = None
    session_type: Annotated[str | None, Field(max_length=100)] = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    location_name: Annotated[str | None, Field(max_length=255)] = None
    status: SessionStatus = SessionStatus.DRAFT
    sort_order: int = 0


class SessionUpdate(SessionWrite):
    expected_revision: Annotated[int, Field(ge=1)]


class SessionPresenterWrite(ApiModel):
    event_participation_id: UUID
    role: Annotated[str, Field(min_length=1, max_length=64)] = "presenter"
    presenter_order: int = 0
    primary_presenter: bool = False
    notes: str | None = None


class PresentationWrite(ApiModel):
    title: Annotated[str, Field(min_length=1, max_length=255)]
    description: str | None = None
    presentation_code: Annotated[str | None, Field(max_length=255)] = None
    workflow_status: PresentationWorkflowStatus = PresentationWorkflowStatus.EXPECTED
    processing_status: PresentationProcessingStatus = PresentationProcessingStatus.NOT_STARTED
    scheduled_at: datetime | None = None
    preferred_session_id: UUID | None = None


class PresentationUpdate(PresentationWrite):
    expected_revision: Annotated[int, Field(ge=1)]


class PresentationSessionWrite(ApiModel):
    session_id: UUID
    association_type: Annotated[str, Field(min_length=1, max_length=64)] = "scheduled"
    sort_order: int = 0
    primary_session: bool = False


class PresentationPresenterWrite(ApiModel):
    event_participation_id: UUID
    role: Annotated[str, Field(min_length=1, max_length=64)] = "presenter"
    presenter_order: int = 0
    primary_presenter: bool = False


class ExternalIdentifierWrite(ApiModel):
    entity_type: ExternalEntityType
    entity_id: UUID
    namespace: Annotated[str, Field(min_length=1, max_length=255)]
    external_id: Annotated[str, Field(min_length=1, max_length=512)]
    event_id: UUID | None = None
    source: Annotated[str | None, Field(max_length=255)] = None


class DecisionWrite(ApiModel):
    action: ReconciliationAction
    selected_person_id: UUID | None = None
    corrected_values: dict[str, object] | None = None
    reason: str | None = None


def _person_view(person: Person) -> dict[str, object]:
    return {
        "person_id": person.person_id,
        "given_name": person.given_name,
        "middle_name": person.middle_name,
        "family_name": person.family_name,
        "display_name": person.display_name,
        "professional_title": person.professional_title,
        "organization": person.organization,
        "primary_email": person.primary_email,
        "phone": person.phone,
        "active": person.active,
        "revision": person.revision,
        "created_at": person.created_at,
        "updated_at": person.updated_at,
    }


def _participant_view(item: EventParticipation) -> dict[str, object]:
    return {
        "event_participation_id": item.event_participation_id,
        "event_id": item.event_id,
        "person_id": item.person_id,
        "person_display_name": item.person.display_name,
        "primary_email": item.person.primary_email,
        "display_name": item.display_name,
        "professional_title": item.professional_title,
        "organization": item.organization,
        "participant_status": item.participant_status,
        "is_presenter": item.is_presenter,
        "notes": item.notes,
        "revision": item.revision,
        "sessions": [
            {"session_id": link.session_id, "title": link.session.title, "role": link.role}
            for link in item.session_participations
        ],
    }


def _session_view(item: ProgramSession) -> dict[str, object]:
    return {
        "session_id": item.session_id,
        "event_id": item.event_id,
        "title": item.title,
        "subtitle": item.subtitle,
        "description": item.description,
        "session_code": item.session_code,
        "session_type": item.session_type,
        "starts_at": item.starts_at,
        "ends_at": item.ends_at,
        "location_name": item.location_name,
        "status": item.status,
        "sort_order": item.sort_order,
        "revision": item.revision,
        "presenters": [
            {
                "session_participant_id": link.session_participant_id,
                "event_participation_id": link.event_participation_id,
                "role": link.role,
                "presenter_order": link.presenter_order,
                "primary_presenter": link.primary_presenter,
                "display_name": link.event_participation.display_name
                or link.event_participation.person.display_name,
            }
            for link in item.participants
        ],
    }


def _presentation_view(item: Presentation) -> dict[str, object]:
    return {
        "presentation_id": item.presentation_id,
        "event_id": item.event_id,
        "title": item.title,
        "description": item.description,
        "presentation_code": item.presentation_code,
        "workflow_status": item.workflow_status,
        "processing_status": item.processing_status,
        "scheduled_at": item.scheduled_at,
        "preferred_session_id": item.session_id,
        "revision": item.revision,
        "sessions": [
            {
                "presentation_session_id": link.presentation_session_id,
                "session_id": link.session_id,
                "association_type": link.association_type,
                "sort_order": link.sort_order,
                "primary_session": link.primary_session,
            }
            for link in item.session_links
        ],
        "presenters": [
            {
                "presentation_presenter_id": link.presentation_presenter_id,
                "event_participation_id": link.event_participation_id,
                "role": link.role,
                "presenter_order": link.presenter_order,
                "primary_presenter": link.primary_presenter,
                "display_name": link.event_participation.display_name
                or link.event_participation.person.display_name,
            }
            for link in item.presenter_links
        ],
    }


def _batch_view(batch: ImportBatch) -> dict[str, object]:
    return {
        "import_batch_id": batch.import_batch_id,
        "event_id": batch.event_id,
        "filename": batch.filename,
        "status": batch.status,
        "row_count": batch.row_count,
        "valid_count": batch.valid_count,
        "warning_count": batch.warning_count,
        "conflict_count": batch.conflict_count,
        "committed_count": batch.committed_count,
        "rejected_count": batch.rejected_count,
        "failure_summary": batch.failure_summary,
        "created_at": batch.created_at,
        "committed_at": batch.committed_at,
    }


def register_program_routes(
    app: FastAPI,
    db: Callable[[], Iterator[Session]],
    require_admin: Callable[..., None],
) -> None:
    DbSession = Annotated[Session, Depends(db)]
    admin = [Depends(require_admin)]

    @app.post("/api/v1/admin/people", status_code=201, dependencies=admin, tags=["people"])
    def create_person(payload: PersonWrite, session: DbSession) -> dict[str, object]:
        normalized_email = normalize_email(payload.primary_email)
        if payload.primary_email and normalized_email is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="invalid email")
        person = Person(
            **payload.model_dump(),
            normalized_name=normalize_text(payload.display_name) or "",
            normalized_email=normalized_email,
        )
        session.add(person)
        session.flush()
        audit(
            session,
            action="central.person.created",
            target_type="person",
            target_id=person.person_id,
            after={"display_name": person.display_name},
        )
        return _person_view(person)

    @app.get("/api/v1/admin/people", dependencies=admin, tags=["people"])
    def list_people(
        session: DbSession, q: str | None = None, include_inactive: bool = False
    ) -> list[dict[str, object]]:
        statement = select(Person).order_by(Person.display_name, Person.person_id).limit(500)
        if not include_inactive:
            statement = statement.where(Person.deleted_at.is_(None))
        if q:
            term = f"%{q.strip()}%"
            statement = statement.where(
                or_(
                    Person.display_name.ilike(term),
                    Person.primary_email.ilike(term),
                    Person.organization.ilike(term),
                )
            )
        return [_person_view(person) for person in session.scalars(statement)]

    @app.get("/api/v1/admin/people/{person_id}", dependencies=admin, tags=["people"])
    def get_person(person_id: UUID, session: DbSession) -> dict[str, object]:
        person = session.get(Person, person_id)
        if person is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="person not found")
        result = _person_view(person)
        result["impact"] = person_impact(session, person_id)
        result["participations"] = [
            {
                "event_participation_id": p.event_participation_id,
                "event_id": p.event_id,
                "display_name": p.display_name,
                "is_presenter": p.is_presenter,
            }
            for p in person.event_participations
        ]
        result["external_identifiers"] = [
            {
                "external_identifier_id": i.external_identifier_id,
                "namespace": i.namespace,
                "external_id": i.external_id,
                "scope": i.scope,
            }
            for i in session.scalars(
                select(ExternalIdentifier).where(
                    ExternalIdentifier.entity_type == ExternalEntityType.PERSON,
                    ExternalIdentifier.entity_id == person_id,
                )
            )
        ]
        return result

    @app.put("/api/v1/admin/people/{person_id}", dependencies=admin, tags=["people"])
    def update_person(
        person_id: UUID, payload: PersonUpdate, session: DbSession
    ) -> dict[str, object]:
        person = session.get(Person, person_id)
        if person is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="person not found")
        if person.revision != payload.expected_revision:
            raise HTTPException(status.HTTP_409_CONFLICT, detail="person was modified")
        normalized_email = normalize_email(payload.primary_email)
        if payload.primary_email and normalized_email is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="invalid email")
        before = _person_view(person)
        for key, value in payload.model_dump(exclude={"expected_revision"}).items():
            setattr(person, key, value)
        person.normalized_name = normalize_text(person.display_name) or ""
        person.normalized_email = normalized_email
        person.revision += 1
        audit(
            session,
            action="central.person.updated",
            target_type="person",
            target_id=person_id,
            before=before,
            after=_person_view(person),
        )
        event_ids = set(
            session.scalars(
                select(EventParticipation.event_id).where(EventParticipation.person_id == person_id)
            )
        )
        for event_id in event_ids:
            touch_event_program(session, session.get(Event, event_id))
        return _person_view(person)

    @app.get(
        "/api/v1/admin/people/{person_id}/deletion-impact", dependencies=admin, tags=["people"]
    )
    def deletion_impact(person_id: UUID, session: DbSession) -> dict[str, object]:
        if session.get(Person, person_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="person not found")
        return {"person_id": person_id, "impact": person_impact(session, person_id)}

    @app.delete("/api/v1/admin/people/{person_id}", dependencies=admin, tags=["people"])
    def remove_person(
        person_id: UUID,
        payload: DeleteConfirmation,
        session: DbSession,
        x_upm_actor: Annotated[str | None, Header()] = None,
    ) -> dict[str, object]:
        person = session.get(Person, person_id)
        if person is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="person not found")
        delete_person(
            session, person, confirmation=payload.confirmation, actor=x_upm_actor or "central-admin"
        )
        return {"person_id": person_id, "deleted": True}

    @app.get("/api/v1/admin/events/{event_id}/participants", dependencies=admin, tags=["program"])
    def list_participants(event_id: UUID, session: DbSession) -> list[dict[str, object]]:
        items = session.scalars(
            select(EventParticipation)
            .where(EventParticipation.event_id == event_id)
            .options(selectinload(EventParticipation.person))
            .options(
                selectinload(EventParticipation.session_participations).selectinload(
                    SessionParticipant.session
                )
            )
            .order_by(EventParticipation.created_at)
        ).all()
        result = []
        for item in items:
            view = _participant_view(item)
            view["external_identifiers"] = [
                {"namespace": identifier.namespace, "external_id": identifier.external_id}
                for identifier in session.scalars(
                    select(ExternalIdentifier).where(
                        ExternalIdentifier.entity_type == ExternalEntityType.PERSON,
                        ExternalIdentifier.entity_id == item.person_id,
                    )
                )
            ]
            result.append(view)
        return result

    @app.post(
        "/api/v1/admin/events/{event_id}/participants",
        status_code=201,
        dependencies=admin,
        tags=["program"],
    )
    def create_participant(
        event_id: UUID, payload: ParticipantWrite, session: DbSession
    ) -> dict[str, object]:
        event, person = session.get(Event, event_id), session.get(Person, payload.person_id)
        if event is None or person is None or person.deleted_at is not None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, detail="event or active person not found"
            )
        exists = session.scalar(
            select(EventParticipation).where(
                EventParticipation.event_id == event_id,
                EventParticipation.person_id == payload.person_id,
            )
        )
        if exists:
            raise HTTPException(
                status.HTTP_409_CONFLICT, detail="person already participates in event"
            )
        item = EventParticipation(event_id=event_id, **payload.model_dump())
        session.add(item)
        session.flush()
        audit(
            session,
            action="central.event_participant.created",
            target_type="event_participation",
            target_id=item.event_participation_id,
            event_id=event_id,
            after={"person_id": str(item.person_id)},
        )
        touch_event_program(session, event)
        return _participant_view(item)

    @app.put(
        "/api/v1/admin/event-participants/{participant_id}",
        dependencies=admin,
        tags=["program"],
    )
    def update_participant(
        participant_id: UUID, payload: ParticipantUpdate, session: DbSession
    ) -> dict[str, object]:
        item = session.get(EventParticipation, participant_id)
        if item is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="participant not found")
        if item.revision != payload.expected_revision:
            raise HTTPException(status.HTTP_409_CONFLICT, detail="participant was modified")
        person = session.get(Person, payload.person_id)
        if person is None or person.deleted_at is not None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, detail="active person required"
            )
        duplicate = session.scalar(
            select(EventParticipation).where(
                EventParticipation.event_id == item.event_id,
                EventParticipation.person_id == payload.person_id,
                EventParticipation.event_participation_id != participant_id,
            )
        )
        if duplicate is not None:
            raise HTTPException(
                status.HTTP_409_CONFLICT, detail="person already participates in event"
            )
        before = _participant_view(item)
        for key, value in payload.model_dump(exclude={"expected_revision"}).items():
            setattr(item, key, value)
        item.revision += 1
        audit(
            session,
            action="central.event_participant.updated",
            target_type="event_participation",
            target_id=participant_id,
            event_id=item.event_id,
            before=before,
            after=_participant_view(item),
        )
        touch_event_program(session, session.get(Event, item.event_id))
        return _participant_view(item)

    @app.delete(
        "/api/v1/admin/event-participants/{participant_id}",
        dependencies=admin,
        tags=["program"],
    )
    def deactivate_participant(participant_id: UUID, session: DbSession) -> dict[str, object]:
        item = session.get(EventParticipation, participant_id)
        if item is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="participant not found")
        item.participant_status = ParticipantStatus.INACTIVE
        item.is_presenter = False
        item.revision += 1
        audit(
            session,
            action="central.event_participant.deactivated",
            target_type="event_participation",
            target_id=participant_id,
            event_id=item.event_id,
        )
        touch_event_program(session, session.get(Event, item.event_id))
        return _participant_view(item)

    @app.get("/api/v1/admin/events/{event_id}/sessions", dependencies=admin, tags=["program"])
    def list_sessions(event_id: UUID, session: DbSession) -> list[dict[str, object]]:
        return [
            _session_view(item)
            for item in session.scalars(
                select(ProgramSession)
                .where(ProgramSession.event_id == event_id)
                .options(
                    selectinload(ProgramSession.participants)
                    .selectinload(SessionParticipant.event_participation)
                    .selectinload(EventParticipation.person)
                )
                .order_by(ProgramSession.starts_at, ProgramSession.sort_order, ProgramSession.title)
            )
        ]

    @app.post(
        "/api/v1/admin/events/{event_id}/sessions",
        status_code=201,
        dependencies=admin,
        tags=["program"],
    )
    def create_session(
        event_id: UUID, payload: SessionWrite, session: DbSession
    ) -> dict[str, object]:
        event = session.get(Event, event_id)
        if event is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="event not found")
        require_aware(payload.starts_at, "starts_at")
        require_aware(payload.ends_at, "ends_at")
        if payload.starts_at and payload.ends_at and payload.ends_at <= payload.starts_at:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, detail="session end must follow start"
            )
        item = ProgramSession(event_id=event_id, **payload.model_dump())
        session.add(item)
        session.flush()
        audit(
            session,
            action="central.session.created",
            target_type="session",
            target_id=item.session_id,
            event_id=event_id,
            after={"title": item.title},
        )
        touch_event_program(session, event)
        return _session_view(item)

    @app.put("/api/v1/admin/sessions/{session_id}", dependencies=admin, tags=["program"])
    def update_session(
        session_id: UUID, payload: SessionUpdate, session: DbSession
    ) -> dict[str, object]:
        item = session.get(ProgramSession, session_id)
        if item is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="session not found")
        if item.revision != payload.expected_revision:
            raise HTTPException(status.HTTP_409_CONFLICT, detail="session was modified")
        require_aware(payload.starts_at, "starts_at")
        require_aware(payload.ends_at, "ends_at")
        if payload.starts_at and payload.ends_at and payload.ends_at <= payload.starts_at:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, detail="session end must follow start"
            )
        before = _session_view(item)
        for key, value in payload.model_dump(exclude={"expected_revision"}).items():
            setattr(item, key, value)
        item.revision += 1
        audit(
            session,
            action="central.session.updated",
            target_type="session",
            target_id=session_id,
            event_id=item.event_id,
            before=before,
            after=_session_view(item),
        )
        touch_event_program(session, session.get(Event, item.event_id))
        return _session_view(item)

    @app.post(
        "/api/v1/admin/sessions/{session_id}/presenters",
        status_code=201,
        dependencies=admin,
        tags=["program"],
    )
    def assign_session_presenter(
        session_id: UUID, payload: SessionPresenterWrite, session: DbSession
    ) -> dict[str, object]:
        item, participant = (
            session.get(ProgramSession, session_id),
            session.get(EventParticipation, payload.event_participation_id),
        )
        if item is None or participant is None or item.event_id != participant.event_id:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="presenter must belong to session event",
            )
        link = SessionParticipant(session_id=session_id, **payload.model_dump())
        participant.is_presenter = True
        session.add(link)
        session.flush()
        audit(
            session,
            action="central.session_presenter.created",
            target_type="session_presenter",
            target_id=link.session_participant_id,
            event_id=item.event_id,
            after={
                "event_participation_id": str(participant.event_participation_id),
                "role": link.role,
            },
        )
        touch_event_program(session, session.get(Event, item.event_id))
        return {"session_participant_id": link.session_participant_id, **payload.model_dump()}

    @app.delete(
        "/api/v1/admin/session-presenters/{relationship_id}",
        dependencies=admin,
        tags=["program"],
    )
    def remove_session_presenter(relationship_id: UUID, session: DbSession) -> dict[str, object]:
        link = session.get(SessionParticipant, relationship_id)
        if link is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="relationship not found")
        program_session = session.get(ProgramSession, link.session_id)
        session.delete(link)
        audit(
            session,
            action="central.session_presenter.deleted",
            target_type="session_presenter",
            target_id=relationship_id,
            event_id=program_session.event_id,
        )
        touch_event_program(session, session.get(Event, program_session.event_id))
        return {"session_participant_id": relationship_id, "deleted": True}

    @app.delete("/api/v1/admin/sessions/{session_id}", dependencies=admin, tags=["program"])
    def archive_session(session_id: UUID, session: DbSession) -> dict[str, object]:
        item = session.get(ProgramSession, session_id)
        if item is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="session not found")
        item.status = SessionStatus.ARCHIVED
        item.revision += 1
        audit(
            session,
            action="central.session.archived",
            target_type="session",
            target_id=session_id,
            event_id=item.event_id,
        )
        touch_event_program(session, session.get(Event, item.event_id))
        return _session_view(item)

    @app.get("/api/v1/admin/events/{event_id}/presentations", dependencies=admin, tags=["program"])
    def list_presentations(event_id: UUID, session: DbSession) -> list[dict[str, object]]:
        return [
            _presentation_view(item)
            for item in session.scalars(
                select(Presentation)
                .where(Presentation.event_id == event_id)
                .options(
                    selectinload(Presentation.session_links),
                    selectinload(Presentation.presenter_links),
                    selectinload(Presentation.presenter_links)
                    .selectinload(PresentationPresenter.event_participation)
                    .selectinload(EventParticipation.person),
                )
                .order_by(Presentation.title)
            )
        ]

    @app.post(
        "/api/v1/admin/events/{event_id}/presentations",
        status_code=201,
        dependencies=admin,
        tags=["program"],
    )
    def create_presentation(
        event_id: UUID, payload: PresentationWrite, session: DbSession
    ) -> dict[str, object]:
        event = session.get(Event, event_id)
        if event is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="event not found")
        if payload.preferred_session_id:
            linked_session = session.get(ProgramSession, payload.preferred_session_id)
            if linked_session is None or linked_session.event_id != event_id:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY, detail="session not in event"
                )
        require_aware(payload.scheduled_at, "scheduled_at")
        values = payload.model_dump()
        values["session_id"] = values.pop("preferred_session_id")
        item = Presentation(event_id=event_id, **values)
        session.add(item)
        session.flush()
        if item.session_id:
            session.add(
                PresentationSession(
                    presentation_id=item.presentation_id,
                    session_id=item.session_id,
                    association_type="scheduled",
                    primary_session=True,
                )
            )
        audit(
            session,
            action="central.presentation.created",
            target_type="presentation",
            target_id=item.presentation_id,
            event_id=event_id,
            after={"title": item.title},
        )
        touch_event_program(session, event)
        return _presentation_view(item)

    @app.put("/api/v1/admin/presentations/{presentation_id}", dependencies=admin, tags=["program"])
    def update_presentation(
        presentation_id: UUID, payload: PresentationUpdate, session: DbSession
    ) -> dict[str, object]:
        item = session.get(Presentation, presentation_id)
        if item is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="presentation not found")
        if item.revision != payload.expected_revision:
            raise HTTPException(status.HTTP_409_CONFLICT, detail="presentation was modified")
        if payload.preferred_session_id:
            linked_session = session.get(ProgramSession, payload.preferred_session_id)
            if linked_session is None or linked_session.event_id != item.event_id:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY, detail="session not in event"
                )
        before = _presentation_view(item)
        require_aware(payload.scheduled_at, "scheduled_at")
        values = payload.model_dump(exclude={"expected_revision"})
        values["session_id"] = values.pop("preferred_session_id")
        for key, value in values.items():
            setattr(item, key, value)
        item.revision += 1
        audit(
            session,
            action="central.presentation.updated",
            target_type="presentation",
            target_id=presentation_id,
            event_id=item.event_id,
            before=before,
            after=_presentation_view(item),
        )
        touch_event_program(session, session.get(Event, item.event_id))
        return _presentation_view(item)

    @app.post(
        "/api/v1/admin/presentations/{presentation_id}/sessions",
        status_code=201,
        dependencies=admin,
        tags=["program"],
    )
    def link_presentation_session(
        presentation_id: UUID, payload: PresentationSessionWrite, session: DbSession
    ) -> dict[str, object]:
        item, linked = (
            session.get(Presentation, presentation_id),
            session.get(ProgramSession, payload.session_id),
        )
        if item is None or linked is None or item.event_id != linked.event_id:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, detail="entities must share an event"
            )
        link = PresentationSession(presentation_id=presentation_id, **payload.model_dump())
        if payload.primary_session:
            item.session_id = payload.session_id
        session.add(link)
        session.flush()
        audit(
            session,
            action="central.presentation_session.created",
            target_type="presentation_session",
            target_id=link.presentation_session_id,
            event_id=item.event_id,
            after={"session_id": str(link.session_id)},
        )
        touch_event_program(session, session.get(Event, item.event_id))
        return {"presentation_session_id": link.presentation_session_id, **payload.model_dump()}

    @app.post(
        "/api/v1/admin/presentations/{presentation_id}/presenters",
        status_code=201,
        dependencies=admin,
        tags=["program"],
    )
    def link_presentation_presenter(
        presentation_id: UUID, payload: PresentationPresenterWrite, session: DbSession
    ) -> dict[str, object]:
        item, participant = (
            session.get(Presentation, presentation_id),
            session.get(EventParticipation, payload.event_participation_id),
        )
        if item is None or participant is None or item.event_id != participant.event_id:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="presenter must belong to presentation event",
            )
        link = PresentationPresenter(presentation_id=presentation_id, **payload.model_dump())
        participant.is_presenter = True
        session.add(link)
        session.flush()
        audit(
            session,
            action="central.presentation_presenter.created",
            target_type="presentation_presenter",
            target_id=link.presentation_presenter_id,
            event_id=item.event_id,
            after={"event_participation_id": str(participant.event_participation_id)},
        )
        touch_event_program(session, session.get(Event, item.event_id))
        return {"presentation_presenter_id": link.presentation_presenter_id, **payload.model_dump()}

    @app.delete(
        "/api/v1/admin/presentation-sessions/{relationship_id}",
        dependencies=admin,
        tags=["program"],
    )
    def remove_presentation_session(relationship_id: UUID, session: DbSession) -> dict[str, object]:
        link = session.get(PresentationSession, relationship_id)
        if link is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="relationship not found")
        item = session.get(Presentation, link.presentation_id)
        if item.session_id == link.session_id:
            item.session_id = None
        session.delete(link)
        audit(
            session,
            action="central.presentation_session.deleted",
            target_type="presentation_session",
            target_id=relationship_id,
            event_id=item.event_id,
        )
        touch_event_program(session, session.get(Event, item.event_id))
        return {"presentation_session_id": relationship_id, "deleted": True}

    @app.delete(
        "/api/v1/admin/presentation-presenters/{relationship_id}",
        dependencies=admin,
        tags=["program"],
    )
    def remove_presentation_presenter(
        relationship_id: UUID, session: DbSession
    ) -> dict[str, object]:
        link = session.get(PresentationPresenter, relationship_id)
        if link is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="relationship not found")
        item = session.get(Presentation, link.presentation_id)
        session.delete(link)
        audit(
            session,
            action="central.presentation_presenter.deleted",
            target_type="presentation_presenter",
            target_id=relationship_id,
            event_id=item.event_id,
        )
        touch_event_program(session, session.get(Event, item.event_id))
        return {"presentation_presenter_id": relationship_id, "deleted": True}

    @app.delete(
        "/api/v1/admin/presentations/{presentation_id}",
        dependencies=admin,
        tags=["program"],
    )
    def archive_presentation(presentation_id: UUID, session: DbSession) -> dict[str, object]:
        item = session.get(Presentation, presentation_id)
        if item is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="presentation not found")
        item.workflow_status = PresentationWorkflowStatus.ARCHIVED
        item.revision += 1
        audit(
            session,
            action="central.presentation.archived",
            target_type="presentation",
            target_id=presentation_id,
            event_id=item.event_id,
        )
        touch_event_program(session, session.get(Event, item.event_id))
        return _presentation_view(item)

    @app.post(
        "/api/v1/admin/external-identifiers", status_code=201, dependencies=admin, tags=["program"]
    )
    def create_external_identifier(
        payload: ExternalIdentifierWrite, session: DbSession
    ) -> dict[str, object]:
        item = external_identifier(
            session,
            entity_type=payload.entity_type,
            entity_id=payload.entity_id,
            namespace=payload.namespace,
            value=payload.external_id,
            event_id=payload.event_id,
            source=payload.source,
        )
        session.flush()
        audit(
            session,
            action="central.external_identifier.created",
            target_type="external_identifier",
            target_id=item.external_identifier_id,
            event_id=payload.event_id,
            after={"namespace": item.namespace, "external_id": item.external_id},
        )
        for event_id in entity_event_ids(
            session, payload.entity_type, payload.entity_id, payload.event_id
        ):
            touch_event_program(session, session.get(Event, event_id))
        return {
            "external_identifier_id": item.external_identifier_id,
            "entity_type": item.entity_type,
            "entity_id": item.entity_id,
            "namespace": item.namespace,
            "external_id": item.external_id,
            "scope": item.scope,
        }

    @app.post(
        "/api/v1/admin/events/{event_id}/imports",
        status_code=201,
        dependencies=admin,
        tags=["imports"],
    )
    async def upload_import(
        event_id: UUID,
        session: DbSession,
        file: Annotated[UploadFile, File()],
        importer_type: Annotated[str, Form(max_length=100)] = "program",
    ) -> dict[str, object]:
        if importer_type != "program":
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, detail="unsupported importer type"
            )
        event = session.get(Event, event_id)
        if event is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="event not found")
        content = await file.read()
        return _batch_view(
            create_batch(
                session,
                event=event,
                filename=file.filename or "import",
                content_type=file.content_type or "application/octet-stream",
                content=content,
            )
        )

    @app.get("/api/v1/admin/events/{event_id}/imports", dependencies=admin, tags=["imports"])
    def list_imports(event_id: UUID, session: DbSession) -> list[dict[str, object]]:
        return [
            _batch_view(batch)
            for batch in session.scalars(
                select(ImportBatch)
                .where(ImportBatch.event_id == event_id)
                .order_by(ImportBatch.created_at.desc())
            )
        ]

    @app.get("/api/v1/admin/imports/{batch_id}", dependencies=admin, tags=["imports"])
    def get_import(batch_id: UUID, session: DbSession) -> dict[str, object]:
        batch = session.get(ImportBatch, batch_id)
        if batch is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="import not found")
        result = _batch_view(batch)
        rows = session.scalars(
            select(ImportRow)
            .where(ImportRow.import_batch_id == batch_id)
            .options(selectinload(ImportRow.issues))
            .order_by(ImportRow.source_row_number)
        ).all()
        result["rows"] = [
            {
                "import_row_id": row.import_row_id,
                "source_row_number": row.source_row_number,
                "worksheet": row.normalized_values.get("_source_worksheet"),
                "raw_values": row.raw_values,
                "normalized_values": row.normalized_values,
                "corrected_values": row.corrected_values,
                "entity_type": row.entity_type,
                "validation_state": row.validation_state,
                "match_outcome": row.match_outcome,
                "proposed_person_id": row.proposed_person_id,
                "candidate_person_ids": row.candidate_person_ids,
                "match_confidence": row.match_confidence,
                "match_reason": row.match_reason,
                "proposed_action": row.proposed_action,
                "conflict_state": row.conflict_state,
                "resolution_action": row.resolution_action,
                "committed_entity_ids": row.committed_entity_ids,
                "issues": [
                    {
                        "severity": issue.severity,
                        "code": issue.code,
                        "field_name": issue.field_name,
                        "message": issue.message,
                    }
                    for issue in row.issues
                ],
            }
            for row in rows
        ]
        headers = list(
            dict.fromkeys(key for row in rows for key in row.raw_values if not key.startswith("__"))
        )
        result["source_headers"] = headers
        result["detected_mapping"] = detect_columns(headers)
        result["sample_rows"] = [row.raw_values for row in rows[:5]]
        result["preview_counts"] = {
            "source_rows": len(rows),
            "total_populated_source_rows": len(rows),
            "accepted_rows": sum(
                row.validation_state != ImportValidationState.ERROR for row in rows
            ),
            "rows_with_warnings": sum(
                row.validation_state == ImportValidationState.WARNING for row in rows
            ),
            "rows_with_blocking_errors": sum(
                row.validation_state == ImportValidationState.ERROR for row in rows
            ),
            "unique_presenters": len(
                {
                    str(
                        row.normalized_values.get("external_id")
                        or row.normalized_values.get("normalized_email")
                    )
                    for row in rows
                    if row.normalized_values.get("external_id")
                    or row.normalized_values.get("normalized_email")
                }
            ),
            "existing_identity_matches": sum(
                str(row.match_outcome or "") == "exact" for row in rows
            ),
            "new_people": sum(str(row.match_outcome or "") == "no_match" for row in rows),
            "ambiguous_identities": sum(bool(row.conflict_state) for row in rows),
            "sessions_or_program_items": len(
                {
                    str(row.normalized_values.get("session_code"))
                    for row in rows
                    if row.normalized_values.get("session_code")
                }
            ),
            "presentations": sum(row.entity_type == ImportEntityType.PRESENTATION for row in rows),
            "warnings": sum(
                issue.severity == ValidationSeverity.WARNING for row in rows for issue in row.issues
            ),
            "errors": sum(
                issue.severity == ValidationSeverity.ERROR for row in rows for issue in row.issues
            ),
            "identity_conflicts": sum(bool(row.conflict_state) for row in rows),
            "unique_room_labels": len(
                {
                    normalize_text(str(row.normalized_values.get("location_name")))
                    for row in rows
                    if row.normalized_values.get("location_name")
                }
            ),
            "presenter_relationships": sum(
                bool(row.normalized_values.get("email"))
                for row in rows
                if row.normalized_values.get("session_code")
            ),
            "unresolved_room_mappings": len(
                {
                    normalize_text(str(row.normalized_values.get("location_name")))
                    for row in rows
                    if row.normalized_values.get("location_name")
                }
            ),
        }
        return result

    @app.post("/api/v1/admin/import-rows/{row_id}/decision", dependencies=admin, tags=["imports"])
    def reconcile_row(
        row_id: UUID, payload: DecisionWrite, session: DbSession
    ) -> dict[str, object]:
        row = session.scalar(
            select(ImportRow)
            .where(ImportRow.import_row_id == row_id)
            .options(selectinload(ImportRow.batch))
        )
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="import row not found")
        decision: ReconciliationDecision = decide(
            session,
            row,
            action=payload.action,
            selected_person_id=payload.selected_person_id,
            corrected_values=payload.corrected_values,
            reason=payload.reason,
        )
        return {
            "reconciliation_decision_id": decision.reconciliation_decision_id,
            "import_row_id": row_id,
            "action": decision.action,
        }

    @app.post("/api/v1/admin/imports/{batch_id}/commit", dependencies=admin, tags=["imports"])
    def commit_import(batch_id: UUID, session: DbSession) -> dict[str, object]:
        batch = session.get(ImportBatch, batch_id)
        if batch is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="import not found")
        return _batch_view(commit_batch(session, batch))

    @app.get("/admin/program", response_class=HTMLResponse, tags=["administration"])
    def program_page() -> str:
        return PROGRAM_ADMIN_HTML

    @app.get("/admin/people", response_class=HTMLResponse, tags=["administration"])
    def people_page() -> str:
        return PEOPLE_ADMIN_HTML


PROGRAM_ADMIN_HTML = """<!doctype html><html><head><title>UPM Central Program</title>
<style>body{font:14px system-ui;max-width:1200px;margin:auto;padding:1rem}nav a{margin-right:1rem}
input,select,button{margin:.25rem;padding:.35rem}pre{background:#f4f4f4;padding:.75rem;overflow:auto}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:1rem}.error{color:#a00}</style></head><body>
<nav><a href="/admin/sites">Sites</a><a href="/admin/events">Events</a>
<a href="/admin/program">Event program</a><a href="/admin/people">People</a></nav>
<h1>Event program administration</h1><label>Admin token <input id="token" type="password"></label>
<button onclick="loadEvents()">Load</button><select id="event" onchange="loadProgram()"></select>
<div class="grid"><section><h2>Schedule and presenters</h2><div id="sessions"></div></section>
<section><h2>Presentations</h2><div id="presentations"></div></section></div>
<section><h2>Participants</h2><div id="participants"></div></section>
<section><h2>Imports</h2><input id="file" type="file" accept=".csv,.xlsx">
<button onclick="upload()">Stage import</button><div id="imports"></div></section><p class="error" id="error"></p>
<script>const h=()=>({'X-UPM-Admin-Token':document.querySelector('#token').value});
async function json(url,opt={}){opt.headers={...(opt.headers||{}),...h()};const r=await fetch(url,opt);
if(!r.ok)throw Error(JSON.stringify(await r.json()));return r.json()}
async function loadEvents(){try{const es=await json('/api/v1/admin/events'),s=document.querySelector('#event');
s.replaceChildren(...es.map(e=>new Option(`${e.name} — ${e.timezone||'UTC'}`,e.event_id)));await loadProgram()}catch(e){error.textContent=e}}
async function loadProgram(){const id=event.value;if(!id)return;try{const [ss,ps,rs,is]=await Promise.all([
json(`/api/v1/admin/events/${id}/sessions`),json(`/api/v1/admin/events/${id}/presentations`),
json(`/api/v1/admin/events/${id}/participants`),json(`/api/v1/admin/events/${id}/imports`)]);
sessions.innerHTML=ss.map(x=>`<pre>${esc(x)}</pre>`).join('');presentations.innerHTML=ps.map(x=>`<pre>${esc(x)}</pre>`).join('');
participants.innerHTML=rs.map(x=>`<pre>${esc(x)}</pre>`).join('');imports.innerHTML=is.map(x=>`<pre>${esc(x)}</pre>`).join('');
error.textContent=''}catch(e){error.textContent=e}}
function esc(x){return JSON.stringify(x,null,2).replaceAll('&','&amp;').replaceAll('<','&lt;')}
async function upload(){const f=file.files[0];if(!f)return;const body=new FormData();body.append('file',f);body.append('importer_type','program');
try{await json(`/api/v1/admin/events/${event.value}/imports`,{method:'POST',body});await loadProgram()}catch(e){error.textContent=e}}</script></body></html>"""

PEOPLE_ADMIN_HTML = """<!doctype html><html><head><title>UPM Central People</title>
<style>body{font:14px system-ui;max-width:1100px;margin:auto;padding:1rem}nav a{margin-right:1rem}
input,button{margin:.25rem;padding:.35rem}pre{background:#f4f4f4;padding:.75rem}</style></head><body>
<nav><a href="/admin/sites">Sites</a><a href="/admin/events">Events</a>
<a href="/admin/program">Event program</a><a href="/admin/people">People</a></nav>
<h1>Permanent identity administration</h1><p>Deletion is protected and requires a separate impact
review plus exact UUID confirmation.</p><input id="token" type="password" placeholder="Admin token">
<input id="query" placeholder="Name, email, organization"><button onclick="load()">Search</button>
<div id="people"></div><script>const esc=x=>JSON.stringify(x,null,2).replaceAll('<','&lt;');
async function load(){const q=encodeURIComponent(query.value),r=await fetch(`/api/v1/admin/people?q=${q}`,
{headers:{'X-UPM-Admin-Token':token.value}}),rows=await r.json();people.innerHTML=rows.map(x=>
`<button onclick="detail('${x.person_id}')">Inspect</button><pre>${esc(x)}</pre>`).join('')}
async function detail(id){const r=await fetch(`/api/v1/admin/people/${id}`,
{headers:{'X-UPM-Admin-Token':token.value}});alert(esc(await r.json()))}</script></body></html>"""
