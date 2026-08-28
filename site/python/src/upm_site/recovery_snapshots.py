"""Complete Site-originated Event snapshots for Central backup and replacement recovery.

This extends the ordered Site outbox transport with the same complete-snapshot shape used for
Central deployments.  It is not row replication: one transaction publishes a complete revision
of an Event's durable program graph, and Central applies it idempotently by UUID.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from upm_shared.contracts.deployments import (
    EventDeploymentSnapshot,
    ParticipationSnapshot,
    PersonProfile,
    PresentationPresenterSnapshot,
    PresentationSessionSnapshot,
    PresentationSnapshot,
    PresentationVersionSnapshot,
    RotationAssignmentSnapshot,
    SessionParticipantSnapshot,
    SessionSnapshot,
)
from upm_shared.contracts.sync import UPM_SYNC_PROTOCOL_VERSION
from upm_shared.enums import SourceSystem, SyncState
from upm_shared.jobs import OutboxPayload
from upm_site.persistence.models import (
    Event,
    EventDeploymentProjection,
    EventParticipation,
    PersonProjection,
    Presentation,
    PresentationPresenter,
    PresentationSession,
    PresentationVersion,
    ProgramRoomMapping,
    Room,
    RoomAssignment,
    RotationAssignment,
    SessionParticipant,
)
from upm_site.persistence.models import Session as ProgramSession
from upm_site.persistence.queue import SiteQueue
from upm_site.sync import next_sequence


def build_site_recovery_snapshot(session: Session, event: Event) -> EventDeploymentSnapshot:
    participations = session.scalars(
        select(EventParticipation).where(
            EventParticipation.event_id == event.event_id,
            EventParticipation.active.is_(True),
        )
    ).all()
    person_ids = {item.person_id for item in participations}
    people = session.scalars(
        select(PersonProjection).where(PersonProjection.person_id.in_(person_ids))
    ).all()
    program_sessions = session.scalars(
        select(ProgramSession).where(
            ProgramSession.event_id == event.event_id, ProgramSession.active.is_(True)
        )
    ).all()
    session_ids = {item.session_id for item in program_sessions}
    session_links: dict[object, list[SessionParticipant]] = {}
    for link in session.scalars(
        select(SessionParticipant).where(
            SessionParticipant.session_id.in_(session_ids), SessionParticipant.active.is_(True)
        )
    ):
        session_links.setdefault(link.session_id, []).append(link)
    presentations = session.scalars(
        select(Presentation).where(
            Presentation.event_id == event.event_id, Presentation.active.is_(True)
        )
    ).all()
    presentation_ids = {item.presentation_id for item in presentations}
    versions: dict[object, list[PresentationVersion]] = {}
    for version in session.scalars(
        select(PresentationVersion).where(PresentationVersion.presentation_id.in_(presentation_ids))
    ):
        versions.setdefault(version.presentation_id, []).append(version)
    presentation_sessions: dict[object, list[PresentationSession]] = {}
    for link in session.scalars(
        select(PresentationSession).where(
            PresentationSession.presentation_id.in_(presentation_ids),
            PresentationSession.active.is_(True),
        )
    ):
        presentation_sessions.setdefault(link.presentation_id, []).append(link)
    presentation_presenters: dict[object, list[PresentationPresenter]] = {}
    for link in session.scalars(
        select(PresentationPresenter).where(
            PresentationPresenter.presentation_id.in_(presentation_ids),
            PresentationPresenter.active.is_(True),
        )
    ):
        presentation_presenters.setdefault(link.presentation_id, []).append(link)
    rotations = session.scalars(
        select(RotationAssignment).where(
            RotationAssignment.event_id == event.event_id,
            RotationAssignment.active.is_(True),
        )
    ).all()
    rooms = session.scalars(select(Room).where(Room.site_id == event.site_id)).all()
    room_ids = {item.room_id for item in rooms}
    mappings = session.scalars(
        select(ProgramRoomMapping).where(ProgramRoomMapping.event_id == event.event_id)
    ).all()
    assignments = session.scalars(
        select(RoomAssignment).where(RoomAssignment.room_id.in_(room_ids))
    ).all()
    return EventDeploymentSnapshot(
        deployment_id=event.event_id,
        deployment_revision=event.revision,
        central_event_revision=event.revision,
        event_id=event.event_id,
        site_id=event.site_id,
        event_name=event.name,
        starts_at=event.starts_at,
        ends_at=event.ends_at,
        timezone=event.timezone,
        event_description=event.description,
        event_configuration={"authority": "site", "recovery_source": "site_complete_snapshot"},
        room_configuration={
            "rooms": [
                {
                    "room_id": str(item.room_id),
                    "label": item.label,
                    "event_id": str(item.event_id) if item.event_id else None,
                    "enabled": item.enabled,
                    "archived_at": item.archived_at.isoformat() if item.archived_at else None,
                    "revision": item.revision,
                }
                for item in rooms
            ],
            "program_room_mappings": [
                {
                    "program_room_mapping_id": str(item.program_room_mapping_id),
                    "imported_label": item.imported_label,
                    "normalized_imported_label": item.normalized_imported_label,
                    "room_id": str(item.room_id) if item.room_id else None,
                    "confirmed_by": item.confirmed_by,
                    "revision": item.revision,
                }
                for item in mappings
            ],
            "mappings": [
                {
                    "imported_label": item.imported_label,
                    "normalized_imported_label": item.normalized_imported_label,
                    "mapping_status": "mapped" if item.room_id else "unmapped",
                    "target_room_id": str(item.room_id) if item.room_id else None,
                    "target_room_label": next(
                        (room.label for room in rooms if room.room_id == item.room_id), None
                    ),
                }
                for item in mappings
            ],
            "room_assignments": [
                {
                    "room_assignment_id": str(item.room_assignment_id),
                    "room_id": str(item.room_id),
                    "session_id": str(item.session_id),
                    "starts_at": item.starts_at.isoformat() if item.starts_at else None,
                    "ends_at": item.ends_at.isoformat() if item.ends_at else None,
                    "active": item.active,
                    "revision": item.revision,
                }
                for item in assignments
                if item.session_id in session_ids
            ],
        },
        people=[
            PersonProfile(
                person_id=item.person_id,
                display_name=item.display_name,
                given_name=item.given_name,
                family_name=item.family_name,
                primary_email=item.primary_email,
                organization=item.organization,
                central_revision=item.revision,
            )
            for item in people
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
                        session_participant_id=link.session_participant_id,
                        event_participation_id=link.event_participation_id,
                        role=link.role,
                        presenter_order=link.presenter_order,
                        primary_presenter=link.primary_presenter,
                        central_revision=link.revision,
                    )
                    for link in session_links.get(item.session_id, [])
                ],
            )
            for item in program_sessions
        ],
        presentations=[
            PresentationSnapshot(
                presentation_id=item.presentation_id,
                session_id=item.session_id,
                title=item.title,
                description=item.description,
                presentation_code=item.presentation_code,
                presentation_identifier=item.presentation_identifier,
                presentation_identifier_source=item.presentation_identifier_source,
                external_presentation_id=item.external_presentation_id,
                workflow_status=item.workflow_status,
                processing_status=item.processing_status,
                scheduled_at=item.scheduled_at,
                central_revision=item.revision,
                versions=[
                    PresentationVersionSnapshot(
                        presentation_version_id=version.presentation_version_id,
                        version_number=version.version_number,
                    )
                    for version in versions.get(item.presentation_id, [])
                ],
                version_numbers=sorted(
                    version.version_number for version in versions.get(item.presentation_id, [])
                ),
                sessions=[
                    PresentationSessionSnapshot(
                        presentation_session_id=link.presentation_session_id,
                        session_id=link.session_id,
                        association_type=link.association_type,
                        sort_order=link.sort_order,
                        primary_session=link.primary_session,
                        central_revision=link.revision,
                    )
                    for link in presentation_sessions.get(item.presentation_id, [])
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
                    for link in presentation_presenters.get(item.presentation_id, [])
                ],
                metadata={"origin_site_id": str(event.site_id)},
            )
            for item in presentations
        ],
        rotation_assignments=[
            RotationAssignmentSnapshot(
                rotation_assignment_id=item.rotation_assignment_id,
                event_day=item.event_day,
                scope=item.scope,
                room_id=item.room_id,
                session_id=item.session_id,
                presentation_version_id=item.presentation_version_id,
                # Site overrides stay Site-owned. Central retains them for recovery rather than
                # deploying them as Central defaults.
                source_authority="central",
                active=item.active,
                central_revision=item.revision,
            )
            for item in rotations
            if item.source_authority == "central"
        ],
        extensions={
            "site_recovery": {
                "origin_site_id": str(event.site_id),
                "site_event_revision": event.revision,
            },
            "site_rotation_overrides": [
                {
                    "rotation_assignment_id": str(item.rotation_assignment_id),
                    "event_day": item.event_day.isoformat(),
                    "scope": item.scope,
                    "room_id": str(item.room_id) if item.room_id else None,
                    "session_id": str(item.session_id) if item.session_id else None,
                    "presentation_version_id": (
                        str(item.presentation_version_id) if item.presentation_version_id else None
                    ),
                    "active": item.active,
                    "revision": item.revision,
                }
                for item in rotations
                if item.source_authority == "site"
            ],
        },
    )


def enqueue_site_recovery_snapshot(session: Session, event: Event) -> bool:
    if session.scalar(
        select(EventDeploymentProjection.deployment_id).where(
            EventDeploymentProjection.central_event_id == event.event_id
        )
    ):
        # Central already owns and can reconstruct deployed program state. Site-owned media/version
        # changes continue through the existing presentation and resumable replication events.
        return False
    snapshot = build_site_recovery_snapshot(session, event)
    SiteQueue(session).enqueue_outbox(
        event_type="site.event_recovery_snapshot.upserted",
        aggregate_type="event",
        aggregate_id=event.event_id,
        site_id=event.site_id,
        protocol_version=UPM_SYNC_PROTOCOL_VERSION,
        source_sequence=next_sequence(session),
        idempotency_key=f"site-event-recovery:{event.event_id}:{event.revision}",
        payload=OutboxPayload(
            source_system=SourceSystem.SITE,
            data=snapshot.model_dump(mode="json"),
        ),
    )
    return True


def touch_site_recovery_snapshot(session: Session, event: Event) -> bool:
    """Advance and publish only a Site-originated Event's complete recovery revision."""
    if session.scalar(
        select(EventDeploymentProjection.deployment_id).where(
            EventDeploymentProjection.central_event_id == event.event_id
        )
    ):
        return False
    event.revision += 1
    event.sync_state = SyncState.PENDING
    session.flush()
    return enqueue_site_recovery_snapshot(session, event)
