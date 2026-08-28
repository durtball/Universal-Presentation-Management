"""Explicit, retry-safe Central lifecycle deletion orchestration."""

from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session, aliased

from upm_central.event_deployments import push_deployment
from upm_central.persistence.models import (
    AuditRecord,
    DeletionOperation,
    Event,
    EventDeployment,
    EventDeploymentRevision,
    EventParticipation,
    ExternalIdentifier,
    ImportBatch,
    ImportRow,
    ImportSource,
    ImportValidationIssue,
    MediaObjectReplica,
    MediaReplicationReceiveSession,
    OutboxEvent,
    Person,
    PersonIdentityLink,
    PersonIdentitySignal,
    Presentation,
    PresentationAsset,
    PresentationMediaImport,
    PresentationPresenter,
    PresentationSession,
    PresentationVersion,
    ProcessingJob,
    ReconciliationDecision,
    RetainedPersonHistory,
    SessionParticipant,
    SyncEvent,
    TransferJob,
    utc_now,
)
from upm_central.persistence.models import (
    Session as ProgramSession,
)
from upm_central.persistence.queue import CentralQueue
from upm_central.program import touch_event_program
from upm_central.sync import next_sequence
from upm_shared.enums import JobPriority, JobStatus, SourceSystem
from upm_shared.identifiers import new_uuid7
from upm_shared.jobs import (
    PRIORITY_VALUES,
    BulkPeopleDeletionJobData,
    BulkPeopleDeletionJobPayload,
    LifecycleDeletionJobData,
    LifecycleDeletionJobPayload,
    OutboxPayload,
)


def _count(session: Session, model, *criteria) -> int:
    return int(session.scalar(select(func.count()).select_from(model).where(*criteria)) or 0)


def event_impact(session: Session, event_id: UUID) -> dict[str, int]:
    session_ids = select(ProgramSession.session_id).where(ProgramSession.event_id == event_id)
    presentation_ids = select(Presentation.presentation_id).where(Presentation.event_id == event_id)
    version_ids = select(PresentationVersion.presentation_version_id).where(
        PresentationVersion.presentation_id.in_(presentation_ids)
    )
    return {
        "sessions": _count(session, ProgramSession, ProgramSession.event_id == event_id),
        "presenters": _count(session, EventParticipation, EventParticipation.event_id == event_id),
        "presentations": _count(session, Presentation, Presentation.event_id == event_id),
        "rooms": len(
            set(
                session.scalars(
                    select(ProgramSession.location_name).where(
                        ProgramSession.event_id == event_id,
                        ProgramSession.location_name.is_not(None),
                    )
                )
            )
        ),
        "media_files": _count(
            session, PresentationAsset, PresentationAsset.presentation_version_id.in_(version_ids)
        ),
        "site_deployments": _count(session, EventDeployment, EventDeployment.event_id == event_id),
        "imports": _count(session, ImportBatch, ImportBatch.event_id == event_id),
        "presentation_media_imports": _count(
            session, PresentationMediaImport, PresentationMediaImport.event_id == event_id
        ),
        "media_replication_sessions": _count(
            session,
            MediaReplicationReceiveSession,
            MediaReplicationReceiveSession.event_id == event_id,
        ),
        "outbox_events_retained": _count(session, OutboxEvent, OutboxEvent.event_id == event_id),
        "sync_events_retained": _count(session, SyncEvent, SyncEvent.event_id == event_id),
        "audit_records_retained": _count(session, AuditRecord, AuditRecord.event_id == event_id),
        "session_participations": _count(
            session, SessionParticipant, SessionParticipant.session_id.in_(session_ids)
        ),
    }


def person_deletion_impact(session: Session, person_id: UUID) -> dict[str, int]:
    participation_ids = select(EventParticipation.event_participation_id).where(
        EventParticipation.person_id == person_id
    )
    retained = _count(session, RetainedPersonHistory, RetainedPersonHistory.person_id == person_id)
    return {
        "event_participations": _count(
            session, EventParticipation, EventParticipation.person_id == person_id
        ),
        "session_participations": _count(
            session,
            SessionParticipant,
            SessionParticipant.event_participation_id.in_(participation_ids),
        ),
        "presentation_relationships": _count(
            session,
            PresentationPresenter,
            PresentationPresenter.event_participation_id.in_(participation_ids),
        ),
        "retained_history": retained,
        "identity_signals": _count(
            session, PersonIdentitySignal, PersonIdentitySignal.person_id == person_id
        ),
        "identity_links": _count(
            session,
            PersonIdentityLink,
            (PersonIdentityLink.person_id == person_id)
            | (PersonIdentityLink.linked_person_id == person_id),
        ),
        "media_files": 0,
    }


def bulk_people_impact(session: Session) -> dict[str, int]:
    """Return an aggregate preview for every currently active permanent identity."""
    person_ids = select(Person.person_id).where(Person.deleted_at.is_(None))
    participation_ids = select(EventParticipation.event_participation_id).where(
        EventParticipation.person_id.in_(person_ids)
    )
    return {
        "people": _count(session, Person, Person.deleted_at.is_(None)),
        "event_participations": _count(
            session, EventParticipation, EventParticipation.person_id.in_(person_ids)
        ),
        "session_participations": _count(
            session,
            SessionParticipant,
            SessionParticipant.event_participation_id.in_(participation_ids),
        ),
        "presentation_relationships": _count(
            session,
            PresentationPresenter,
            PresentationPresenter.event_participation_id.in_(participation_ids),
        ),
        "retained_history": _count(
            session, RetainedPersonHistory, RetainedPersonHistory.person_id.in_(person_ids)
        ),
        "identity_signals": _count(
            session, PersonIdentitySignal, PersonIdentitySignal.person_id.in_(person_ids)
        ),
        "identity_links": _count(
            session,
            PersonIdentityLink,
            (PersonIdentityLink.person_id.in_(person_ids))
            | (PersonIdentityLink.linked_person_id.in_(person_ids)),
        ),
        "media_files": 0,
    }


def request_bulk_people_deletion(
    session: Session, *, confirmation: str, actor: str
) -> DeletionOperation:
    if confirmation != "delete all":
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="type delete all exactly to confirm",
        )
    existing = session.scalar(
        select(DeletionOperation)
        .where(
            DeletionOperation.target_type == "people_bulk",
            DeletionOperation.status.in_(["pending", "running", "retry_wait"]),
        )
        .order_by(DeletionOperation.created_at.desc())
        .limit(1)
    )
    if existing is not None:
        return existing
    person_ids = list(
        session.scalars(
            select(Person.person_id).where(Person.deleted_at.is_(None)).order_by(Person.person_id)
        )
    )
    if not person_ids:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="there are no people to delete")
    operation = DeletionOperation(
        target_type="people_bulk",
        target_id=new_uuid7(),
        target_display_name="All Permanent People",
        initiated_by=actor,
        dependency_counts=bulk_people_impact(session),
        site_statuses=[],
    )
    session.add(operation)
    session.flush()
    CentralQueue(session).enqueue_processing(
        job_type="lifecycle.delete_people_bulk",
        payload=BulkPeopleDeletionJobPayload(
            data=BulkPeopleDeletionJobData(
                deletion_operation_id=operation.deletion_operation_id,
                person_ids=person_ids,
            )
        ),
        idempotency_key=str(operation.deletion_operation_id),
        max_attempts=10,
        priority=PRIORITY_VALUES[JobPriority.HIGH],
        required_capabilities=[],
    )
    session.add(
        AuditRecord(
            actor_id=actor,
            action="central.people_bulk.deletion_requested",
            target_type="people_bulk",
            target_id=operation.target_id,
            before_context={"person_count": len(person_ids)},
            after_context={
                "operation_id": str(operation.deletion_operation_id),
                "counts": operation.dependency_counts,
            },
        )
    )
    return operation


def request_deletion(
    session: Session, target_type: str, target_id: UUID, confirmation: str, actor: str
) -> DeletionOperation:
    target = session.get(Event if target_type == "event" else Person, target_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"{target_type} not found")
    name = target.name if target_type == "event" else target.display_name
    if confirmation != name:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"type the exact {target_type} name to confirm",
        )
    existing = session.scalar(
        select(DeletionOperation).where(
            DeletionOperation.target_type == target_type, DeletionOperation.target_id == target_id
        )
    )
    if existing:
        return existing
    impact = (
        event_impact(session, target_id)
        if target_type == "event"
        else person_deletion_impact(session, target_id)
    )
    sites = (
        [
            {
                "site_id": str(d.site_id),
                "display_name": d.site.display_name if getattr(d, "site", None) else str(d.site_id),
                "status": "pending",
            }
            for d in session.scalars(
                select(EventDeployment).where(EventDeployment.event_id == target_id)
            )
        ]
        if target_type == "event"
        else []
    )
    operation = DeletionOperation(
        target_type=target_type,
        target_id=target_id,
        target_display_name=name,
        initiated_by=actor,
        dependency_counts=impact,
        site_statuses=sites,
    )
    session.add(operation)
    session.flush()
    CentralQueue(session).enqueue_processing(
        job_type=f"lifecycle.delete_{target_type}",
        payload=LifecycleDeletionJobPayload(
            data=LifecycleDeletionJobData(deletion_operation_id=operation.deletion_operation_id)
        ),
        idempotency_key=str(operation.deletion_operation_id),
        max_attempts=10,
        priority=PRIORITY_VALUES[JobPriority.HIGH],
        required_capabilities=[],
    )
    session.add(
        AuditRecord(
            actor_id=actor,
            action=f"central.{target_type}.deletion_requested",
            target_type=target_type,
            target_id=target_id,
            event_id=target_id if target_type == "event" else None,
            before_context={"display_name": name},
            after_context={"operation_id": str(operation.deletion_operation_id), "counts": impact},
        )
    )
    return operation


def retry_deletion(session: Session, operation: DeletionOperation) -> DeletionOperation:
    """Requeue a corrected failed operation without changing its stable identity."""
    if operation.status != "failed":
        raise HTTPException(status.HTTP_409_CONFLICT, detail="only failed deletions can be retried")
    job = session.scalar(
        select(ProcessingJob).where(
            ProcessingJob.idempotency_key == str(operation.deletion_operation_id),
            ProcessingJob.job_type.like("lifecycle.delete%"),
        )
    )
    if job is None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="deletion job is unavailable")
    job.status = JobStatus.PENDING
    job.attempt_count = 0
    job.next_attempt_at = utc_now()
    job.completed_at = None
    job.error_code = None
    job.last_error = None
    job.error_metadata = None
    operation.status = "pending"
    operation.stage = "queued"
    operation.last_error = None
    return operation


def run_deletion(session: Session, operation: DeletionOperation) -> None:
    """Execute ordered cleanup inside the worker's atomic database transaction."""
    if operation.status == "completed":
        return
    operation.status = "running"
    operation.stage = "central_cleanup"
    operation.attempt_count += 1
    if operation.target_type == "event":
        _delete_event(session, operation)
    else:
        affected_event_ids = set(
            session.scalars(
                select(EventParticipation.event_id).where(
                    EventParticipation.person_id == operation.target_id
                )
            )
        )
        _delete_person(session, operation)
        operation.site_statuses = _publish_people_deletion(
            session, operation, [operation.target_id], affected_event_ids
        )
    if operation.target_type == "event" and operation.site_statuses:
        operation.status = "awaiting_sites"
        operation.stage = "site_deletion_pending"
    else:
        operation.status = "completed"
        operation.stage = "completed"
        operation.completed_at = utc_now()
    session.add(
        AuditRecord(
            actor_id=operation.initiated_by,
            action=f"central.{operation.target_type}.deleted",
            target_type=operation.target_type,
            target_id=operation.target_id,
            event_id=operation.target_id if operation.target_type == "event" else None,
            before_context={"display_name": operation.target_display_name},
            after_context={
                "operation_id": str(operation.deletion_operation_id),
                "counts": operation.dependency_counts,
                "sites": operation.site_statuses,
                "media": operation.media_results,
                "state": operation.status,
            },
        )
    )


def run_bulk_people_deletion(
    session: Session, operation: DeletionOperation, person_ids: list[UUID]
) -> None:
    """Delete the request-time identity snapshot and republish affected Event projections."""
    if operation.status == "completed":
        return
    operation.status = "running"
    operation.stage = "central_cleanup"
    operation.attempt_count += 1
    affected_event_ids = set(
        session.scalars(
            select(EventParticipation.event_id).where(EventParticipation.person_id.in_(person_ids))
        )
    )
    deleted_count = 0
    for person_id in person_ids:
        person = session.get(Person, person_id)
        if person is None:
            continue
        _delete_person(session, operation, person=person)
        deleted_count += 1
    session.flush()
    operation.stage = "publishing_site_updates"
    operation.site_statuses = _publish_people_deletion(
        session, operation, person_ids, affected_event_ids
    )
    deployment_ids: list[str] = []
    for event_id in sorted(affected_event_ids, key=str):
        event = session.get(Event, event_id)
        if event is not None:
            active_deployment_ids = touch_event_program(session, event)
            for deployment_id in active_deployment_ids:
                deployment = session.get(EventDeployment, deployment_id)
                if deployment is not None:
                    push_deployment(session, deployment)
                    deployment_ids.append(str(deployment_id))
    operation.media_results = {"eligible_removed": 0, "shared_preserved": 0}
    operation.status = "completed"
    operation.stage = "completed"
    operation.completed_at = utc_now()
    session.add(
        AuditRecord(
            actor_id=operation.initiated_by,
            action="central.people_bulk.deleted",
            target_type="people_bulk",
            target_id=operation.target_id,
            before_context={"person_count": len(person_ids)},
            after_context={
                "operation_id": str(operation.deletion_operation_id),
                "requested_count": len(person_ids),
                "deleted_count": deleted_count,
                "counts": operation.dependency_counts,
                "deployments_published": deployment_ids,
                "state": "completed",
            },
        )
    )


def _publish_people_deletion(
    session: Session,
    operation: DeletionOperation,
    person_ids: list[UUID],
    affected_event_ids: set[UUID],
) -> list[dict[str, object]]:
    """Publish one ordered tombstone per Site through the existing protocol-v1 outbox."""
    if not affected_event_ids:
        return []
    site_ids = set(
        session.scalars(
            select(EventDeployment.site_id).where(EventDeployment.event_id.in_(affected_event_ids))
        )
    )
    statuses: list[dict[str, object]] = []
    for site_id in sorted(site_ids, key=str):
        CentralQueue(session).enqueue_outbox(
            event_type="central.people.deleted",
            aggregate_type="people_bulk",
            aggregate_id=operation.target_id,
            owning_site_id=site_id,
            event_id=None,
            source_sequence=next_sequence(session, site_id),
            idempotency_key=f"people-delete:{operation.deletion_operation_id}:{site_id}",
            payload=OutboxPayload(
                source_system=SourceSystem.CENTRAL,
                schema_version=1,
                data={
                    "deletion_operation_id": str(operation.deletion_operation_id),
                    "person_ids": [str(person_id) for person_id in person_ids],
                },
            ),
        )
        statuses.append({"site_id": str(site_id), "status": "pending"})
    return statuses


def _delete_event(session: Session, op: DeletionOperation) -> None:
    event = session.get(Event, op.target_id)
    if event is None:
        return
    participations = session.scalars(
        select(EventParticipation).where(EventParticipation.event_id == event.event_id)
    ).all()
    for p in participations:
        exists = session.scalar(
            select(RetainedPersonHistory).where(
                RetainedPersonHistory.person_id == p.person_id,
                RetainedPersonHistory.source_event_id == event.event_id,
            )
        )
        if not exists:
            session.add(
                RetainedPersonHistory(
                    person_id=p.person_id,
                    source_event_id=event.event_id,
                    event_name=event.name,
                    participation_summary={
                        "role": p.role,
                        "display_name": p.display_name,
                        "is_presenter": p.is_presenter,
                        "source": p.source,
                    },
                )
            )
    deployment_rows = session.scalars(
        select(EventDeployment).where(EventDeployment.event_id == event.event_id)
    ).all()
    site_statuses: list[dict[str, object]] = []
    for deployment in deployment_rows:
        CentralQueue(session).enqueue_outbox(
            event_type="central.event.deleted",
            aggregate_type="event",
            aggregate_id=event.event_id,
            owning_site_id=deployment.site_id,
            event_id=None,
            source_sequence=next_sequence(session, deployment.site_id),
            idempotency_key=f"event-delete:{event.event_id}:{deployment.site_id}",
            payload=OutboxPayload(
                source_system=SourceSystem.CENTRAL,
                schema_version=1,
                data={
                    "event_id": str(event.event_id),
                    "deletion_operation_id": str(op.deletion_operation_id),
                },
            ),
        )
        site_statuses.append(
            {
                "site_id": str(deployment.site_id),
                "status": "pending",
                "deletion_operation_id": str(op.deletion_operation_id),
            }
        )
    op.site_statuses = site_statuses
    pids = select(Presentation.presentation_id).where(Presentation.event_id == event.event_id)
    vids = select(PresentationVersion.presentation_version_id).where(
        PresentationVersion.presentation_id.in_(pids)
    )
    media_ids = set(
        session.scalars(
            select(PresentationAsset.media_object_id).where(
                PresentationAsset.presentation_version_id.in_(vids)
            )
        )
    )
    media_ids.update(
        session.scalars(
            select(MediaObjectReplica.media_object_id).where(
                MediaObjectReplica.event_id == event.event_id
            )
        )
    )
    import_transfer_ids = set(
        session.scalars(
            select(PresentationMediaImport.transfer_job_id).where(
                PresentationMediaImport.event_id == event.event_id,
                PresentationMediaImport.transfer_job_id.is_not(None),
            )
        )
    )
    shared_import = aliased(PresentationMediaImport)
    shared_replica = aliased(MediaObjectReplica)
    physical_objects = [
        {
            "storage_target_id": str(root_id),
            "object_key": key,
        }
        for root_id, key in session.execute(
            select(
                PresentationMediaImport.committed_storage_root_id,
                PresentationMediaImport.committed_storage_key,
            ).where(
                PresentationMediaImport.event_id == event.event_id,
                PresentationMediaImport.committed_storage_root_id.is_not(None),
                PresentationMediaImport.committed_storage_key.is_not(None),
                ~select(shared_import.media_import_id)
                .where(
                    shared_import.event_id != event.event_id,
                    shared_import.committed_storage_root_id
                    == PresentationMediaImport.committed_storage_root_id,
                    shared_import.committed_storage_key
                    == PresentationMediaImport.committed_storage_key,
                )
                .exists(),
                ~select(shared_replica.media_object_id)
                .where(
                    shared_replica.event_id != event.event_id,
                    shared_replica.object_key == PresentationMediaImport.committed_storage_key,
                )
                .exists(),
            )
        )
    ]
    disposition_objects = [
        {"storage_target_id": str(root_id), "object_key": key}
        for root_id, key in session.execute(
            select(
                PresentationMediaImport.intake_storage_root_id,
                PresentationMediaImport.intake_storage_key,
            ).where(
                PresentationMediaImport.event_id == event.event_id,
                PresentationMediaImport.intake_storage_root_id.is_not(None),
                PresentationMediaImport.intake_storage_key.is_not(None),
            )
        )
    ]
    disposition_objects.extend(
        {"storage_target_id": str(root_id), "object_key": key}
        for root_id, key in session.execute(
            select(
                PresentationMediaImport.rejected_storage_root_id,
                PresentationMediaImport.rejected_storage_key,
            ).where(
                PresentationMediaImport.event_id == event.event_id,
                PresentationMediaImport.rejected_storage_root_id.is_not(None),
                PresentationMediaImport.rejected_storage_key.is_not(None),
            )
        )
    )
    staging_objects = [
        {"storage_target_id": str(root_id), "object_key": key}
        for root_id, key in session.execute(
            select(
                PresentationMediaImport.staging_storage_root_id,
                PresentationMediaImport.staging_key,
            ).where(
                PresentationMediaImport.event_id == event.event_id,
                PresentationMediaImport.staging_storage_root_id.is_not(None),
            )
        )
    ]
    physical_objects.extend(disposition_objects)
    # Imports and receive sessions can reference presentations, versions, transfer
    # jobs, and replicas.  Remove these subordinate operational rows first,
    # including failed/unmatched imports which have no Presentation relationship.
    session.execute(
        delete(PresentationMediaImport).where(PresentationMediaImport.event_id == event.event_id)
    )
    session.execute(
        delete(MediaReplicationReceiveSession).where(
            MediaReplicationReceiveSession.event_id == event.event_id
        )
    )
    session.execute(
        delete(ProcessingJob).where(
            (ProcessingJob.payload["event_id"].as_string() == str(event.event_id))
            | (ProcessingJob.payload["data"]["event_id"].as_string() == str(event.event_id))
        )
    )
    session.execute(
        delete(TransferJob).where(
            (TransferJob.payload["event_id"].as_string() == str(event.event_id))
            | (TransferJob.payload["data"]["event_id"].as_string() == str(event.event_id))
        )
    )
    if import_transfer_ids:
        session.execute(
            delete(TransferJob).where(TransferJob.transfer_job_id.in_(import_transfer_ids))
        )
    session.execute(
        delete(PresentationAsset).where(PresentationAsset.presentation_version_id.in_(vids))
    )
    session.execute(
        delete(PresentationVersion).where(PresentationVersion.presentation_id.in_(pids))
    )
    session.execute(
        delete(PresentationPresenter).where(PresentationPresenter.presentation_id.in_(pids))
    )
    session.execute(
        delete(PresentationSession).where(PresentationSession.presentation_id.in_(pids))
    )
    session.execute(delete(Presentation).where(Presentation.event_id == event.event_id))
    sids = select(ProgramSession.session_id).where(ProgramSession.event_id == event.event_id)
    epids = select(EventParticipation.event_participation_id).where(
        EventParticipation.event_id == event.event_id
    )
    session.execute(
        delete(SessionParticipant).where(
            (SessionParticipant.session_id.in_(sids))
            | (SessionParticipant.event_participation_id.in_(epids))
        )
    )
    session.execute(delete(ProgramSession).where(ProgramSession.event_id == event.event_id))
    session.execute(delete(EventParticipation).where(EventParticipation.event_id == event.event_id))
    batches = session.scalars(
        select(ImportBatch).where(ImportBatch.event_id == event.event_id)
    ).all()
    for batch in batches:
        rows = select(ImportRow.import_row_id).where(
            ImportRow.import_batch_id == batch.import_batch_id
        )
        session.execute(
            delete(ReconciliationDecision).where(ReconciliationDecision.import_row_id.in_(rows))
        )
        session.execute(
            delete(ImportValidationIssue).where(ImportValidationIssue.import_row_id.in_(rows))
        )
        session.execute(delete(ImportRow).where(ImportRow.import_batch_id == batch.import_batch_id))
        source_id = batch.import_source_id
        session.delete(batch)
        session.flush()
        if not session.scalar(
            select(ImportBatch.import_batch_id).where(ImportBatch.import_source_id == source_id)
        ):
            session.execute(delete(ImportSource).where(ImportSource.import_source_id == source_id))
    deployment_ids = select(EventDeployment.deployment_id).where(
        EventDeployment.event_id == event.event_id
    )
    session.execute(
        delete(EventDeploymentRevision).where(
            EventDeploymentRevision.deployment_id.in_(deployment_ids)
        )
    )
    session.execute(delete(EventDeployment).where(EventDeployment.event_id == event.event_id))
    session.execute(delete(ExternalIdentifier).where(ExternalIdentifier.event_id == event.event_id))
    # Transport and audit history survive independently.  The deletion tombstone
    # above is event_id-free and remains deliverable to an offline Site.  Older
    # envelopes retain their payload/sequence while dropping only the live FK.
    session.execute(
        update(SyncEvent).where(SyncEvent.event_id == event.event_id).values(event_id=None)
    )
    session.execute(
        update(OutboxEvent).where(OutboxEvent.event_id == event.event_id).values(event_id=None)
    )
    # AuditRecord.event_id is intentionally a historical UUID without a live FK.
    deleted_media = 0
    for media_id in media_ids:
        if not session.scalar(
            select(PresentationAsset.presentation_asset_id).where(
                PresentationAsset.media_object_id == media_id
            )
        ):
            session.execute(delete(ProcessingJob).where(ProcessingJob.media_object_id == media_id))
            session.execute(delete(TransferJob).where(TransferJob.media_object_id == media_id))
            session.execute(
                delete(MediaObjectReplica).where(MediaObjectReplica.media_object_id == media_id)
            )
            deleted_media += 1
    # Shared objects lose only the deleted Event association; their stable media identity remains.
    session.execute(
        update(MediaObjectReplica)
        .where(MediaObjectReplica.event_id == event.event_id)
        .values(event_id=None)
    )
    op.media_results = {
        "eligible_removed": deleted_media,
        "shared_preserved": len(media_ids) - deleted_media,
        "physical_cleanup_queued": len(physical_objects),
        "staging_cleanup_queued": len(staging_objects),
    }
    if physical_objects or staging_objects:
        CentralQueue(session).enqueue_processing(
            job_type="lifecycle.delete_media_objects",
            payload={
                "data": {
                    "event_id": str(event.event_id),
                    "deletion_operation_id": str(op.deletion_operation_id),
                    "objects": physical_objects,
                    "staging": staging_objects,
                }
            },
            idempotency_key=f"event-media-delete:{op.deletion_operation_id}",
            max_attempts=10,
            required_capabilities=["storage"],
        )
    session.delete(event)


def _delete_person(
    session: Session, op: DeletionOperation, *, person: Person | None = None
) -> None:
    person = person or session.get(Person, op.target_id)
    if person is None:
        return
    epids = select(EventParticipation.event_participation_id).where(
        EventParticipation.person_id == person.person_id
    )
    session.execute(
        delete(SessionParticipant).where(SessionParticipant.event_participation_id.in_(epids))
    )
    session.execute(
        delete(PresentationPresenter).where(PresentationPresenter.event_participation_id.in_(epids))
    )
    session.execute(
        delete(EventParticipation).where(EventParticipation.person_id == person.person_id)
    )
    session.execute(
        delete(RetainedPersonHistory).where(RetainedPersonHistory.person_id == person.person_id)
    )
    session.execute(
        delete(PersonIdentitySignal).where(PersonIdentitySignal.person_id == person.person_id)
    )
    session.execute(
        delete(PersonIdentityLink).where(
            (PersonIdentityLink.person_id == person.person_id)
            | (PersonIdentityLink.linked_person_id == person.person_id)
        )
    )
    session.execute(
        delete(ExternalIdentifier).where(ExternalIdentifier.entity_id == person.person_id)
    )
    session.execute(
        update(ImportRow)
        .where(
            (ImportRow.proposed_person_id == person.person_id)
            | (ImportRow.resolved_person_id == person.person_id)
        )
        .values(proposed_person_id=None, resolved_person_id=None)
    )
    session.execute(
        update(ReconciliationDecision)
        .where(ReconciliationDecision.selected_person_id == person.person_id)
        .values(selected_person_id=None)
    )
    session.delete(person)
    if op.target_type == "person":
        op.media_results = {"eligible_removed": 0, "shared_preserved": 0}
