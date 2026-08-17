"""Durable Central receiver for Site-originated authoritative media replicas."""

from __future__ import annotations

import hashlib
import logging
import os
from datetime import datetime
from pathlib import Path
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

logger = logging.getLogger(__name__)


def safe_replication_path(root: Path, key: str, *, final: bool = False) -> Path:
    """Resolve only opaque UUID keys beneath the private receiver roots."""
    try:
        UUID(key)
    except ValueError as exc:
        raise ValueError("invalid opaque replication key") from exc
    directory = "site-replicas" if final else ".replication-partials"
    base = (root / directory).resolve()
    base.mkdir(parents=True, exist_ok=True)
    path = (base / key).resolve()
    if path.parent != base:
        raise ValueError("replication path escapes storage root")
    return path


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


def recover_partial(root: Path, receiver: MediaReplicationReceiveSession) -> Path:
    path = safe_replication_path(root, receiver.partial_key)
    path.touch(exist_ok=True)
    size = path.stat().st_size
    if size < receiver.confirmed_offset:
        raise RuntimeError("replication partial is shorter than confirmed offset")
    if size > receiver.confirmed_offset:
        with path.open("r+b") as partial:
            partial.truncate(receiver.confirmed_offset)
            partial.flush()
            os.fsync(partial.fileno())
    return path


def finalize_replication(
    session: Session, root: Path, receiver: MediaReplicationReceiveSession
) -> MediaObjectReplica | None:
    if receiver.finalized_media_object_id is not None:
        replica = session.get(MediaObjectReplica, receiver.finalized_media_object_id)
        if replica is None:
            raise RuntimeError("finalized replica mapping is invalid")
        return replica
    if receiver.confirmed_offset != receiver.expected_size:
        raise ValueError("replication byte range is incomplete")
    receiver.state = MediaTransferState.VERIFYING
    media_id = receiver.source_media_object_id
    final_path = safe_replication_path(root, str(media_id), final=True)
    partial = final_path if final_path.exists() else recover_partial(root, receiver)
    digest = hashlib.sha256()
    with partial.open("rb") as source:
        while block := source.read(4 * 1024 * 1024):
            digest.update(block)
    if digest.hexdigest() != receiver.sha256:
        receiver.state = MediaTransferState.FAILED
        receiver.replication_state = MediaReplicationState.FAILED
        receiver.error_detail = "sha256 mismatch"
        return None
    authorize_replication_context(
        session,
        site_id=receiver.origin_site_id,
        event_id=receiver.event_id,
        presentation_id=receiver.presentation_id,
        presentation_version_id=receiver.presentation_version_id,
    )
    if not final_path.exists():
        os.replace(partial, final_path)
    replica = MediaObjectReplica(
        media_object_id=media_id,
        authoritative_site_id=receiver.origin_site_id,
        event_id=receiver.event_id,
        category=MediaCategory.PRESENTATION_VERSION,
        object_key=f"site-replicas/{media_id}",
        content_hash=receiver.sha256,
        size_bytes=receiver.expected_size,
        source_revision=1,
    )
    session.add(replica)
    session.add(
        PresentationAsset(
            presentation_version_id=receiver.presentation_version_id,
            media_object_id=media_id,
            kind=AssetKind.ORIGINAL,
        )
    )
    receiver.finalized_media_object_id = media_id
    receiver.state = MediaTransferState.COMPLETED
    receiver.replication_state = MediaReplicationState.SYNCED
    receiver.last_progress_at = utc_now()
    receiver.error_detail = None
    session.flush()
    return replica


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


def cleanup_replication_partials(session: Session, root: Path, cutoff: datetime) -> int:
    """Expire only terminal, old, non-finalized receiver sessions."""
    receivers = session.scalars(
        select(MediaReplicationReceiveSession).where(
            MediaReplicationReceiveSession.finalized_media_object_id.is_(None),
            MediaReplicationReceiveSession.updated_at < cutoff,
            MediaReplicationReceiveSession.state.in_(
                [MediaTransferState.FAILED, MediaTransferState.CANCELLED]
            ),
        )
    ).all()
    for receiver in receivers:
        safe_replication_path(root, receiver.partial_key).unlink(missing_ok=True)
        receiver.state = MediaTransferState.EXPIRED
        logger.info(
            "replication_partial_expired",
            extra={"replication_session_id": str(receiver.replication_session_id)},
        )
    return len(receivers)
