"""ADR-0011 Site-pull execution with durable confirmed-offset semantics."""

import logging
from datetime import datetime
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from upm_shared.contracts.media_transfer import MediaTransferProgress
from upm_shared.contracts.sync import UPM_SYNC_PROTOCOL_VERSION
from upm_shared.enums import JobStatus, MediaCategory, MediaTransferState, SourceSystem
from upm_shared.jobs import OutboxPayload
from upm_shared.media_storage_client import MediaStorageClient
from upm_site.config import SiteSettings
from upm_site.media.ingestion import IngestionRequest, MediaIngestionService
from upm_site.persistence.models import MediaTransferSession, OutboxEvent, TransferJob, utc_now
from upm_site.persistence.queue import SiteQueue
from upm_site.sync_transport import auth_context, checked

logger = logging.getLogger(__name__)


def execute_central_pull(
    session: Session,
    factory: sessionmaker[Session],
    settings: SiteSettings,
    work: TransferJob,
    client: httpx.Client,
) -> bool:
    transfer = session.get(MediaTransferSession, work.transfer_job_id, with_for_update=True)
    if transfer is None:
        raise ValueError("transfer session is missing")
    if transfer.state is MediaTransferState.COMPLETED:
        return True
    _, registration, headers = auth_context(session, settings)
    storage = MediaStorageClient(settings.media_storage_url, settings.media_storage_token)
    if transfer.storage_target_id is None:
        allocation = storage.allocate_staging()
        transfer.storage_target_id = UUID(allocation["storage_target_id"])
        transfer.partial_key = allocation["storage_key"]
    transfer.state = MediaTransferState.TRANSFERRING
    url = (
        f"{registration.central_url}/api/v1/media-transfers/{transfer.transfer_session_id}/content"
    )
    with client.stream(
        "GET", url, headers=headers, params={"offset": transfer.confirmed_offset}
    ) as response:
        checked(response)
        expected_offset = int(response.headers["X-UPM-Transfer-Offset"])
        next_offset = int(response.headers["X-UPM-Transfer-Next-Offset"])
        if expected_offset != transfer.confirmed_offset or next_offset > transfer.expected_size:
            raise ValueError("Central returned invalid transfer range")
        block = b"".join(response.iter_bytes(settings.transfer_block_bytes))
        persisted = storage.append_staging(
            transfer.storage_target_id, transfer.partial_key, transfer.confirmed_offset, block
        )
        if persisted["confirmed_offset"] != next_offset:
            raise ValueError("received byte count does not match acknowledged range")
        transfer.confirmed_offset = next_offset
        transfer.last_progress_at = utc_now()
    if transfer.confirmed_offset != transfer.expected_size:
        enqueue_transfer_progress(session, transfer)
        return False
    transfer.state = MediaTransferState.VERIFYING
    committed = storage.commit(transfer.storage_target_id, transfer.partial_key, transfer.sha256)
    result = MediaIngestionService(
        factory, max_upload_bytes=settings.max_upload_bytes
    ).adopt_committed(
        IngestionRequest(
            site_id=transfer.site_id,
            event_id=transfer.event_id,
            presentation_version_id=transfer.presentation_version_id,
            original_filename=transfer.original_filename,
            category=MediaCategory.PRESENTATION_VERSION,
            expected_size=transfer.expected_size,
            idempotency_key=f"transfer:{transfer.transfer_session_id}",
            client_mime_type=transfer.media_type,
            replicate_to_central=False,
        ),
        committed,
        transfer.expected_size,
        transfer.sha256,
    )
    transfer.media_object_id = result.media_object_id
    transfer.state = MediaTransferState.COMPLETED
    transfer.error_detail = None
    storage.release_staging(transfer.storage_target_id, transfer.partial_key)
    enqueue_transfer_progress(session, transfer, local_media_ready=True)
    return True


def enqueue_transfer_progress(
    session: Session,
    transfer: MediaTransferSession,
    *,
    local_media_ready: bool = False,
) -> OutboxEvent:
    """Publish one durable event per acknowledged block or state transition."""
    from upm_site.sync import next_sequence

    observed_at = transfer.last_progress_at or utc_now()
    progress = MediaTransferProgress(
        transfer_session_id=transfer.transfer_session_id,
        site_id=transfer.site_id,
        event_id=transfer.event_id,
        presentation_id=transfer.presentation_id,
        presentation_version_id=transfer.presentation_version_id,
        expected_size=transfer.expected_size,
        confirmed_offset=transfer.confirmed_offset,
        state=transfer.state,
        retry_count=transfer.retry_count,
        last_progress_at=observed_at,
        media_object_id=transfer.media_object_id,
        local_media_ready=local_media_ready,
        error_detail=transfer.error_detail,
    )
    idempotency_key = (
        f"media-progress:{transfer.transfer_session_id}:"
        f"{transfer.confirmed_offset}:{transfer.state}"
    )
    existing = session.scalar(
        select(OutboxEvent)
        .where(
            OutboxEvent.source_system == SourceSystem.SITE,
            OutboxEvent.idempotency_key == idempotency_key,
        )
        .with_for_update()
    )
    payload = OutboxPayload(
        source_system=SourceSystem.SITE,
        data=progress.model_dump(mode="json"),
    )
    if existing is not None:
        if (
            existing.event_type != "site.media_transfer.progress"
            or existing.aggregate_id != transfer.transfer_session_id
        ):
            raise ValueError("media transfer progress idempotency conflict")
        if existing.status in {JobStatus.PENDING, JobStatus.RETRY_WAIT}:
            existing.payload = payload.data
        return existing
    return SiteQueue(session).enqueue_outbox(
        event_type="site.media_transfer.progress",
        aggregate_type="media_transfer",
        aggregate_id=transfer.transfer_session_id,
        site_id=transfer.site_id,
        protocol_version=UPM_SYNC_PROTOCOL_VERSION,
        source_sequence=next_sequence(session),
        idempotency_key=idempotency_key,
        payload=payload,
    )


def cleanup_transfer_partials(session: Session, settings: SiteSettings, cutoff: datetime) -> int:
    """Expire old terminal pulls while preserving active/retryable/finalized work."""
    transfers = session.scalars(
        select(MediaTransferSession)
        .join(TransferJob, TransferJob.transfer_job_id == MediaTransferSession.transfer_session_id)
        .where(
            MediaTransferSession.media_object_id.is_(None),
            MediaTransferSession.updated_at < cutoff,
            MediaTransferSession.state.in_(
                [MediaTransferState.FAILED, MediaTransferState.CANCELLED]
            ),
            TransferJob.lease_expires_at.is_(None),
            TransferJob.status.in_([JobStatus.FAILED, JobStatus.EXHAUSTED, JobStatus.CANCELLED]),
        )
    ).all()
    for transfer in transfers:
        if transfer.storage_target_id:
            MediaStorageClient(
                settings.media_storage_url, settings.media_storage_token
            ).release_staging(transfer.storage_target_id, transfer.partial_key)
        transfer.state = MediaTransferState.EXPIRED
        logger.info(
            "transfer_partial_expired",
            extra={"transfer_session_id": str(transfer.transfer_session_id)},
        )
    return len(transfers)
