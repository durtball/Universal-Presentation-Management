"""Independent UPM Site FastAPI application boundary and media API."""

from collections.abc import Iterator
from datetime import datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from upm_shared.contracts.sync import (
    UPM_SYNC_PROTOCOL_VERSION,
    CentralEndpointUpdate,
    SiteIdentityUpdate,
    SyncBatchRequest,
    SyncBatchResponse,
)
from upm_shared.enums import (
    EnrollmentState,
    JobStatus,
    MediaAvailability,
    MediaCategory,
    StorageHealth,
)
from upm_site.config import SiteSettings
from upm_site.media.ingestion import (
    IngestionConflictError,
    IngestionError,
    IngestionRequest,
    MediaIngestionService,
)
from upm_site.media.storage import (
    InsufficientCapacityError,
    StorageError,
    StorageObservation,
    observe_storage,
)
from upm_site.persistence.database import create_site_engine, create_site_session_factory
from upm_site.persistence.models import (
    CentralRegistration,
    EventDeploymentProjection,
    LocalSiteIdentity,
    ManagedSetting,
    MediaObject,
    OutboxEvent,
    PresentationAsset,
    ProcessingJob,
    StorageTarget,
    SyncCursor,
    utc_now,
)
from upm_site.program_api import register_program_routes
from upm_site.sync import (
    apply_central_event,
    bootstrap_identity,
    credential_matches,
    enqueue_heartbeat,
)


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    service: Literal["upm-site"] = "upm-site"
    status: Literal["foundation-ready"] = "foundation-ready"


class MediaResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    media_object_id: UUID
    site_id: UUID
    event_id: UUID | None
    storage_target_id: UUID
    object_key: str
    category: MediaCategory
    original_filename: str
    mime_type: str | None
    size_bytes: int | None = Field(ge=0)
    hash_algorithm: str | None
    content_hash: str | None
    availability: MediaAvailability
    failure_reason: str | None
    presentation_asset_id: UUID | None = None
    processing_job_id: UUID | None = None
    created_at: datetime


class IngestionResponse(MediaResponse):
    duplicate_retry: bool = False


class IngestionStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    media_object_id: UUID
    availability: MediaAvailability
    failure_reason: str | None
    size_bytes: int | None
    content_hash: str | None


class StorageHealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    storage_target_id: UUID
    display_name: str
    enabled: bool
    primary_media: bool
    observed_at: datetime
    available: bool
    root_exists: bool
    writable: bool
    total_bytes: int | None
    used_bytes: int | None
    free_bytes: int | None
    health: StorageHealth
    warning_threshold_reached: bool
    critical_threshold_reached: bool
    detail: str | None


def _media_response(session: Session, media: MediaObject) -> MediaResponse:
    asset_id = session.scalar(
        select(PresentationAsset.presentation_asset_id).where(
            PresentationAsset.media_object_id == media.media_object_id
        )
    )
    job_id = session.scalar(
        select(ProcessingJob.processing_job_id).where(
            ProcessingJob.media_object_id == media.media_object_id
        )
    )
    return MediaResponse(
        media_object_id=media.media_object_id,
        site_id=media.site_id,
        event_id=media.event_id,
        storage_target_id=media.storage_target_id,
        object_key=media.object_key,
        category=media.category,
        original_filename=media.original_filename,
        mime_type=media.mime_type,
        size_bytes=media.size_bytes,
        hash_algorithm=media.hash_algorithm,
        content_hash=media.content_hash,
        availability=media.availability,
        failure_reason=media.failure_reason,
        presentation_asset_id=asset_id,
        processing_job_id=job_id,
        created_at=media.created_at,
    )


def _health_response(
    target: StorageTarget, observation: StorageObservation
) -> StorageHealthResponse:
    return StorageHealthResponse(
        display_name=target.display_name,
        enabled=target.enabled,
        primary_media=target.primary_media,
        **{field: getattr(observation, field) for field in StorageObservation.__dataclass_fields__},
    )


def create_app(
    *,
    settings: SiteSettings | None = None,
    session_factory: sessionmaker[Session] | None = None,
) -> FastAPI:
    """Create the Site API without importing Central application state."""
    app = FastAPI(
        title="UPM Site API",
        version="0.2.0",
        description="Site-local operational API including authoritative media ingestion.",
    )
    resources: dict[str, object] = {}

    def get_settings() -> SiteSettings:
        configured = settings
        if configured is None:
            configured = resources.get("settings")  # type: ignore[assignment]
        if configured is None:
            configured = SiteSettings()
            resources["settings"] = configured
        return configured

    @app.middleware("http")
    async def limit_sync_requests(request: Request, call_next):
        if request.method in {"POST", "PUT"} and request.url.path.startswith("/api/v1/sync"):
            body = await request.body()
            if len(body) > get_settings().sync_max_payload_bytes:
                return JSONResponse(status_code=413, content={"detail": "request_too_large"})
        return await call_next(request)

    def get_factory() -> sessionmaker[Session]:
        configured = session_factory
        if configured is not None:
            return configured
        cached = resources.get("factory")
        if cached is None:
            engine = create_site_engine(get_settings())
            cached = create_site_session_factory(engine)
            resources["engine"] = engine
            resources["factory"] = cached
        return cached  # type: ignore[return-value]

    def get_session() -> Iterator[Session]:
        with get_factory()() as session:
            yield session

    def transaction() -> Iterator[Session]:
        with get_factory().begin() as session:
            yield session

    @app.get("/health", response_model=HealthResponse, tags=["system"])
    def health() -> HealthResponse:
        return HealthResponse()

    @app.post(
        "/api/v1/media/ingestions",
        response_model=IngestionResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["media"],
    )
    async def ingest_media(
        http_request: Request,
        site_id: Annotated[UUID, Query()],
        category: Annotated[MediaCategory, Query()],
        original_filename: Annotated[str, Header(alias="X-UPM-Original-Filename")],
        expected_size: Annotated[int | None, Query(ge=0)] = None,
        event_id: Annotated[UUID | None, Query()] = None,
        presentation_version_id: Annotated[UUID | None, Query()] = None,
        storage_target_id: Annotated[UUID | None, Query()] = None,
        idempotency_key: Annotated[
            str | None, Header(alias="Idempotency-Key", max_length=255)
        ] = None,
        content_length: Annotated[int | None, Header(alias="Content-Length", ge=0)] = None,
        content_type: Annotated[str | None, Header(alias="Content-Type")] = None,
    ) -> IngestionResponse:
        service = MediaIngestionService(
            get_factory(), max_upload_bytes=get_settings().max_upload_bytes
        )
        request = IngestionRequest(
            site_id=site_id,
            original_filename=original_filename,
            category=category,
            expected_size=expected_size if expected_size is not None else content_length,
            event_id=event_id,
            presentation_version_id=presentation_version_id,
            storage_target_id=storage_target_id,
            idempotency_key=idempotency_key,
            client_mime_type=content_type,
        )
        try:
            result = await service.ingest_async(request, http_request.stream())
        except IngestionConflictError as error:
            raise HTTPException(status.HTTP_409_CONFLICT, detail=str(error)) from error
        except InsufficientCapacityError as error:
            raise HTTPException(status.HTTP_507_INSUFFICIENT_STORAGE, detail=str(error)) from error
        except (IngestionError, StorageError, ValueError) as error:
            code = (
                status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
                if getattr(error, "code", "") == "too_large"
                else status.HTTP_422_UNPROCESSABLE_ENTITY
            )
            raise HTTPException(code, detail=str(error)) from error
        with get_factory()() as session:
            media = session.get(MediaObject, result.media_object_id)
            if media is None:
                raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "media metadata missing")
            response = _media_response(session, media)
        return IngestionResponse(**response.model_dump(), duplicate_retry=result.duplicate_retry)

    @app.get("/api/v1/media/{media_object_id}", response_model=MediaResponse, tags=["media"])
    def get_media(
        media_object_id: UUID, session: Annotated[Session, Depends(get_session)]
    ) -> MediaResponse:
        media = session.get(MediaObject, media_object_id)
        if media is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "media object not found")
        return _media_response(session, media)

    @app.get(
        "/api/v1/media/{media_object_id}/status",
        response_model=IngestionStatusResponse,
        tags=["media"],
    )
    def get_ingestion_status(
        media_object_id: UUID, session: Annotated[Session, Depends(get_session)]
    ) -> IngestionStatusResponse:
        media = session.get(MediaObject, media_object_id)
        if media is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "media object not found")
        return IngestionStatusResponse(
            media_object_id=media.media_object_id,
            availability=media.availability,
            failure_reason=media.failure_reason,
            size_bytes=media.size_bytes,
            content_hash=media.content_hash,
        )

    @app.get(
        "/api/v1/storage-targets/health",
        response_model=list[StorageHealthResponse],
        tags=["storage"],
    )
    def storage_health(
        session: Annotated[Session, Depends(get_session)],
    ) -> list[StorageHealthResponse]:
        targets = session.scalars(select(StorageTarget).order_by(StorageTarget.display_name)).all()
        return [_health_response(target, observe_storage(target)) for target in targets]

    @app.get("/api/v1/central-registration", tags=["synchronization"])
    def registration_status(session: Annotated[Session, Depends(transaction)]) -> dict[str, object]:
        site, registration = bootstrap_identity(session, get_settings())
        counts = dict(
            session.execute(
                select(OutboxEvent.status, func.count())
                .where(OutboxEvent.source_sequence.is_not(None))
                .group_by(OutboxEvent.status)
            ).all()
        )
        return {
            "site_id": site.site_id,
            "display_name": site.display_name,
            "central_url": registration.central_url,
            "registration_state": registration.state,
            "connection_status": "connected"
            if registration.last_connection_at
            else "never_connected",
            "last_successful_heartbeat": registration.last_heartbeat_at,
            "last_successful_sync": registration.last_successful_sync_at,
            "pending_outbound": counts.get(JobStatus.PENDING, 0)
            + counts.get(JobStatus.RETRY_WAIT, 0)
            + counts.get(JobStatus.RUNNING, 0),
            "failed_sync": counts.get(JobStatus.FAILED, 0) + counts.get(JobStatus.EXHAUSTED, 0),
            "protocol_version": UPM_SYNC_PROTOCOL_VERSION,
            "protocol_compatible": registration.protocol_compatible,
            "last_error": registration.last_error,
            "credential_present": registration.credential_encrypted is not None,
        }

    @app.put("/api/v1/central-registration/endpoint", tags=["synchronization"])
    def configure_endpoint(
        update: CentralEndpointUpdate, session: Annotated[Session, Depends(transaction)]
    ) -> dict[str, object]:
        site, registration = bootstrap_identity(session, get_settings())
        registration.central_url = str(update.central_url).rstrip("/")
        registration.last_error = None
        return {"site_id": site.site_id, "central_url": registration.central_url}

    @app.post("/api/v1/central-registration/request", tags=["synchronization"])
    def restart_enrollment(
        session: Annotated[Session, Depends(transaction)],
    ) -> dict[str, object]:
        site, registration = bootstrap_identity(session, get_settings())
        registration.state = EnrollmentState.UNREGISTERED
        registration.claim_secret_encrypted = None
        registration.poll_token_encrypted = None
        registration.credential_encrypted = None
        registration.last_error = None
        return {"site_id": site.site_id, "registration_state": registration.state}

    @app.put("/api/v1/site-identity", tags=["system"])
    def update_identity(
        update: SiteIdentityUpdate, session: Annotated[Session, Depends(transaction)]
    ) -> dict[str, object]:
        site, _ = bootstrap_identity(session, get_settings())
        site.display_name = update.display_name
        site.revision += 1
        identity = session.get(LocalSiteIdentity, site.site_id)
        identity.display_name = update.display_name
        return {"site_id": site.site_id, "display_name": site.display_name}

    @app.post(
        "/api/v1/sync/central-events", response_model=SyncBatchResponse, tags=["synchronization"]
    )
    def receive_central_events(
        payload: SyncBatchRequest,
        session: Annotated[Session, Depends(transaction)],
        authorization: Annotated[str | None, Header()] = None,
    ) -> SyncBatchResponse:
        site, registration = bootstrap_identity(session, get_settings())
        bearer = (
            authorization[7:] if authorization and authorization.startswith("Bearer ") else None
        )
        if not credential_matches(get_settings(), registration, bearer):
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED, detail="invalid synchronization credential"
            )
        acknowledgements = [apply_central_event(session, event) for event in payload.events]
        cursor = session.get(SyncCursor, "central_to_site")
        return SyncBatchResponse(
            acknowledgements=acknowledgements,
            checkpoint_sequence=cursor.last_sequence if cursor else 0,
        )

    @app.get("/api/v1/managed-settings", tags=["synchronization"])
    def managed_settings(
        session: Annotated[Session, Depends(get_session)],
    ) -> list[dict[str, object]]:
        return [
            {
                "setting_key": item.setting_key,
                "value": item.value,
                "central_revision": item.central_revision,
            }
            for item in session.scalars(select(ManagedSetting).order_by(ManagedSetting.setting_key))
        ]

    @app.get("/api/v1/event-deployments", tags=["events", "synchronization"])
    def event_deployments(
        session: Annotated[Session, Depends(get_session)],
    ) -> list[dict[str, object]]:
        registration = session.scalar(select(CentralRegistration))
        connected = bool(
            registration
            and registration.last_connection_at
            and utc_now() - registration.last_connection_at < timedelta(seconds=90)
        )
        return [
            {
                "deployment_id": item.deployment_id,
                "central_event_id": item.central_event_id,
                "site_id": item.site_id,
                "event_name": item.current_snapshot.get("event_name"),
                "status": item.status,
                "desired_revision": item.desired_revision,
                "applied_revision": item.applied_revision,
                "last_central_synchronization_at": item.last_central_synchronization_at,
                "applied_at": item.applied_at,
                "failure_reason": item.failure_reason,
                "summary_counts": item.summary_counts,
                "central_connected": connected,
            }
            for item in session.scalars(
                select(EventDeploymentProjection).order_by(
                    EventDeploymentProjection.updated_at.desc()
                )
            )
        ]

    @app.post("/api/v1/sync/heartbeat", tags=["synchronization"])
    def queue_heartbeat(
        session: Annotated[Session, Depends(transaction)],
    ) -> dict[str, object]:
        site, _ = bootstrap_identity(session, get_settings())
        event = enqueue_heartbeat(
            session,
            site,
            {
                "observed_at": datetime.now().astimezone().isoformat(),
                "application_version": get_settings().application_version,
                "protocol_version": UPM_SYNC_PROTOCOL_VERSION,
                "site_health": "healthy",
                "database_health": "healthy",
                "worker_health": "healthy",
                "storage": {},
                "queue": {},
                "capabilities": ["sync-v1", "site-health", "managed-settings"],
            },
        )
        return {"event_id": event.outbox_event_id, "queued": True}

    @app.post("/api/v1/sync/retry-failed", tags=["synchronization"])
    def retry_failed_sync(
        session: Annotated[Session, Depends(transaction)],
    ) -> dict[str, object]:
        events = session.scalars(
            select(OutboxEvent).where(
                OutboxEvent.source_sequence.is_not(None),
                OutboxEvent.status.in_([JobStatus.FAILED, JobStatus.EXHAUSTED]),
            )
        ).all()
        for event in events:
            event.status = JobStatus.RETRY_WAIT
            event.available_at = datetime.now().astimezone()
            event.claimed_by_worker_id = None
            event.lease_expires_at = None
            event.error_code = None
            event.last_error = None
        return {"retried": len(events)}

    @app.get("/admin/central-registration", response_class=HTMLResponse, tags=["administration"])
    def registration_page() -> str:
        return """<!doctype html>
<html><head><title>UPM Site Registration</title></head><body>
<h1>Central registration</h1><button onclick="load()">Refresh</button><pre id="o"></pre>
<script>async function load(){const r=await fetch('/api/v1/central-registration');
document.querySelector('#o').textContent=JSON.stringify(await r.json(),null,2)}load();</script>
</body></html>"""

    @app.get("/admin/event-deployments", response_class=HTMLResponse, tags=["administration"])
    def event_deployments_page() -> str:
        return """<!doctype html>
<html><head><title>UPM Site Event Deployments</title></head><body>
<nav><a href="/admin/central-registration">Central registration</a> |
<a href="/admin/event-deployments">Event deployments</a> |
<a href="/admin/program">Program</a></nav>
<h1>Site event deployments</h1><button onclick="load()">Refresh</button><div id="o"></div>
<script>async function load(){const r=await fetch('/api/v1/event-deployments');
const rows=await r.json(),out=document.querySelector('#o');out.replaceChildren();
for(const row of rows){const pre=document.createElement('pre');
pre.textContent=JSON.stringify(row,null,2);out.append(pre)}}load();</script></body></html>"""

    register_program_routes(app, get_session)
    return app
