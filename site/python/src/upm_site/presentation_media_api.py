"""Site-local presentation-media operations that never call Central."""

from collections.abc import Callable, Iterator
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from upm_shared.contracts.sync import UPM_SYNC_PROTOCOL_VERSION
from upm_shared.enums import (
    JobStatus,
    MediaReplicationState,
    PresentationProcessingStatus,
    PresentationWorkflowStatus,
    SourceSystem,
    SyncState,
)
from upm_shared.identifiers import new_uuid7
from upm_shared.jobs import OutboxPayload
from upm_shared.presentation_media import (
    MatchCandidate,
    allocate_presentation_identifier,
    match_presentation,
)
from upm_site.persistence.models import (
    Event,
    LocalSiteIdentity,
    MediaObject,
    MediaReplicationSession,
    Presentation,
    PresentationAsset,
    PresentationVersion,
    TransferJob,
)
from upm_site.persistence.models import Session as ProgramSession
from upm_site.persistence.queue import SiteQueue
from upm_site.sync import next_sequence


class SitePresentationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: UUID | None = None
    title: Annotated[str, Field(min_length=1, max_length=255)]
    source_presentation_id: Annotated[str | None, Field(max_length=512)] = None


def register_presentation_media_routes(
    app: FastAPI,
    db: Callable[[], Iterator[Session]],
    transaction: Callable[[], Iterator[Session]],
) -> None:
    ReadSession = Annotated[Session, Depends(db)]
    WriteSession = Annotated[Session, Depends(transaction)]

    @app.post("/api/v1/events/{event_id}/presentations", status_code=201, tags=["media"])
    def create_local_presentation(
        event_id: UUID, payload: SitePresentationCreate, session: WriteSession
    ) -> dict[str, object]:
        event = session.get(Event, event_id)
        if event is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "event not found")
        if payload.session_id:
            program_session = session.get(ProgramSession, payload.session_id)
            if program_session is None or program_session.event_id != event_id:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "session not in event")
        identity = session.scalar(select(LocalSiteIdentity))
        origin = identity.display_name if identity else str(event.site_id)[:8]
        presentation_id = new_uuid7()
        identifier, source = allocate_presentation_identifier(
            payload.source_presentation_id, origin, presentation_id
        )
        item = Presentation(
            presentation_id=presentation_id,
            event_id=event_id,
            session_id=payload.session_id,
            title=payload.title.strip(),
            presentation_code=payload.source_presentation_id,
            presentation_identifier=identifier,
            presentation_identifier_source=source,
            external_presentation_id=payload.source_presentation_id,
            workflow_status=PresentationWorkflowStatus.EXPECTED,
            processing_status=PresentationProcessingStatus.NOT_STARTED,
            active=True,
            sync_state=SyncState.PENDING,
        )
        session.add(item)
        session.flush()
        SiteQueue(session).enqueue_outbox(
            event_type="site.presentation.upserted",
            aggregate_type="presentation",
            aggregate_id=item.presentation_id,
            site_id=event.site_id,
            protocol_version=UPM_SYNC_PROTOCOL_VERSION,
            source_sequence=next_sequence(session),
            idempotency_key=f"presentation:{item.presentation_id}:{item.revision}",
            payload=OutboxPayload(
                source_system=SourceSystem.SITE,
                data={
                    "presentation_id": str(item.presentation_id),
                    "event_id": str(item.event_id),
                    "session_id": str(item.session_id) if item.session_id else None,
                    "title": item.title,
                    "presentation_identifier": item.presentation_identifier,
                    "presentation_identifier_source": item.presentation_identifier_source,
                    "external_presentation_id": item.external_presentation_id,
                    "revision": item.revision,
                },
            ),
        )
        return {
            "presentation_id": item.presentation_id,
            "presentation_identifier": item.presentation_identifier,
            "presentation_identifier_source": item.presentation_identifier_source,
            "sync_state": item.sync_state,
        }

    @app.post("/api/v1/presentations/{presentation_id}/versions", status_code=201, tags=["media"])
    def create_local_version(presentation_id: UUID, session: WriteSession) -> dict[str, object]:
        presentation = session.get(Presentation, presentation_id)
        if presentation is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "presentation not found")
        # Serialize allocation for this logical presentation while retaining UUID identity.
        session.execute(
            select(Presentation.presentation_id)
            .where(Presentation.presentation_id == presentation_id)
            .with_for_update()
        )
        number = (
            session.scalar(
                select(func.max(PresentationVersion.version_number)).where(
                    PresentationVersion.presentation_id == presentation_id
                )
            )
            or 0
        ) + 1
        version = PresentationVersion(
            presentation_version_id=new_uuid7(),
            presentation_id=presentation_id,
            version_number=number,
            sync_state=SyncState.PENDING,
        )
        session.add(version)
        presentation.workflow_status = PresentationWorkflowStatus.RECEIVED
        presentation.sync_state = SyncState.PENDING
        session.flush()
        SiteQueue(session).enqueue_outbox(
            event_type="site.presentation_version.created",
            aggregate_type="presentation_version",
            aggregate_id=version.presentation_version_id,
            site_id=session.get(Event, presentation.event_id).site_id,
            protocol_version=UPM_SYNC_PROTOCOL_VERSION,
            source_sequence=next_sequence(session),
            idempotency_key=f"presentation-version:{version.presentation_version_id}",
            payload=OutboxPayload(
                source_system=SourceSystem.SITE,
                data={
                    "presentation_version_id": str(version.presentation_version_id),
                    "presentation_id": str(presentation_id),
                    "version_number": number,
                    "created_at": version.created_at.isoformat(),
                },
            ),
        )
        return {
            "presentation_version_id": version.presentation_version_id,
            "presentation_id": presentation_id,
            "version_number": number,
            "sync_state": version.sync_state,
        }

    @app.get("/api/v1/events/{event_id}/media", tags=["media"])
    def event_media(
        event_id: UUID,
        session: ReadSession,
        search: Annotated[str | None, Query(max_length=255)] = None,
    ) -> dict[str, object]:
        query = select(Presentation).where(
            Presentation.event_id == event_id, Presentation.active.is_(True)
        )
        if search:
            term = f"%{search.strip()}%"
            query = query.where(
                or_(
                    Presentation.presentation_identifier.ilike(term),
                    Presentation.external_presentation_id.ilike(term),
                    Presentation.title.ilike(term),
                    Presentation.presentation_code.ilike(term),
                )
            )
        presentations = session.scalars(
            query.order_by(
                Presentation.scheduled_at.asc().nulls_last(),
                Presentation.title,
                Presentation.presentation_id,
            )
        ).all()
        rows = []
        for item in presentations:
            versions = session.scalars(
                select(PresentationVersion)
                .where(PresentationVersion.presentation_id == item.presentation_id)
                .order_by(PresentationVersion.version_number.desc())
            ).all()
            history = []
            for version in versions:
                media = session.scalar(
                    select(MediaObject)
                    .join(
                        PresentationAsset,
                        PresentationAsset.media_object_id == MediaObject.media_object_id,
                    )
                    .where(
                        PresentationAsset.presentation_version_id == version.presentation_version_id
                    )
                    .order_by(MediaObject.created_at.desc())
                )
                history.append(
                    {
                        "presentation_version_id": version.presentation_version_id,
                        "version_number": version.version_number,
                        "sync_state": version.sync_state,
                        "media": None
                        if media is None
                        else {
                            "media_object_id": media.media_object_id,
                            "original_filename": media.original_filename,
                            "canonical_filename": media.canonical_filename,
                            "size_bytes": media.size_bytes,
                            "sha256": media.content_hash,
                            "availability": media.availability,
                            "failure_reason": media.failure_reason,
                        },
                        "replication": None,
                    }
                )
                if media is not None:
                    replication = session.scalar(
                        select(MediaReplicationSession).where(
                            MediaReplicationSession.media_object_id == media.media_object_id,
                            MediaReplicationSession.presentation_version_id
                            == version.presentation_version_id,
                        )
                    )
                    if replication is not None:
                        transfer = session.get(TransferJob, replication.replication_session_id)
                        history[-1]["replication"] = {
                            "replication_session_id": replication.replication_session_id,
                            "state": replication.state,
                            "confirmed_offset": replication.confirmed_offset,
                            "expected_size": replication.expected_size,
                            "retry_count": replication.retry_count,
                            "last_progress_at": replication.last_progress_at,
                            "last_error": replication.last_error,
                            "job_status": transfer.status if transfer else None,
                        }
            rows.append(
                {
                    "presentation_id": item.presentation_id,
                    "presentation_identifier": item.presentation_identifier,
                    "presentation_identifier_source": item.presentation_identifier_source,
                    "external_presentation_id": item.external_presentation_id,
                    "title": item.title,
                    "scheduled_at": item.scheduled_at,
                    "media_state": "missing"
                    if not history or history[0]["media"] is None
                    else history[0]["media"]["availability"],
                    "sync_state": item.sync_state,
                    "versions": history,
                }
            )
        return {
            "summary": {
                "expected": len(rows),
                "missing": sum(row["media_state"] == "missing" for row in rows),
                "ready": sum(row["media_state"] == "available" for row in rows),
                "sync_pending": sum(row["sync_state"] == SyncState.PENDING for row in rows),
            },
            "presentations": rows,
        }

    @app.get("/api/v1/events/{event_id}/media/match", tags=["media"])
    def match_upload(event_id: UUID, filename: Annotated[str, Query()], session: ReadSession):
        candidates = [
            MatchCandidate(
                presentation_id=item.presentation_id,
                presentation_identifier=item.presentation_identifier,
                external_presentation_id=item.external_presentation_id,
                title=item.title,
            )
            for item in session.scalars(
                select(Presentation).where(
                    Presentation.event_id == event_id, Presentation.active.is_(True)
                )
            )
        ]
        return match_presentation(filename, candidates)

    @app.post("/api/v1/media-replications/{replication_session_id}/retry", tags=["media"])
    def retry_replication(replication_session_id: UUID, session: WriteSession) -> dict[str, object]:
        replication = session.get(
            MediaReplicationSession, replication_session_id, with_for_update=True
        )
        transfer = session.get(TransferJob, replication_session_id, with_for_update=True)
        if replication is None or transfer is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "replication not found")
        if transfer.status not in {JobStatus.FAILED, JobStatus.EXHAUSTED, JobStatus.RETRY_WAIT}:
            raise HTTPException(status.HTTP_409_CONFLICT, "replication is not retryable")
        transfer.status = JobStatus.RETRY_WAIT
        transfer.claimed_by_worker_id = None
        transfer.lease_expires_at = None
        transfer.error_code = None
        transfer.last_error = None
        replication.state = MediaReplicationState.RETRY_WAIT
        replication.retry_count += 1
        replication.last_error = None
        return {
            "replication_session_id": replication.replication_session_id,
            "state": replication.state,
            "retry_count": replication.retry_count,
        }
