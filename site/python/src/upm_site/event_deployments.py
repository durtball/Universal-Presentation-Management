"""Transactional application of Central event deployment snapshots at a Site."""
# ruff: noqa: E501

from datetime import UTC, date, datetime
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
from upm_shared.enums import AuthorityScope, EventDeploymentStatus, SourceSystem, SyncState
from upm_shared.identifiers import new_uuid7
from upm_shared.jobs import OutboxPayload
from upm_shared.presentation_media import allocate_presentation_identifier
from upm_site.persistence.models import (
    Event,
    EventDeploymentProjection,
    EventDeploymentRevisionProjection,
    EventParticipation,
    ExternalIdentifierProjection,
    LocalSiteIdentity,
    MediaReplicationSession,
    MediaTransferSession,
    OperationalLog,
    OutboxEvent,
    PersonProjection,
    Presentation,
    PresentationAsset,
    PresentationPresenter,
    PresentationSession,
    PresentationVersion,
    Room,
    RoomAssignment,
    RotationAssignment,
    SessionParticipant,
    User,
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


def converge_presentation_version_identity(
    session: Session,
    *,
    presentation_version_id: UUID,
    presentation_id: UUID,
    version_number: int,
) -> PresentationVersion:
    """Converge an old Site-generated version UUID to Central's canonical identity."""
    canonical = session.get(PresentationVersion, presentation_version_id)
    if canonical is not None:
        if (
            canonical.presentation_id != presentation_id
            or canonical.version_number != version_number
        ):
            raise ValueError("presentation version metadata conflicts with Central")
        canonical.sync_state = SyncState.SYNCHRONIZED
        return canonical

    versions = session.scalars(
        select(PresentationVersion)
        .where(PresentationVersion.presentation_id == presentation_id)
        .with_for_update()
    ).all()
    legacy = next((item for item in versions if item.version_number == version_number), None)
    if legacy is None:
        canonical = PresentationVersion(
            presentation_version_id=presentation_version_id,
            presentation_id=presentation_id,
            version_number=version_number,
            sync_state=SyncState.SYNCHRONIZED,
        )
        session.add(canonical)
        return canonical

    temporary_number = max(item.version_number for item in versions) + 1
    canonical = PresentationVersion(
        presentation_version_id=presentation_version_id,
        presentation_id=presentation_id,
        version_number=temporary_number,
        sync_state=SyncState.SYNCHRONIZED,
        created_at=legacy.created_at,
        revision=legacy.revision,
    )
    session.add(canonical)
    session.flush([canonical])
    for model in (PresentationAsset, MediaTransferSession, MediaReplicationSession, OperationalLog):
        session.execute(
            update(model)
            .where(model.presentation_version_id == legacy.presentation_version_id)
            .values(presentation_version_id=presentation_version_id)
            .execution_options(synchronize_session=False)
        )
    session.delete(legacy)
    session.flush()
    canonical.version_number = version_number
    session.flush([canonical])
    return canonical


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


def _upsert_snapshot(
    session: Session, snapshot: EventDeploymentSnapshot, *, local_smb_enabled: bool = False
) -> dict[str, int]:
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
    projected_ids = {item.user_id for item in snapshot.users}
    for existing in session.scalars(select(User).where(User.user_type == "central_managed")).all():
        if existing.central_user_id not in projected_ids:
            existing.active = False
            existing.web_access = False
            existing.smb_enabled = False
            if local_smb_enabled:
                SiteQueue(session).enqueue_processing(
                    site_id=snapshot.site_id,
                    job_type="smb.user.revoke",
                    payload={"data": {"username": existing.username}},
                    idempotency_key=(
                        f"smb-user-revoke:{existing.user_id}:{snapshot.deployment_revision}"
                    ),
                    required_capabilities=["cpu"],
                )
    for item in snapshot.users:
        user = session.scalar(select(User).where(User.central_user_id == item.user_id))
        collision = session.scalar(
            select(User).where(
                User.normalized_username == item.username.strip().casefold(),
                User.user_type == "site_local",
            )
        )
        if user is None:
            user = User(
                central_user_id=item.user_id,
                user_type="central_managed",
                username=item.username,
                normalized_username=(
                    f"central:{item.user_id}" if collision else item.username.strip().casefold()
                ),
                display_name=item.display_name,
                roles=[item.role],
                permissions=item.permissions,
            )
            session.add(user)
        if item.central_revision >= user.revision:
            user.username = item.username
            user.display_name = item.display_name
            user.email = item.email
            user.roles = [item.role]
            user.permissions = item.permissions
            user.active = item.enabled and collision is None
            user.web_access = item.web_access and collision is None
            user.smb_enabled = local_smb_enabled and item.smb_enabled and collision is None
            user.web_password_hash = item.password_verifier
            user.revision = item.central_revision
            if local_smb_enabled and (not user.active or not user.smb_enabled):
                SiteQueue(session).enqueue_processing(
                    site_id=snapshot.site_id,
                    job_type="smb.user.revoke",
                    payload={"data": {"username": user.username}},
                    idempotency_key=f"smb-user-revoke:{user.user_id}:{item.central_revision}",
                    required_capabilities=["cpu"],
                )
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
    for room_data in snapshot.room_configuration.get("rooms", []):
        if not isinstance(room_data, dict):
            continue
        try:
            room_id = UUID(str(room_data["room_id"]))
        except (KeyError, TypeError, ValueError):
            continue
        room = session.get(Room, room_id)
        label = str(room_data.get("label") or "").strip()
        if not label:
            continue
        if room is None:
            label_conflict = session.scalar(
                select(Room).where(Room.site_id == snapshot.site_id, Room.label == label)
            )
            if label_conflict is not None:
                # Existing physical-room UUID remains authoritative; the recovered mapping below
                # will surface unresolved rather than silently replacing it.
                continue
            room = Room(
                room_id=room_id,
                site_id=snapshot.site_id,
                event_id=snapshot.event_id,
                label=label,
                enabled=bool(room_data.get("enabled", True)),
                revision=max(1, int(room_data.get("revision") or 1)),
                sync_state=SyncState.SYNCHRONIZED,
            )
            session.add(room)
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
        explicit_version_numbers = {version.version_number for version in item.versions}
        for version in item.versions:
            converge_presentation_version_identity(
                session,
                presentation_version_id=version.presentation_version_id,
                presentation_id=item.presentation_id,
                version_number=version.version_number,
            )
        existing_versions = set(
            session.scalars(
                select(PresentationVersion.version_number).where(
                    PresentationVersion.presentation_id == item.presentation_id
                )
            )
        )
        for version_number in item.version_numbers:
            if (
                version_number not in explicit_version_numbers
                and version_number not in existing_versions
            ):
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
    deployed_rotation_ids = {item.rotation_assignment_id for item in snapshot.rotation_assignments}
    for existing in session.scalars(
        select(RotationAssignment).where(
            RotationAssignment.event_id == snapshot.event_id,
            RotationAssignment.source_authority == "central",
        )
    ):
        if existing.central_assignment_id not in deployed_rotation_ids:
            existing.active = False
            existing.override_state = "cleared"
    for item in snapshot.rotation_assignments:
        local = session.scalar(
            select(RotationAssignment).where(
                RotationAssignment.central_assignment_id == item.rotation_assignment_id
            )
        )
        if local is None:
            local = RotationAssignment(
                central_assignment_id=item.rotation_assignment_id,
                event_id=snapshot.event_id,
                event_day=item.event_day,
                scope=item.scope,
                room_id=item.room_id,
                session_id=item.session_id,
                presentation_version_id=item.presentation_version_id,
                source_authority="central",
                override_state="configured",
                active=item.active,
                revision=item.central_revision,
                sync_state=SyncState.SYNCHRONIZED,
            )
            session.add(local)
        else:
            local.event_day = item.event_day
            local.scope = item.scope
            local.room_id = item.room_id
            local.session_id = item.session_id
            local.presentation_version_id = item.presentation_version_id
            local.override_state = "configured"
            local.active = item.active
            local.revision = max(local.revision, item.central_revision)
            local.sync_state = SyncState.SYNCHRONIZED
    for recovered in snapshot.extensions.get("site_rotation_overrides", []):
        if not isinstance(recovered, dict):
            continue
        try:
            assignment_id = UUID(str(recovered["rotation_assignment_id"]))
            event_day = date.fromisoformat(str(recovered["event_day"]))
            room_id = UUID(str(recovered["room_id"])) if recovered.get("room_id") else None
            session_id = UUID(str(recovered["session_id"])) if recovered.get("session_id") else None
            version_id = (
                UUID(str(recovered["presentation_version_id"]))
                if recovered.get("presentation_version_id")
                else None
            )
        except (KeyError, TypeError, ValueError):
            continue
        local = session.get(RotationAssignment, assignment_id)
        if local is None:
            local = RotationAssignment(
                rotation_assignment_id=assignment_id,
                event_id=snapshot.event_id,
                event_day=event_day,
                scope=str(recovered["scope"]),
                room_id=room_id,
                session_id=session_id,
                presentation_version_id=version_id,
                source_authority="site",
                override_state="configured" if recovered.get("active", True) else "cleared",
                active=bool(recovered.get("active", True)),
                revision=max(1, int(recovered.get("revision") or 1)),
                sync_state=SyncState.SYNCHRONIZED,
            )
            session.add(local)
        elif (
            local.source_authority == "site"
            and int(recovered.get("revision") or 1) > local.revision
        ):
            local.event_day = event_day
            local.scope = str(recovered["scope"])
            local.room_id = room_id
            local.session_id = session_id
            local.presentation_version_id = version_id
            local.active = bool(recovered.get("active", True))
            local.override_state = "configured" if local.active else "cleared"
            local.revision = int(recovered.get("revision") or 1)
            local.sync_state = SyncState.SYNCHRONIZED
    return {
        "people": len(snapshot.people),
        "participants": len(snapshot.participations),
        "presenters": sum(item.is_presenter for item in snapshot.participations),
        "sessions": len(snapshot.sessions),
        "session_presenters": sum(len(item.participants) for item in snapshot.sessions),
        "presentations": len(snapshot.presentations),
        "presentation_sessions": sum(len(item.sessions) for item in snapshot.presentations),
        "presentation_presenters": sum(len(item.presenters) for item in snapshot.presentations),
        "rotation_assignments": len(snapshot.rotation_assignments),
        "external_identifiers": len(snapshot.external_identifiers),
        "rooms": mapped_rooms,
        "unresolved_rooms": unresolved_rooms,
        "room_conflicts": room_conflicts,
        "rooms_created": materialized_rooms["created_rooms"],
        "rooms_reused": materialized_rooms["reused_rooms"],
        "room_mappings_created": materialized_rooms["created_mappings"],
        "room_labels_ambiguous": materialized_rooms["ambiguous_labels"],
    }


def event_is_deleted(session: Session, event_id: UUID) -> bool:
    """Return whether the durable deletion barrier forbids recreating an Event projection."""
    return (
        session.scalar(
            select(OutboxEvent.outbox_event_id).where(
                OutboxEvent.event_type.in_(
                    ["site.event_deletion.applied", "site.event_deletion.requested"]
                ),
                OutboxEvent.payload["event_id"].as_string() == str(event_id),
            )
        )
        is not None
    )


def apply_snapshot_event(
    session: Session, event: SyncEventEnvelope, *, local_smb_enabled: bool = False
) -> str:
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
    if event_is_deleted(session, snapshot.event_id):
        return "deleted"
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
    counts = _upsert_snapshot(session, snapshot, local_smb_enabled=local_smb_enabled)
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


def apply_event_deletion(
    session: Session, event: SyncEventEnvelope, *, publish_acknowledgement: bool = True
) -> str:
    """Authoritatively purge an event graph and durably schedule physical garbage collection."""
    event_id = UUID(str(event.payload["event_id"]))
    if event.entity_id != event_id:
        raise ValueError("event identity does not match deletion envelope")
    site_id = _site_id(session)
    deletion_operation_id = str(event.payload.get("deletion_operation_id", event.event_id))
    params = {"event_id": event_id}

    # Persist the deletion barrier before removing the live Event FK. Every future snapshot checks
    # this durable, event-id-free history row, so an old deployment can never resurrect the Event.
    queue = SiteQueue(session)
    if publish_acknowledgement:
        from upm_site.sync import next_sequence

        acknowledgement_key = f"event-deletion-applied:{deletion_operation_id}:{site_id}"
        if (
            session.scalar(
                select(OutboxEvent.outbox_event_id).where(
                    OutboxEvent.source_system == SourceSystem.SITE,
                    OutboxEvent.idempotency_key == acknowledgement_key,
                )
            )
            is None
        ):
            queue.enqueue_outbox(
                site_id=site_id,
                event_type="site.event_deletion.applied",
                aggregate_type="event_deletion",
                aggregate_id=event_id,
                event_id=None,
                source_sequence=next_sequence(session),
                payload=OutboxPayload(
                    source_system=SourceSystem.SITE,
                    schema_version=1,
                    data={
                        "event_id": str(event_id),
                        "deletion_operation_id": deletion_operation_id,
                    },
                ),
                idempotency_key=acknowledgement_key,
            )

    media_rows = (
        session.execute(
            text(
                "SELECT DISTINCT m.media_object_id, m.storage_target_id, m.object_key "
                "FROM media_objects m LEFT JOIN presentation_assets a ON a.media_object_id=m.media_object_id "
                "LEFT JOIN presentation_versions v ON v.presentation_version_id=a.presentation_version_id "
                "LEFT JOIN presentations p ON p.presentation_id=v.presentation_id "
                "WHERE m.event_id=:event_id OR p.event_id=:event_id"
            ),
            params,
        )
        .mappings()
        .all()
    )
    media_ids = [row["media_object_id"] for row in media_rows]
    staging_rows = (
        session.execute(
            text(
                "SELECT storage_target_id, partial_key FROM media_transfer_sessions "
                "WHERE event_id=:event_id AND storage_target_id IS NOT NULL"
            ),
            params,
        )
        .mappings()
        .all()
    )

    # Quiesce and remove all event-owned durable work first. Historical jobs are identified by
    # their canonical manifest payload as well as session/media identity, covering legacy rows.
    session.execute(
        text(
            "DELETE FROM transfer_jobs WHERE transfer_job_id IN ("
            "SELECT transfer_session_id FROM media_transfer_sessions WHERE event_id=:event_id "
            "UNION SELECT replication_session_id FROM media_replication_sessions WHERE event_id=:event_id) "
            "OR payload->>'event_id'=CAST(:event_id AS text) "
            "OR payload->'data'->>'event_id'=CAST(:event_id AS text) "
            "OR media_object_id = ANY(CAST(:media_ids AS uuid[]))"
        ),
        {**params, "media_ids": media_ids},
    )
    session.execute(text("DELETE FROM media_transfer_sessions WHERE event_id=:event_id"), params)
    session.execute(text("DELETE FROM media_replication_sessions WHERE event_id=:event_id"), params)
    session.execute(
        text(
            "DELETE FROM processing_jobs WHERE payload->>'event_id'=CAST(:event_id AS text) "
            "OR payload->'data'->>'event_id'=CAST(:event_id AS text) "
            "OR media_object_id = ANY(CAST(:media_ids AS uuid[]))"
        ),
        {**params, "media_ids": media_ids},
    )

    ordered = [
        "DELETE FROM room_assignments WHERE session_id IN (SELECT session_id FROM sessions WHERE event_id=:event_id)",
        "DELETE FROM presentation_assets WHERE source_asset_id IN (SELECT presentation_asset_id FROM presentation_assets WHERE presentation_version_id IN (SELECT presentation_version_id FROM presentation_versions WHERE presentation_id IN (SELECT presentation_id FROM presentations WHERE event_id=:event_id)))",
        "DELETE FROM presentation_assets WHERE presentation_version_id IN (SELECT presentation_version_id FROM presentation_versions WHERE presentation_id IN (SELECT presentation_id FROM presentations WHERE event_id=:event_id))",
        "DELETE FROM presentation_versions WHERE presentation_id IN (SELECT presentation_id FROM presentations WHERE event_id=:event_id)",
        "DELETE FROM presentation_presenters WHERE presentation_id IN (SELECT presentation_id FROM presentations WHERE event_id=:event_id)",
        "DELETE FROM presentation_sessions WHERE presentation_id IN (SELECT presentation_id FROM presentations WHERE event_id=:event_id)",
        "DELETE FROM presentations WHERE event_id=:event_id",
        "DELETE FROM session_participants WHERE session_id IN (SELECT session_id FROM sessions WHERE event_id=:event_id) OR event_participation_id IN (SELECT event_participation_id FROM event_participations WHERE event_id=:event_id)",
        "DELETE FROM sessions WHERE event_id=:event_id",
        "DELETE FROM event_participations WHERE event_id=:event_id",
        "DELETE FROM external_identifier_projections WHERE event_id=:event_id",
        "DELETE FROM event_deployment_revisions WHERE deployment_id IN (SELECT deployment_id FROM event_deployments WHERE central_event_id=:event_id)",
        "DELETE FROM event_deployments WHERE central_event_id=:event_id",
        "DELETE FROM device_assignments WHERE room_id IN (SELECT room_id FROM rooms WHERE event_id=:event_id)",
        "DELETE FROM program_room_mappings WHERE event_id=:event_id OR room_id IN (SELECT room_id FROM rooms WHERE event_id=:event_id)",
        "DELETE FROM rooms WHERE event_id=:event_id",
    ]
    for statement in ordered:
        session.execute(text(statement), params)

    physical = []
    for row in media_rows:
        media_id = row["media_object_id"]
        referenced = session.scalar(
            text("SELECT EXISTS(SELECT 1 FROM presentation_assets WHERE media_object_id=:id)"),
            {"id": media_id},
        )
        if referenced:
            session.execute(
                text("UPDATE media_objects SET event_id=NULL WHERE media_object_id=:id"),
                {"id": media_id},
            )
            continue
        shared_key = session.scalar(
            text(
                "SELECT EXISTS(SELECT 1 FROM media_objects WHERE media_object_id<>:id "
                "AND storage_target_id=:target AND object_key=:key)"
            ),
            {"id": media_id, "target": row["storage_target_id"], "key": row["object_key"]},
        )
        session.execute(
            text(
                "UPDATE media_objects SET source_media_object_id=NULL WHERE source_media_object_id=:id"
            ),
            {"id": media_id},
        )
        session.execute(
            text("DELETE FROM media_objects WHERE media_object_id=:id"), {"id": media_id}
        )
        if not shared_key:
            physical.append(
                {
                    "storage_target_id": str(row["storage_target_id"]),
                    "object_key": row["object_key"],
                }
            )

    if physical or staging_rows:
        queue.enqueue_processing(
            site_id=site_id,
            job_type="lifecycle.delete_media_objects",
            payload={
                "data": {
                    "event_id": str(event_id),
                    "objects": physical,
                    "staging": [
                        {
                            "storage_target_id": str(row["storage_target_id"]),
                            "object_key": row["partial_key"],
                        }
                        for row in staging_rows
                    ],
                }
            },
            idempotency_key=f"event-media-delete:{deletion_operation_id}",
            required_capabilities=["storage"],
        )
    session.execute(text("UPDATE sync_events SET event_id=NULL WHERE event_id=:event_id"), params)
    session.execute(text("UPDATE outbox_events SET event_id=NULL WHERE event_id=:event_id"), params)
    session.execute(text("UPDATE audit_records SET event_id=NULL WHERE event_id=:event_id"), params)
    session.execute(text("DELETE FROM events WHERE event_id=:event_id"), params)
    session.execute(
        text(
            "DELETE FROM person_projections p WHERE NOT EXISTS (SELECT 1 FROM event_participations ep WHERE ep.person_id=p.person_id)"
        )
    )
    return "deleted"


def request_site_event_deletion(session: Session, event_id: UUID) -> str:
    """Durably request global deletion for a Central projection, then purge it locally."""
    projected = session.scalar(
        select(EventDeploymentProjection.deployment_id).where(
            EventDeploymentProjection.central_event_id == event_id
        )
    )
    deletion_operation_id = new_uuid7()
    if projected is not None:
        from upm_site.sync import next_sequence

        SiteQueue(session).enqueue_outbox(
            site_id=_site_id(session),
            event_type="site.event_deletion.requested",
            aggregate_type="event_deletion",
            aggregate_id=event_id,
            event_id=None,
            source_sequence=next_sequence(session),
            payload=OutboxPayload(
                source_system=SourceSystem.SITE,
                schema_version=1,
                data={
                    "event_id": str(event_id),
                    "deletion_operation_id": str(deletion_operation_id),
                },
            ),
            idempotency_key=f"event-deletion-requested:{event_id}",
        )
    envelope = SyncEventEnvelope(
        event_id=new_uuid7(),
        event_type="central.event.deleted",
        protocol_version=1,
        source="site-local-deletion",
        source_sequence=0,
        authority=AuthorityScope.CENTRAL,
        entity_type="event",
        entity_id=event_id,
        occurred_at=utc_now(),
        payload={
            "event_id": str(event_id),
            "deletion_operation_id": str(deletion_operation_id),
        },
    )
    return apply_event_deletion(session, envelope, publish_acknowledgement=False)


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
