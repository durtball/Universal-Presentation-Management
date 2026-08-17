"""Durable, streaming Central presentation-media staging and assignment."""

from __future__ import annotations

import logging
import re
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from upm_central.persistence.models import (
    AuditRecord,
    Event,
    EventParticipation,
    OutboxEvent,
    Person,
    Presentation,
    PresentationMediaImport,
    PresentationPresenter,
    PresentationVersion,
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
    JobStatus,
    MediaImportState,
    MediaMatchState,
    MediaTransferState,
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
    canonical_presentation_filename,
    match_presentation,
    normalize_source_relative_path,
)

logger = logging.getLogger(__name__)


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
    ) -> None:
        self.factory = factory
        self.storage = storage
        self.max_upload_bytes = max_upload_bytes

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
        chunks: AsyncIterator[bytes],
        actor: str,
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
        recognized_presentation = Path(filename).suffix.lower() in SUPPORTED_PRESENTATION_EXTENSIONS
        try:
            relative_path = normalize_source_relative_path(source_relative_path, filename)
        except ValueError as error:
            raise MediaStagingError(str(error), "invalid_source_relative_path") from error
        try:
            allocation = await self.storage.allocate_staging()
        except MediaStorageOperationError as error:
            raise MediaStagingError(str(error), error.code) from error
        except MediaStorageUnavailable as error:
            raise MediaStagingError(str(error), "storage_service_unavailable") from error
        with self.factory.begin() as session:
            if session.get(Event, event_id) is None:
                raise MediaStagingError("event not found", "event_not_found")
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
                )
                session.add(record)
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
            committed = await self.storage.commit(
                allocation["storage_target_id"], allocation["storage_key"], staged["sha256"]
            )
        except (MediaStorageUnavailable, MediaStorageOperationError) as error:
            error_code = getattr(error, "code", "storage_service_unavailable")
            with self.factory.begin() as session:
                failed = session.get(PresentationMediaImport, import_id)
                failed.import_state = MediaImportState.FAILED
                failed.error_code = error_code
                failed.error_detail = str(error)[:2048]
            raise MediaStagingError(str(error), error_code) from error
        except Exception as error:
            with self.factory.begin() as session:
                failed = session.get(PresentationMediaImport, import_id)
                failed.import_state = MediaImportState.FAILED
                failed.error_code = getattr(error, "code", "staging_failed")
                failed.error_detail = str(error)[:2048]
            raise
        try:
            with self.factory.begin() as session:
                record = session.get(PresentationMediaImport, import_id)
                media_root = self._storage_root(session, committed, "media")
                record.size_bytes = staged["size_bytes"]
                record.sha256 = staged["sha256"]
                record.committed_storage_root_id = media_root.storage_root_id
                record.committed_storage_key = committed["storage_key"]
                record.import_state = MediaImportState.STAGED
                if recognized_presentation:
                    self._automatic_match_and_assign(session, record)
                else:
                    record.match_state = MediaMatchState.UNMATCHED
                    record.match_reason = "Unclassified media type preserved for operator review"
                    record.import_state = MediaImportState.NEEDS_REVIEW
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
                            "match_state": record.match_state,
                        },
                    )
                )
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
                        "match_result": str(record.match_state),
                        "presentation_identifier_candidate": record.external_presentation_id,
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

    def _automatic_match_and_assign(
        self, session: Session, record: PresentationMediaImport
    ) -> None:
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
            .where(Presentation.event_id == record.event_id)
            .order_by(
                Presentation.presentation_id,
                PresentationPresenter.primary_presenter.desc(),
                PresentationPresenter.presenter_order,
            )
        ).all()
        candidates = []
        seen: set[UUID] = set()
        for item, program_session, presenter in rows:
            if item.presentation_id in seen:
                continue
            seen.add(item.presentation_id)
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
        result = match_presentation(
            record.original_filename,
            candidates,
        )
        record.match_state = result.state
        record.match_reason = result.reason
        record.match_candidates = list(result.candidates)
        if result.state is MediaMatchState.UNMATCHED:
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
        # Matching is suggestion-only. Even an exact identifier never creates a version.
        record.import_state = MediaImportState.NEEDS_REVIEW

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
        if latest is None or latest_assigned:
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
        record.import_state = MediaImportState.ASSIGNED
        if record.destination_site_id:
            payload = {
                "media_import_id": str(record.media_import_id),
                "presentation_id": str(presentation_id),
                "presentation_version_id": str(latest.presentation_version_id),
                "staging_key": record.staging_key,
                "sha256": record.sha256,
                "size_bytes": record.size_bytes,
                "canonical_filename": record.canonical_filename,
            }
            transfer = (
                session.get(TransferJob, record.transfer_job_id) if record.transfer_job_id else None
            )
            if transfer is None:
                transfer = TransferJob(
                    owning_site_id=record.destination_site_id,
                    transfer_type="presentation_media.central_to_site",
                    payload=payload,
                    status=JobStatus.PENDING,
                    # Site-pull manifests are completed only from Site progress projection;
                    # Central's general transfer worker must never fake delivery completion.
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
                presentation_id=presentation_id,
                presentation_version_id=latest.presentation_version_id,
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
