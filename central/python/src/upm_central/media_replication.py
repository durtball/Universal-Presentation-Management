"""Durable Central receiver for Site-originated authoritative media replicas."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from upm_central.persistence.models import (
    EventDeployment,
    MediaObjectReplica,
    MediaReplicationReceiveSession,
    Presentation,
    PresentationAsset,
    PresentationVersion,
    utc_now,
)
from upm_shared.enums import (
    AssetKind,
    MediaCategory,
    MediaReplicationState,
    MediaTransferState,
)


def authorize_replication_context(
    session: Session,
    *,
    site_id: UUID,
    event_id: UUID,
    presentation_id: UUID,
    presentation_version_id: UUID,
) -> Presentation:
    deployment = session.scalar(
        select(EventDeployment).where(
            EventDeployment.site_id == site_id,
            EventDeployment.event_id == event_id,
            EventDeployment.status != "revoked",
        )
    )
    presentation = session.get(Presentation, presentation_id)
    version = session.get(PresentationVersion, presentation_version_id)
    if (
        deployment is None
        or presentation is None
        or presentation.event_id != event_id
        or version is None
        or version.presentation_id != presentation_id
    ):
        raise LookupError("replication context is not available")
    return presentation


def finalize_replication_reference(
    session: Session, receiver: MediaReplicationReceiveSession, committed: dict
) -> MediaObjectReplica:
    """Finalize domain metadata after Media Storage has checksum-verified publication."""
    existing = (
        session.get(MediaObjectReplica, receiver.finalized_media_object_id)
        if receiver.finalized_media_object_id
        else None
    )
    if existing is not None:
        return existing
    authorize_replication_context(
        session,
        site_id=receiver.origin_site_id,
        event_id=receiver.event_id,
        presentation_id=receiver.presentation_id,
        presentation_version_id=receiver.presentation_version_id,
    )
    replica = MediaObjectReplica(
        media_object_id=receiver.source_media_object_id,
        authoritative_site_id=receiver.origin_site_id,
        event_id=receiver.event_id,
        category=MediaCategory.PRESENTATION_VERSION,
        object_key=committed["storage_key"],
        content_hash=receiver.sha256,
        size_bytes=receiver.expected_size,
        source_revision=1,
    )
    session.add(replica)
    session.add(
        PresentationAsset(
            presentation_version_id=receiver.presentation_version_id,
            media_object_id=receiver.source_media_object_id,
            original_filename=receiver.original_filename,
            kind=AssetKind.ORIGINAL,
        )
    )
    receiver.finalized_media_object_id = receiver.source_media_object_id
    receiver.state = MediaTransferState.COMPLETED
    receiver.replication_state = MediaReplicationState.SYNCED
    receiver.last_progress_at = utc_now()
    receiver.error_detail = None
    session.flush()
    return replica
