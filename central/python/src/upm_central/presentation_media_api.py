"""Authenticated Central presentation-media staging and review APIs."""

import asyncio
import logging
from collections.abc import Callable, Iterator
from contextlib import asynccontextmanager
from typing import Annotated
from urllib.parse import unquote
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.exc import ProgrammingError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from upm_central.config import CentralDatabaseSettings
from upm_central.imports import (
    repair_event_presentation_materialization,
    unmaterialized_imported_sessions,
)
from upm_central.media_replication import (
    authorize_replication_context,
    finalize_replication_reference,
)
from upm_central.persistence.models import (
    Event,
    EventDeployment,
    EventParticipation,
    MediaReplicationReceiveSession,
    Person,
    Presentation,
    PresentationMediaImport,
    PresentationPresenter,
    PresentationVersion,
    TransferJob,
    utc_now,
)
from upm_central.persistence.models import (
    Session as ProgramSession,
)
from upm_central.presentation_media import (
    CentralMediaStagingService,
    MediaStagingError,
)
from upm_central.program import touch_event_program
from upm_central.sync import authenticate_site
from upm_shared.enums import (
    JobStatus,
    MediaImportState,
    MediaMatchState,
    MediaReplicationState,
    MediaTransferState,
)
from upm_shared.media_storage_client import AsyncMediaStorageClient, MediaStorageUnavailable

logger = logging.getLogger(__name__)


class ReplicationCreate(BaseModel):
    replication_session_id: UUID
    event_id: UUID
    presentation_id: UUID
    presentation_version_id: UUID
    media_object_id: UUID
    presentation_identifier: str = Field(min_length=1, max_length=128)
    original_filename: str = Field(min_length=1, max_length=1024)
    canonical_filename: str | None = Field(default=None, max_length=1024)
    expected_size: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    media_type: str | None = Field(default=None, max_length=255)


class MatchConfirmation(BaseModel):
    media_import_id: UUID
    presentation_id: UUID


class MatchConfirmationBatch(BaseModel):
    items: list[MatchConfirmation] = Field(min_length=1, max_length=1000)


def _candidate_views(
    session: Session, event_id: UUID, search: str | None = None
) -> list[dict[str, object]]:
    presentations = session.scalars(
        select(Presentation)
        .where(Presentation.event_id == event_id)
        .order_by(Presentation.presentation_identifier)
    ).all()
    result = []
    needle = (search or "").strip().casefold()
    for item in presentations:
        program_session = session.get(ProgramSession, item.session_id) if item.session_id else None
        presenters = session.execute(
            select(Person.family_name, Person.given_name, Person.display_name)
            .join(EventParticipation, EventParticipation.person_id == Person.person_id)
            .join(
                PresentationPresenter,
                PresentationPresenter.event_participation_id
                == EventParticipation.event_participation_id,
            )
            .where(PresentationPresenter.presentation_id == item.presentation_id)
            .order_by(
                PresentationPresenter.primary_presenter.desc(),
                PresentationPresenter.presenter_order,
            )
        ).all()
        view = {
            "presentation_id": item.presentation_id,
            "presentation_identifier": item.presentation_identifier,
            "external_presentation_id": item.external_presentation_id,
            "title": item.title,
            "session_title": program_session.title if program_session else None,
            "session_external_id": program_session.session_code if program_session else None,
            "room": program_session.location_name if program_session else None,
            "starts_at": item.scheduled_at
            or (program_session.starts_at if program_session else None),
            "presenters": [
                {
                    "family_name": family,
                    "given_name": given,
                    "display_name": display_name or " ".join(filter(None, [given, family])),
                }
                for family, given, display_name in presenters
            ],
        }
        haystack = " ".join(
            str(value)
            for value in [
                item.presentation_identifier,
                item.external_presentation_id,
                item.title,
                view["session_title"],
                view["session_external_id"],
                view["room"],
                *(name for row in view["presenters"] for name in row.values()),
            ]
            if value
        ).casefold()
        if not needle or needle in haystack:
            result.append(view)
    return result[:500]


def _view(item: PresentationMediaImport) -> dict[str, object]:
    return {
        "media_import_id": item.media_import_id,
        "event_id": item.event_id,
        "destination_site_id": item.destination_site_id,
        "presentation_id": item.presentation_id,
        "presentation_version_id": item.presentation_version_id,
        "presentation_identifier": item.presentation_identifier,
        "external_presentation_id": item.external_presentation_id,
        "original_filename": item.original_filename,
        "source_relative_path": item.source_relative_path,
        "canonical_filename": item.canonical_filename,
        "size_bytes": item.size_bytes,
        "mime_type": item.mime_type,
        "sha256": item.sha256,
        "match_state": item.match_state,
        "match_reason": item.match_reason,
        "match_candidates": item.match_candidates,
        "confirmed_by": item.confirmed_by,
        "confirmed_at": item.confirmed_at,
        "import_state": item.import_state,
        "sync_state": item.sync_state,
        "transfer_job_id": item.transfer_job_id,
        "site_media_object_id": item.site_media_object_id,
        "committed_storage_target_id": item.committed_storage_root_id,
        "committed_storage_key": item.committed_storage_key,
        "retry_count": item.retry_count,
        "error_code": item.error_code,
        "error_detail": item.error_detail,
        "origin": item.origin,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }


def register_presentation_media_routes(
    app: FastAPI,
    db: Callable[[], Iterator[Session]],
    require_admin: Callable[..., None],
    factory: Callable[[], sessionmaker[Session]],
    settings: Callable[[], CentralDatabaseSettings],
) -> None:
    admin = [Depends(require_admin)]
    DbSession = Annotated[Session, Depends(db)]
    upload_admission_lock = asyncio.Lock()
    active_staging_uploads = 0

    @asynccontextmanager
    async def staging_admission():
        nonlocal active_staging_uploads
        async with upload_admission_lock:
            if active_staging_uploads >= settings().staging_upload_concurrency:
                raise HTTPException(
                    status.HTTP_429_TOO_MANY_REQUESTS,
                    detail={
                        "code": "staging_capacity",
                        "message": "Staging capacity is temporarily full.",
                    },
                    headers={"Retry-After": str(settings().staging_retry_after_seconds)},
                )
            active_staging_uploads += 1
        try:
            yield
        finally:
            async with upload_admission_lock:
                active_staging_uploads -= 1

    def media_storage() -> AsyncMediaStorageClient:
        configured = settings()
        return AsyncMediaStorageClient(configured.media_storage_url, configured.media_storage_token)

    def staging_service() -> CentralMediaStagingService:
        return CentralMediaStagingService(factory(), media_storage(), settings().max_upload_bytes)

    @app.post(
        "/api/v1/admin/events/{event_id}/media-imports",
        status_code=status.HTTP_201_CREATED,
        dependencies=admin,
        tags=["media"],
    )
    async def upload(
        event_id: UUID,
        request: Request,
        original_filename: Annotated[str, Header(alias="X-UPM-Original-Filename")],
        source_relative_path: Annotated[
            str | None, Header(alias="X-UPM-Source-Relative-Path")
        ] = None,
        destination_site_id: Annotated[UUID | None, Query()] = None,
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
        content_type: Annotated[str | None, Header(alias="Content-Type")] = None,
    ) -> dict[str, object]:
        service = staging_service()
        try:
            async with staging_admission():
                item = await service.stage(
                    event_id=event_id,
                    destination_site_id=destination_site_id,
                    original_filename=unquote(original_filename),
                    source_relative_path=unquote(source_relative_path)
                    if source_relative_path
                    else None,
                    content_type=content_type,
                    idempotency_key=idempotency_key,
                    chunks=request.stream(),
                    actor=getattr(request.state, "admin_actor", "central-admin"),
                )
        except MediaStagingError as error:
            code = status.HTTP_413_REQUEST_ENTITY_TOO_LARGE if error.code == "too_large" else 422
            if error.code == "event_not_found":
                code = 404
            if error.code == "storage_service_unavailable":
                code = status.HTTP_503_SERVICE_UNAVAILABLE
            if error.code == "storage_write_error":
                code = status.HTTP_507_INSUFFICIENT_STORAGE
            raise HTTPException(code, detail={"code": error.code, "message": str(error)}) from error
        except OSError as error:
            logger.exception(
                "central_media_staging_storage_error",
                extra={"event_id": str(event_id), "exception_type": type(error).__name__},
            )
            reason = error.strerror or "configured path is not mounted or writable"
            raise HTTPException(
                status.HTTP_507_INSUFFICIENT_STORAGE,
                detail={
                    "code": "staging_storage_error",
                    "message": f"Staging storage is unavailable: {reason}.",
                },
            ) from error
        except ProgrammingError as error:
            logger.exception(
                "central_media_staging_schema_error",
                extra={"event_id": str(event_id), "exception_type": type(error).__name__},
            )
            raise HTTPException(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={
                    "code": "database_schema_error",
                    "message": "Media upload failed because the application schema is invalid.",
                },
            ) from error
        except SQLAlchemyError as error:
            logger.exception(
                "central_media_staging_database_error",
                extra={"event_id": str(event_id), "exception_type": type(error).__name__},
            )
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "database_error",
                    "message": "Media metadata service is temporarily unavailable.",
                },
            ) from error
        except Exception as error:
            logger.exception(
                "central_media_staging_internal_error",
                extra={"event_id": str(event_id), "exception_type": type(error).__name__},
            )
            raise HTTPException(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={
                    "code": "server_internal_error",
                    "message": "Unexpected media staging failure.",
                },
            ) from error
        return _view(item)

    @app.get(
        "/api/v1/admin/events/{event_id}/media-imports",
        dependencies=admin,
        tags=["media"],
    )
    def list_imports(
        event_id: UUID,
        session: DbSession,
        search: Annotated[str | None, Query(max_length=255)] = None,
        import_state: Annotated[MediaImportState | None, Query()] = None,
    ) -> dict[str, object]:
        query = select(PresentationMediaImport).where(PresentationMediaImport.event_id == event_id)
        if search:
            term = f"%{search.strip()}%"
            query = query.where(
                or_(
                    PresentationMediaImport.original_filename.ilike(term),
                    PresentationMediaImport.canonical_filename.ilike(term),
                    PresentationMediaImport.presentation_identifier.ilike(term),
                    PresentationMediaImport.external_presentation_id.ilike(term),
                )
            )
        if import_state:
            query = query.where(PresentationMediaImport.import_state == import_state)
        imports = session.scalars(query.order_by(PresentationMediaImport.created_at.desc())).all()
        presentations = session.scalars(
            select(Presentation).where(Presentation.event_id == event_id)
        ).all()
        assigned = {item.presentation_id for item in imports if item.presentation_id}
        return {
            "summary": {
                "expected": len(presentations),
                "with_media": len(assigned),
                "missing": sum(item.presentation_id not in assigned for item in presentations),
                "needs_review": sum(
                    item.import_state == MediaImportState.NEEDS_REVIEW for item in imports
                ),
                "unmatched": sum(item.presentation_id is None for item in imports),
                "transferring": sum(
                    item.import_state
                    in {MediaImportState.TRANSFER_QUEUED, MediaImportState.TRANSFERRING}
                    for item in imports
                ),
                "failed": sum(item.import_state == MediaImportState.FAILED for item in imports),
            },
            "imports": [_view(item) for item in imports],
        }

    @app.get("/api/v1/admin/media-imports/{media_import_id}", dependencies=admin, tags=["media"])
    def detail(media_import_id: UUID, session: DbSession) -> dict[str, object]:
        item = session.get(PresentationMediaImport, media_import_id)
        if item is None:
            raise HTTPException(404, "media import not found")
        result = _view(item)
        result["version_history"] = (
            [
                {
                    "presentation_version_id": version.presentation_version_id,
                    "version_number": version.version_number,
                }
                for version in session.scalars(
                    select(PresentationVersion)
                    .where(PresentationVersion.presentation_id == item.presentation_id)
                    .order_by(PresentationVersion.version_number.desc())
                )
            ]
            if item.presentation_id
            else []
        )
        return result

    @app.get(
        "/api/v1/admin/events/{event_id}/presentation-match-candidates",
        dependencies=admin,
        tags=["media"],
    )
    def candidates(
        event_id: UUID,
        session: DbSession,
        search: Annotated[str | None, Query(max_length=255)] = None,
    ) -> dict[str, object]:
        return {"candidates": _candidate_views(session, event_id, search)}

    @app.post(
        "/api/v1/admin/media-imports/{media_import_id}/match", dependencies=admin, tags=["media"]
    )
    def refresh_match(media_import_id: UUID, session: DbSession) -> dict[str, object]:
        item = session.get(PresentationMediaImport, media_import_id, with_for_update=True)
        if item is None:
            raise HTTPException(404, "media import not found")
        if item.match_state is MediaMatchState.CONFIRMED or item.presentation_id:
            return _view(item)
        event = session.get(Event, item.event_id)
        missing = unmaterialized_imported_sessions(session, item.event_id)
        if missing:
            repair_event_presentation_materialization(
                session,
                event,
                actor="presentation-media-rematch",
            )
            touch_event_program(session, event)
        staging_service()._automatic_match_and_assign(session, item)
        return _view(item)

    @app.post(
        "/api/v1/admin/events/{event_id}/presentation-materialization",
        dependencies=admin,
        tags=["media"],
    )
    def repair_materialization(
        event_id: UUID, request: Request, session: DbSession
    ) -> dict[str, object]:
        event = session.get(Event, event_id)
        if event is None:
            raise HTTPException(404, "event not found")
        missing = unmaterialized_imported_sessions(session, event_id)
        repaired = repair_event_presentation_materialization(
            session,
            event,
            actor=getattr(request.state, "admin_actor", "central-admin"),
        )
        if missing:
            touch_event_program(session, event)
        return {
            "event_id": event_id,
            "repaired_count": len(missing),
            "presentation_ids": [item.presentation_id for item in repaired],
        }

    @app.put(
        "/api/v1/admin/media-imports/{media_import_id}/assignment/{presentation_id}",
        dependencies=admin,
        tags=["media"],
    )
    def assign(
        media_import_id: UUID, presentation_id: UUID, request: Request, session: DbSession
    ) -> dict[str, object]:
        item = session.get(PresentationMediaImport, media_import_id, with_for_update=True)
        if item is None:
            raise HTTPException(404, "media import not found")
        if item.import_state in {MediaImportState.CANCELLED, MediaImportState.SITE_READY}:
            raise HTTPException(409, "media import can no longer be reassigned")
        if item.match_state is MediaMatchState.CONFIRMED:
            if item.presentation_id == presentation_id:
                return _view(item)
            raise HTTPException(409, "confirmed media cannot be reassigned")
        actor = getattr(request.state, "admin_actor", "central-admin")
        staging_service().queue_promotion(session, item, presentation_id, actor=actor)
        return {**_view(item), "confirmation_state": "queued"}

    @app.post("/api/v1/admin/media-imports/confirmations", dependencies=admin, tags=["media"])
    def confirm_batch(
        body: MatchConfirmationBatch, request: Request, session: DbSession
    ) -> dict[str, object]:
        actor = getattr(request.state, "admin_actor", "central-admin")
        results = []
        for requested in body.items:
            try:
                with session.begin_nested():
                    item = session.get(
                        PresentationMediaImport, requested.media_import_id, with_for_update=True
                    )
                    if item is None:
                        raise MediaStagingError("media import not found", "not_found")
                    if item.match_state is MediaMatchState.CONFIRMED:
                        if item.presentation_id != requested.presentation_id:
                            raise MediaStagingError(
                                "media import was confirmed to another presentation",
                                "already_confirmed",
                            )
                    else:
                        staging_service().queue_promotion(
                            session,
                            item,
                            requested.presentation_id,
                            actor=actor,
                        )
                    results.append(
                        {
                            "media_import_id": requested.media_import_id,
                            "status": "confirmed"
                            if item.match_state is MediaMatchState.CONFIRMED
                            else "queued",
                            "presentation_version_id": item.presentation_version_id,
                        }
                    )
            except MediaStagingError as error:
                results.append(
                    {
                        "media_import_id": requested.media_import_id,
                        "status": "failed",
                        "code": error.code,
                        "message": str(error),
                    }
                )
        return {"results": results}

    @app.post(
        "/api/v1/admin/media-imports/{media_import_id}/retry",
        dependencies=admin,
        tags=["media"],
    )
    def retry(media_import_id: UUID, session: DbSession) -> dict[str, object]:
        item = session.get(PresentationMediaImport, media_import_id, with_for_update=True)
        if item is None:
            raise HTTPException(404, "media import not found")
        if item.transfer_job_id is None:
            raise HTTPException(409, "media import has no transfer")
        transfer = session.get(TransferJob, item.transfer_job_id)
        if transfer.status not in {JobStatus.FAILED, JobStatus.EXHAUSTED, JobStatus.RETRY_WAIT}:
            raise HTTPException(409, "transfer is not retryable")
        transfer.status = JobStatus.RETRY_WAIT
        transfer.claimed_by_worker_id = None
        transfer.lease_expires_at = None
        transfer.error_code = None
        transfer.last_error = None
        item.import_state = MediaImportState.RETRY_WAIT
        item.retry_count += 1
        return _view(item)

    @app.post(
        "/api/v1/admin/media-imports/{media_import_id}/cancel",
        dependencies=admin,
        tags=["media"],
    )
    def cancel(media_import_id: UUID, session: DbSession) -> dict[str, object]:
        item = session.get(PresentationMediaImport, media_import_id, with_for_update=True)
        if item is None:
            raise HTTPException(404, "media import not found")
        if item.presentation_id or item.import_state == MediaImportState.SITE_READY:
            raise HTTPException(409, "assigned or delivered media cannot be cancelled")
        item.import_state = MediaImportState.CANCELLED
        return _view(item)

    def machine_transfer(
        transfer_session_id: UUID,
        session: Session,
        authorization: str | None,
        site_id: UUID | None,
    ) -> tuple[TransferJob, PresentationMediaImport]:
        token = authorization[7:] if authorization and authorization.startswith("Bearer ") else None
        if site_id is None:
            raise HTTPException(401, "missing site identity")
        authenticate_site(session, site_id, token)
        transfer = session.get(TransferJob, transfer_session_id)
        item = session.scalar(
            select(PresentationMediaImport).where(
                PresentationMediaImport.transfer_job_id == transfer_session_id
            )
        )
        if transfer is None or item is None or transfer.owning_site_id != site_id:
            raise HTTPException(404, "transfer not found")
        deployment = session.scalar(
            select(EventDeployment).where(
                EventDeployment.site_id == site_id,
                EventDeployment.event_id == item.event_id,
                EventDeployment.status != "revoked",
            )
        )
        if deployment is None:
            raise HTTPException(404, "transfer not found")
        return transfer, item

    @app.get("/api/v1/media-transfers/{transfer_session_id}", tags=["media-transfer"])
    def transfer_status(
        transfer_session_id: UUID,
        session: DbSession,
        authorization: Annotated[str | None, Header()] = None,
        x_upm_site_id: Annotated[UUID | None, Header()] = None,
    ) -> dict[str, object]:
        transfer, item = machine_transfer(
            transfer_session_id, session, authorization, x_upm_site_id
        )
        return {
            "transfer_session_id": transfer.transfer_job_id,
            "event_id": item.event_id,
            "presentation_id": item.presentation_id,
            "presentation_version_id": item.presentation_version_id,
            "presentation_identifier": item.presentation_identifier,
            "original_filename": item.original_filename,
            "canonical_filename": item.canonical_filename,
            "expected_size": item.size_bytes,
            "sha256": item.sha256,
            "media_type": item.mime_type,
            "state": item.import_state,
        }

    @app.get("/api/v1/media-transfers/{transfer_session_id}/content", tags=["media-transfer"])
    async def transfer_content(
        transfer_session_id: UUID,
        session: DbSession,
        offset: Annotated[int, Query(ge=0)] = 0,
        authorization: Annotated[str | None, Header()] = None,
        x_upm_site_id: Annotated[UUID | None, Header()] = None,
    ) -> StreamingResponse:
        _, item = machine_transfer(transfer_session_id, session, authorization, x_upm_site_id)
        expected_size = item.size_bytes or 0
        if offset > expected_size:
            raise HTTPException(416, "offset exceeds expected size")
        if item.committed_storage_root_id is None or item.committed_storage_key is None:
            raise HTTPException(409, "committed transfer source is unavailable")
        count = min(settings().transfer_block_bytes, expected_size - offset)

        async def content():
            try:
                async for chunk in media_storage().stream_object(
                    item.committed_storage_root_id, item.committed_storage_key, offset, count
                ):
                    yield chunk
            except MediaStorageUnavailable as error:
                logger.exception("central_media_transfer_storage_unavailable")
                raise RuntimeError("transfer storage unavailable") from error

        headers = {
            "X-UPM-Transfer-Offset": str(offset),
            "X-UPM-Transfer-Next-Offset": str(offset + count),
            "X-UPM-Transfer-Size": str(expected_size),
            "X-UPM-Transfer-SHA256": item.sha256 or "",
        }
        return StreamingResponse(content(), media_type="application/octet-stream", headers=headers)

    def replication_for_site(
        replication_session_id: UUID,
        session: Session,
        authorization: str | None,
        site_id: UUID | None,
    ) -> MediaReplicationReceiveSession:
        token = authorization[7:] if authorization and authorization.startswith("Bearer ") else None
        if site_id is None:
            raise HTTPException(401, "missing site identity")
        authenticate_site(session, site_id, token)
        receiver = session.get(
            MediaReplicationReceiveSession, replication_session_id, with_for_update=True
        )
        if receiver is None or receiver.origin_site_id != site_id:
            raise HTTPException(404, "replication session not found")
        try:
            authorize_replication_context(
                session,
                site_id=site_id,
                event_id=receiver.event_id,
                presentation_id=receiver.presentation_id,
                presentation_version_id=receiver.presentation_version_id,
            )
        except LookupError as exc:
            raise HTTPException(404, "replication session not found") from exc
        return receiver

    def replication_view(receiver: MediaReplicationReceiveSession) -> dict[str, object]:
        return {
            "replication_session_id": receiver.replication_session_id,
            "confirmed_offset": receiver.confirmed_offset,
            "expected_size": receiver.expected_size,
            "sha256": receiver.sha256,
            "state": receiver.state,
            "replication_state": receiver.replication_state,
            "central_media_object_id": receiver.finalized_media_object_id,
            "presentation_version_id": receiver.presentation_version_id,
            "error_detail": receiver.error_detail,
        }

    @app.post("/api/v1/media-replications", tags=["media-transfer"])
    async def create_replication(
        payload: ReplicationCreate,
        session: DbSession,
        authorization: Annotated[str | None, Header()] = None,
        x_upm_site_id: Annotated[UUID | None, Header()] = None,
    ) -> dict[str, object]:
        token = authorization[7:] if authorization and authorization.startswith("Bearer ") else None
        if x_upm_site_id is None:
            raise HTTPException(401, "missing site identity")
        authenticate_site(session, x_upm_site_id, token)
        try:
            presentation = authorize_replication_context(
                session,
                site_id=x_upm_site_id,
                event_id=payload.event_id,
                presentation_id=payload.presentation_id,
                presentation_version_id=payload.presentation_version_id,
            )
        except LookupError as exc:
            raise HTTPException(409, "presentation metadata is not synchronized") from exc
        existing = session.get(
            MediaReplicationReceiveSession,
            payload.replication_session_id,
            with_for_update=True,
        )
        if existing is not None:
            immutable = (
                existing.origin_site_id,
                existing.event_id,
                existing.presentation_version_id,
                existing.source_media_object_id,
                existing.expected_size,
                existing.sha256,
            )
            requested = (
                x_upm_site_id,
                payload.event_id,
                payload.presentation_version_id,
                payload.media_object_id,
                payload.expected_size,
                payload.sha256,
            )
            if immutable != requested:
                raise HTTPException(409, "replication session metadata conflict")
            if existing.storage_target_id is None and existing.finalized_media_object_id is None:
                allocation = await media_storage().allocate_staging()
                existing.storage_target_id = UUID(allocation["storage_target_id"])
                existing.partial_key = allocation["storage_key"]
            return replication_view(existing)
        if presentation.presentation_identifier != payload.presentation_identifier:
            raise HTTPException(409, "presentation identifier mismatch")
        if payload.expected_size > settings().max_upload_bytes:
            raise HTTPException(413, "replication exceeds configured maximum")
        try:
            allocation = await media_storage().allocate_staging()
        except MediaStorageUnavailable as error:
            raise HTTPException(503, str(error)) from error
        receiver = MediaReplicationReceiveSession(
            replication_session_id=payload.replication_session_id,
            origin_site_id=x_upm_site_id,
            event_id=payload.event_id,
            presentation_id=payload.presentation_id,
            presentation_version_id=payload.presentation_version_id,
            source_media_object_id=payload.media_object_id,
            presentation_identifier=payload.presentation_identifier,
            original_filename=payload.original_filename,
            canonical_filename=payload.canonical_filename,
            expected_size=payload.expected_size,
            sha256=payload.sha256,
            media_type=payload.media_type,
            partial_key=allocation["storage_key"],
            storage_target_id=UUID(allocation["storage_target_id"]),
            state=MediaTransferState.AVAILABLE,
            replication_state=MediaReplicationState.QUEUED,
        )
        session.add(receiver)
        session.flush()
        return replication_view(receiver)

    @app.get("/api/v1/media-replications/{replication_session_id}", tags=["media-transfer"])
    def replication_status(
        replication_session_id: UUID,
        session: DbSession,
        authorization: Annotated[str | None, Header()] = None,
        x_upm_site_id: Annotated[UUID | None, Header()] = None,
    ) -> dict[str, object]:
        return replication_view(
            replication_for_site(replication_session_id, session, authorization, x_upm_site_id)
        )

    @app.put("/api/v1/media-replications/{replication_session_id}/content", tags=["media-transfer"])
    async def replication_content(
        replication_session_id: UUID,
        request: Request,
        session: DbSession,
        offset: Annotated[int, Query(ge=0)],
        authorization: Annotated[str | None, Header()] = None,
        x_upm_site_id: Annotated[UUID | None, Header()] = None,
    ) -> dict[str, object]:
        receiver = replication_for_site(
            replication_session_id, session, authorization, x_upm_site_id
        )
        if receiver.finalized_media_object_id is not None:
            return replication_view(receiver)
        if offset != receiver.confirmed_offset:
            raise HTTPException(409, detail={"confirmed_offset": receiver.confirmed_offset})
        written = 0

        async def bounded():
            nonlocal written
            async for chunk in request.stream():
                written += len(chunk)
                if written > settings().transfer_block_bytes:
                    raise HTTPException(413, "block exceeds configured maximum")
                if offset + written > receiver.expected_size:
                    raise HTTPException(413, "block exceeds expected size")
                yield chunk

        if receiver.storage_target_id is None:
            raise HTTPException(409, "replication storage reference is missing")
        result = await media_storage().append_staging(
            receiver.storage_target_id, receiver.partial_key, offset, bounded()
        )
        receiver.confirmed_offset = result["confirmed_offset"]
        receiver.state = MediaTransferState.TRANSFERRING
        receiver.replication_state = MediaReplicationState.SYNCING
        receiver.last_progress_at = utc_now()
        return replication_view(receiver)

    @app.post(
        "/api/v1/media-replications/{replication_session_id}/finalize",
        tags=["media-transfer"],
    )
    async def finalize_replication_endpoint(
        replication_session_id: UUID,
        session: DbSession,
        authorization: Annotated[str | None, Header()] = None,
        x_upm_site_id: Annotated[UUID | None, Header()] = None,
    ) -> dict[str, object]:
        receiver = replication_for_site(
            replication_session_id, session, authorization, x_upm_site_id
        )
        if receiver.storage_target_id is None:
            raise HTTPException(409, "replication storage reference is missing")
        if receiver.confirmed_offset != receiver.expected_size:
            raise HTTPException(409, "replication byte range is incomplete")
        committed = await media_storage().commit(
            receiver.storage_target_id, receiver.partial_key, receiver.sha256
        )
        finalize_replication_reference(session, receiver, committed)
        return replication_view(receiver)
