"""Central-authoritative event program domain helpers."""

import re
import unicodedata
from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from upm_central.event_deployments import push_deployment
from upm_central.persistence.models import (
    AuditRecord,
    Event,
    EventDeployment,
    EventParticipation,
    ExternalIdentifier,
    Person,
    Presentation,
    PresentationPresenter,
    PresentationSession,
    SessionParticipant,
    utc_now,
)
from upm_central.persistence.models import Session as ProgramSession
from upm_shared.enums import EventDeploymentStatus, ExternalEntityType, ExternalIdentifierScope


def normalize_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = " ".join(unicodedata.normalize("NFKC", value).strip().split()).casefold()
    return normalized or None


def normalize_email(value: str | None) -> str | None:
    normalized = normalize_text(value)
    return (
        normalized if normalized and re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", normalized) else None
    )


def validate_timezone(value: str) -> str:
    try:
        ZoneInfo(value)
    except ZoneInfoNotFoundError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, detail="invalid IANA timezone"
        ) from exc
    return value


def require_aware(value: datetime | None, field_name: str) -> None:
    if value is not None and value.utcoffset() is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"{field_name} must include a UTC offset",
        )


def audit(
    session: Session,
    *,
    action: str,
    target_type: str,
    target_id: UUID | None,
    event_id: UUID | None = None,
    before: dict[str, object] | None = None,
    after: dict[str, object] | None = None,
    actor: str = "central-admin",
) -> None:
    session.add(
        AuditRecord(
            actor_id=actor,
            action=action,
            target_type=target_type,
            target_id=target_id,
            event_id=event_id,
            before_context=before,
            after_context=after,
        )
    )


def touch_event_program(session: Session, event: Event) -> list[UUID]:
    """Advance the event domain revision and every active ADR-0007 deployment once."""
    event.revision += 1
    event.updated_at = utc_now()
    session.flush()
    deployment_ids: list[UUID] = []
    deployments = session.scalars(
        select(EventDeployment).where(
            EventDeployment.event_id == event.event_id,
            EventDeployment.status.notin_(
                [EventDeploymentStatus.REVOKED, EventDeploymentStatus.ARCHIVED]
            ),
        )
    ).all()
    for deployment in deployments:
        push_deployment(session, deployment)
        deployment_ids.append(deployment.deployment_id)
    return deployment_ids


def external_identifier(
    session: Session,
    *,
    entity_type: ExternalEntityType,
    entity_id: UUID,
    namespace: str,
    value: str,
    event_id: UUID | None,
    source: str | None = None,
) -> ExternalIdentifier:
    namespace_normalized = normalize_text(namespace)
    value_normalized = normalize_text(value)
    if not namespace_normalized or not value_normalized:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, detail="invalid external identifier"
        )
    model_by_type = {
        ExternalEntityType.PERSON: Person,
        ExternalEntityType.EVENT_PARTICIPATION: EventParticipation,
        ExternalEntityType.SESSION: ProgramSession,
        ExternalEntityType.SESSION_PRESENTER: SessionParticipant,
        ExternalEntityType.PRESENTATION: Presentation,
        ExternalEntityType.PRESENTATION_SESSION: PresentationSession,
        ExternalEntityType.PRESENTATION_PRESENTER: PresentationPresenter,
    }
    if session.get(model_by_type[entity_type], entity_id) is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail="external identifier entity not found"
        )
    scope = ExternalIdentifierScope.EVENT if event_id else ExternalIdentifierScope.GLOBAL
    scope_key = str(event_id) if event_id else "global"
    existing = session.scalar(
        select(ExternalIdentifier).where(
            ExternalIdentifier.namespace == namespace_normalized,
            ExternalIdentifier.normalized_external_id == value_normalized,
            ExternalIdentifier.scope_key == scope_key,
        )
    )
    if existing is not None:
        if existing.entity_type != entity_type or existing.entity_id != entity_id:
            raise HTTPException(
                status.HTTP_409_CONFLICT, detail="external identifier is already mapped"
            )
        return existing
    item = ExternalIdentifier(
        entity_type=entity_type,
        entity_id=entity_id,
        namespace=namespace_normalized,
        external_id=value.strip(),
        normalized_external_id=value_normalized,
        scope=scope,
        scope_key=scope_key,
        event_id=event_id,
        source=source,
    )
    session.add(item)
    return item


def person_impact(session: Session, person_id: UUID) -> dict[str, int]:
    participation_ids = select(EventParticipation.event_participation_id).where(
        EventParticipation.person_id == person_id
    )
    return {
        "event_participations": session.scalar(
            select(func.count())
            .select_from(EventParticipation)
            .where(EventParticipation.person_id == person_id)
        )
        or 0,
        "session_presenter_relationships": session.scalar(
            select(func.count())
            .select_from(SessionParticipant)
            .where(SessionParticipant.event_participation_id.in_(participation_ids))
        )
        or 0,
        "presentation_presenter_relationships": session.scalar(
            select(func.count())
            .select_from(PresentationPresenter)
            .where(PresentationPresenter.event_participation_id.in_(participation_ids))
        )
        or 0,
    }


def entity_event_ids(
    session: Session,
    entity_type: ExternalEntityType,
    entity_id: UUID,
    explicit_event_id: UUID | None = None,
) -> set[UUID]:
    if explicit_event_id:
        return {explicit_event_id}
    if entity_type == ExternalEntityType.PERSON:
        return set(
            session.scalars(
                select(EventParticipation.event_id).where(EventParticipation.person_id == entity_id)
            )
        )
    if entity_type == ExternalEntityType.EVENT_PARTICIPATION:
        item = session.get(EventParticipation, entity_id)
        return {item.event_id} if item else set()
    if entity_type == ExternalEntityType.SESSION:
        item = session.get(ProgramSession, entity_id)
        return {item.event_id} if item else set()
    if entity_type == ExternalEntityType.SESSION_PRESENTER:
        link = session.get(SessionParticipant, entity_id)
        item = session.get(ProgramSession, link.session_id) if link else None
        return {item.event_id} if item else set()
    if entity_type == ExternalEntityType.PRESENTATION:
        item = session.get(Presentation, entity_id)
        return {item.event_id} if item else set()
    if entity_type == ExternalEntityType.PRESENTATION_SESSION:
        link = session.get(PresentationSession, entity_id)
        item = session.get(Presentation, link.presentation_id) if link else None
        return {item.event_id} if item else set()
    link = session.get(PresentationPresenter, entity_id)
    item = session.get(Presentation, link.presentation_id) if link else None
    return {item.event_id} if item else set()


def delete_person(session: Session, person: Person, *, confirmation: str, actor: str) -> None:
    if confirmation != str(person.person_id):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, detail="person UUID confirmation required"
        )
    impact = person_impact(session, person.person_id)
    if any(impact.values()):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={"message": "person has retained dependencies", "impact": impact},
        )
    before = {"display_name": person.display_name, "active": person.active}
    person.active = False
    person.deleted_at = utc_now()
    person.revision += 1
    audit(
        session,
        action="central.person.deleted",
        target_type="person",
        target_id=person.person_id,
        before=before,
        after={"active": False, "deleted_at": person.deleted_at.isoformat()},
        actor=actor,
    )


def assert_same_event(session: Session, event_id: UUID, *entity_ids: tuple[str, UUID]) -> None:
    model_by_name = {
        "participation": EventParticipation,
        "presentation": Presentation,
        "presentation_session": PresentationSession,
    }
    for name, entity_id in entity_ids:
        model = model_by_name[name]
        entity = session.get(model, entity_id)
        if entity is None or getattr(entity, "event_id", event_id) != event_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"{name} not found in event")
