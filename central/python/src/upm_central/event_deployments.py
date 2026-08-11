"""Central-owned event deployment lifecycle, snapshot generation, and outbox writes."""

from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from upm_central.persistence.models import (
    AuditRecord,
    Event,
    EventDeployment,
    EventDeploymentRevision,
    EventParticipation,
    ExternalIdentifier,
    Presentation,
    Site,
    SiteRoomMapping,
    utc_now,
)
from upm_central.persistence.models import (
    Session as CentralSession,
)
from upm_central.persistence.queue import CentralQueue
from upm_central.sync import next_sequence
from upm_shared.contracts.deployments import (
    EVENT_DEPLOYMENT_SCHEMA_VERSION,
    DeploymentRevocation,
    EventDeploymentSnapshot,
    ExternalIdentifierSnapshot,
    ParticipationSnapshot,
    PersonProfile,
    PresentationPresenterSnapshot,
    PresentationSessionSnapshot,
    PresentationSnapshot,
    SessionParticipantSnapshot,
    SessionSnapshot,
)
from upm_shared.contracts.sync import UPM_SYNC_PROTOCOL_VERSION
from upm_shared.enums import EnrollmentState, EventDeploymentStatus, SourceSystem
from upm_shared.identifiers import new_uuid7
from upm_shared.jobs import OutboxPayload

DEPLOYABLE_STATUSES = {
    EventDeploymentStatus.DRAFT,
    EventDeploymentStatus.PENDING,
    EventDeploymentStatus.DEPLOYING,
    EventDeploymentStatus.DEPLOYED,
    EventDeploymentStatus.UPDATE_PENDING,
    EventDeploymentStatus.FAILED,
}

ALLOWED_TRANSITIONS = {
    EventDeploymentStatus.DRAFT: {EventDeploymentStatus.PENDING, EventDeploymentStatus.ARCHIVED},
    EventDeploymentStatus.PENDING: {
        EventDeploymentStatus.DEPLOYING,
        EventDeploymentStatus.DEPLOYED,
        EventDeploymentStatus.UPDATE_PENDING,
        EventDeploymentStatus.FAILED,
        EventDeploymentStatus.REVOKED,
    },
    EventDeploymentStatus.DEPLOYING: {
        EventDeploymentStatus.DEPLOYED,
        EventDeploymentStatus.UPDATE_PENDING,
        EventDeploymentStatus.FAILED,
        EventDeploymentStatus.REVOKED,
    },
    EventDeploymentStatus.DEPLOYED: {
        EventDeploymentStatus.UPDATE_PENDING,
        EventDeploymentStatus.REVOKED,
        EventDeploymentStatus.ARCHIVED,
    },
    EventDeploymentStatus.UPDATE_PENDING: {
        EventDeploymentStatus.DEPLOYING,
        EventDeploymentStatus.DEPLOYED,
        EventDeploymentStatus.FAILED,
        EventDeploymentStatus.REVOKED,
    },
    EventDeploymentStatus.FAILED: {
        EventDeploymentStatus.PENDING,
        EventDeploymentStatus.UPDATE_PENDING,
        EventDeploymentStatus.REVOKED,
        EventDeploymentStatus.ARCHIVED,
    },
    EventDeploymentStatus.REVOKED: {
        EventDeploymentStatus.PENDING,
        EventDeploymentStatus.ARCHIVED,
    },
    EventDeploymentStatus.ARCHIVED: set(),
}


def transition(deployment: EventDeployment, target: EventDeploymentStatus) -> None:
    if target == deployment.status:
        return
    if target not in ALLOWED_TRANSITIONS[deployment.status]:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"invalid deployment transition {deployment.status} -> {target}",
        )
    deployment.status = target


def load_event(session: Session, event_id: UUID) -> Event:
    event = session.scalar(
        select(Event)
        .where(Event.event_id == event_id)
        .options(
            selectinload(Event.participations).selectinload(EventParticipation.person),
            selectinload(Event.sessions).selectinload(CentralSession.participants),
        )
    )
    if event is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="event not found")
    return event


def build_snapshot(
    session: Session, deployment: EventDeployment, revision: int
) -> EventDeploymentSnapshot:
    event = load_event(session, deployment.event_id)
    presentations = session.scalars(
        select(Presentation)
        .where(Presentation.event_id == event.event_id)
        .options(
            selectinload(Presentation.versions),
            selectinload(Presentation.session_links),
            selectinload(Presentation.presenter_links),
        )
        .order_by(Presentation.presentation_id)
    ).all()
    participations = sorted(event.participations, key=lambda item: str(item.event_participation_id))
    entity_ids = {
        event.event_id,
        *(item.person_id for item in participations),
        *(item.event_participation_id for item in participations),
        *(item.session_id for item in event.sessions),
        *(item.presentation_id for item in presentations),
    }
    external_identifiers = session.scalars(
        select(ExternalIdentifier)
        .where(
            or_(
                ExternalIdentifier.event_id == event.event_id,
                ExternalIdentifier.entity_id.in_(entity_ids),
            )
        )
        .order_by(ExternalIdentifier.external_identifier_id)
    ).all()
    room_mappings = session.scalars(
        select(SiteRoomMapping).where(SiteRoomMapping.site_id == deployment.site_id)
    ).all()
    return EventDeploymentSnapshot(
        deployment_id=deployment.deployment_id,
        deployment_revision=revision,
        event_id=event.event_id,
        site_id=deployment.site_id,
        event_name=event.name,
        event_description=event.description,
        starts_at=event.starts_at,
        ends_at=event.ends_at,
        timezone=event.timezone,
        room_configuration={
            "mappings": [
                {
                    "imported_label": item.imported_label,
                    "normalized_imported_label": item.normalized_imported_label,
                    "mapping_status": item.mapping_status,
                    "target_room_id": str(item.target_room_id) if item.target_room_id else None,
                    "target_room_label": item.target_room_label,
                }
                for item in room_mappings
            ]
        },
        people=[
            PersonProfile(
                person_id=item.person.person_id,
                display_name=item.person.display_name,
                given_name=item.person.given_name,
                family_name=item.person.family_name,
                primary_email=item.person.primary_email,
                organization=item.person.organization,
                central_revision=item.person.revision,
            )
            for item in participations
        ],
        participations=[
            ParticipationSnapshot(
                event_participation_id=item.event_participation_id,
                person_id=item.person_id,
                role=item.role,
                display_name=item.display_name,
                professional_title=item.professional_title,
                organization=item.organization,
                participant_status=item.participant_status,
                is_presenter=item.is_presenter,
                central_revision=item.revision,
            )
            for item in participations
        ],
        sessions=[
            SessionSnapshot(
                session_id=item.session_id,
                title=item.title,
                subtitle=item.subtitle,
                description=item.description,
                session_code=item.session_code,
                session_type=item.session_type,
                starts_at=item.starts_at,
                ends_at=item.ends_at,
                location_name=item.location_name,
                status=item.status,
                sort_order=item.sort_order,
                central_revision=item.revision,
                participants=[
                    SessionParticipantSnapshot(
                        session_participant_id=participant.session_participant_id,
                        event_participation_id=participant.event_participation_id,
                        role=participant.role,
                        presenter_order=participant.presenter_order,
                        primary_presenter=participant.primary_presenter,
                        central_revision=participant.revision,
                    )
                    for participant in sorted(
                        item.participants, key=lambda row: str(row.session_participant_id)
                    )
                ],
            )
            for item in sorted(event.sessions, key=lambda row: str(row.session_id))
        ],
        presentations=[
            PresentationSnapshot(
                presentation_id=item.presentation_id,
                session_id=item.session_id,
                title=item.title,
                description=item.description,
                presentation_code=item.presentation_code,
                workflow_status=item.workflow_status,
                processing_status=item.processing_status,
                scheduled_at=item.scheduled_at,
                central_revision=item.revision,
                version_numbers=sorted(version.version_number for version in item.versions),
                sessions=[
                    PresentationSessionSnapshot(
                        presentation_session_id=link.presentation_session_id,
                        session_id=link.session_id,
                        association_type=link.association_type,
                        sort_order=link.sort_order,
                        primary_session=link.primary_session,
                        central_revision=link.revision,
                    )
                    for link in sorted(
                        item.session_links, key=lambda row: str(row.presentation_session_id)
                    )
                ],
                presenters=[
                    PresentationPresenterSnapshot(
                        presentation_presenter_id=link.presentation_presenter_id,
                        event_participation_id=link.event_participation_id,
                        role=link.role,
                        presenter_order=link.presenter_order,
                        primary_presenter=link.primary_presenter,
                        central_revision=link.revision,
                    )
                    for link in sorted(
                        item.presenter_links, key=lambda row: str(row.presentation_presenter_id)
                    )
                ],
            )
            for item in presentations
        ],
        external_identifiers=[
            ExternalIdentifierSnapshot(
                external_identifier_id=item.external_identifier_id,
                entity_type=item.entity_type,
                entity_id=item.entity_id,
                namespace=item.namespace,
                external_id=item.external_id,
                central_revision=item.revision,
            )
            for item in external_identifiers
        ],
    )


def _enqueue(
    session: Session,
    deployment: EventDeployment,
    *,
    event_type: str,
    payload: dict[str, object],
    revision: int,
    causation_id: UUID | None = None,
    retry_id: UUID | None = None,
):
    return CentralQueue(session).enqueue_outbox(
        event_type=event_type,
        aggregate_type="event_deployment",
        aggregate_id=deployment.deployment_id,
        event_id=deployment.event_id,
        owning_site_id=deployment.site_id,
        source_sequence=next_sequence(session, deployment.site_id),
        protocol_version=UPM_SYNC_PROTOCOL_VERSION,
        correlation_id=deployment.deployment_id,
        causation_id=causation_id,
        idempotency_key=(
            f"deployment:{deployment.deployment_id}:{revision}:{event_type}"
            + (f":retry:{retry_id}" if retry_id else "")
        ),
        payload=OutboxPayload(
            source_system=SourceSystem.CENTRAL,
            schema_version=EVENT_DEPLOYMENT_SCHEMA_VERSION,
            data=payload,
        ),
    )


def push_deployment(
    session: Session, deployment: EventDeployment, *, initial: bool = False
) -> EventDeployment:
    if deployment.status not in DEPLOYABLE_STATUSES:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="deployment cannot be pushed")
    revision = deployment.desired_revision + 1
    snapshot = build_snapshot(session, deployment, revision)
    event_type = (
        "central.event_deployment.requested"
        if initial or deployment.desired_revision == 0
        else "central.event_deployment.updated"
    )
    payload = snapshot.model_dump(mode="json")
    session.add(
        EventDeploymentRevision(
            deployment_id=deployment.deployment_id,
            deployment_revision=revision,
            event_type=event_type,
            schema_version=EVENT_DEPLOYMENT_SCHEMA_VERSION,
            snapshot=payload,
        )
    )
    target = (
        EventDeploymentStatus.PENDING
        if deployment.desired_revision == 0
        else EventDeploymentStatus.UPDATE_PENDING
    )
    transition(deployment, target)
    deployment.desired_revision = revision
    deployment.deployment_requested_at = utc_now()
    deployment.failure_at = None
    deployment.failure_reason = None
    _enqueue(session, deployment, event_type=event_type, payload=payload, revision=revision)
    session.add(
        AuditRecord(
            actor_id="central-admin",
            action=event_type,
            target_type="event_deployment",
            target_id=deployment.deployment_id,
            site_id=deployment.site_id,
            event_id=deployment.event_id,
            after_context={"desired_revision": revision},
        )
    )
    return deployment


def create_deployment(session: Session, event_id: UUID, site_id: UUID) -> EventDeployment:
    load_event(session, event_id)
    site = session.get(Site, site_id)
    if site is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="site not found")
    if not site.enabled or site.enrollment_state != EnrollmentState.ACTIVE:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="site is not active")
    existing = session.scalar(
        select(EventDeployment).where(
            EventDeployment.event_id == event_id, EventDeployment.site_id == site_id
        )
    )
    if existing is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="event is already assigned to site")
    deployment = EventDeployment(event_id=event_id, site_id=site_id)
    session.add(deployment)
    session.flush()
    return push_deployment(session, deployment, initial=True)


def retry_deployment(session: Session, deployment: EventDeployment) -> EventDeployment:
    if deployment.status != EventDeploymentStatus.FAILED:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="only failed deployments can retry")
    revision = session.scalar(
        select(EventDeploymentRevision).where(
            EventDeploymentRevision.deployment_id == deployment.deployment_id,
            EventDeploymentRevision.deployment_revision == deployment.desired_revision,
        )
    )
    if revision is None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="deployment revision is missing")
    target = (
        EventDeploymentStatus.PENDING
        if deployment.acknowledged_revision == 0
        else EventDeploymentStatus.UPDATE_PENDING
    )
    transition(deployment, target)
    deployment.failure_at = None
    deployment.failure_reason = None
    _enqueue(
        session,
        deployment,
        event_type=revision.event_type,
        payload=revision.snapshot,
        revision=revision.deployment_revision,
        retry_id=new_uuid7(),
    )
    session.add(
        AuditRecord(
            actor_id="central-admin",
            action="central.event_deployment.retried",
            target_type="event_deployment",
            target_id=deployment.deployment_id,
            site_id=deployment.site_id,
            event_id=deployment.event_id,
            after_context={"desired_revision": deployment.desired_revision},
        )
    )
    return deployment


def revoke_deployment(
    session: Session, deployment: EventDeployment, reason: str | None
) -> EventDeployment:
    transition(deployment, EventDeploymentStatus.REVOKED)
    revision = deployment.desired_revision + 1
    payload = DeploymentRevocation(
        deployment_id=deployment.deployment_id,
        deployment_revision=revision,
        event_id=deployment.event_id,
        site_id=deployment.site_id,
        reason=reason,
    ).model_dump(mode="json")
    session.add(
        EventDeploymentRevision(
            deployment_id=deployment.deployment_id,
            deployment_revision=revision,
            event_type="central.event_deployment.revoked",
            schema_version=EVENT_DEPLOYMENT_SCHEMA_VERSION,
            snapshot=payload,
        )
    )
    deployment.desired_revision = revision
    deployment.deployment_requested_at = utc_now()
    _enqueue(
        session,
        deployment,
        event_type="central.event_deployment.revoked",
        payload=payload,
        revision=revision,
    )
    session.add(
        AuditRecord(
            actor_id="central-admin",
            action="central.event_deployment.revoked",
            target_type="event_deployment",
            target_id=deployment.deployment_id,
            site_id=deployment.site_id,
            event_id=deployment.event_id,
            after_context={"desired_revision": revision, "reason": reason},
        )
    )
    return deployment
