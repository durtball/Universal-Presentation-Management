"""Site-local presentation-media operations that never call Central."""

# ruff: noqa: E501

from collections.abc import Callable, Iterator
from typing import Annotated
from urllib.parse import quote
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from upm_shared.contracts.sync import UPM_SYNC_PROTOCOL_VERSION
from upm_shared.enums import (
    AssetKind,
    JobStatus,
    MediaAvailability,
    MediaCategory,
    MediaReplicationState,
    PresentationProcessingStatus,
    PresentationWorkflowStatus,
    SourceSystem,
    SyncState,
)
from upm_shared.identifiers import new_uuid7
from upm_shared.jobs import OutboxPayload
from upm_shared.media_storage_client import AsyncMediaStorageClient
from upm_shared.presentation_media import (
    CanonicalPresentationMetadata,
    MatchCandidate,
    allocate_presentation_identifier,
    canonical_presentation_filename,
    match_presentation,
)
from upm_site.persistence.models import (
    AuditRecord,
    Event,
    EventParticipation,
    LocalSiteIdentity,
    MediaObject,
    MediaReplicationSession,
    PersonProjection,
    Presentation,
    PresentationAsset,
    PresentationPresenter,
    PresentationSession,
    PresentationVersion,
    Room,
    RoomAssignment,
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


class SiteMediaConfirmation(BaseModel):
    presentation_id: UUID


class SiteMediaConfirmationBatch(BaseModel):
    items: list[dict[str, UUID]] = Field(min_length=1, max_length=500)


def _candidate_rows(session: Session, event_id: UUID, search: str, limit: int,
                    terms: list[str] | None = None) -> list[dict[str, object]]:
    query = (
        select(Presentation, ProgramSession)
        .outerjoin(ProgramSession, ProgramSession.session_id == Presentation.session_id)
        .where(Presentation.event_id == event_id, Presentation.active.is_(True))
    )
    needles = terms or ([search.strip()] if search.strip() else [])
    if needles:
        query = query.where(or_(*[
            condition for needle in needles[:100] for condition in (
                Presentation.presentation_identifier.ilike(f"%{needle}%"),
                Presentation.external_presentation_id.ilike(f"%{needle}%"),
                Presentation.title.ilike(f"%{needle}%"),
                ProgramSession.session_code.ilike(f"%{needle}%"),
                ProgramSession.title.ilike(f"%{needle}%"),
                ProgramSession.location_name.ilike(f"%{needle}%"),
                ProgramSession.session_id.in_(
                    select(RoomAssignment.session_id)
                    .join(Room, Room.room_id == RoomAssignment.room_id)
                    .where(
                        RoomAssignment.active.is_(True),
                        Room.label.ilike(f"%{needle}%"),
                    )
                ),
                Presentation.presentation_id.in_(
                    select(PresentationPresenter.presentation_id)
                    .join(EventParticipation, EventParticipation.event_participation_id
                          == PresentationPresenter.event_participation_id)
                    .join(PersonProjection, PersonProjection.person_id
                          == EventParticipation.person_id)
                    .where(or_(EventParticipation.display_name.ilike(f"%{needle}%"),
                               PersonProjection.given_name.ilike(f"%{needle}%"),
                               PersonProjection.family_name.ilike(f"%{needle}%")))
                ),
            )]))
    rows = session.execute(query.order_by(Presentation.scheduled_at.asc().nulls_last(),
                                           Presentation.presentation_identifier).limit(limit)).all()
    ids = [item.presentation_id for item, _ in rows]
    presenter_rows = session.execute(
        select(PresentationPresenter.presentation_id, EventParticipation.display_name,
               PersonProjection.given_name, PersonProjection.family_name)
        .join(EventParticipation, EventParticipation.event_participation_id == PresentationPresenter.event_participation_id)
        .join(PersonProjection, PersonProjection.person_id == EventParticipation.person_id)
        .where(PresentationPresenter.presentation_id.in_(ids), PresentationPresenter.active.is_(True))
        .order_by(PresentationPresenter.primary_presenter.desc(), PresentationPresenter.presenter_order)
    ).all() if ids else []
    names: dict[UUID, list[str]] = {}
    primary_names: dict[UUID, tuple[str | None, str | None]] = {}
    for presentation_id, display, given, family in presenter_rows:
        names.setdefault(presentation_id, []).append(display or " ".join(filter(None, [given, family])))
        primary_names.setdefault(presentation_id, (given, family))
    session_ids = [program_session.session_id for _, program_session in rows if program_session]
    room_names = dict(session.execute(
        select(RoomAssignment.session_id, Room.label)
        .join(Room, Room.room_id == RoomAssignment.room_id)
        .where(
            RoomAssignment.session_id.in_(session_ids),
            RoomAssignment.active.is_(True),
        )
    ).all()) if session_ids else {}
    return [{
        "presentation_id": item.presentation_id,
        "presentation_identifier": item.presentation_identifier,
        "external_presentation_id": item.external_presentation_id,
        "title": item.title,
        "presenters": names.get(item.presentation_id, []),
        "presenter_given_name": primary_names.get(item.presentation_id, (None, None))[0],
        "presenter_family_name": primary_names.get(item.presentation_id, (None, None))[1],
        "session_id": program_session.session_id if program_session else None,
        "session_code": program_session.session_code if program_session else None,
        "session_title": program_session.title if program_session else None,
        "session": program_session.title if program_session else None,
        "starts_at": item.scheduled_at or (program_session.starts_at if program_session else None),
        "room": (room_names.get(program_session.session_id) or program_session.location_name
                 if program_session else None),
    } for item, program_session in rows]


def register_presentation_media_routes(
    app: FastAPI,
    db: Callable[[], Iterator[Session]],
    transaction: Callable[[], Iterator[Session]],
    settings: Callable[[], object],
) -> None:
    ReadSession = Annotated[Session, Depends(db)]
    WriteSession = Annotated[Session, Depends(transaction)]

    @app.get("/api/v1/presentation-versions/{presentation_version_id}/download", tags=["media"])
    async def download_presentation_version(
        presentation_version_id: UUID, session: ReadSession
    ) -> StreamingResponse:
        media = session.scalar(
            select(MediaObject)
            .join(PresentationAsset, PresentationAsset.media_object_id == MediaObject.media_object_id)
            .where(
                PresentationAsset.presentation_version_id == presentation_version_id,
                PresentationAsset.kind == AssetKind.ORIGINAL,
                MediaObject.availability == MediaAvailability.AVAILABLE,
                MediaObject.deleted_at.is_(None),
            )
        )
        if media is None:
            raise HTTPException(404, "current presentation media is not available")
        config = settings()
        storage = AsyncMediaStorageClient(config.media_storage_url, config.media_storage_token)
        return StreamingResponse(
            storage.stream_object(media.storage_target_id, media.object_key, 0, media.size_bytes or 0),
            media_type=media.mime_type or "application/octet-stream",
            headers={
                "Content-Disposition": f"attachment; filename*=UTF-8''{quote(media.original_filename)}",
                "Content-Length": str(media.size_bytes),
                "X-Content-Type-Options": "nosniff",
            },
        )

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

    @app.get("/api/v1/events/{event_id}/media/intake", tags=["media"])
    def intake_queue(
        event_id: UUID, session: ReadSession,
        search: Annotated[str | None, Query(max_length=255)] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> dict[str, object]:
        unassigned = ~select(PresentationAsset.presentation_asset_id).where(
            PresentationAsset.media_object_id == MediaObject.media_object_id
        ).exists()
        conditions = [MediaObject.event_id == event_id, MediaObject.deleted_at.is_(None), unassigned]
        if search:
            term = f"%{search.strip()}%"
            conditions.append(or_(MediaObject.original_filename.ilike(term),
                                  MediaObject.source_relative_path.ilike(term)))
        total = session.scalar(select(func.count()).select_from(MediaObject).where(*conditions)) or 0
        media = session.scalars(select(MediaObject).where(*conditions).order_by(
            MediaObject.created_at.desc(), MediaObject.media_object_id.desc()
        ).offset(offset).limit(limit)).all()
        # Candidate discovery is bounded to tokens present in this page; no Event-wide ORM graph is loaded.
        tokens = sorted({token.rsplit(".", 1)[0] for item in media for token in
            item.original_filename.replace("_", " ").replace("-", " ").split()
            if len(token) >= 3}, key=len, reverse=True)
        candidates = _candidate_rows(session, event_id, "", min(200, max(25, limit * 4)),
                                     terms=tokens)
        match_candidates = [MatchCandidate(
            UUID(str(item["presentation_id"])), str(item["presentation_identifier"]),
            str(item["external_presentation_id"]) if item["external_presentation_id"] else None,
            title=str(item["title"]),
            presenter_family_name=(str(item["presenter_family_name"])
                                   if item["presenter_family_name"] else None),
            presenter_given_name=(str(item["presenter_given_name"])
                                  if item["presenter_given_name"] else None),
            session_title=str(item["session_title"]) if item["session_title"] else None,
            session_external_id=str(item["session_code"]) if item["session_code"] else None,
            room=str(item["room"]) if item["room"] else None,
            starts_at=item["starts_at"],
        ) for item in candidates]
        candidate_by_id = {str(item["presentation_id"]): item for item in candidates}
        items = []
        for item in media:
            match = match_presentation(item.source_relative_path or item.original_filename,
                                       match_candidates)
            suggestion = candidate_by_id.get(str(match.presentation_id)) if match.presentation_id else None
            items.append({
                "media_object_id": item.media_object_id, "filename": item.original_filename,
                "source_relative_path": item.source_relative_path, "size_bytes": item.size_bytes,
                "source": item.intake_origin, "received_at": item.created_at,
                "availability": item.availability, "suggestion": suggestion,
                "confidence": match.confidence, "match_state": match.state,
                "match_reason": match.reason,
            })
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    @app.get("/api/v1/events/{event_id}/presentation-lookup", tags=["media"])
    def presentation_lookup(
        event_id: UUID, session: ReadSession,
        search: Annotated[str, Query(min_length=1, max_length=255)],
        limit: Annotated[int, Query(ge=1, le=50)] = 25,
    ) -> dict[str, object]:
        return {"items": _candidate_rows(session, event_id, search, limit)}

    def confirm_one(session: Session, media_id: UUID, presentation_id: UUID) -> dict[str, object]:
        media = session.get(MediaObject, media_id, with_for_update=True)
        presentation = session.get(Presentation, presentation_id)
        if media is None or media.deleted_at is not None:
            raise HTTPException(404, "media intake item not found")
        if presentation is None or presentation.event_id != media.event_id:
            raise HTTPException(422, "presentation is not in the media Event")
        existing = session.scalar(select(PresentationAsset).where(
            PresentationAsset.media_object_id == media_id))
        if existing:
            version = session.get(PresentationVersion, existing.presentation_version_id)
            return {"media_object_id": media_id, "presentation_id": version.presentation_id,
                    "presentation_version_id": version.presentation_version_id,
                    "version_number": version.version_number, "duplicate": True}
        session.execute(select(Presentation.presentation_id).where(
            Presentation.presentation_id == presentation_id).with_for_update())
        number = (session.scalar(select(func.max(PresentationVersion.version_number)).where(
            PresentationVersion.presentation_id == presentation_id)) or 0) + 1
        version = PresentationVersion(presentation_version_id=new_uuid7(),
                                      presentation_id=presentation_id,
                                      version_number=number, sync_state=SyncState.PENDING)
        session.add(version)
        session.flush()
        program_session = session.get(ProgramSession, presentation.session_id) if presentation.session_id else None
        presenter = session.execute(
            select(PersonProjection.family_name, PersonProjection.given_name)
            .join(EventParticipation, EventParticipation.person_id == PersonProjection.person_id)
            .join(PresentationPresenter, PresentationPresenter.event_participation_id == EventParticipation.event_participation_id)
            .where(PresentationPresenter.presentation_id == presentation_id,
                   PresentationPresenter.active.is_(True)).limit(1)
        ).one_or_none()
        try:
            media.canonical_filename = canonical_presentation_filename(CanonicalPresentationMetadata(
                presentation_identifier=presentation.presentation_identifier,
                event_timezone=session.get(Event, presentation.event_id).timezone,
                starts_at=presentation.scheduled_at or (program_session.starts_at if program_session else None),
                room_label=program_session.location_name if program_session else None,
                presenter_family_name=presenter[0] if presenter else None,
                presenter_given_name=presenter[1] if presenter else None,
                title=presentation.title, version_number=number,
                original_filename=media.original_filename,
            ))
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        media.category = MediaCategory.PRESENTATION_VERSION
        session.add(PresentationAsset(presentation_version_id=version.presentation_version_id,
                                      media_object_id=media_id, kind=AssetKind.ORIGINAL))
        presentation.workflow_status = PresentationWorkflowStatus.RECEIVED
        presentation.sync_state = SyncState.PENDING
        SiteQueue(session).enqueue_outbox(
            event_type="site.presentation_version.created",
            aggregate_type="presentation_version", aggregate_id=version.presentation_version_id,
            site_id=media.site_id, protocol_version=UPM_SYNC_PROTOCOL_VERSION,
            source_sequence=next_sequence(session),
            idempotency_key=f"presentation-version:{version.presentation_version_id}",
            payload=OutboxPayload(source_system=SourceSystem.SITE, data={
                "presentation_version_id": str(version.presentation_version_id),
                "presentation_id": str(presentation_id), "version_number": number,
                "created_at": version.created_at.isoformat(),
            }),
        )
        if media.content_hash and media.size_bytes is not None:
            replication_id = new_uuid7()
            session.add(MediaReplicationSession(
                replication_session_id=replication_id, site_id=media.site_id,
                event_id=presentation.event_id, presentation_id=presentation_id,
                presentation_version_id=version.presentation_version_id,
                media_object_id=media_id, expected_size=media.size_bytes,
                sha256=media.content_hash, original_filename=media.original_filename,
                canonical_filename=media.canonical_filename, media_type=media.mime_type,
                state=MediaReplicationState.QUEUED,
            ))
            SiteQueue(session).enqueue_transfer(
                transfer_job_id=replication_id, site_id=media.site_id, media_object_id=media_id,
                transfer_type="presentation_media.central_push",
                payload={"schema_version": 1, "data": {"replication_session_id": str(replication_id)}},
                idempotency_key=f"media.replicate:{media_id}:{version.presentation_version_id}",
                required_capabilities=["transfer"],
            )
        session.add(AuditRecord(
            actor_id="site-operator", action="site.presentation_media.confirmed",
            target_type="media_object", target_id=media_id, site_id=media.site_id,
            event_id=media.event_id, after_context={
                "presentation_id": str(presentation_id),
                "presentation_version_id": str(version.presentation_version_id),
                "version_number": number,
            }))
        return {"media_object_id": media_id, "presentation_id": presentation_id,
                "presentation_version_id": version.presentation_version_id,
                "version_number": number, "duplicate": False}

    @app.post("/api/v1/media/{media_id}/confirmation", tags=["media"])
    def confirm_media(media_id: UUID, payload: SiteMediaConfirmation,
                      session: WriteSession) -> dict[str, object]:
        return confirm_one(session, media_id, payload.presentation_id)

    @app.post("/api/v1/media/confirmations", tags=["media"])
    def confirm_media_batch(payload: SiteMediaConfirmationBatch,
                            session: WriteSession) -> dict[str, object]:
        results = []
        for requested in payload.items:
            try:
                with session.begin_nested():
                    results.append({"status": "confirmed", **confirm_one(
                        session, requested["media_object_id"], requested["presentation_id"])})
            except HTTPException as error:
                results.append({"media_object_id": requested["media_object_id"],
                                "status": "failed", "message": str(error.detail)})
        return {"results": results}

    @app.get("/api/v1/events/{event_id}/media", tags=["media"])
    def event_media(
        event_id: UUID,
        session: ReadSession,
        search: Annotated[str | None, Query(max_length=255)] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
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
            ).offset(offset).limit(limit)
        ).all()
        rows = []
        for item in presentations:
            versions = session.scalars(
                select(PresentationVersion)
                .where(PresentationVersion.presentation_id == item.presentation_id)
                .order_by(PresentationVersion.version_number.desc())
                .limit(20)
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
                "expected": session.scalar(select(func.count()).select_from(Presentation).where(
                    Presentation.event_id == event_id, Presentation.active.is_(True))) or 0,
                "missing": sum(row["media_state"] == "missing" for row in rows),
                "ready": sum(row["media_state"] == "available" for row in rows),
                "sync_pending": sum(row["sync_state"] == SyncState.PENDING for row in rows),
            },
            "presentations": rows,
        }

    @app.get("/api/v1/events/{event_id}/presentations/operations", tags=["media"])
    def presentation_operations(
        event_id: UUID, session: ReadSession,
        search: Annotated[str | None, Query(max_length=255)] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> dict[str, object]:
        conditions = [Presentation.event_id == event_id, Presentation.active.is_(True)]
        if search:
            term = f"%{search.strip()}%"
            conditions.append(or_(Presentation.title.ilike(term),
                                  Presentation.presentation_identifier.ilike(term),
                                  Presentation.external_presentation_id.ilike(term)))
        total = session.scalar(select(func.count()).select_from(Presentation).where(*conditions)) or 0
        presentations = session.scalars(select(Presentation).where(*conditions).order_by(
            Presentation.scheduled_at.asc().nulls_last(), Presentation.title,
            Presentation.presentation_id).offset(offset).limit(limit)).all()
        ids = [item.presentation_id for item in presentations]
        sessions = {item.session_id: item for item in session.scalars(
            select(ProgramSession).where(ProgramSession.session_id.in_(
                [item.session_id for item in presentations if item.session_id])))}
        presenter_rows = session.execute(
            select(PresentationPresenter.presentation_id, EventParticipation.display_name,
                   PersonProjection.given_name, PersonProjection.family_name)
            .join(EventParticipation, EventParticipation.event_participation_id
                  == PresentationPresenter.event_participation_id)
            .join(PersonProjection, PersonProjection.person_id == EventParticipation.person_id)
            .where(PresentationPresenter.presentation_id.in_(ids),
                   PresentationPresenter.active.is_(True))
            .order_by(PresentationPresenter.primary_presenter.desc(),
                      PresentationPresenter.presenter_order)).all() if ids else []
        presenters: dict[UUID, list[str]] = {}
        for presentation_id, display, given, family in presenter_rows:
            presenters.setdefault(presentation_id, []).append(
                display or " ".join(filter(None, [given, family])))
        versions = session.scalars(select(PresentationVersion).where(
            PresentationVersion.presentation_id.in_(ids)).distinct(
                PresentationVersion.presentation_id).order_by(
                    PresentationVersion.presentation_id,
                    PresentationVersion.version_number.desc())).all() if ids else []
        version_by_presentation = {item.presentation_id: item for item in versions}
        version_ids = [item.presentation_version_id for item in versions]
        assets = session.scalars(select(PresentationAsset).where(
            PresentationAsset.presentation_version_id.in_(version_ids),
            PresentationAsset.kind == AssetKind.ORIGINAL)).all() if version_ids else []
        asset_by_version = {item.presentation_version_id: item for item in assets}
        media_ids = [item.media_object_id for item in assets]
        media = {item.media_object_id: item for item in session.scalars(select(MediaObject).where(
            MediaObject.media_object_id.in_(media_ids)))} if media_ids else {}
        transfers = session.scalars(select(TransferJob).where(
            TransferJob.media_object_id.in_(media_ids)).distinct(TransferJob.media_object_id)
            .order_by(TransferJob.media_object_id, TransferJob.created_at.desc())).all() if media_ids else []
        transfer_by_media = {item.media_object_id: item for item in transfers}
        room_rows = session.execute(select(RoomAssignment.session_id, Room.label).join(
            Room, Room.room_id == RoomAssignment.room_id).where(
                RoomAssignment.session_id.in_(list(sessions)), RoomAssignment.active.is_(True))).all() if sessions else []
        rooms = dict(room_rows)
        items = []
        for item in presentations:
            program_session = sessions.get(item.session_id)
            associated_sessions = session.scalars(
                select(ProgramSession)
                .join(PresentationSession, PresentationSession.session_id == ProgramSession.session_id)
                .where(
                    PresentationSession.presentation_id == item.presentation_id,
                    PresentationSession.active.is_(True),
                )
                .order_by(PresentationSession.sort_order, ProgramSession.starts_at)
            ).all()
            if program_session and all(
                value.session_id != program_session.session_id for value in associated_sessions
            ):
                associated_sessions.insert(0, program_session)
            version = version_by_presentation.get(item.presentation_id)
            asset = asset_by_version.get(version.presentation_version_id) if version else None
            current_media = media.get(asset.media_object_id) if asset else None
            confirmation = session.scalar(
                select(AuditRecord)
                .where(
                    AuditRecord.action == "site.presentation_media.confirmed",
                    AuditRecord.target_id == (current_media.media_object_id if current_media else None),
                )
                .order_by(AuditRecord.occurred_at.desc())
                .limit(1)
            ) if current_media else None
            version_history = []
            for historical_version in session.scalars(
                select(PresentationVersion)
                .where(PresentationVersion.presentation_id == item.presentation_id)
                .order_by(PresentationVersion.version_number.desc())
            ):
                historical_media = session.scalar(
                    select(MediaObject)
                    .join(PresentationAsset, PresentationAsset.media_object_id == MediaObject.media_object_id)
                    .where(
                        PresentationAsset.presentation_version_id == historical_version.presentation_version_id,
                        PresentationAsset.kind == AssetKind.ORIGINAL,
                    )
                )
                version_history.append({
                    "presentation_version_id": historical_version.presentation_version_id,
                    "version_number": historical_version.version_number,
                    "filename": historical_media.original_filename if historical_media else None,
                    "size_bytes": historical_media.size_bytes if historical_media else None,
                    "received_at": historical_media.created_at if historical_media else None,
                    "availability": historical_media.availability if historical_media else "missing",
                })
            transfer = transfer_by_media.get(current_media.media_object_id) if current_media else None
            delivery = "not_delivered"
            if transfer and transfer.status is JobStatus.SUCCEEDED:
                delivery = "current"
            elif version and version.version_number > 1:
                delivery = "outdated"
            readiness = "missing" if current_media is None else (
                "ready" if current_media.availability is MediaAvailability.AVAILABLE else
                str(current_media.availability))
            items.append({
                "presentation_id": item.presentation_id,
                "presentation_identifier": item.presentation_identifier,
                "title": item.title, "presenters": presenters.get(item.presentation_id, []),
                "session": program_session.title if program_session else None,
                "session_code": program_session.session_code if program_session else None,
                "starts_at": item.scheduled_at or (program_session.starts_at if program_session else None),
                "room": rooms.get(item.session_id) or (program_session.location_name if program_session else None),
                "current_version": version.version_number if version else None,
                "readiness": readiness, "delivery_state": delivery,
                "source": current_media.category if current_media else None,
                "received_at": current_media.created_at if current_media else None,
                "confirmed_at": confirmation.occurred_at if confirmation else None,
                "confirmed_by": confirmation.actor_id if confirmation else None,
                "updated_at": max(item.updated_at, current_media.updated_at) if current_media else item.updated_at,
                "filename": current_media.original_filename if current_media else None,
                "size_bytes": current_media.size_bytes if current_media else None,
                "mime_type": current_media.mime_type if current_media else None,
                "sha256": current_media.content_hash if current_media else None,
                "download_url": (
                    f"/api/v1/presentation-versions/{version.presentation_version_id}/download"
                    if current_media and current_media.availability is MediaAvailability.AVAILABLE
                    else None
                ),
                "sessions": [
                    {
                        "session_id": value.session_id,
                        "session_code": value.session_code,
                        "title": value.title,
                        "starts_at": value.starts_at,
                        "room": rooms.get(value.session_id) or value.location_name,
                    }
                    for value in associated_sessions
                ],
                "version_history": version_history,
            })
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    @app.get("/api/v1/events/{event_id}/media/match", tags=["media"])
    def match_upload(event_id: UUID, filename: Annotated[str, Query()], session: ReadSession):
        candidates = [
            MatchCandidate(
                presentation_id=item["presentation_id"],
                presentation_identifier=str(item["presentation_identifier"]),
                external_presentation_id=item["external_presentation_id"],
                title=str(item["title"]),
            )
            for row in _candidate_rows(session, event_id, filename, 50)
            for item in [row]
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
