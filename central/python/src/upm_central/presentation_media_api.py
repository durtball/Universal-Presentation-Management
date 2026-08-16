"""Authenticated Central presentation-media staging and review APIs."""

import os
import shutil
from collections.abc import Callable, Iterator
from typing import Annotated
from urllib.parse import unquote
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, sessionmaker

from upm_central.config import CentralDatabaseSettings
from upm_central.media_replication import (
    authorize_replication_context,
    finalize_replication,
    recover_partial,
)
from upm_central.persistence.models import (
    AuditRecord,
    EventDeployment,
    MediaReplicationReceiveSession,
    Presentation,
    PresentationMediaImport,
    PresentationVersion,
    TransferJob,
    utc_now,
)
from upm_central.presentation_media import (
    CentralMediaStagingService,
    MediaStagingError,
    _safe_staging_path,
)
from upm_central.sync import authenticate_site
from upm_shared.enums import (
    JobStatus,
    MediaImportState,
    MediaReplicationState,
    MediaTransferState,
)


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
        "import_state": item.import_state,
        "sync_state": item.sync_state,
        "transfer_job_id": item.transfer_job_id,
        "site_media_object_id": item.site_media_object_id,
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

    @app.post(
        "/api/v1/admin/events/{event_id}/media-imports",
        status_code=201,
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
        service = CentralMediaStagingService(
            factory(), settings().media_staging_path, settings().max_upload_bytes
        )
        try:
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
            raise HTTPException(code, detail={"code": error.code, "message": str(error)}) from error
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
        before = {
            "presentation_id": str(item.presentation_id) if item.presentation_id else None,
            "presentation_version_id": str(item.presentation_version_id)
            if item.presentation_version_id
            else None,
            "match_state": str(item.match_state),
        }
        CentralMediaStagingService(
            factory(), settings().media_staging_path, settings().max_upload_bytes
        ).assign(session, item, presentation_id, manual=True)
        session.add(
            AuditRecord(
                actor_id=getattr(request.state, "admin_actor", "central-admin"),
                action="central.presentation_media.manual_assignment",
                target_type="presentation_media_import",
                target_id=item.media_import_id,
                site_id=item.destination_site_id,
                event_id=item.event_id,
                before_context=before,
                after_context={
                    "presentation_id": str(item.presentation_id),
                    "presentation_version_id": str(item.presentation_version_id),
                    "match_state": str(item.match_state),
                    "canonical_filename": item.canonical_filename,
                },
            )
        )
        return _view(item)

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
    def transfer_content(
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
        path = _safe_staging_path(
            CentralMediaStagingService(
                factory(), settings().media_staging_path, settings().max_upload_bytes
            ).root,
            item.staging_key,
        )
        if not path.is_file() or path.stat().st_size != expected_size:
            raise HTTPException(409, "staged transfer source is unavailable")
        count = min(settings().transfer_block_bytes, expected_size - offset)

        def content():
            with path.open("rb") as source:
                source.seek(offset)
                remaining = count
                while remaining:
                    chunk = source.read(min(1024 * 1024, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    yield chunk

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
    def create_replication(
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
            return replication_view(existing)
        if presentation.presentation_identifier != payload.presentation_identifier:
            raise HTTPException(409, "presentation identifier mismatch")
        root = CentralMediaStagingService(
            factory(), settings().media_staging_path, settings().max_upload_bytes
        ).root
        if payload.expected_size > settings().max_upload_bytes:
            raise HTTPException(413, "replication exceeds configured maximum")
        if shutil.disk_usage(root).free < payload.expected_size:
            raise HTTPException(507, "insufficient replication storage")
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
            partial_key=str(payload.replication_session_id),
            state=MediaTransferState.AVAILABLE,
            replication_state=MediaReplicationState.QUEUED,
        )
        session.add(receiver)
        session.flush()
        recover_partial(root, receiver)
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
        root = CentralMediaStagingService(
            factory(), settings().media_staging_path, settings().max_upload_bytes
        ).root
        path = recover_partial(root, receiver)
        written = 0
        try:
            with path.open("ab", buffering=0) as partial:
                async for chunk in request.stream():
                    written += len(chunk)
                    if written > settings().transfer_block_bytes:
                        raise HTTPException(413, "block exceeds configured maximum")
                    if offset + written > receiver.expected_size:
                        raise HTTPException(413, "block exceeds expected size")
                    partial.write(chunk)
                partial.flush()
                os.fsync(partial.fileno())
        except Exception:
            with path.open("r+b") as partial:
                partial.truncate(receiver.confirmed_offset)
            raise
        receiver.confirmed_offset += written
        receiver.state = MediaTransferState.TRANSFERRING
        receiver.replication_state = MediaReplicationState.SYNCING
        receiver.last_progress_at = utc_now()
        return replication_view(receiver)

    @app.post(
        "/api/v1/media-replications/{replication_session_id}/finalize",
        tags=["media-transfer"],
    )
    def finalize_replication_endpoint(
        replication_session_id: UUID,
        session: DbSession,
        authorization: Annotated[str | None, Header()] = None,
        x_upm_site_id: Annotated[UUID | None, Header()] = None,
    ) -> dict[str, object]:
        receiver = replication_for_site(
            replication_session_id, session, authorization, x_upm_site_id
        )
        root = CentralMediaStagingService(
            factory(), settings().media_staging_path, settings().max_upload_bytes
        ).root
        finalize_replication(session, root, receiver)
        return replication_view(receiver)
