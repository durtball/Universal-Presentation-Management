"""Transactional application of Central event deployment snapshots at a Site."""

from datetime import UTC, datetime
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from upm_shared.contracts.deployments import (
    EVENT_DEPLOYMENT_SCHEMA_VERSION,
    DeploymentRevocation,
    EventDeploymentSnapshot,
    SiteDeploymentStatus,
)
from upm_shared.contracts.sync import SyncEventEnvelope
from upm_shared.enums import EventDeploymentStatus, SourceSystem, SyncState
from upm_shared.jobs import OutboxPayload
from upm_site.persistence.models import (
    Event,
    EventDeploymentProjection,
    EventDeploymentRevisionProjection,
    EventParticipation,
    LocalSiteIdentity,
    OutboxEvent,
    PersonProjection,
    Presentation,
    PresentationPresenter,
    PresentationVersion,
    SessionParticipant,
    utc_now,
)
from upm_site.persistence.models import (
    Session as SiteSession,
)
from upm_site.persistence.queue import SiteQueue


def _status_event(
    session: Session,
    deployment: EventDeploymentProjection,
    status_value: str,
    *,
    causation_id: UUID,
    failure_reason: str | None = None,
):
    from upm_site.sync import next_sequence

    idempotency_key = (
        f"deployment-status:{deployment.deployment_id}:{deployment.desired_revision}:"
        f"{deployment.applied_revision}:{status_value}"
    )
    existing = session.scalar(
        select(OutboxEvent).where(
            OutboxEvent.source_system == SourceSystem.SITE,
            OutboxEvent.idempotency_key == idempotency_key,
        )
    )
    if existing is not None:
        return existing
    payload = SiteDeploymentStatus(
        deployment_id=deployment.deployment_id,
        event_id=deployment.central_event_id,
        site_id=deployment.site_id,
        desired_revision=deployment.desired_revision,
        applied_revision=deployment.applied_revision,
        status=status_value,
        failure_reason=failure_reason,
        summary_counts={key: int(value) for key, value in deployment.summary_counts.items()},
        observed_at=datetime.now(UTC),
    )
    sequence = next_sequence(session)
    return SiteQueue(session).enqueue_outbox(
        event_type=f"site.event_deployment.{status_value}",
        aggregate_type="event_deployment",
        aggregate_id=deployment.deployment_id,
        event_id=deployment.central_event_id,
        site_id=deployment.site_id,
        source_sequence=sequence,
        correlation_id=deployment.deployment_id,
        causation_id=causation_id,
        idempotency_key=idempotency_key,
        payload=OutboxPayload(
            source_system=SourceSystem.SITE,
            schema_version=EVENT_DEPLOYMENT_SCHEMA_VERSION,
            data=payload.model_dump(mode="json"),
        ),
    )


def _site_id(session: Session) -> UUID:
    identity = session.scalar(select(LocalSiteIdentity).where(LocalSiteIdentity.singleton_key == 1))
    if identity is None:
        raise ValueError("Site identity has not been initialized")
    return identity.site_id


def _upsert_snapshot(session: Session, snapshot: EventDeploymentSnapshot) -> dict[str, int]:
    event = session.get(Event, snapshot.event_id)
    if event is None:
        event = Event(
            event_id=snapshot.event_id,
            site_id=snapshot.site_id,
            name=snapshot.event_name,
            starts_at=snapshot.starts_at,
            ends_at=snapshot.ends_at,
            sync_state=SyncState.SYNCHRONIZED,
        )
        session.add(event)
    else:
        if event.site_id != snapshot.site_id:
            raise ValueError("event belongs to another Site")
        event.name = snapshot.event_name
        event.starts_at = snapshot.starts_at
        event.ends_at = snapshot.ends_at
        event.sync_state = SyncState.SYNCHRONIZED
        event.revision = max(event.revision, snapshot.deployment_revision)
    for profile in snapshot.people:
        person = session.get(PersonProjection, profile.person_id)
        if person is None:
            person = PersonProjection(
                person_id=profile.person_id,
                display_name=profile.display_name,
                primary_email=profile.primary_email,
                organization=profile.organization,
                central_revision=profile.central_revision,
                sync_state=SyncState.SYNCHRONIZED,
            )
            session.add(person)
        elif profile.central_revision >= person.central_revision:
            person.display_name = profile.display_name
            person.primary_email = profile.primary_email
            person.organization = profile.organization
            person.central_revision = profile.central_revision
            person.sync_state = SyncState.SYNCHRONIZED
    session.flush()
    for item in snapshot.participations:
        participation = session.get(EventParticipation, item.event_participation_id)
        if participation is None:
            participation = EventParticipation(
                event_participation_id=item.event_participation_id,
                event_id=snapshot.event_id,
                person_id=item.person_id,
                role=item.role,
                revision=item.central_revision,
                sync_state=SyncState.SYNCHRONIZED,
            )
            session.add(participation)
        else:
            participation.role = item.role
            participation.revision = max(participation.revision, item.central_revision)
            participation.sync_state = SyncState.SYNCHRONIZED
    for item in snapshot.sessions:
        local = session.get(SiteSession, item.session_id)
        if local is None:
            local = SiteSession(
                session_id=item.session_id,
                event_id=snapshot.event_id,
                title=item.title,
                starts_at=item.starts_at,
                ends_at=item.ends_at,
                revision=item.central_revision,
                sync_state=SyncState.SYNCHRONIZED,
            )
            session.add(local)
        else:
            local.title = item.title
            local.starts_at = item.starts_at
            local.ends_at = item.ends_at
            local.revision = max(local.revision, item.central_revision)
            local.sync_state = SyncState.SYNCHRONIZED
    session.flush()
    for item in snapshot.sessions:
        for participant in item.participants:
            local = session.get(SessionParticipant, participant.session_participant_id)
            if local is None:
                session.add(
                    SessionParticipant(
                        session_participant_id=participant.session_participant_id,
                        session_id=item.session_id,
                        event_participation_id=participant.event_participation_id,
                        role=participant.role,
                        revision=participant.central_revision,
                        sync_state=SyncState.SYNCHRONIZED,
                    )
                )
            else:
                local.role = participant.role
                local.revision = max(local.revision, participant.central_revision)
                local.sync_state = SyncState.SYNCHRONIZED
    for item in snapshot.presentations:
        presentation = session.get(Presentation, item.presentation_id)
        if presentation is None:
            presentation = Presentation(
                presentation_id=item.presentation_id,
                event_id=snapshot.event_id,
                session_id=item.session_id,
                title=item.title,
                revision=item.central_revision,
                sync_state=SyncState.SYNCHRONIZED,
            )
            session.add(presentation)
        else:
            presentation.session_id = item.session_id
            presentation.title = item.title
            presentation.revision = max(presentation.revision, item.central_revision)
            presentation.sync_state = SyncState.SYNCHRONIZED
    session.flush()
    for item in snapshot.presentations:
        existing_versions = set(
            session.scalars(
                select(PresentationVersion.version_number).where(
                    PresentationVersion.presentation_id == item.presentation_id
                )
            )
        )
        for version_number in item.version_numbers:
            if version_number not in existing_versions:
                session.add(
                    PresentationVersion(
                        presentation_id=item.presentation_id,
                        version_number=version_number,
                        sync_state=SyncState.SYNCHRONIZED,
                    )
                )
        for participant_id in item.presenter_session_participant_ids:
            key = (item.presentation_id, participant_id)
            if session.get(PresentationPresenter, key) is None:
                session.add(
                    PresentationPresenter(
                        presentation_id=item.presentation_id,
                        session_participant_id=participant_id,
                    )
                )
    return {
        "presenters": len(snapshot.people),
        "sessions": len(snapshot.sessions),
        "presentations": len(snapshot.presentations),
        "rooms": 0,
    }


def apply_snapshot_event(session: Session, event: SyncEventEnvelope) -> str:
    if event.payload_schema_version != EVENT_DEPLOYMENT_SCHEMA_VERSION:
        raise ValueError("unsupported deployment payload schema version")
    try:
        snapshot = EventDeploymentSnapshot.model_validate(event.payload)
    except ValidationError as exc:
        raise ValueError(f"malformed deployment snapshot: {exc.errors()[0]['msg']}") from exc
    if snapshot.site_id != _site_id(session):
        raise PermissionError("deployment is addressed to another Site")
    if event.entity_id != snapshot.deployment_id:
        raise ValueError("deployment identity does not match event envelope")
    deployment = session.get(EventDeploymentProjection, snapshot.deployment_id)
    if deployment is not None and deployment.central_event_id != snapshot.event_id:
        raise ValueError("deployment Event UUID cannot change")
    if deployment is not None and snapshot.deployment_revision <= deployment.applied_revision:
        deployment.desired_revision = max(deployment.desired_revision, snapshot.deployment_revision)
        deployment.last_central_synchronization_at = utc_now()
        status_value = (
            "applied" if snapshot.deployment_revision == deployment.applied_revision else "stale"
        )
        _status_event(session, deployment, status_value, causation_id=event.event_id)
        return status_value
    counts = _upsert_snapshot(session, snapshot)
    session.flush()
    if deployment is None:
        deployment = EventDeploymentProjection(
            deployment_id=snapshot.deployment_id,
            central_event_id=snapshot.event_id,
            site_id=snapshot.site_id,
            status=EventDeploymentStatus.DEPLOYED,
            desired_revision=snapshot.deployment_revision,
            applied_revision=snapshot.deployment_revision,
        )
        session.add(deployment)
    deployment.status = EventDeploymentStatus.DEPLOYED
    deployment.desired_revision = snapshot.deployment_revision
    deployment.applied_revision = snapshot.deployment_revision
    deployment.last_central_synchronization_at = utc_now()
    deployment.applied_at = utc_now()
    deployment.failure_at = None
    deployment.failure_reason = None
    deployment.current_snapshot = snapshot.model_dump(mode="json")
    deployment.summary_counts = counts
    session.add(
        EventDeploymentRevisionProjection(
            deployment_id=snapshot.deployment_id,
            deployment_revision=snapshot.deployment_revision,
            event_type=event.event_type,
            schema_version=snapshot.schema_version,
            snapshot=snapshot.model_dump(mode="json"),
        )
    )
    session.flush()
    _status_event(session, deployment, "received", causation_id=event.event_id)
    _status_event(session, deployment, "applied", causation_id=event.event_id)
    return "applied"


def apply_revocation_event(session: Session, event: SyncEventEnvelope) -> str:
    if event.payload_schema_version != EVENT_DEPLOYMENT_SCHEMA_VERSION:
        raise ValueError("unsupported deployment payload schema version")
    revocation = DeploymentRevocation.model_validate(event.payload)
    if revocation.site_id != _site_id(session):
        raise PermissionError("deployment is addressed to another Site")
    if event.entity_id != revocation.deployment_id:
        raise ValueError("deployment identity does not match event envelope")
    deployment = session.get(EventDeploymentProjection, revocation.deployment_id)
    if deployment is None or deployment.central_event_id != revocation.event_id:
        raise ValueError("cannot revoke an unknown deployment")
    deployment.desired_revision = max(deployment.desired_revision, revocation.deployment_revision)
    deployment.applied_revision = max(deployment.applied_revision, revocation.deployment_revision)
    deployment.status = EventDeploymentStatus.REVOKED
    deployment.revoked_at = utc_now()
    deployment.last_central_synchronization_at = utc_now()
    session.add(
        EventDeploymentRevisionProjection(
            deployment_id=revocation.deployment_id,
            deployment_revision=revocation.deployment_revision,
            event_type=event.event_type,
            schema_version=revocation.schema_version,
            snapshot=revocation.model_dump(mode="json"),
        )
    )
    _status_event(session, deployment, "revoked", causation_id=event.event_id)
    return "revoked"
