"""Recovery-safe Site media ingestion orchestration."""

from __future__ import annotations

import hashlib
import logging
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO
from uuid import UUID

from anyio import to_thread
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from upm_shared.enums import (
    AssetKind,
    JobPriority,
    MediaAvailability,
    MediaCategory,
    MediaReplicationState,
    StorageHealth,
    StorageType,
)
from upm_shared.identifiers import new_uuid7
from upm_shared.jobs import PRIORITY_VALUES
from upm_shared.media_storage_client import (
    AsyncMediaStorageClient,
    MediaStorageOperationError,
    MediaStorageUnavailable,
)
from upm_shared.presentation_media import (
    CanonicalPresentationMetadata,
    canonical_presentation_filename,
    normalize_source_relative_path,
)
from upm_site.media.storage import (
    StorageError,
    atomic_finalize,
    ensure_safe_parent,
    generate_object_key,
    require_capacity,
    resolve_object_path,
    staging_path,
    validate_original_filename,
)
from upm_site.persistence.models import (
    Event,
    EventParticipation,
    MediaObject,
    MediaReplicationSession,
    PersonProjection,
    Presentation,
    PresentationAsset,
    PresentationPresenter,
    PresentationVersion,
    ProcessingJob,
    Room,
    RoomAssignment,
    StorageTarget,
)
from upm_site.persistence.queue import SiteQueue

logger = logging.getLogger(__name__)
DEFAULT_CHUNK_SIZE = 1024 * 1024


class IngestionError(RuntimeError):
    def __init__(self, message: str, *, code: str = "ingestion_failed") -> None:
        super().__init__(message)
        self.code = code


class IngestionConflictError(IngestionError):
    pass


@dataclass(frozen=True, slots=True)
class IngestionRequest:
    site_id: UUID
    original_filename: str
    category: MediaCategory
    expected_size: int | None = None
    event_id: UUID | None = None
    presentation_version_id: UUID | None = None
    storage_target_id: UUID | None = None
    idempotency_key: str | None = None
    client_mime_type: str | None = None
    source_relative_path: str | None = None
    replicate_to_central: bool = True
    intake_origin: str = "browser"
    source_actor: str | None = None
    source_share: str | None = None


@dataclass(frozen=True, slots=True)
class IngestionResult:
    media_object_id: UUID
    presentation_asset_id: UUID | None
    processing_job_id: UUID | None
    availability: MediaAvailability
    size_bytes: int | None
    content_hash: str | None
    duplicate_retry: bool = False


def _detect_mime_type(header: bytes) -> str:
    signatures = (
        (b"%PDF-", "application/pdf"),
        (b"\x89PNG\r\n\x1a\n", "image/png"),
        (b"\xff\xd8\xff", "image/jpeg"),
        (b"GIF87a", "image/gif"),
        (b"GIF89a", "image/gif"),
        (b"PK\x03\x04", "application/zip"),
        (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", "application/x-ole-storage"),
    )
    for signature, mime_type in signatures:
        if header.startswith(signature):
            return mime_type
    if len(header) >= 12 and header[4:8] == b"ftyp":
        return "video/mp4"
    return "application/octet-stream"


class MediaIngestionService:
    """Coordinates several recoverable DB/filesystem phases without claiming ACID."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        max_upload_bytes: int,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        storage_client: AsyncMediaStorageClient | None = None,
    ) -> None:
        if max_upload_bytes <= 0 or chunk_size <= 0:
            raise ValueError("upload and chunk sizes must be positive")
        self.session_factory = session_factory
        self.max_upload_bytes = max_upload_bytes
        self.chunk_size = chunk_size
        self.storage_client = storage_client

    def ingest(self, request: IngestionRequest, source: BinaryIO) -> IngestionResult:
        filename = validate_original_filename(request.original_filename)
        self._validate_request(request)
        if request.expected_size is not None and request.expected_size > self.max_upload_bytes:
            raise IngestionError(
                "expected upload size exceeds configured maximum", code="too_large"
            )

        existing = self._find_retry(request)
        if existing is not None:
            return existing

        media_id = new_uuid7()
        object_key = generate_object_key(request.category, media_id)
        target, event_id = self._load_target_and_event(request)
        self._preflight(target, request.expected_size or 0, media_id)
        try:
            asset_id = self._create_staging_record(
                request, media_id, object_key, filename, target.storage_target_id, event_id
            )
        except IntegrityError as error:
            retry = self._find_retry(request)
            if retry is not None:
                return retry
            raise IngestionConflictError("ingestion idempotency conflict") from error
        staged: Path | None = None
        finalized = False
        bytes_written = 0
        logger.info(
            "upload_started",
            extra={
                "media_object_id": str(media_id),
                "storage_target_id": str(target.storage_target_id),
            },
        )
        try:
            staged = staging_path(target, media_id)
            destination = ensure_safe_parent(target, object_key)
            content_hash, bytes_written, detected_mime = self._stream_to_staging(
                source, staged, target
            )
            if request.expected_size is not None and bytes_written != request.expected_size:
                raise IngestionError(
                    "uploaded size does not match expected size", code="size_mismatch"
                )
            require_capacity(target, 0)
            self._record_finalizing(media_id, content_hash, bytes_written, detected_mime)
            atomic_finalize(staged, destination)
            finalized = True
            processing_job_id = self._mark_available_and_enqueue(
                media_id, request.site_id, replicate_to_central=request.replicate_to_central
            )
            logger.info(
                "upload_completed",
                extra={
                    "media_object_id": str(media_id),
                    "storage_target_id": str(target.storage_target_id),
                    "bytes_written": bytes_written,
                    "content_hash": content_hash,
                },
            )
            return IngestionResult(
                media_id,
                asset_id,
                processing_job_id,
                MediaAvailability.AVAILABLE,
                bytes_written,
                content_hash,
            )
        except Exception as error:
            if not finalized:
                try:
                    if staged is not None:
                        staged.unlink(missing_ok=True)
                except OSError:
                    logger.exception(
                        "failed_upload_cleanup", extra={"media_object_id": str(media_id)}
                    )
            self._record_failure(media_id, error, finalized=finalized)
            logger.exception(
                "upload_failed",
                extra={"media_object_id": str(media_id), "bytes_written": bytes_written},
            )
            if isinstance(error, (IngestionError, StorageError)):
                raise
            raise IngestionError("media ingestion failed") from error

    async def ingest_async(
        self, request: IngestionRequest, chunks: AsyncIterator[bytes]
    ) -> IngestionResult:
        """Ingest an ASGI request stream without multipart pre-buffering."""
        filename = validate_original_filename(request.original_filename)
        self._validate_request(request)
        if request.expected_size is not None and request.expected_size > self.max_upload_bytes:
            raise IngestionError(
                "expected upload size exceeds configured maximum", code="too_large"
            )
        existing = self._find_retry(request)
        if existing is not None:
            return existing

        if self.storage_client is None:
            raise IngestionError(
                "Media Storage service is unavailable", code="storage_service_unavailable"
            )
        try:
            allocation = await self.storage_client.allocate_staging()
        except MediaStorageOperationError as error:
            raise IngestionError(str(error), code=error.code) from error
        except MediaStorageUnavailable as error:
            raise IngestionError(str(error), code="storage_service_unavailable") from error
        media_id = new_uuid7()
        object_key = generate_object_key(request.category, media_id)
        target, event_id = self._load_service_target_and_event(request, allocation)
        try:
            asset_id = self._create_staging_record(
                request, media_id, object_key, filename, target.storage_target_id, event_id
            )
        except IntegrityError as error:
            retry = self._find_retry(request)
            if retry is not None:
                return retry
            raise IngestionConflictError("ingestion idempotency conflict") from error
        finalized = False
        bytes_written = 0
        logger.info(
            "upload_started",
            extra={
                "media_object_id": str(media_id),
                "storage_target_id": str(target.storage_target_id),
            },
        )
        try:

            async def bounded():
                nonlocal bytes_written
                async for chunk in chunks:
                    bytes_written += len(chunk)
                    if bytes_written > self.max_upload_bytes:
                        raise IngestionError("upload exceeds configured maximum", code="too_large")
                    yield chunk

            staged_result = await self.storage_client.write_staging(
                allocation["storage_target_id"], allocation["storage_key"], bounded()
            )
            if request.expected_size is not None and bytes_written != request.expected_size:
                raise IngestionError(
                    "uploaded size does not match expected size", code="size_mismatch"
                )
            content_hash = staged_result["sha256"]
            detected_mime = request.client_mime_type or "application/octet-stream"
            self._record_finalizing(media_id, content_hash, bytes_written, detected_mime)
            committed = await self.storage_client.publish_intake(
                allocation["storage_target_id"], allocation["storage_key"], content_hash
            )
            self._record_service_commit(request.site_id, media_id, committed)
            finalized = True
            processing_job_id = self._mark_available_and_enqueue(
                media_id, request.site_id, replicate_to_central=request.replicate_to_central
            )
            try:
                await self.storage_client.release_staging(
                    allocation["storage_target_id"], allocation["storage_key"]
                )
            except (MediaStorageUnavailable, MediaStorageOperationError):
                logger.warning("staged_release_deferred", extra={"media_object_id": str(media_id)})
            logger.info(
                "upload_completed",
                extra={
                    "media_object_id": str(media_id),
                    "storage_target_id": str(target.storage_target_id),
                    "bytes_written": bytes_written,
                    "content_hash": content_hash,
                },
            )
            return IngestionResult(
                media_id,
                asset_id,
                processing_job_id,
                MediaAvailability.AVAILABLE,
                bytes_written,
                content_hash,
            )
        except Exception as error:
            self._record_failure(media_id, error, finalized=finalized)
            logger.exception(
                "upload_failed",
                extra={"media_object_id": str(media_id), "bytes_written": bytes_written},
            )
            if isinstance(error, (IngestionError, StorageError)):
                raise
            raise IngestionError("media ingestion failed") from error

    def _load_service_target_and_event(
        self, request: IngestionRequest, target: dict
    ) -> tuple[StorageTarget, UUID | None]:
        target_id = UUID(target["storage_target_id"])
        with self.session_factory.begin() as session:
            event_id = request.event_id
            if request.presentation_version_id is not None:
                version_event = session.scalar(
                    select(Presentation.event_id)
                    .join(
                        PresentationVersion,
                        PresentationVersion.presentation_id == Presentation.presentation_id,
                    )
                    .join(Event, Event.event_id == Presentation.event_id)
                    .where(
                        PresentationVersion.presentation_version_id
                        == request.presentation_version_id,
                        Event.site_id == request.site_id,
                    )
                )
                if version_event is None:
                    raise IngestionError("presentation version was not found", code="invalid_link")
                if event_id is not None and event_id != version_event:
                    raise IngestionError("event does not own the presentation version")
                event_id = version_event
            elif (
                event_id is not None
                and session.scalar(
                    select(Event.event_id).where(
                        Event.event_id == event_id, Event.site_id == request.site_id
                    )
                )
                is None
            ):
                raise IngestionError("event was not found at this Site", code="invalid_link")
            record = session.get(StorageTarget, target_id)
            if record is None:
                record = StorageTarget(
                    storage_target_id=target_id,
                    site_id=request.site_id,
                    display_name=target["name"],
                    storage_type=StorageType.LOCAL_FILESYSTEM,
                    root_path=target["internal_path"],
                    enabled=True,
                    primary_media=False,
                    health=StorageHealth.UNKNOWN,
                    safety_reserve_bytes=0,
                )
                session.add(record)
            return record, event_id

    def _record_service_commit(self, site_id: UUID, media_id: UUID, committed: dict) -> None:
        target_id = UUID(committed["storage_target_id"])
        with self.session_factory.begin() as session:
            target = session.get(StorageTarget, target_id)
            if target is None:
                target = StorageTarget(
                    storage_target_id=target_id,
                    site_id=site_id,
                    display_name=committed["name"],
                    storage_type=StorageType.LOCAL_FILESYSTEM,
                    root_path=committed["internal_path"],
                    enabled=True,
                    primary_media=False,
                    health=StorageHealth.UNKNOWN,
                    safety_reserve_bytes=0,
                )
                session.add(target)
                session.flush()
            media = session.get(MediaObject, media_id)
            media.storage_target_id = target_id
            media.object_key = committed["storage_key"]

    def adopt_committed(
        self, request: IngestionRequest, committed: dict, size_bytes: int, sha256: str
    ) -> IngestionResult:
        """Materialize already verified receiver bytes without copying them through the app."""
        # Look up the committed object before rejecting an incomplete idempotent
        # attempt.  A process may have committed the bytes and database rows but
        # died before marking the media available.
        existing = self._find_retry(request)
        if existing is not None:
            return existing
        target, event_id = self._load_service_target_and_event(request, committed)
        reused = self._reuse_committed_object(request, committed, size_bytes, sha256)
        if reused is not None:
            return reused
        media_id = new_uuid7()
        try:
            asset_id = self._create_staging_record(
                request,
                media_id,
                committed["storage_key"],
                validate_original_filename(request.original_filename),
                target.storage_target_id,
                event_id,
            )
        except IntegrityError:
            reused = self._reuse_committed_object(request, committed, size_bytes, sha256)
            if reused is not None:
                return reused
            raise
        self._record_finalizing(
            media_id, sha256, size_bytes, request.client_mime_type or "application/octet-stream"
        )
        self._record_service_commit(request.site_id, media_id, committed)
        job_id = self._mark_available_and_enqueue(
            media_id, request.site_id, replicate_to_central=request.replicate_to_central
        )
        return IngestionResult(
            media_id, asset_id, job_id, MediaAvailability.AVAILABLE, size_bytes, sha256
        )

    def _reuse_committed_object(
        self, request: IngestionRequest, committed: dict, size_bytes: int, sha256: str
    ) -> IngestionResult | None:
        target_id = UUID(str(committed["storage_target_id"]))
        with self.session_factory.begin() as session:
            media = session.scalar(
                select(MediaObject)
                .where(
                    MediaObject.storage_target_id == target_id,
                    MediaObject.object_key == committed["storage_key"],
                )
                .with_for_update()
            )
            if media is None:
                return None
            if (media.content_hash not in (None, sha256)) or media.size_bytes not in (
                None,
                size_bytes,
            ):
                raise IngestionConflictError(
                    "content-addressed object metadata conflicts with committed bytes"
                )
            media.content_hash = sha256
            media.size_bytes = size_bytes
            media.hash_algorithm = "sha256"
            media.mime_type = (
                request.client_mime_type or media.mime_type or "application/octet-stream"
            )
            media.failure_reason = None
            media.availability = MediaAvailability.AVAILABLE
            asset = None
            if request.presentation_version_id is not None:
                asset = session.scalar(
                    select(PresentationAsset)
                    .where(
                        PresentationAsset.presentation_version_id
                        == request.presentation_version_id,
                        PresentationAsset.kind == AssetKind.ORIGINAL,
                    )
                    .with_for_update()
                )
                if asset is not None and asset.media_object_id != media.media_object_id:
                    linked = session.get(MediaObject, asset.media_object_id, with_for_update=True)
                    same_content = linked is not None and linked.content_hash == sha256
                    incomplete = linked is None or (
                        linked.availability is not MediaAvailability.AVAILABLE
                        or not linked.content_hash
                        or linked.size_bytes is None
                    )
                    if not (same_content or incomplete):
                        raise IngestionConflictError(
                            "presentation version already has a different original asset"
                        )
                    asset.media_object_id = media.media_object_id
                    asset.original_filename = validate_original_filename(request.original_filename)
                if asset is None:
                    asset = PresentationAsset(
                        presentation_version_id=request.presentation_version_id,
                        media_object_id=media.media_object_id,
                        original_filename=validate_original_filename(request.original_filename),
                        kind=AssetKind.ORIGINAL,
                    )
                    session.add(asset)
                    session.flush([asset])
            job_id = session.scalar(
                select(ProcessingJob.processing_job_id).where(
                    ProcessingJob.media_object_id == media.media_object_id
                )
            )
            if job_id is None:
                job_id = (
                    SiteQueue(session)
                    .enqueue_processing(
                        site_id=request.site_id,
                        media_object_id=media.media_object_id,
                        job_type="media.inspect",
                        payload={
                            "schema_version": 1,
                            "data": {"media_object_id": str(media.media_object_id)},
                        },
                        idempotency_key=f"media.inspect:{media.media_object_id}",
                        priority=PRIORITY_VALUES[JobPriority.NORMAL],
                        required_capabilities=["cpu"],
                    )
                    .processing_job_id
                )
            return IngestionResult(
                media.media_object_id,
                asset.presentation_asset_id if asset else None,
                job_id,
                media.availability,
                media.size_bytes,
                media.content_hash,
                True,
            )

    def _validate_request(self, request: IngestionRequest) -> None:
        if request.expected_size is not None and request.expected_size < 0:
            raise IngestionError("expected size cannot be negative", code="invalid_metadata")
        if request.idempotency_key is not None and not 1 <= len(request.idempotency_key) <= 255:
            raise IngestionError("idempotency key must contain 1 to 255 characters")
        try:
            normalize_source_relative_path(request.source_relative_path, request.original_filename)
        except ValueError as error:
            raise IngestionError(str(error), code="invalid_source_relative_path") from error
        linked = request.presentation_version_id is not None
        if linked and request.category not in {
            MediaCategory.PRESENTATION,
            MediaCategory.PRESENTATION_VERSION,
        }:
            raise IngestionError("presentation-linked ingestion requires a presentation category")
        if not linked and request.category not in {MediaCategory.OPEN_FILE, MediaCategory.SIGNAGE}:
            raise IngestionError("open ingestion requires an open-file or signage category")

    def _preflight(self, target: StorageTarget, expected_size: int, media_object_id: UUID) -> None:
        try:
            require_capacity(target, expected_size)
        except StorageError:
            logger.warning(
                "capacity_rejection",
                extra={
                    "media_object_id": str(media_object_id),
                    "storage_target_id": str(target.storage_target_id),
                    "expected_size": expected_size,
                },
            )
            raise

    def _find_retry(self, request: IngestionRequest) -> IngestionResult | None:
        if request.idempotency_key is None:
            return None
        with self.session_factory() as session:
            media = session.scalar(
                select(MediaObject).where(
                    MediaObject.site_id == request.site_id,
                    MediaObject.ingestion_idempotency_key == request.idempotency_key,
                )
            )
            if media is None:
                return None
            if media.availability != MediaAvailability.AVAILABLE:
                # adopt_committed can reconcile this row by its committed object
                # identity.  Do not short-circuit that recovery path.
                return None
            asset_id = session.scalar(
                select(PresentationAsset.presentation_asset_id).where(
                    PresentationAsset.media_object_id == media.media_object_id,
                    *(
                        (
                            PresentationAsset.presentation_version_id
                            == request.presentation_version_id,
                            PresentationAsset.kind == AssetKind.ORIGINAL,
                        )
                        if request.presentation_version_id is not None
                        else ()
                    ),
                )
            )
            if request.presentation_version_id is not None and asset_id is None:
                return None
            job_id = session.scalar(
                select(ProcessingJob.processing_job_id).where(
                    ProcessingJob.media_object_id == media.media_object_id
                )
            )
            return IngestionResult(
                media.media_object_id,
                asset_id,
                job_id,
                media.availability,
                media.size_bytes,
                media.content_hash,
                True,
            )

    def _load_target_and_event(
        self, request: IngestionRequest
    ) -> tuple[StorageTarget, UUID | None]:
        with self.session_factory() as session:
            target_query = select(StorageTarget).where(
                StorageTarget.site_id == request.site_id, StorageTarget.enabled.is_(True)
            )
            if request.storage_target_id is None:
                target_query = target_query.where(StorageTarget.primary_media.is_(True))
            else:
                target_query = target_query.where(
                    StorageTarget.storage_target_id == request.storage_target_id
                )
            target = session.scalar(target_query)
            if target is None:
                raise IngestionError(
                    "enabled storage target was not found", code="target_unavailable"
                )
            event_id = request.event_id
            if request.presentation_version_id is not None:
                version_event = session.scalar(
                    select(Presentation.event_id)
                    .join(
                        PresentationVersion,
                        PresentationVersion.presentation_id == Presentation.presentation_id,
                    )
                    .where(
                        PresentationVersion.presentation_version_id
                        == request.presentation_version_id,
                        Presentation.event_id == Event.event_id,
                        Event.site_id == request.site_id,
                    )
                )
                if version_event is None:
                    raise IngestionError("presentation version was not found", code="invalid_link")
                if event_id is not None and event_id != version_event:
                    raise IngestionError("event does not own the presentation version")
                event_id = version_event
            elif event_id is not None:
                owned_event = session.scalar(
                    select(Event.event_id).where(
                        Event.event_id == event_id, Event.site_id == request.site_id
                    )
                )
                if owned_event is None:
                    raise IngestionError("event was not found at this Site", code="invalid_link")
            session.expunge(target)
            return target, event_id

    def _create_staging_record(
        self,
        request: IngestionRequest,
        media_id: UUID,
        object_key: str,
        filename: str,
        storage_target_id: UUID,
        event_id: UUID | None,
    ) -> UUID | None:
        with self.session_factory.begin() as session:
            canonical_filename = None
            if request.presentation_version_id is not None:
                row = session.execute(
                    select(PresentationVersion, Presentation, Event)
                    .join(
                        Presentation,
                        Presentation.presentation_id == PresentationVersion.presentation_id,
                    )
                    .join(Event, Event.event_id == Presentation.event_id)
                    .where(
                        PresentationVersion.presentation_version_id
                        == request.presentation_version_id
                    )
                ).one()
                version, presentation, event = row
                program_session = presentation.session
                room_label = (
                    session.scalar(
                        select(Room.label)
                        .join(RoomAssignment, RoomAssignment.room_id == Room.room_id)
                        .where(
                            RoomAssignment.session_id == presentation.session_id,
                            RoomAssignment.active.is_(True),
                        )
                    )
                    if presentation.session_id
                    else None
                )
                presenter = session.execute(
                    select(PersonProjection.family_name, PersonProjection.given_name)
                    .join(
                        EventParticipation,
                        EventParticipation.person_id == PersonProjection.person_id,
                    )
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
                        PresentationPresenter.primary_presenter.desc(),
                        PresentationPresenter.presenter_order,
                        PresentationPresenter.presentation_presenter_id,
                    )
                    .limit(1)
                ).one_or_none()
                canonical_filename = canonical_presentation_filename(
                    CanonicalPresentationMetadata(
                        presentation_identifier=presentation.presentation_identifier,
                        event_timezone=event.timezone,
                        starts_at=(
                            presentation.scheduled_at
                            or (program_session.starts_at if program_session else None)
                        ),
                        room_label=room_label,
                        presenter_family_name=presenter[0] if presenter else None,
                        presenter_given_name=presenter[1] if presenter else None,
                        title=presentation.title,
                        version_number=version.version_number,
                        original_filename=filename,
                    )
                )
            media = MediaObject(
                media_object_id=media_id,
                site_id=request.site_id,
                event_id=event_id,
                storage_target_id=storage_target_id,
                object_key=object_key,
                category=request.category,
                original_filename=filename,
                source_relative_path=normalize_source_relative_path(
                    request.source_relative_path, filename
                ),
                intake_origin=request.intake_origin,
                source_actor=request.source_actor,
                source_share=request.source_share,
                canonical_filename=canonical_filename,
                mime_type=request.client_mime_type,
                availability=MediaAvailability.STAGING,
                ingestion_idempotency_key=request.idempotency_key,
            )
            session.add(media)
            if request.presentation_version_id is None:
                return None
            asset = session.scalar(
                select(PresentationAsset)
                .where(
                    PresentationAsset.presentation_version_id == request.presentation_version_id,
                    PresentationAsset.kind == AssetKind.ORIGINAL,
                )
                .with_for_update()
            )
            if asset is None:
                asset = PresentationAsset(
                    presentation_version_id=request.presentation_version_id,
                    media_object_id=media_id,
                    original_filename=filename,
                    kind=AssetKind.ORIGINAL,
                )
                session.add(asset)
            else:
                linked = session.get(MediaObject, asset.media_object_id, with_for_update=True)
                if linked is not None and linked.availability is MediaAvailability.AVAILABLE:
                    raise IngestionConflictError(
                        "presentation version already has a different original asset"
                    )
                asset.media_object_id = media_id
                asset.original_filename = filename
            session.flush()
            return asset.presentation_asset_id

    def _stream_to_staging(
        self, source: BinaryIO, staged: Path, target: StorageTarget
    ) -> tuple[str, int, str]:
        digest = hashlib.sha256()
        total = 0
        header = b""
        try:
            with staged.open("xb", buffering=0) as destination:
                while chunk := source.read(self.chunk_size):
                    if not isinstance(chunk, bytes):
                        raise IngestionError("upload stream must return bytes")
                    total += len(chunk)
                    if total > self.max_upload_bytes:
                        raise IngestionError("upload exceeds configured maximum", code="too_large")
                    if len(header) < 512:
                        header += chunk[: 512 - len(header)]
                    require_capacity(target, len(chunk))
                    digest.update(chunk)
                    destination.write(chunk)
                os.fsync(destination.fileno())
        except FileExistsError as error:
            raise IngestionConflictError("ingestion staging path already exists") from error
        logger.info("hash_completed", extra={"bytes_written": total, "algorithm": "sha256"})
        return digest.hexdigest(), total, _detect_mime_type(header)

    async def _stream_async_to_staging(
        self,
        chunks: AsyncIterator[bytes],
        staged: Path,
        target: StorageTarget,
    ) -> tuple[str, int, str]:
        digest = hashlib.sha256()
        total = 0
        header = b""
        try:
            with staged.open("xb", buffering=0) as destination:
                async for chunk in chunks:
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > self.max_upload_bytes:
                        raise IngestionError("upload exceeds configured maximum", code="too_large")
                    if len(header) < 512:
                        header += chunk[: 512 - len(header)]
                    await to_thread.run_sync(require_capacity, target, len(chunk))
                    digest.update(chunk)
                    await to_thread.run_sync(destination.write, chunk)
                await to_thread.run_sync(os.fsync, destination.fileno())
        except FileExistsError as error:
            raise IngestionConflictError("ingestion staging path already exists") from error
        logger.info("hash_completed", extra={"bytes_written": total, "algorithm": "sha256"})
        return digest.hexdigest(), total, _detect_mime_type(header)

    def _record_finalizing(
        self, media_id: UUID, content_hash: str, size_bytes: int, mime_type: str
    ) -> None:
        with self.session_factory.begin() as session:
            media = session.get(MediaObject, media_id, with_for_update=True)
            if media is None:
                raise IngestionError("staging media record disappeared")
            media.content_hash = content_hash
            media.hash_algorithm = "sha256"
            media.size_bytes = size_bytes
            media.mime_type = mime_type
            media.availability = MediaAvailability.FINALIZING

    def _mark_available_and_enqueue(
        self, media_id: UUID, site_id: UUID, *, replicate_to_central: bool = True
    ) -> UUID:
        with self.session_factory.begin() as session:
            media = session.get(MediaObject, media_id, with_for_update=True)
            if media is None or media.availability != MediaAvailability.FINALIZING:
                raise IngestionError("media is not ready for availability transition")
            media.availability = MediaAvailability.AVAILABLE
            job = SiteQueue(session).enqueue_processing(
                site_id=site_id,
                media_object_id=media_id,
                job_type="media.inspect",
                payload={"schema_version": 1, "data": {"media_object_id": str(media_id)}},
                idempotency_key=f"media.inspect:{media_id}",
                priority=PRIORITY_VALUES[JobPriority.NORMAL],
                required_capabilities=["cpu"],
            )
            asset = session.scalar(
                select(PresentationAsset).where(PresentationAsset.media_object_id == media_id)
            )
            if (
                replicate_to_central
                and not (media.ingestion_idempotency_key or "").startswith("transfer:")
                and asset is not None
                and media.content_hash is not None
                and media.size_bytes is not None
            ):
                version = session.get(PresentationVersion, asset.presentation_version_id)
                presentation = (
                    session.get(Presentation, version.presentation_id)
                    if version is not None
                    else None
                )
                if presentation is not None and media.event_id is not None:
                    replication_id = new_uuid7()
                    replication = MediaReplicationSession(
                        replication_session_id=replication_id,
                        site_id=site_id,
                        event_id=media.event_id,
                        presentation_id=presentation.presentation_id,
                        presentation_version_id=asset.presentation_version_id,
                        media_object_id=media_id,
                        expected_size=media.size_bytes,
                        sha256=media.content_hash,
                        original_filename=media.original_filename,
                        canonical_filename=media.canonical_filename,
                        media_type=media.mime_type,
                        state=MediaReplicationState.QUEUED,
                    )
                    session.add(replication)
                    SiteQueue(session).enqueue_transfer(
                        transfer_job_id=replication_id,
                        site_id=site_id,
                        media_object_id=media_id,
                        transfer_type="presentation_media.central_push",
                        payload={
                            "schema_version": 1,
                            "data": {"replication_session_id": str(replication_id)},
                        },
                        idempotency_key=f"media.replicate:{media_id}:{asset.presentation_version_id}",
                        priority=PRIORITY_VALUES[JobPriority.NORMAL],
                        required_capabilities=["transfer"],
                        max_attempts=100,
                    )
            return job.processing_job_id

    def _record_failure(self, media_id: UUID, error: Exception, *, finalized: bool) -> None:
        try:
            with self.session_factory.begin() as session:
                media = session.get(MediaObject, media_id, with_for_update=True)
                if media is None:
                    return
                media.failure_reason = str(error)[:1024]
                if not finalized:
                    media.availability = MediaAvailability.FAILED
        except Exception:
            logger.exception(
                "failed_to_persist_ingestion_failure", extra={"media_object_id": str(media_id)}
            )

    def reconcile_finalizing(self, media_id: UUID) -> IngestionResult:
        """Complete the safe final-file/DB failure window idempotently."""
        with self.session_factory() as session:
            media = session.get(MediaObject, media_id)
            if media is None or media.availability != MediaAvailability.FINALIZING:
                raise IngestionError("media is not awaiting reconciliation")
            target = session.get(StorageTarget, media.storage_target_id)
            if target is None:
                raise IngestionError("storage target was not found")
            session.expunge(target)
            expected_hash = media.content_hash
            expected_size = media.size_bytes
            site_id = media.site_id
        path = resolve_object_path(target, media.object_key)
        digest = hashlib.sha256()
        size = 0
        try:
            with path.open("rb") as source:
                while chunk := source.read(self.chunk_size):
                    digest.update(chunk)
                    size += len(chunk)
        except OSError as error:
            raise IngestionError("finalized file is unavailable") from error
        if size != expected_size or digest.hexdigest() != expected_hash:
            raise IngestionError("finalized file does not match persisted metadata")
        job_id = self._mark_available_and_enqueue(media_id, site_id)
        return IngestionResult(
            media_id,
            None,
            job_id,
            MediaAvailability.AVAILABLE,
            size,
            digest.hexdigest(),
        )
