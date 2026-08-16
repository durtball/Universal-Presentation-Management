"""Site-initiated resumable push of locally authoritative presentation media."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session, sessionmaker

from upm_shared.enums import MediaReplicationState
from upm_site.config import SiteSettings
from upm_site.media.storage import resolve_object_path
from upm_site.persistence.models import (
    CentralRegistration,
    MediaObject,
    MediaReplicationSession,
    Presentation,
    StorageTarget,
    TransferJob,
    utc_now,
)
from upm_site.sync import decrypt_secret
from upm_site.sync_transport import checked


def execute_central_push(
    session: Session,
    factory: sessionmaker[Session],
    settings: SiteSettings,
    job: TransferJob,
    client,
) -> bool:
    replication = session.get(MediaReplicationSession, job.transfer_job_id, with_for_update=True)
    if replication is None:
        raise ValueError("replication session does not exist")
    media = session.get(MediaObject, replication.media_object_id)
    target = session.get(StorageTarget, media.storage_target_id) if media is not None else None
    presentation = session.get(Presentation, replication.presentation_id)
    registration = session.get(CentralRegistration, replication.site_id)
    if media is None or target is None or presentation is None or registration is None:
        raise ValueError("replication source metadata is unavailable")
    token = decrypt_secret(settings, registration.credential_encrypted)
    if not token or not registration.central_url:
        raise RuntimeError("Central registration is unavailable")
    source_path = resolve_object_path(target, media.object_key)
    if not source_path.is_file() or source_path.stat().st_size != replication.expected_size:
        raise RuntimeError("authoritative Site media is unavailable")
    headers = {
        "Authorization": f"Bearer {token}",
        "X-UPM-Site-ID": str(replication.site_id),
    }
    base = f"{registration.central_url}/api/v1/media-replications"
    manifest = {
        "replication_session_id": str(replication.replication_session_id),
        "event_id": str(replication.event_id),
        "presentation_id": str(replication.presentation_id),
        "presentation_version_id": str(replication.presentation_version_id),
        "media_object_id": str(replication.media_object_id),
        "presentation_identifier": presentation.presentation_identifier,
        "original_filename": replication.original_filename,
        "canonical_filename": replication.canonical_filename,
        "expected_size": replication.expected_size,
        "sha256": replication.sha256,
        "media_type": replication.media_type,
    }
    remote = checked(client.post(base, headers=headers, json=manifest)).json()
    confirmed = int(remote["confirmed_offset"])
    if confirmed < 0 or confirmed > replication.expected_size:
        raise ValueError("Central returned an invalid confirmed offset")
    replication.confirmed_offset = confirmed
    replication.state = MediaReplicationState.SYNCING
    with source_path.open("rb") as source:
        while confirmed < replication.expected_size:
            source.seek(confirmed)
            block = source.read(
                min(settings.transfer_block_bytes, replication.expected_size - confirmed)
            )
            if not block:
                raise RuntimeError("Site media ended before expected size")
            remote = checked(
                client.put(
                    f"{base}/{replication.replication_session_id}/content",
                    headers=headers,
                    params={"offset": confirmed},
                    content=block,
                )
            ).json()
            next_offset = int(remote["confirmed_offset"])
            if next_offset != confirmed + len(block):
                raise ValueError("Central acknowledgment does not match uploaded block")
            confirmed = next_offset
            replication.confirmed_offset = confirmed
            replication.last_progress_at = utc_now()
            job.progress = (
                100
                if replication.expected_size == 0
                else confirmed * 100 / replication.expected_size
            )
            session.flush()
    result = checked(
        client.post(f"{base}/{replication.replication_session_id}/finalize", headers=headers)
    ).json()
    if (
        result.get("replication_state") != MediaReplicationState.SYNCED
        or result.get("presentation_version_id") != str(replication.presentation_version_id)
        or not result.get("central_media_object_id")
    ):
        raise ValueError("Central did not return a finalized replication result")
    replication.central_media_object_id = UUID(result["central_media_object_id"])
    replication.state = MediaReplicationState.SYNCED
    replication.last_error = None
    replication.last_progress_at = utc_now()
    return True
