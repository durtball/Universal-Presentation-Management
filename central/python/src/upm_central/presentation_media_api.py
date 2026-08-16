"""Authenticated Central presentation-media staging and review APIs."""

from collections.abc import Callable, Iterator
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, sessionmaker

from upm_central.config import CentralDatabaseSettings
from upm_central.persistence.models import (
    AuditRecord,
    Presentation,
    PresentationMediaImport,
    PresentationVersion,
    TransferJob,
)
from upm_central.presentation_media import CentralMediaStagingService, MediaStagingError
from upm_shared.enums import JobStatus, MediaImportState


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
                original_filename=original_filename,
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
