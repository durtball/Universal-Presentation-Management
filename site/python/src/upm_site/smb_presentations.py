"""Offline Site reconciliation of local canonical media into the read-only SMB view."""

from sqlalchemy import select

from upm_shared.enums import (
    AssetKind,
    ExternalEntityType,
    MediaAvailability,
    PresentationIdentifierSource,
    PresentationWorkflowStatus,
)
from upm_shared.identifiers import new_uuid7
from upm_shared.smb_materialization import (
    MaterializationItem,
    paths_for,
    with_collision_suffixes,
)
from upm_site.persistence.models import (
    Event,
    EventParticipation,
    ExternalIdentifierProjection,
    MediaObject,
    PersonProjection,
    Presentation,
    PresentationAsset,
    PresentationPresenter,
    PresentationSession,
    PresentationVersion,
    ProcessingJob,
    Room,
    RoomAssignment,
    Session,
    SessionParticipant,
    utc_now,
)

JOB = "smb.presentations.reconcile"
VISIBLE = {
    PresentationWorkflowStatus.RECEIVED,
    PresentationWorkflowStatus.UPDATED,
    PresentationWorkflowStatus.APPROVED,
    PresentationWorkflowStatus.READY,
    PresentationWorkflowStatus.DEPLOYED,
}
ACTIVE = {"pending", "running", "retry_wait"}


def enqueue(session, site_id, *, delay_seconds=30, current_job_id=None):
    existing = session.scalar(
        select(ProcessingJob).where(
            ProcessingJob.site_id == site_id,
            ProcessingJob.job_type == JOB,
            ProcessingJob.status.in_(ACTIVE),
            ProcessingJob.processing_job_id != current_job_id,
        )
    )
    if existing:
        return existing
    from datetime import timedelta

    job = ProcessingJob(
        site_id=site_id,
        job_type=JOB,
        payload={"schema_version": 1, "data": {"site_id": str(site_id)}},
        idempotency_key=f"smb-presentations:{new_uuid7()}",
        required_capabilities=["cpu"],
        next_attempt_at=utc_now() + timedelta(seconds=delay_seconds),
    )
    session.add(job)
    return job


def reconcile(session, storage, *, site_id, current_job_id=None, interval_seconds=30):
    entries = []
    all_items = []
    for presentation in session.scalars(
        select(Presentation).where(
            Presentation.active.is_(True), Presentation.workflow_status.in_(VISIBLE)
        )
    ):
        event = session.get(Event, presentation.event_id)
        if event is None or event.site_id != site_id or event.archived_at is not None:
            continue
        version = session.scalar(
            select(PresentationVersion)
            .where(PresentationVersion.presentation_id == presentation.presentation_id)
            .order_by(PresentationVersion.version_number.desc())
        )
        if version is None:
            continue
        row = session.execute(
            select(PresentationAsset, MediaObject)
            .join(MediaObject, MediaObject.media_object_id == PresentationAsset.media_object_id)
            .where(
                PresentationAsset.presentation_version_id == version.presentation_version_id,
                PresentationAsset.kind == AssetKind.ORIGINAL,
                MediaObject.availability == MediaAvailability.AVAILABLE,
                MediaObject.deleted_at.is_(None),
            )
        ).first()
        if row is None:
            continue
        asset, media = row
        session_ids = {
            x.session_id
            for x in session.scalars(
                select(PresentationSession).where(
                    PresentationSession.presentation_id == presentation.presentation_id,
                    PresentationSession.active.is_(True),
                )
            )
        }
        if presentation.session_id:
            session_ids.add(presentation.session_id)
        people = session.execute(
            select(
                PersonProjection.family_name,
                PersonProjection.display_name,
                EventParticipation.display_name,
            )
            .join(EventParticipation, EventParticipation.person_id == PersonProjection.person_id)
            .join(
                PresentationPresenter,
                PresentationPresenter.event_participation_id
                == EventParticipation.event_participation_id,
            )
            .where(
                PresentationPresenter.presentation_id == presentation.presentation_id,
                PresentationPresenter.active.is_(True),
            )
            .order_by(
                PresentationPresenter.presenter_order,
                PresentationPresenter.presentation_presenter_id,
            )
        ).all()
        if not people and session_ids:
            people = session.execute(
                select(
                    PersonProjection.family_name,
                    PersonProjection.display_name,
                    EventParticipation.display_name,
                )
                .join(
                    EventParticipation, EventParticipation.person_id == PersonProjection.person_id
                )
                .join(
                    SessionParticipant,
                    SessionParticipant.event_participation_id
                    == EventParticipation.event_participation_id,
                )
                .where(
                    SessionParticipant.session_id.in_(session_ids),
                    SessionParticipant.active.is_(True),
                )
                .order_by(
                    SessionParticipant.presenter_order, SessionParticipant.session_participant_id
                )
            ).all()
        presenters = tuple(dict.fromkeys(a or b or c for a, b, c in people if a or b or c))
        external_id = session.scalar(
            select(ExternalIdentifierProjection.external_id)
            .where(
                ExternalIdentifierProjection.entity_type == ExternalEntityType.PRESENTATION,
                ExternalIdentifierProjection.entity_id == presentation.presentation_id,
                ExternalIdentifierProjection.active.is_(True),
            )
            .order_by(
                ExternalIdentifierProjection.created_at,
                ExternalIdentifierProjection.external_identifier_id,
            )
        )
        base = dict(
            presentation_id=presentation.presentation_id,
            version_id=version.presentation_version_id,
            event_id=event.event_id,
            event_name=event.name,
            event_timezone=event.timezone,
            presentation_identifier=external_id
            or presentation.external_presentation_id
            or (
                presentation.presentation_identifier
                if presentation.presentation_identifier_source
                != PresentationIdentifierSource.GENERATED
                else None
            )
            or str(presentation.presentation_id)[:8],
            title=presentation.title,
            presenters=presenters,
            original_filename=asset.original_filename or media.original_filename,
            storage_target_id=media.storage_target_id,
            storage_key=media.object_key,
            sha256=media.content_hash,
        )
        items = [MaterializationItem(**base)]
        for sid in session_ids:
            scheduled = session.get(Session, sid)
            if scheduled is None or not scheduled.active:
                continue
            assignment = session.scalar(
                select(RoomAssignment).where(
                    RoomAssignment.session_id == sid, RoomAssignment.active.is_(True)
                )
            )
            room = session.get(Room, assignment.room_id) if assignment else None
            items.append(
                MaterializationItem(
                    **base,
                    session_id=sid,
                    session_external_id=scheduled.session_code,
                    session_title=scheduled.title,
                    starts_at=scheduled.starts_at,
                    room_name=room.label if room else scheduled.location_name,
                )
            )
        all_items.extend(items)
    for item in with_collision_suffixes(all_items):
        entries.extend(
            {
                "relative_path": path,
                "storage_target_id": str(item.storage_target_id),
                "storage_key": item.storage_key,
                "sha256": item.sha256,
            }
            for path in paths_for(item)
        )
    result = storage.reconcile_smb_presentations(entries)
    enqueue(session, site_id, delay_seconds=interval_seconds, current_job_id=current_job_id)
    return result
