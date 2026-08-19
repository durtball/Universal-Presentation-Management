"""Durable reconciliation of Central canonical media into the read-only SMB view."""

from pathlib import Path

from sqlalchemy import select

from upm_central.persistence.models import (
    Event,
    EventParticipation,
    MediaObjectReplica,
    MediaReplicationReceiveSession,
    Presentation,
    PresentationAsset,
    PresentationPresenter,
    PresentationSession,
    PresentationVersion,
    ProcessingJob,
    Session,
)
from upm_shared.enums import AssetKind, PresentationWorkflowStatus
from upm_shared.identifiers import new_uuid7
from upm_shared.smb_materialization import MaterializationItem, paths_for

JOB = "smb.presentations.reconcile"
VISIBLE = {
    PresentationWorkflowStatus.RECEIVED,
    PresentationWorkflowStatus.UPDATED,
    PresentationWorkflowStatus.APPROVED,
    PresentationWorkflowStatus.READY,
    PresentationWorkflowStatus.DEPLOYED,
}
ACTIVE = {"pending", "running", "retry_wait"}


def enqueue(session, *, delay_seconds: float = 30, current_job_id=None):
    existing = session.scalar(
        select(ProcessingJob).where(
            ProcessingJob.job_type == JOB,
            ProcessingJob.status.in_(ACTIVE),
            ProcessingJob.processing_job_id != current_job_id,
        )
    )
    if existing:
        return existing
    from datetime import timedelta

    from upm_central.persistence.models import utc_now

    job = ProcessingJob(
        job_type=JOB,
        payload={"schema_version": 1, "data": {}},
        idempotency_key=f"smb-presentations:{new_uuid7()}",
        required_capabilities=["cpu"],
        next_attempt_at=utc_now() + timedelta(seconds=delay_seconds),
    )
    session.add(job)
    return job


def reconcile(session, storage, *, current_job_id=None, interval_seconds=30):
    entries = []
    presentations = session.scalars(
        select(Presentation).where(Presentation.workflow_status.in_(VISIBLE))
    ).all()
    for presentation in presentations:
        event = session.get(Event, presentation.event_id)
        if event is None or event.archived_at is not None:
            continue
        version = session.scalar(
            select(PresentationVersion)
            .where(PresentationVersion.presentation_id == presentation.presentation_id)
            .order_by(PresentationVersion.version_number.desc())
        )
        if version is None:
            continue
        row = session.execute(
            select(PresentationAsset, MediaObjectReplica)
            .join(
                MediaObjectReplica,
                MediaObjectReplica.media_object_id == PresentationAsset.media_object_id,
            )
            .where(
                PresentationAsset.presentation_version_id == version.presentation_version_id,
                PresentationAsset.kind == AssetKind.ORIGINAL,
            )
        ).first()
        if row is None:
            continue
        _asset, media = row
        if not media.object_key or not media.content_hash or media.size_bytes is None:
            continue
        presenter_link = session.scalar(
            select(PresentationPresenter)
            .where(PresentationPresenter.presentation_id == presentation.presentation_id)
            .order_by(
                PresentationPresenter.primary_presenter.desc(),
                PresentationPresenter.presenter_order,
            )
        )
        participation = (
            session.get(EventParticipation, presenter_link.event_participation_id)
            if presenter_link
            else None
        )
        presenter = (
            participation.display_name
            if participation and participation.display_name
            else "Unknown Presenter"
        )
        session_ids = {
            link.session_id
            for link in session.scalars(
                select(PresentationSession).where(
                    PresentationSession.presentation_id == presentation.presentation_id
                )
            )
        }
        if presentation.session_id:
            session_ids.add(presentation.session_id)
        base = dict(
            presentation_id=presentation.presentation_id,
            version_id=version.presentation_version_id,
            event_id=event.event_id,
            event_name=event.name,
            event_timezone=event.timezone,
            title=presentation.title,
            presenter=presenter,
            extension="",
            storage_target_id=media.authoritative_site_id,
            storage_key=media.object_key,
            sha256=media.content_hash,
        )
        # Central replicas use their deployment-local committed root recorded by the import.
        imports = __import__(
            "upm_central.persistence.models", fromlist=["PresentationMediaImport"]
        ).PresentationMediaImport
        imported = session.scalar(
            select(imports).where(
                imports.presentation_version_id == version.presentation_version_id,
                imports.committed_storage_key == media.object_key,
            )
        )
        if imported is not None and imported.committed_storage_root_id is not None:
            base["storage_target_id"] = imported.committed_storage_root_id
            base["extension"] = Path(
                imported.canonical_filename or imported.original_filename
            ).suffix
        else:
            replica = session.scalar(
                select(MediaReplicationReceiveSession).where(
                    MediaReplicationReceiveSession.finalized_media_object_id
                    == media.media_object_id
                )
            )
            if replica is None or replica.storage_target_id is None:
                continue
            base["storage_target_id"] = replica.storage_target_id
            base["extension"] = Path(replica.canonical_filename or replica.original_filename).suffix
        items = [MaterializationItem(**base)]
        for sid in session_ids:
            scheduled = session.get(Session, sid)
            if scheduled:
                items.append(
                    MaterializationItem(
                        **base,
                        session_id=sid,
                        session_external_id=scheduled.session_code,
                        session_title=scheduled.title,
                        starts_at=scheduled.starts_at,
                        room_name=scheduled.location_name,
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
    enqueue(session, delay_seconds=interval_seconds, current_job_id=current_job_id)
    return result
