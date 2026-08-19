"""Offline Site reconciliation of local canonical media into the read-only SMB view."""

from pathlib import Path

from sqlalchemy import select

from upm_shared.enums import AssetKind, MediaAvailability, PresentationWorkflowStatus
from upm_shared.identifiers import new_uuid7
from upm_shared.smb_materialization import MaterializationItem, paths_for
from upm_site.persistence.models import (
    Event,
    EventParticipation,
    MediaObject,
    Presentation,
    PresentationAsset,
    PresentationPresenter,
    PresentationSession,
    PresentationVersion,
    ProcessingJob,
    Room,
    RoomAssignment,
    Session,
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
        _asset, media = row
        link = session.scalar(
            select(PresentationPresenter)
            .where(
                PresentationPresenter.presentation_id == presentation.presentation_id,
                PresentationPresenter.active.is_(True),
            )
            .order_by(
                PresentationPresenter.primary_presenter.desc(),
                PresentationPresenter.presenter_order,
            )
        )
        participation = (
            session.get(EventParticipation, link.event_participation_id) if link else None
        )
        presenter = (
            participation.display_name
            if participation and participation.display_name
            else "Unknown Presenter"
        )
        base = dict(
            presentation_id=presentation.presentation_id,
            version_id=version.presentation_version_id,
            event_id=event.event_id,
            event_name=event.name,
            event_timezone=event.timezone,
            title=presentation.title,
            presenter=presenter,
            extension=Path(media.canonical_filename or media.original_filename).suffix,
            storage_target_id=media.storage_target_id,
            storage_key=media.object_key,
            sha256=media.content_hash,
        )
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
        for item in items:
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
