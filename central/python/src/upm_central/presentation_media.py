"""Durable, streaming Central presentation-media staging and assignment."""

from __future__ import annotations

import logging
import multiprocessing
import re
from collections import Counter
from collections.abc import AsyncIterator
from concurrent.futures import Executor, ProcessPoolExecutor, ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from threading import Lock
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from upm_central.operational_logs import record_log
from upm_central.persistence.models import (
    AuditRecord,
    Event,
    EventDeployment,
    EventParticipation,
    MediaObjectReplica,
    OutboxEvent,
    Person,
    Presentation,
    PresentationAsset,
    PresentationMediaImport,
    PresentationMediaImportBatch,
    PresentationPresenter,
    PresentationVersion,
    ProcessingJob,
    StorageRoot,
    TransferJob,
    utc_now,
)
from upm_central.persistence.models import Session as ProgramSession
from upm_central.persistence.queue import CentralQueue
from upm_central.sync import next_sequence
from upm_shared.contracts.media_transfer import MediaTransferManifest
from upm_shared.contracts.sync import UPM_SYNC_PROTOCOL_VERSION
from upm_shared.enums import (
    AssetKind,
    EventDeploymentStatus,
    JobStatus,
    MediaCategory,
    MediaImportState,
    MediaMatchState,
    MediaTransferState,
    PresentationWorkflowStatus,
    SourceSystem,
    SyncState,
)
from upm_shared.identifiers import new_uuid7
from upm_shared.jobs import OutboxPayload
from upm_shared.media_storage_client import (
    AsyncMediaStorageClient,
    MediaStorageOperationError,
    MediaStorageUnavailable,
)
from upm_shared.presentation_media import (
    SUPPORTED_PRESENTATION_EXTENSIONS,
    CanonicalPresentationMetadata,
    MatchCandidate,
    MatchResult,
    canonical_presentation_filename,
    match_presentation,
    normalize_source_relative_path,
)

logger = logging.getLogger(__name__)
_PROCESS_MATCH_CANDIDATES: list[MatchCandidate] = []
_PROCESS_MATCH_TIMEZONE = "UTC"


def _initialize_rescan_matcher(
    candidates: list[MatchCandidate], event_timezone: str
) -> None:
    global _PROCESS_MATCH_CANDIDATES, _PROCESS_MATCH_TIMEZONE
    _PROCESS_MATCH_CANDIDATES = candidates
    _PROCESS_MATCH_TIMEZONE = event_timezone


def _match_rescan_process_item(source_path: str) -> MatchResult:
    return match_presentation(
        source_path, _PROCESS_MATCH_CANDIDATES, event_timezone=_PROCESS_MATCH_TIMEZONE
    )
ASSET_RECONCILIATION_JOB = "presentation_media.assets.reconcile"
INTAKE_PUBLISH_JOB = "presentation_media.intake.publish"
INTAKE_REJECT_JOB = "presentation_media.intake.reject"
RESCAN_JOB = "presentation_media.rescan"
RESCAN_BATCH_SIZE = 75
MATCH_ALGORITHM_VERSION = 2

ACTIVE_DEPLOYMENT_STATUSES = {
    EventDeploymentStatus.PENDING,
    EventDeploymentStatus.DEPLOYING,
    EventDeploymentStatus.DEPLOYED,
    EventDeploymentStatus.UPDATE_PENDING,
    EventDeploymentStatus.FAILED,
}


def enqueue_match_repair_rescans(session: Session) -> int:
    """Queue one deployment repair pass for existing review rows under this matcher version."""
    event_ids = session.scalars(
        select(PresentationMediaImport.event_id)
        .where(
            PresentationMediaImport.presentation_id.is_(None),
            PresentationMediaImport.match_state != MediaMatchState.CONFIRMED,
            PresentationMediaImport.import_state == MediaImportState.NEEDS_REVIEW,
        )
        .distinct()
    ).all()
    queued = 0
    for event_id in event_ids:
        key = f"match-repair-v{MATCH_ALGORITHM_VERSION}:{event_id}"
        if session.scalar(select(ProcessingJob).where(ProcessingJob.idempotency_key == key)):
            continue
        ids = list(
            session.scalars(
                select(PresentationMediaImport.media_import_id)
                .where(
                    PresentationMediaImport.event_id == event_id,
                    PresentationMediaImport.presentation_id.is_(None),
                    PresentationMediaImport.match_state != MediaMatchState.CONFIRMED,
                    PresentationMediaImport.import_state == MediaImportState.NEEDS_REVIEW,
                )
                .order_by(PresentationMediaImport.media_import_id)
            )
        )
        operation_id = new_uuid7()
        CentralQueue(session).enqueue_processing(
            job_type=RESCAN_JOB,
            payload={
                "data": {
                    "operation_id": str(operation_id),
                    "event_id": str(event_id),
                    "media_import_ids": [str(value) for value in ids],
                    "total": len(ids),
                    "processed": 0,
                    "suggested": 0,
                    "unmatched": 0,
                    "failed": 0,
                }
            },
            idempotency_key=key,
            required_capabilities=["cpu"],
            max_attempts=5,
        )
        queued += 1
    return queued


def enqueue_asset_reconciliation(session: Session, *, current_job_id=None):
    existing = session.scalar(
        select(ProcessingJob).where(
            ProcessingJob.job_type == ASSET_RECONCILIATION_JOB,
            ProcessingJob.status.in_(["pending", "running", "retry_wait"]),
            ProcessingJob.processing_job_id != current_job_id,
        )
    )
    if existing is not None:
        return existing
    job = ProcessingJob(
        job_type=ASSET_RECONCILIATION_JOB,
        payload={"schema_version": 1, "data": {}},
        idempotency_key=f"presentation-assets:{new_uuid7()}",
        required_capabilities=["cpu"],
        next_attempt_at=utc_now() + timedelta(seconds=30),
    )
    session.add(job)
    return job


def ensure_confirmed_original_asset(
    session: Session, record: PresentationMediaImport
) -> PresentationAsset:
    """Repair or create the canonical media linkage for one unambiguous confirmation."""
    if (
        record.match_state is not MediaMatchState.CONFIRMED
        or record.import_state is not MediaImportState.ASSIGNED
        or record.presentation_version_id is None
        or record.presentation_id is None
        or record.committed_storage_root_id is None
        or not record.committed_storage_key
        or not record.sha256
        or record.size_bytes is None
    ):
        raise MediaStagingError("confirmed media linkage is incomplete", "incomplete_confirmation")
    version = session.get(PresentationVersion, record.presentation_version_id)
    if version is None or version.presentation_id != record.presentation_id:
        raise MediaStagingError("confirmed presentation version is invalid", "invalid_confirmation")
    presentation = session.get(Presentation, record.presentation_id)
    if presentation is None:
        raise MediaStagingError("confirmed presentation is missing", "invalid_confirmation")
    presentation.workflow_status = PresentationWorkflowStatus.RECEIVED
    media_id = record.media_import_id
    media = session.get(MediaObjectReplica, media_id)
    if media is None:
        media = MediaObjectReplica(
            media_object_id=media_id,
            authoritative_site_id=record.destination_site_id,
            event_id=record.event_id,
            category=MediaCategory.PRESENTATION_VERSION,
            object_key=record.committed_storage_key,
            content_hash=record.sha256,
            size_bytes=record.size_bytes,
            source_revision=1,
        )
        session.add(media)
    elif (
        media.object_key != record.committed_storage_key
        or media.content_hash != record.sha256
        or media.size_bytes != record.size_bytes
    ):
        raise MediaStagingError("confirmed media identity conflicts", "media_identity_conflict")
    extension = Path(record.original_filename).suffix.lower()
    kind = (
        AssetKind.ORIGINAL
        if extension in SUPPORTED_PRESENTATION_EXTENSIONS
        else AssetKind.IMAGE
        if extension in {".jpg", ".jpeg", ".png"}
        else AssetKind.VIDEO
        if extension in {".mp4", ".mov", ".mkv", ".webm"}
        else AssetKind.DOCUMENT
        if extension in {".doc", ".docx", ".txt"}
        else AssetKind.OTHER
    )
    existing = session.scalar(
        select(PresentationAsset).where(
            PresentationAsset.presentation_version_id == record.presentation_version_id,
            PresentationAsset.kind == kind,
            PresentationAsset.media_object_id == media_id,
        )
    )
    if existing is not None:
        if existing.media_object_id != media_id:
            raise MediaStagingError("presentation version has another original", "asset_conflict")
        return existing
    asset = PresentationAsset(
        presentation_version_id=record.presentation_version_id,
        media_object_id=media_id,
        original_filename=record.original_filename,
        kind=kind,
    )
    session.add(asset)
    session.flush()
    return asset


def backfill_confirmed_original_assets(session: Session) -> int:
    """Backfill only confirmed imports that uniquely name their version and committed bytes."""
    repaired = 0
    records = list(
        session.scalars(
            select(PresentationMediaImport).where(
                PresentationMediaImport.match_state == MediaMatchState.CONFIRMED,
                PresentationMediaImport.import_state == MediaImportState.ASSIGNED,
                PresentationMediaImport.presentation_version_id.is_not(None),
                PresentationMediaImport.committed_storage_root_id.is_not(None),
                PresentationMediaImport.committed_storage_key.is_not(None),
                PresentationMediaImport.sha256.is_not(None),
                PresentationMediaImport.size_bytes.is_not(None),
            )
        )
    )
    version_counts = Counter(record.presentation_version_id for record in records)
    for record in records:
        if Path(record.original_filename).suffix.lower() not in SUPPORTED_PRESENTATION_EXTENSIONS:
            continue
        if version_counts[record.presentation_version_id] != 1:
            continue
        before = session.scalar(
            select(PresentationAsset.presentation_asset_id).where(
                PresentationAsset.presentation_version_id == record.presentation_version_id,
                PresentationAsset.kind == AssetKind.ORIGINAL,
            )
        )
        if before is None:
            ensure_confirmed_original_asset(session, record)
            repaired += 1
    session.flush()
    return repaired


def queue_central_to_site_transfer(
    session: Session, record: PresentationMediaImport
) -> TransferJob:
    """Idempotently publish the established Site-pull manifest for confirmed media."""
    if (
        not record.destination_site_id
        or not record.presentation_id
        or not record.presentation_version_id
    ):
        raise MediaStagingError("confirmed media has no Site destination", "missing_destination")
    presentation = session.get(Presentation, record.presentation_id)
    payload = {
        "media_import_id": str(record.media_import_id),
        "presentation_id": str(record.presentation_id),
        "presentation_version_id": str(record.presentation_version_id),
        "staging_key": record.staging_key,
        "sha256": record.sha256,
        "size_bytes": record.size_bytes,
        "canonical_filename": record.canonical_filename,
    }
    transfer = session.get(TransferJob, record.transfer_job_id) if record.transfer_job_id else None
    if transfer is None:
        transfer = TransferJob(
            owning_site_id=record.destination_site_id,
            transfer_type="presentation_media.central_to_site",
            payload=payload,
            status=JobStatus.PENDING,
            required_capabilities=["site-pull-manifest"],
            idempotency_key=f"central-media:{record.media_import_id}",
        )
        session.add(transfer)
    else:
        if transfer.status is JobStatus.RUNNING:
            raise MediaStagingError(
                "a running transfer cannot be reassigned", "transfer_in_progress"
            )
        transfer.payload = payload
        transfer.status = JobStatus.PENDING
        transfer.claimed_by_worker_id = None
        transfer.lease_expires_at = None
    session.flush()
    record.transfer_job_id = transfer.transfer_job_id
    record.import_state = MediaImportState.TRANSFER_QUEUED
    record.sync_state = SyncState.PENDING
    manifest = MediaTransferManifest(
        transfer_session_id=transfer.transfer_job_id,
        origin_system=SourceSystem.CENTRAL,
        destination_site_id=record.destination_site_id,
        event_id=record.event_id,
        presentation_id=record.presentation_id,
        presentation_version_id=record.presentation_version_id,
        presentation_version_number=session.get(
            PresentationVersion, record.presentation_version_id
        ).version_number,
        presentation_identifier=presentation.presentation_identifier,
        original_filename=record.original_filename,
        canonical_filename=record.canonical_filename,
        expected_size=record.size_bytes or 0,
        sha256=record.sha256 or "",
        media_type=record.mime_type,
        created_at=record.created_at,
        state=MediaTransferState.AVAILABLE,
    )
    outbox_key = f"media-transfer-available:{transfer.transfer_job_id}"
    existing_event = session.scalar(
        select(OutboxEvent).where(OutboxEvent.idempotency_key == outbox_key)
    )
    if existing_event is None:
        CentralQueue(session).enqueue_outbox(
            event_type="central.media_transfer.available",
            aggregate_type="media_transfer",
            aggregate_id=transfer.transfer_job_id,
            owning_site_id=record.destination_site_id,
            source_sequence=next_sequence(session, record.destination_site_id),
            protocol_version=UPM_SYNC_PROTOCOL_VERSION,
            idempotency_key=outbox_key,
            payload=OutboxPayload(
                source_system=SourceSystem.CENTRAL,
                data=manifest.model_dump(mode="json"),
            ),
        )
    elif existing_event.status is JobStatus.PENDING:
        existing_event.payload = manifest.model_dump(mode="json")
    else:
        raise MediaStagingError(
            "a published transfer cannot be reassigned", "manifest_already_published"
        )
    return transfer


def target_confirmed_event_media(session: Session, event_id: UUID, site_id: UUID) -> int:
    """Target confirmed canonical event media and create any missing transfer manifests."""
    records = session.scalars(
        select(PresentationMediaImport).where(
            PresentationMediaImport.event_id == event_id,
            PresentationMediaImport.match_state == MediaMatchState.CONFIRMED,
            PresentationMediaImport.import_state.in_(
                [MediaImportState.ASSIGNED, MediaImportState.TRANSFER_QUEUED]
            ),
            PresentationMediaImport.transfer_job_id.is_(None),
            PresentationMediaImport.presentation_id.is_not(None),
            PresentationMediaImport.presentation_version_id.is_not(None),
            PresentationMediaImport.committed_storage_key.is_not(None),
        )
    ).all()
    queued = 0
    for record in records:
        if record.destination_site_id not in (None, site_id):
            continue
        record.destination_site_id = site_id
        queue_central_to_site_transfer(session, record)
        queued += 1
    return queued


def _complete_batch_if_accounted(session: Session, batch_id: UUID | None) -> None:
    if not batch_id:
        return
    batch = session.get(PresentationMediaImportBatch, batch_id)
    if batch is None or batch.completed_at:
        return
    registered = (
        session.scalar(
            select(func.count())
            .select_from(PresentationMediaImport)
            .where(PresentationMediaImport.batch_id == batch_id)
        )
        or 0
    )
    active = (
        session.scalar(
            select(func.count())
            .select_from(PresentationMediaImport)
            .where(
                PresentationMediaImport.batch_id == batch_id,
                PresentationMediaImport.import_state.in_(
                    [MediaImportState.UPLOADING, MediaImportState.STAGED]
                ),
            )
        )
        or 0
    )
    if registered + batch.skipped_count >= batch.selected_count and active == 0:
        batch.status = "completed"
        batch.completed_at = utc_now()
        record_log(
            session,
            service="central-worker",
            event_type="batch.completed",
            message="Bulk import processing reached an accounted terminal state",
            batch_id=batch_id,
            event_id=batch.event_id,
            context={
                "selected_count": batch.selected_count,
                "registered_count": registered,
                "skipped_count": batch.skipped_count,
            },
        )


class MediaStagingError(RuntimeError):
    def __init__(self, message: str, code: str = "staging_failed") -> None:
        super().__init__(message)
        self.code = code


def _safe_staging_path(root: Path, key: str) -> Path:
    root = root.resolve()
    candidate = (root / key).resolve()
    if candidate.parent != root:
        raise MediaStagingError("invalid staging key", "invalid_staging_key")
    return candidate


class CentralMediaStagingService:
    def __init__(
        self,
        factory: sessionmaker[Session],
        storage: AsyncMediaStorageClient,
        max_upload_bytes: int,
        matching_concurrency: int = 1,
    ) -> None:
        self.factory = factory
        self.storage = storage
        self.max_upload_bytes = max_upload_bytes
        if not 1 <= matching_concurrency <= 16:
            raise ValueError("matching concurrency must be between 1 and 16")
        self.matching_concurrency = matching_concurrency
        self._candidate_cache: dict[UUID, tuple[int, list[MatchCandidate]]] = {}
        self._candidate_cache_lock = Lock()

    @staticmethod
    def _storage_root(session: Session, target: dict, role: str) -> StorageRoot:
        target_id = UUID(target["storage_target_id"])
        root = session.get(StorageRoot, target_id)
        if root is None:
            root = StorageRoot(
                storage_root_id=target_id,
                role=role,
                display_name=target["name"],
                path=target["internal_path"],
                backend_type="filesystem",
                enabled=False,
            )
            session.add(root)
            session.flush()
        else:
            root.display_name = target["name"]
            root.path = target["internal_path"]
        return root

    async def stage(
        self,
        *,
        event_id: UUID,
        destination_site_id: UUID | None,
        original_filename: str,
        source_relative_path: str | None,
        content_type: str | None,
        idempotency_key: str | None,
        batch_id: UUID | None = None,
        chunks: AsyncIterator[bytes],
        actor: str,
        origin: str = "browser",
        source_share: str | None = None,
    ) -> PresentationMediaImport:
        raw_filename = original_filename.strip()
        filename = Path(raw_filename).name
        if (
            filename != raw_filename
            or not filename
            or "\\" in raw_filename
            or len(raw_filename) > 1024
            or any(ord(character) < 32 for character in raw_filename)
        ):
            raise MediaStagingError("invalid original filename", "invalid_filename")
        try:
            relative_path = normalize_source_relative_path(source_relative_path, filename)
        except ValueError as error:
            raise MediaStagingError(str(error), "invalid_source_relative_path") from error
        # Resolve an idempotent replay before allocating storage.  This transaction ends before
        # any request bytes are read, so an upload cannot occupy a database connection while the
        # client is sending a large file.
        with self.factory.begin() as session:
            if session.get(Event, event_id) is None:
                raise MediaStagingError("event not found", "event_not_found")
            if batch_id:
                batch = session.get(PresentationMediaImportBatch, batch_id)
                if batch is None or batch.event_id != event_id:
                    raise MediaStagingError("media import batch not found", "batch_not_found")
            if idempotency_key:
                existing = session.scalar(
                    select(PresentationMediaImport).where(
                        PresentationMediaImport.idempotency_key == idempotency_key
                    )
                )
                if existing and existing.import_state is not MediaImportState.FAILED:
                    session.refresh(existing)
                    session.expunge(existing)
                    return existing
        try:
            allocation = await self.storage.allocate_staging()
        except MediaStorageOperationError as error:
            raise MediaStagingError(str(error), error.code) from error
        except MediaStorageUnavailable as error:
            raise MediaStagingError(str(error), "storage_service_unavailable") from error
        with self.factory.begin() as session:
            existing = None
            if idempotency_key:
                existing = session.scalar(
                    select(PresentationMediaImport).where(
                        PresentationMediaImport.idempotency_key == idempotency_key
                    )
                )
                if existing and existing.import_state is not MediaImportState.FAILED:
                    session.refresh(existing)
                    session.expunge(existing)
                    return existing
            import_id = existing.media_import_id if existing else new_uuid7()
            storage_root = self._storage_root(session, allocation, "staging")
            if existing:
                existing.import_state = MediaImportState.UPLOADING
                existing.error_code = None
                existing.error_detail = None
                existing.retry_count += 1
                existing.staging_key = allocation["storage_key"]
                existing.staging_storage_root_id = storage_root.storage_root_id
                existing.committed_storage_root_id = None
                existing.committed_storage_key = None
            else:
                record = PresentationMediaImport(
                    media_import_id=import_id,
                    batch_id=batch_id,
                    event_id=event_id,
                    destination_site_id=destination_site_id,
                    original_filename=filename,
                    source_relative_path=relative_path,
                    staging_key=allocation["storage_key"],
                    staging_storage_root_id=storage_root.storage_root_id,
                    mime_type=content_type,
                    idempotency_key=idempotency_key,
                    match_state=MediaMatchState.UNMATCHED,
                    import_state=MediaImportState.UPLOADING,
                    sync_state=SyncState.LOCAL,
                    origin=origin,
                    source_actor=actor,
                    source_share=source_share,
                )
                session.add(record)
                record_log(
                    session,
                    service="central-api",
                    event_type="file.registered",
                    message="Presentation file registered for upload",
                    batch_id=batch_id,
                    media_import_id=import_id,
                    event_id=event_id,
                    context={"filename": filename, "source_relative_path": relative_path},
                )
        total = 0

        async def bounded_chunks():
            nonlocal total
            async for chunk in chunks:
                if not chunk:
                    continue
                total += len(chunk)
                if total > self.max_upload_bytes:
                    raise MediaStagingError("upload exceeds configured maximum", "too_large")
                yield chunk

        try:
            staged = await self.storage.write_staging(
                allocation["storage_target_id"], allocation["storage_key"], bounded_chunks()
            )
        except (MediaStorageUnavailable, MediaStorageOperationError) as error:
            error_code = getattr(error, "code", "storage_service_unavailable")
            with self.factory.begin() as session:
                failed = session.get(PresentationMediaImport, import_id)
                failed.import_state = MediaImportState.FAILED
                failed.error_code = error_code
                failed.error_detail = str(error)[:2048]
                record_log(
                    session,
                    service="central-api",
                    severity="error",
                    event_type="upload.failed",
                    message="Presentation staging failed",
                    batch_id=failed.batch_id,
                    media_import_id=import_id,
                    event_id=failed.event_id,
                    context={"error_code": error_code, "safe_message": str(error)},
                )
            raise MediaStagingError(str(error), error_code) from error
        except Exception as error:
            with self.factory.begin() as session:
                failed = session.get(PresentationMediaImport, import_id)
                failed.import_state = MediaImportState.FAILED
                failed.error_code = getattr(error, "code", "staging_failed")
                failed.error_detail = str(error)[:2048]
                record_log(
                    session,
                    service="central-api",
                    severity="error",
                    event_type="upload.failed",
                    message="Presentation staging failed",
                    batch_id=failed.batch_id,
                    media_import_id=import_id,
                    event_id=failed.event_id,
                    context={"error_code": failed.error_code, "safe_message": str(error)},
                )
            raise
        try:
            with self.factory.begin() as session:
                record = session.get(PresentationMediaImport, import_id)
                record.size_bytes = staged["size_bytes"]
                record.sha256 = staged["sha256"]
                record.import_state = MediaImportState.STAGED
                record.match_reason = "Durably staged; downstream processing is queued"
                CentralQueue(session).enqueue_processing(
                    job_type=INTAKE_PUBLISH_JOB,
                    payload={"data": {"media_import_id": str(import_id)}},
                    idempotency_key=f"intake:{import_id}",
                    required_capabilities=["cpu"],
                    max_attempts=5,
                )
                session.add(
                    AuditRecord(
                        actor_id=actor,
                        action="central.presentation_media.uploaded",
                        target_type="presentation_media_import",
                        target_id=record.media_import_id,
                        site_id=destination_site_id,
                        event_id=event_id,
                        after_context={
                            "original_filename": filename,
                            "size_bytes": total,
                            "sha256": record.sha256,
                            "processing_queued": True,
                        },
                    )
                )
                record_log(
                    session,
                    service="central-api",
                    event_type="upload.staged",
                    message="Presentation received into durable staging",
                    batch_id=batch_id,
                    media_import_id=import_id,
                    event_id=event_id,
                    context={
                        "filename": filename,
                        "size_bytes": total,
                        "processing_job_id": str(import_id),
                    },
                )
                _complete_batch_if_accounted(session, record.batch_id)
                session.flush()
                session.refresh(record)
                session.expunge(record)
                logger.info(
                    "presentation_media_staged",
                    extra={
                        "media_import_id": str(import_id),
                        "event_id": str(event_id),
                        "original_filename": filename,
                        "source_relative_path": relative_path,
                        "result": str(record.import_state),
                        "processing_queued": True,
                    },
                )
                return record
        except Exception as error:
            logger.exception(
                "presentation_media_post_staging_failed",
                extra={
                    "media_import_id": str(import_id),
                    "event_id": str(event_id),
                    "original_filename": filename,
                    "source_relative_path": relative_path,
                    "exception_type": type(error).__name__,
                },
            )
            with self.factory.begin() as session:
                record = session.get(PresentationMediaImport, import_id)
                record.size_bytes = total
                record.sha256 = staged["sha256"]
                record.match_state = MediaMatchState.UNMATCHED
                record.match_reason = "Post-upload processing failed; file preserved for review"
                record.import_state = MediaImportState.NEEDS_REVIEW
                record.error_code = (
                    error.code if isinstance(error, MediaStagingError) else "processing_failed"
                )
                record.error_detail = f"{type(error).__name__}: {error}"[:2048]
                session.flush()
                session.refresh(record)
                session.expunge(record)
                return record

    def analyze(self, media_import_id: UUID) -> PresentationMediaImport:
        """Match one staged import without promoting or assigning its media."""
        try:
            with self.factory.begin() as session:
                record = session.get(PresentationMediaImport, media_import_id, with_for_update=True)
                if record is None:
                    raise MediaStagingError("media import not found", "not_found")
                if record.import_state not in {
                    MediaImportState.STAGED,
                    MediaImportState.NEEDS_REVIEW,
                }:
                    session.expunge(record)
                    return record
                self.refresh_match_suggestion(session, record)
                record_log(
                    session,
                    service="central-worker",
                    event_type="match.suggested"
                    if record.match_state is MediaMatchState.SUGGESTED
                    else "match.unmatched",
                    message="Presentation match suggested"
                    if record.match_state is MediaMatchState.SUGGESTED
                    else "Presentation needs operator review",
                    batch_id=record.batch_id,
                    media_import_id=record.media_import_id,
                    event_id=record.event_id,
                    context={"reason": record.match_reason},
                )
                _complete_batch_if_accounted(session, record.batch_id)
                session.flush()
                session.refresh(record)
                session.expunge(record)
                return record
        except Exception as error:
            with self.factory.begin() as session:
                record = session.get(PresentationMediaImport, media_import_id)
                if record is not None:
                    record.match_state = MediaMatchState.UNMATCHED
                    record.match_reason = "Analysis failed; staged media is preserved for review"
                    record.import_state = MediaImportState.NEEDS_REVIEW
                    record.error_code = "analysis_failed"
                    record.error_detail = f"{type(error).__name__}: {error}"[:2048]
                    record_log(
                        session,
                        service="central-worker",
                        severity="error",
                        event_type="match.analysis_failed",
                        message="Presentation analysis failed; staged media preserved",
                        batch_id=record.batch_id,
                        media_import_id=record.media_import_id,
                        event_id=record.event_id,
                        context={"error_code": record.error_code, "safe_message": str(error)},
                    )
                    _complete_batch_if_accounted(session, record.batch_id)
            raise

    async def publish_intake(self, media_import_id: UUID) -> PresentationMediaImport:
        """Idempotently publish staged bytes to durable Intake and enqueue analysis."""
        with self.factory() as session:
            record = session.get(PresentationMediaImport, media_import_id)
            if record is None:
                raise MediaStagingError("media import not found", "not_found")
            if record.intake_storage_root_id and record.intake_storage_key:
                return record
            target_id, key, sha256 = (
                record.staging_storage_root_id,
                record.staging_key,
                record.sha256,
            )
        if target_id is None or not sha256:
            raise MediaStagingError("staged media reference is incomplete", "invalid_staging")
        published = await self.storage.publish_intake(target_id, key, sha256)
        with self.factory.begin() as session:
            record = session.get(PresentationMediaImport, media_import_id, with_for_update=True)
            root = self._storage_root(session, published, "media")
            record.intake_storage_root_id = root.storage_root_id
            record.intake_storage_key = published["storage_key"]
            record.import_state = MediaImportState.NEEDS_REVIEW
            existing = session.scalar(
                select(ProcessingJob).where(
                    ProcessingJob.job_type == "presentation_media.process",
                    ProcessingJob.idempotency_key == str(media_import_id),
                )
            )
            if existing is None:
                CentralQueue(session).enqueue_processing(
                    job_type="presentation_media.process",
                    payload={"data": {"media_import_id": str(media_import_id)}},
                    idempotency_key=str(media_import_id),
                    required_capabilities=["cpu"],
                    max_attempts=5,
                )
            session.flush()
            session.refresh(record)
            session.expunge(record)
            return record

    async def reject_intake(self, media_import_id: UUID) -> PresentationMediaImport:
        """Idempotently quarantine a rejected Intake object."""
        with self.factory() as session:
            record = session.get(PresentationMediaImport, media_import_id)
            if record is None:
                raise MediaStagingError("media import not found", "not_found")
            if record.rejected_storage_root_id and record.rejected_storage_key:
                return record
            target_id = record.intake_storage_root_id or record.staging_storage_root_id
            key = record.intake_storage_key or record.staging_key
            sha256 = record.sha256
        if target_id is None or not sha256:
            raise MediaStagingError("Intake media reference is incomplete", "invalid_intake")
        rejected = await self.storage.reject_intake(target_id, key, sha256)
        with self.factory.begin() as session:
            record = session.get(PresentationMediaImport, media_import_id, with_for_update=True)
            root = self._storage_root(session, rejected, "media")
            record.rejected_storage_root_id = root.storage_root_id
            record.rejected_storage_key = rejected["storage_key"]
            record.intake_storage_root_id = None
            record.intake_storage_key = None
            record.import_state = MediaImportState.REJECTED
            session.flush()
            session.refresh(record)
            session.expunge(record)
            return record

    def refresh_match_suggestion(self, session: Session, record: PresentationMediaImport) -> None:
        """Refresh metadata-only suggestions without assigning or touching stored media."""
        self._automatic_match_and_assign(session, record)

    @staticmethod
    def _match_rescan_item(
        source_path: str,
        candidates: list[MatchCandidate],
        event_timezone: str,
    ) -> MatchResult:
        return match_presentation(source_path, candidates, event_timezone=event_timezone)

    def _match_rescan_batch(
        self,
        items: list[tuple[UUID, str]],
        candidates: list[MatchCandidate],
        event_timezone: str,
        executor: Executor | None = None,
    ) -> list[tuple[UUID, MatchResult | Exception]]:
        """CPU-match a bounded snapshot without sharing ORM state across workers."""
        if self.matching_concurrency == 1:
            output = []
            for media_import_id, source_path in items:
                try:
                    output.append(
                        (
                            media_import_id,
                            self._match_rescan_item(source_path, candidates, event_timezone),
                        )
                    )
                except Exception as exc:
                    output.append((media_import_id, exc))
            return output
        owned_executor = (
            ThreadPoolExecutor(
                max_workers=self.matching_concurrency,
                thread_name_prefix="central-media-rescan",
            )
            if executor is None
            else None
        )
        active_executor = executor or owned_executor
        try:
            futures = [
                (
                    media_import_id,
                    active_executor.submit(
                        self._match_rescan_item, source_path, candidates, event_timezone
                    )
                    if owned_executor is not None
                    else active_executor.submit(_match_rescan_process_item, source_path),
                )
                for media_import_id, source_path in items
            ]
            output = []
            for media_import_id, future in futures:
                try:
                    output.append((media_import_id, future.result()))
                except Exception as exc:
                    output.append((media_import_id, exc))
            return output
        finally:
            if owned_executor is not None:
                owned_executor.shutdown()

    def rescan(
        self,
        processing_job_id: UUID,
        worker_id: str,
        *,
        lease_seconds: int = 60,
        batch_size: int = RESCAN_BATCH_SIZE,
    ) -> dict[str, int]:
        """Run a resumable rescan with one bounded process pool for all CPU batches."""
        if self.matching_concurrency == 1:
            return self._rescan_batches(
                processing_job_id,
                worker_id,
                lease_seconds=lease_seconds,
                batch_size=batch_size,
            )
        # Spawn is safe even when independent Intake jobs are active in worker threads. Candidate
        # metadata is serialized once per process and then reused for every bounded rescan batch.
        executors: list[ProcessPoolExecutor] = []
        try:
            return self._rescan_batches(
                processing_job_id,
                worker_id,
                lease_seconds=lease_seconds,
                batch_size=batch_size,
                executor_holder=executors,
            )
        finally:
            for executor in executors:
                executor.shutdown()

    def _rescan_batches(
        self,
        processing_job_id: UUID,
        worker_id: str,
        *,
        lease_seconds: int = 60,
        batch_size: int = RESCAN_BATCH_SIZE,
        executor_holder: list[ProcessPoolExecutor] | None = None,
    ) -> dict[str, int]:
        """Resume a durable metadata-only rescan with bounded parallel CPU matching."""
        batch_candidates: list[MatchCandidate] | None = None
        unmaterialized_session_codes: dict[str, str] = {}
        batch_event_id: UUID | None = None
        batch_timezone = "UTC"
        executor: Executor | None = None
        while True:
            # Snapshot one bounded batch without row locks. Matching below is persistence-free and
            # may run for a while; authoritative eligibility is checked again under lock on write.
            with self.factory() as session:
                job = session.get(ProcessingJob, processing_job_id)
                if job is None or job.job_type != RESCAN_JOB:
                    raise MediaStagingError("media rescan job not found", "not_found")
                if job.status is not JobStatus.RUNNING or job.claimed_by_worker_id != worker_id:
                    raise MediaStagingError("media rescan lease is no longer owned", "lease_lost")
                data = dict(job.payload.get("data", {}))
                media_import_ids = list(data.get("media_import_ids", []))
                processed = int(data.get("processed", 0))
                if processed >= len(media_import_ids):
                    return {
                        key: int(data.get(key, 0))
                        for key in ("total", "processed", "suggested", "unmatched", "failed")
                    }
                current_ids = media_import_ids[processed : processed + batch_size]
                event_id = UUID(str(data["event_id"]))
                if batch_candidates is None or batch_event_id != event_id:
                    batch_timezone = (
                        session.scalar(select(Event.timezone).where(Event.event_id == event_id))
                        or "UTC"
                    )
                    batch_candidates = self._load_match_candidates(session, event_id)
                    if executor_holder is not None:
                        executor = ProcessPoolExecutor(
                            max_workers=self.matching_concurrency,
                            mp_context=multiprocessing.get_context("spawn"),
                            initializer=_initialize_rescan_matcher,
                            initargs=(batch_candidates, batch_timezone),
                        )
                        executor_holder.append(executor)
                    unmaterialized_session_codes = {
                        code.casefold(): code
                        for code in session.scalars(
                            select(ProgramSession.session_code).where(
                                ProgramSession.event_id == event_id,
                                ProgramSession.session_code.is_not(None),
                                ~ProgramSession.session_id.in_(
                                    select(Presentation.session_id).where(
                                        Presentation.event_id == event_id,
                                        Presentation.session_id.is_not(None),
                                    )
                                ),
                            )
                        )
                    }
                    batch_event_id = event_id
                records = list(
                    session.scalars(
                        select(PresentationMediaImport).where(
                            PresentationMediaImport.media_import_id.in_(
                                [UUID(str(value)) for value in current_ids]
                            )
                        )
                    )
                )
                snapshots = [
                    (
                        record.media_import_id,
                        record.source_relative_path or record.original_filename,
                    )
                    for record in records
                    if record.event_id == event_id
                    and record.presentation_id is None
                    and record.match_state is not MediaMatchState.CONFIRMED
                    and record.import_state is MediaImportState.NEEDS_REVIEW
                ]

            results = self._match_rescan_batch(
                snapshots, batch_candidates, batch_timezone, executor
            )

            with self.factory.begin() as session:
                job = session.get(ProcessingJob, processing_job_id, with_for_update=True)
                if (
                    job is None
                    or job.status is not JobStatus.RUNNING
                    or job.claimed_by_worker_id != worker_id
                ):
                    raise MediaStagingError("media rescan lease is no longer owned", "lease_lost")
                latest_data = dict(job.payload.get("data", {}))
                if int(latest_data.get("processed", 0)) != processed:
                    raise MediaStagingError(
                        "media rescan progress changed concurrently", "lease_lost"
                    )
                result_ids = [media_import_id for media_import_id, _ in results]
                writable = {
                    record.media_import_id: record
                    for record in session.scalars(
                        select(PresentationMediaImport)
                        .where(PresentationMediaImport.media_import_id.in_(result_ids))
                        .with_for_update()
                    )
                }
                counts = {
                    "suggested": int(latest_data.get("suggested", 0)),
                    "unmatched": int(latest_data.get("unmatched", 0)),
                    "failed": int(latest_data.get("failed", 0)),
                }
                for media_import_id, result in results:
                    if isinstance(result, Exception):
                        logger.error(
                            "presentation_media_rescan_item_failed",
                            extra={
                                "media_import_id": str(media_import_id),
                                "exception_type": type(result).__name__,
                            },
                        )
                        counts["failed"] += 1
                        continue
                    record = writable.get(media_import_id)
                    if not (
                        record
                        and record.event_id == event_id
                        and record.presentation_id is None
                        and record.match_state is not MediaMatchState.CONFIRMED
                        and record.import_state is MediaImportState.NEEDS_REVIEW
                    ):
                        continue
                    record.match_state = result.state
                    record.match_reason = result.reason
                    record.match_candidates = list(result.candidates)
                    if result.state is MediaMatchState.UNMATCHED:
                        session_code = next(
                            (
                                unmaterialized_session_codes[token.casefold()]
                                for token in re.split(
                                    r"[^A-Za-z0-9]+", Path(record.original_filename).stem
                                )
                                if token.casefold() in unmaterialized_session_codes
                            ),
                            None,
                        )
                        if session_code:
                            record.match_reason = (
                                f"Session {session_code} found, but no assignable Presentation "
                                "record is materialized. Re-run matching to repair imported "
                                "program data."
                            )
                    record.import_state = MediaImportState.NEEDS_REVIEW
                    if record.match_state is MediaMatchState.SUGGESTED:
                        counts["suggested"] += 1
                    else:
                        counts["unmatched"] += 1
                processed += len(current_ids)
                latest_data.update(counts)
                latest_data["processed"] = processed
                job.payload = {**job.payload, "data": latest_data}
                job.progress = Decimal(processed * 100 / max(len(media_import_ids), 1)).quantize(
                    Decimal("0.01")
                )
                now = utc_now()
                job.heartbeat_at = now
                job.lease_expires_at = now + timedelta(seconds=lease_seconds)
                session.flush()

    def queue_promotion(
        self,
        session: Session,
        record: PresentationMediaImport,
        presentation_id: UUID,
        *,
        actor: str,
    ) -> None:
        presentation = session.get(Presentation, presentation_id)
        if presentation is None or presentation.event_id != record.event_id:
            raise MediaStagingError("presentation is not in the import event", "invalid_match")
        if record.match_state is MediaMatchState.CONFIRMED:
            if record.presentation_id != presentation_id:
                raise MediaStagingError("media import is already confirmed", "already_confirmed")
            return
        if record.import_state is MediaImportState.REJECTED or record.rejected_at:
            raise MediaStagingError("rejected media cannot be confirmed", "rejected")
        if not record.sha256 or record.size_bytes is None or not record.intake_storage_root_id:
            raise MediaStagingError("media import is not in durable Intake", "not_in_intake")
        idempotency_key = f"{record.media_import_id}:{presentation_id}"
        existing = session.scalar(
            select(ProcessingJob).where(
                ProcessingJob.job_type == "presentation_media.promote",
                ProcessingJob.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            return
        CentralQueue(session).enqueue_processing(
            job_type="presentation_media.promote",
            payload={
                "data": {
                    "media_import_id": str(record.media_import_id),
                    "presentation_id": str(presentation_id),
                    "actor": actor,
                }
            },
            idempotency_key=idempotency_key,
            required_capabilities=["cpu"],
            max_attempts=5,
        )
        record_log(
            session,
            service="central-api",
            event_type="confirmation.requested",
            message="Presentation confirmation queued",
            batch_id=record.batch_id,
            media_import_id=record.media_import_id,
            event_id=record.event_id,
            presentation_id=presentation_id,
            context={"processing_job_id": idempotency_key},
        )

    async def promote_and_assign(
        self, media_import_id: UUID, presentation_id: UUID, *, actor: str
    ) -> PresentationMediaImport:
        """Idempotently promote staged bytes, then confirm inside a short transaction."""
        with self.factory() as session:
            record = session.get(PresentationMediaImport, media_import_id)
            if record is None:
                raise MediaStagingError("media import not found", "not_found")
            if record.match_state is MediaMatchState.CONFIRMED:
                if record.presentation_id != presentation_id:
                    raise MediaStagingError(
                        "media import is already confirmed", "already_confirmed"
                    )
                with self.factory.begin() as repair_session:
                    repair = repair_session.get(
                        PresentationMediaImport, media_import_id, with_for_update=True
                    )
                    ensure_confirmed_original_asset(repair_session, repair)
                    repair_session.flush()
                    repair_session.refresh(repair)
                    repair_session.expunge(repair)
                    return repair
            target_id = record.intake_storage_root_id
            staging_key = record.intake_storage_key
            sha256 = record.sha256
        if target_id is None or sha256 is None:
            raise MediaStagingError("staged media reference is incomplete", "invalid_staging")
        # No PostgreSQL session or transaction is held during the external storage operation.
        committed = await self.storage.promote_intake(str(target_id), staging_key, sha256)
        with self.factory.begin() as session:
            record = session.get(PresentationMediaImport, media_import_id, with_for_update=True)
            if record.match_state is not MediaMatchState.CONFIRMED:
                media_root = self._storage_root(session, committed, "media")
                record.committed_storage_root_id = media_root.storage_root_id
                record.committed_storage_key = committed["storage_key"]
                record.intake_storage_root_id = None
                record.intake_storage_key = None
                self.assign(session, record, presentation_id, manual=True, actor=actor)
                ensure_confirmed_original_asset(session, record)
                record_log(
                    session,
                    service="central-worker",
                    event_type="media.promoted",
                    message="Presentation confirmed and canonical media published",
                    batch_id=record.batch_id,
                    media_import_id=record.media_import_id,
                    event_id=record.event_id,
                    presentation_id=record.presentation_id,
                    presentation_version_id=record.presentation_version_id,
                    context={"storage_key": record.committed_storage_key},
                )
                session.add(
                    AuditRecord(
                        actor_id=actor,
                        action="central.presentation_media.confirmed",
                        target_type="presentation_media_import",
                        target_id=record.media_import_id,
                        site_id=record.destination_site_id,
                        event_id=record.event_id,
                        after_context={
                            "presentation_id": str(record.presentation_id),
                            "presentation_version_id": str(record.presentation_version_id),
                            "committed_storage_key": record.committed_storage_key,
                        },
                    )
                )
            session.flush()
            session.refresh(record)
            session.expunge(record)
            return record

    def _automatic_match_and_assign(
        self,
        session: Session,
        record: PresentationMediaImport,
        *,
        candidates: list[MatchCandidate] | None = None,
        event_timezone: str | None = None,
    ) -> None:
        if candidates is None:
            event_revision, event_timezone = session.execute(
                select(Event.revision, Event.timezone).where(Event.event_id == record.event_id)
            ).one_or_none() or (1, "UTC")
            with self._candidate_cache_lock:
                cached = self._candidate_cache.get(record.event_id)
                if cached and cached[0] == event_revision:
                    candidates = cached[1]
                else:
                    candidates = self._load_match_candidates(session, record.event_id)
                    self._candidate_cache[record.event_id] = (event_revision, candidates)
        event_timezone = event_timezone or "UTC"
        result = match_presentation(
            record.source_relative_path or record.original_filename,
            candidates,
            event_timezone=event_timezone,
        )
        record.match_state = result.state
        record.match_reason = result.reason
        record.match_candidates = list(result.candidates)
        if result.state is MediaMatchState.UNMATCHED:
            self._explain_unmaterialized_session(session, record)
        # Matching is suggestion-only. Even an exact identifier never creates a version.
        record.import_state = MediaImportState.NEEDS_REVIEW

    def _load_match_candidates(self, session: Session, event_id: UUID) -> list[MatchCandidate]:
        rows = session.execute(
            select(Presentation, ProgramSession, Person)
            .outerjoin(ProgramSession, ProgramSession.session_id == Presentation.session_id)
            .outerjoin(
                PresentationPresenter,
                PresentationPresenter.presentation_id == Presentation.presentation_id,
            )
            .outerjoin(
                EventParticipation,
                EventParticipation.event_participation_id
                == PresentationPresenter.event_participation_id,
            )
            .outerjoin(Person, Person.person_id == EventParticipation.person_id)
            .where(Presentation.event_id == event_id)
            .order_by(
                Presentation.presentation_id,
                PresentationPresenter.primary_presenter.desc(),
                PresentationPresenter.presenter_order,
            )
        ).all()
        candidates = []
        for item, program_session, presenter in rows:
            # Keep every presenter alias as evidence. The persistence-free matcher collapses
            # these joined rows by canonical presentation_id before ranking or ambiguity.
            candidates.append(
                MatchCandidate(
                    item.presentation_id,
                    item.presentation_identifier,
                    item.external_presentation_id,
                    title=item.title,
                    presenter_family_name=presenter.family_name if presenter else None,
                    presenter_given_name=presenter.given_name if presenter else None,
                    session_title=program_session.title if program_session else None,
                    session_external_id=program_session.session_code if program_session else None,
                    room=program_session.location_name if program_session else None,
                    starts_at=item.scheduled_at
                    or (program_session.starts_at if program_session else None),
                )
            )
        return candidates

    def _explain_unmaterialized_session(
        self, session: Session, record: PresentationMediaImport
    ) -> None:
        filename_tokens = {
            token.casefold()
            for token in re.split(r"[^A-Za-z0-9]+", Path(record.original_filename).stem)
            if token
        }
        matching_unmaterialized = next(
            (
                item
                for item in session.scalars(
                    select(ProgramSession).where(
                        ProgramSession.event_id == record.event_id,
                        ProgramSession.session_code.is_not(None),
                    )
                )
                if item.session_code.casefold() in filename_tokens
                and session.scalar(
                    select(Presentation.presentation_id).where(
                        Presentation.event_id == record.event_id,
                        Presentation.session_id == item.session_id,
                    )
                )
                is None
            ),
            None,
        )
        if matching_unmaterialized:
            record.match_reason = (
                f"Session {matching_unmaterialized.session_code} found, but no assignable "
                "Presentation record is materialized. Re-run matching to repair imported "
                "program data."
            )

    def assign(
        self,
        session: Session,
        record: PresentationMediaImport,
        presentation_id: UUID,
        *,
        manual: bool,
        actor: str | None = None,
    ) -> None:
        presentation = session.get(Presentation, presentation_id)
        if presentation is None or presentation.event_id != record.event_id:
            raise MediaStagingError("presentation is not in the import event", "invalid_match")
        session.execute(
            select(Presentation.presentation_id)
            .where(Presentation.presentation_id == presentation_id)
            .with_for_update()
        )
        latest = session.scalar(
            select(PresentationVersion)
            .where(PresentationVersion.presentation_id == presentation_id)
            .order_by(PresentationVersion.version_number.desc())
            .limit(1)
        )
        latest_assigned = bool(
            latest
            and session.scalar(
                select(PresentationMediaImport.media_import_id).where(
                    PresentationMediaImport.presentation_version_id
                    == latest.presentation_version_id,
                    PresentationMediaImport.import_state != MediaImportState.CANCELLED,
                )
            )
        )
        primary_asset = (
            Path(record.original_filename).suffix.lower() in SUPPORTED_PRESENTATION_EXTENSIONS
        )
        if latest is None or (latest_assigned and primary_asset):
            latest = PresentationVersion(
                presentation_id=presentation_id,
                version_number=(latest.version_number if latest else 0) + 1,
            )
            session.add(latest)
            session.flush()
        event = session.get(Event, record.event_id)
        program_session = session.get(ProgramSession, presentation.session_id)
        presenter = session.execute(
            select(Person.family_name, Person.given_name)
            .join(EventParticipation, EventParticipation.person_id == Person.person_id)
            .join(
                PresentationPresenter,
                PresentationPresenter.event_participation_id
                == EventParticipation.event_participation_id,
            )
            .where(PresentationPresenter.presentation_id == presentation_id)
            .order_by(
                PresentationPresenter.primary_presenter.desc(),
                PresentationPresenter.presenter_order,
                PresentationPresenter.presentation_presenter_id,
            )
            .limit(1)
        ).one_or_none()
        record.presentation_id = presentation_id
        record.presentation_version_id = latest.presentation_version_id
        record.presentation_identifier = presentation.presentation_identifier
        record.external_presentation_id = presentation.external_presentation_id
        record.match_state = MediaMatchState.CONFIRMED
        record.confirmed_by = actor or "operator"
        record.confirmed_at = utc_now()
        if manual:
            record.match_reason = "Operator manual assignment"
        if Path(record.original_filename).suffix.lower() in SUPPORTED_PRESENTATION_EXTENSIONS:
            record.canonical_filename = canonical_presentation_filename(
                CanonicalPresentationMetadata(
                    presentation_identifier=presentation.presentation_identifier,
                    event_timezone=event.timezone,
                    starts_at=presentation.scheduled_at
                    or (program_session.starts_at if program_session else None),
                    room_label=program_session.location_name if program_session else None,
                    presenter_family_name=presenter[0] if presenter else None,
                    presenter_given_name=presenter[1] if presenter else None,
                    title=presentation.title,
                    version_number=latest.version_number,
                    original_filename=record.original_filename,
                )
            )
        else:
            record.canonical_filename = record.original_filename
        record.import_state = MediaImportState.ASSIGNED
        presentation.workflow_status = PresentationWorkflowStatus.RECEIVED
        if record.destination_site_id is None:
            record.destination_site_id = session.scalar(
                select(EventDeployment.site_id)
                .where(
                    EventDeployment.event_id == record.event_id,
                    EventDeployment.status.in_(ACTIVE_DEPLOYMENT_STATUSES),
                )
                .order_by(EventDeployment.created_at.desc())
                .limit(1)
            )
        if record.destination_site_id:
            queue_central_to_site_transfer(session, record)
