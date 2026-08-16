"""ADR-0011 Site-pull execution with durable confirmed-offset semantics."""

import hashlib
import os
from pathlib import Path

import httpx
from sqlalchemy.orm import Session, sessionmaker

from upm_shared.enums import MediaCategory, MediaTransferState
from upm_site.config import SiteSettings
from upm_site.media.ingestion import IngestionRequest, MediaIngestionService
from upm_site.persistence.models import MediaTransferSession, TransferJob, utc_now
from upm_site.sync_transport import auth_context, checked


def partial_path(settings: SiteSettings, transfer_session_id) -> Path:
    root = (Path(settings.media_mount_path) / ".transfers").resolve()
    root.mkdir(mode=0o750, parents=True, exist_ok=True)
    result = (root / f"{transfer_session_id}.partial").resolve()
    if result.parent != root:
        raise ValueError("invalid transfer identity")
    return result


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
    path = partial_path(settings, transfer.transfer_session_id)
    if path.exists() and path.stat().st_size != transfer.confirmed_offset:
        with path.open("r+b") as partial:
            partial.truncate(transfer.confirmed_offset)
            partial.flush()
            os.fsync(partial.fileno())
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
        with path.open("ab") as partial:
            for chunk in response.iter_bytes(settings.transfer_block_bytes):
                partial.write(chunk)
            partial.flush()
            os.fsync(partial.fileno())
        if path.stat().st_size != next_offset:
            raise ValueError("received byte count does not match acknowledged range")
        transfer.confirmed_offset = next_offset
        transfer.last_progress_at = utc_now()
    if transfer.confirmed_offset != transfer.expected_size:
        return False
    transfer.state = MediaTransferState.VERIFYING
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(settings.transfer_block_bytes):
            digest.update(chunk)
    if digest.hexdigest() != transfer.sha256:
        transfer.state = MediaTransferState.FAILED
        transfer.error_detail = "sha256 mismatch"
        raise ValueError("sha256 mismatch")
    with path.open("rb") as source:
        result = MediaIngestionService(factory, max_upload_bytes=settings.max_upload_bytes).ingest(
            IngestionRequest(
                site_id=transfer.site_id,
                event_id=transfer.event_id,
                presentation_version_id=transfer.presentation_version_id,
                original_filename=transfer.original_filename,
                category=MediaCategory.PRESENTATION_VERSION,
                expected_size=transfer.expected_size,
                idempotency_key=f"transfer:{transfer.transfer_session_id}",
                client_mime_type=transfer.media_type,
            ),
            source,
        )
    transfer.media_object_id = result.media_object_id
    transfer.state = MediaTransferState.COMPLETED
    transfer.error_detail = None
    path.unlink(missing_ok=True)
    return True
