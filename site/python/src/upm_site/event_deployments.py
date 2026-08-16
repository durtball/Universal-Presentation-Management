"""Transactional application of Central event deployment snapshots at a Site."""
# ruff: noqa: E501

from datetime import UTC, datetime
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import delete, select, text, update
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
from upm_shared.presentation_media import allocate_presentation_identifier
from upm_site.persistence.models import (
    Event,
    EventDeploymentProjection,
    EventDeploymentRevisionProjection,
    EventParticipation,
    ExternalIdentifierProjection,
    LocalSiteIdentity,
    OutboxEvent,
    PersonProjection,
    Presentation,
    PresentationPresenter,
    PresentationSession,
    PresentationVersion,
    Room,
    RoomAssignment,
    SessionParticipant,
    utc_now,
)
from upm_site.persistence.models import (
    Session as SiteSession,
)
from upm_site.persistence.queue import SiteQueue
from upm_site.room_operations import (
    materialize_program_room_mappings,
    reconcile_program_room_assignments,
)


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
            description=snapshot.event_description,
            timezone=snapshot.timezone or "UTC",
            starts_at=snapshot.starts_at,
            ends_at=snapshot.ends_at,
            sync_state=SyncState.SYNCHRONIZED,
        )
        session.add(event)
    else:
        if event.site_id != snapshot.site_id:
            raise ValueError("event belongs to another Site")
        event.name = snapshot.event_name
        event.description = snapshot.event_description
        event.timezone = snapshot.timezone or "UTC"
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
                given_name=profile.given_name,
                family_name=profile.family_name,
                primary_email=profile.primary_email,
                organization=profile.organization,
                central_revision=profile.central_revision,
                sync_state=SyncState.SYNCHRONIZED,
            )
            session.add(person)
        elif profile.central_revision >= person.central_revision:
            person.display_name = profile.display_name
            person.given_name = profile.given_name
            person.family_name = profile.family_name
            person.primary_email = profile.primary_email
            person.organization = profile.organization
            person.central_revision = profile.central_revision
            person.sync_state = SyncState.SYNCHRONIZED
    session.flush()
    session.execute(
        update(EventParticipation)
        .where(EventParticipation.event_id == snapshot.event_id)
        .values(active=False)
    )
    session.execute(
        update(SiteSession).where(SiteSession.event_id == snapshot.event_id).values(active=False)
    )
    session.execute(
        update(Presentation).where(Presentation.event_id == snapshot.event_id).values(active=False)
    )
    session.execute(
        update(SessionParticipant)
        .where(
            SessionParticipant.session_id.in_(
                select(SiteSession.session_id).where(SiteSession.event_id == snapshot.event_id)
            )
        )
        .values(active=False)
    )
    session.execute(
        update(PresentationSession)
        .where(
            PresentationSession.presentation_id.in_(
                select(Presentation.presentation_id).where(
                    Presentation.event_id == snapshot.event_id
                )
            )
        )
        .values(active=False)
    )
    session.execute(
        update(PresentationPresenter)
        .where(
            PresentationPresenter.presentation_id.in_(
                select(Presentation.presentation_id).where(
                    Presentation.event_id == snapshot.event_id
                )
            )
        )
        .values(active=False)
    )
    session.execute(
        update(ExternalIdentifierProjection)
        .where(ExternalIdentifierProjection.event_id == snapshot.event_id)
        .values(active=False)
    )
    for item in snapshot.participations:
        participation = session.get(EventParticipation, item.event_participation_id)
        if participation is None:
            participation = EventParticipation(
                event_participation_id=item.event_participation_id,
                event_id=snapshot.event_id,
                person_id=item.person_id,
                role=item.role,
                display_name=item.display_name,
                professional_title=item.professional_title,
                organization=item.organization,
                participant_status=item.participant_status,
                is_presenter=item.is_presenter,
                active=True,
                revision=item.central_revision,
                sync_state=SyncState.SYNCHRONIZED,
            )
            session.add(participation)
        else:
            participation.role = item.role
            participation.display_name = item.display_name
            participation.professional_title = item.professional_title
            participation.organization = item.organization
            participation.participant_status = item.participant_status
            participation.is_presenter = item.is_presenter
            participation.active = True
            participation.revision = max(participation.revision, item.central_revision)
            participation.sync_state = SyncState.SYNCHRONIZED
    for item in snapshot.sessions:
        local = session.get(SiteSession, item.session_id)
        if local is None:
            local = SiteSession(
                session_id=item.session_id,
                event_id=snapshot.event_id,
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
                active=True,
                revision=item.central_revision,
                sync_state=SyncState.SYNCHRONIZED,
            )
            session.add(local)
        else:
            local.title = item.title
            local.subtitle = item.subtitle
            local.description = item.description
            local.session_code = item.session_code
            local.session_type = item.session_type
            local.starts_at = item.starts_at
            local.ends_at = item.ends_at
            local.location_name = item.location_name
            local.status = item.status
            local.sort_order = item.sort_order
            local.active = True
            local.revision = max(local.revision, item.central_revision)
            local.sync_state = SyncState.SYNCHRONIZED
    session.flush()
    room_mappings = {
        str(mapping.get("normalized_imported_label") or ""): mapping
        for mapping in snapshot.room_configuration.get("mappings", [])
        if isinstance(mapping, dict)
    }
    centrally_mapped_labels = {
        label
        for label, mapping in room_mappings.items()
        if mapping.get("mapping_status") == "mapped"
    }
    materialized_rooms = materialize_program_room_mappings(
        session,
        snapshot.event_id,
        excluded_labels=centrally_mapped_labels,
    )
    mapped_rooms = 0
    unresolved_rooms = 0
    room_conflicts = 0
    for item in snapshot.sessions:
        if not item.location_name:
            continue
        normalized_label = " ".join(item.location_name.strip().casefold().split())
        mapping = room_mappings.get(normalized_label)
        if not mapping or mapping.get("mapping_status") != "mapped":
            unresolved_rooms += 1
            continue
        try:
            room_id = UUID(str(mapping.get("target_room_id")))
        except (TypeError, ValueError):
            unresolved_rooms += 1
            continue
        room = session.get(Room, room_id)
        if room is None or room.site_id != snapshot.site_id:
            unresolved_rooms += 1
            continue
        assignment = session.scalar(
            select(RoomAssignment).where(
                RoomAssignment.session_id == item.session_id,
                RoomAssignment.active.is_(True),
            )
        )
        if assignment and assignment.room_id != room_id:
            room_conflicts += 1
            continue
        if assignment is None:
            session.add(
                RoomAssignment(
                    room_id=room_id,
                    session_id=item.session_id,
                    starts_at=item.starts_at,
                    ends_at=item.ends_at,
                    active=True,
                )
            )
        mapped_rooms += 1
    local_mapping_counts = reconcile_program_room_assignments(session, snapshot.event_id)
    mapped_rooms = local_mapping_counts["mapped_sessions"]
    unresolved_rooms = local_mapping_counts["unmapped_sessions"]
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
                        presenter_order=participant.presenter_order,
                        primary_presenter=participant.primary_presenter,
                        active=True,
                        revision=participant.central_revision,
                        sync_state=SyncState.SYNCHRONIZED,
                    )
                )
            else:
                local.role = participant.role
                local.presenter_order = participant.presenter_order
                local.primary_presenter = participant.primary_presenter
                local.active = True
                local.revision = max(local.revision, participant.central_revision)
                local.sync_state = SyncState.SYNCHRONIZED
    for item in snapshot.presentations:
        identifier, identifier_source = allocate_presentation_identifier(
            item.presentation_identifier or item.presentation_code, "SITE", item.presentation_id
        )
        if item.presentation_identifier_source is not None:
            identifier_source = item.presentation_identifier_source
        presentation = session.get(Presentation, item.presentation_id)
        if presentation is None:
            presentation = Presentation(
                presentation_id=item.presentation_id,
                event_id=snapshot.event_id,
                session_id=item.session_id,
                title=item.title,
                description=item.description,
                presentation_code=item.presentation_code,
                presentation_identifier=identifier,
                presentation_identifier_source=identifier_source,
                external_presentation_id=item.external_presentation_id or item.presentation_code,
                workflow_status=item.workflow_status,
                processing_status=item.processing_status,
                scheduled_at=item.scheduled_at,
                active=True,
                revision=item.central_revision,
                sync_state=SyncState.SYNCHRONIZED,
            )
            session.add(presentation)
        else:
            presentation.session_id = item.session_id
            presentation.title = item.title
            presentation.description = item.description
            presentation.presentation_code = item.presentation_code
            if item.presentation_identifier:
                presentation.presentation_identifier = identifier
                presentation.presentation_identifier_source = identifier_source
            if item.external_presentation_id or item.presentation_code:
                presentation.external_presentation_id = (
                    item.external_presentation_id or item.presentation_code
                )
            presentation.workflow_status = item.workflow_status
            presentation.processing_status = item.processing_status
            presentation.scheduled_at = item.scheduled_at
            presentation.active = True
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
        for link in item.sessions:
            local = session.get(PresentationSession, link.presentation_session_id)
            if local is None:
                local = PresentationSession(
                    presentation_session_id=link.presentation_session_id,
                    presentation_id=item.presentation_id,
                    session_id=link.session_id,
                    association_type=link.association_type,
                    sort_order=link.sort_order,
                    primary_session=link.primary_session,
                    active=True,
                    revision=link.central_revision,
                )
                session.add(local)
            else:
                local.session_id = link.session_id
                local.association_type = link.association_type
                local.sort_order = link.sort_order
                local.primary_session = link.primary_session
                local.active = True
                local.revision = max(local.revision, link.central_revision)
        for link in item.presenters:
            local = session.get(PresentationPresenter, link.presentation_presenter_id)
            if local is None:
                local = PresentationPresenter(
                    presentation_presenter_id=link.presentation_presenter_id,
                    presentation_id=item.presentation_id,
                    event_participation_id=link.event_participation_id,
                    role=link.role,
                    presenter_order=link.presenter_order,
                    primary_presenter=link.primary_presenter,
                    active=True,
                    revision=link.central_revision,
                )
                session.add(local)
            else:
                local.event_participation_id = link.event_participation_id
                local.role = link.role
                local.presenter_order = link.presenter_order
                local.primary_presenter = link.primary_presenter
                local.active = True
                local.revision = max(local.revision, link.central_revision)
    for item in snapshot.external_identifiers:
        local = session.get(ExternalIdentifierProjection, item.external_identifier_id)
        if local is None:
            local = ExternalIdentifierProjection(
                external_identifier_id=item.external_identifier_id,
                event_id=snapshot.event_id,
                entity_type=item.entity_type,
                entity_id=item.entity_id,
                namespace=item.namespace,
                external_id=item.external_id,
                active=True,
                revision=item.central_revision,
            )
            session.add(local)
        else:
            local.entity_type = item.entity_type
            local.entity_id = item.entity_id
            local.namespace = item.namespace
            local.external_id = item.external_id
            local.active = True
            local.revision = max(local.revision, item.central_revision)
    return {
        "people": len(snapshot.people),
        "participants": len(snapshot.participations),
        "presenters": sum(item.is_presenter for item in snapshot.participations),
        "sessions": len(snapshot.sessions),
        "session_presenters": sum(len(item.participants) for item in snapshot.sessions),
        "presentations": len(snapshot.presentations),
        "presentation_sessions": sum(len(item.sessions) for item in snapshot.presentations),
        "presentation_presenters": sum(len(item.presenters) for item in snapshot.presentations),
        "external_identifiers": len(snapshot.external_identifiers),
        "rooms": mapped_rooms,
        "unresolved_rooms": unresolved_rooms,
        "room_conflicts": room_conflicts,
        "rooms_created": materialized_rooms["created_rooms"],
        "rooms_reused": materialized_rooms["reused_rooms"],
        "room_mappings_created": materialized_rooms["created_mappings"],
        "room_labels_ambiguous": materialized_rooms["ambiguous_labels"],
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


def apply_event_deletion(session: Session, event: SyncEventEnvelope) -> str:
    """Explicitly purge one Central event projection while preserving shared Site resources."""
    event_id = UUID(str(event.payload["event_id"]))
    if event.entity_id != event_id:
        raise ValueError("event identity does not match deletion envelope")
    # Ordered statements intentionally document ownership instead of relying on FK cascades.
    params = {"event_id": event_id}
    statements = [
        "DELETE FROM room_assignments WHERE session_id IN (SELECT session_id FROM sessions WHERE event_id=:event_id)",
        "DELETE FROM media_transfer_sessions WHERE event_id=:event_id",
        "DELETE FROM media_replication_sessions WHERE event_id=:event_id",
        "DELETE FROM presentation_assets WHERE presentation_version_id IN (SELECT presentation_version_id FROM presentation_versions WHERE presentation_id IN (SELECT presentation_id FROM presentations WHERE event_id=:event_id))",
        "DELETE FROM presentation_versions WHERE presentation_id IN (SELECT presentation_id FROM presentations WHERE event_id=:event_id)",
        "DELETE FROM presentation_presenters WHERE presentation_id IN (SELECT presentation_id FROM presentations WHERE event_id=:event_id)",
        "DELETE FROM presentation_sessions WHERE presentation_id IN (SELECT presentation_id FROM presentations WHERE event_id=:event_id)",
        "DELETE FROM presentations WHERE event_id=:event_id",
        "DELETE FROM session_participants WHERE session_id IN (SELECT session_id FROM sessions WHERE event_id=:event_id)",
        "DELETE FROM sessions WHERE event_id=:event_id",
        "DELETE FROM event_participations WHERE event_id=:event_id",
        "DELETE FROM external_identifier_projections WHERE event_id=:event_id",
        "DELETE FROM program_room_mappings WHERE event_id=:event_id",
        "DELETE FROM event_deployment_revisions WHERE deployment_id IN (SELECT deployment_id FROM event_deployments WHERE central_event_id=:event_id)",
        "DELETE FROM event_deployments WHERE central_event_id=:event_id",
        # Only explicitly event-owned rooms are eligible; reusable rooms have a NULL/different event.
        "DELETE FROM device_assignments WHERE room_id IN (SELECT room_id FROM rooms WHERE event_id=:event_id)",
        "DELETE FROM rooms WHERE event_id=:event_id AND NOT EXISTS (SELECT 1 FROM program_room_mappings m WHERE m.room_id=rooms.room_id)",
        "UPDATE media_objects SET deleted_at=COALESCE(deleted_at, now()) WHERE event_id=:event_id AND NOT EXISTS (SELECT 1 FROM presentation_assets a WHERE a.media_object_id=media_objects.media_object_id)",
        "UPDATE media_objects SET event_id=NULL WHERE event_id=:event_id",
        "UPDATE sync_events SET event_id=NULL WHERE event_id=:event_id",
        "UPDATE outbox_events SET event_id=NULL WHERE event_id=:event_id",
        "UPDATE audit_records SET event_id=NULL WHERE event_id=:event_id",
        "DELETE FROM events WHERE event_id=:event_id",
        "DELETE FROM person_projections p WHERE NOT EXISTS (SELECT 1 FROM event_participations ep WHERE ep.person_id=p.person_id)",
    ]
    for statement in statements:
        session.execute(text(statement), params)
    return "deleted"


def apply_people_deletion(session: Session, event: SyncEventEnvelope) -> str:
    """Remove projected relationships for Central-deleted permanent identities."""
    raw_ids = event.payload.get("person_ids")
    if not isinstance(raw_ids, list) or not raw_ids:
        raise ValueError("person deletion requires a nonempty person_ids list")
    person_ids = [UUID(str(value)) for value in raw_ids]
    participation_ids = select(EventParticipation.event_participation_id).where(
        EventParticipation.person_id.in_(person_ids)
    )
    session.execute(
        delete(SessionParticipant).where(
            SessionParticipant.event_participation_id.in_(participation_ids)
        )
    )
    session.execute(
        delete(PresentationPresenter).where(
            PresentationPresenter.event_participation_id.in_(participation_ids)
        )
    )
    session.execute(delete(EventParticipation).where(EventParticipation.person_id.in_(person_ids)))
    session.execute(delete(PersonProjection).where(PersonProjection.person_id.in_(person_ids)))
    return "deleted"
