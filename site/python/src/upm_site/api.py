"""Independent UPM Site FastAPI application boundary and media API."""

from collections.abc import Iterator
from datetime import datetime, timedelta
from typing import Annotated, Literal
from urllib.parse import unquote
from uuid import UUID

import httpx
from fastapi import (
    Cookie,
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
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
from upm_shared.media_storage_client import (
    AsyncMediaStorageClient,
    MediaStorageClient,
    MediaStorageUnavailable,
)
from upm_site.agent_control import register_agent_control_routes
from upm_site.auth import (
    authenticate,
    bootstrap,
    create_session,
    csrf_matches,
    has_permission,
    resolve,
    rotate_csrf,
)
from upm_site.config import SiteSettings
from upm_site.event_deployments import request_site_event_deletion
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
from upm_site.operational_logs import record_log, register_log_routes
from upm_site.operations_api import register_operations_routes
from upm_site.persistence.database import create_site_engine, create_site_session_factory
from upm_site.persistence.models import (
    CentralRegistration,
    Event,
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
from upm_site.presentation_media_api import register_presentation_media_routes
from upm_site.program_api import register_program_routes
from upm_site.rotation_api import register_rotation_routes
from upm_site.sync import (
    apply_central_event,
    bootstrap_identity,
    credential_matches,
    enqueue_heartbeat,
    outbox_health,
)
from upm_site.users_api import register_user_routes


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    service: Literal["upm-site"] = "upm-site"
    status: Literal["foundation-ready"] = "foundation-ready"


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1, max_length=1024)


class EventDeletionRequest(BaseModel):
    confirmation: str = Field(min_length=1, max_length=255)


class MediaResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    media_object_id: UUID
    site_id: UUID
    event_id: UUID | None
    storage_target_id: UUID
    object_key: str
    category: MediaCategory
    original_filename: str
    source_relative_path: str | None
    canonical_filename: str | None
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
    role: Literal["staging", "media"] = "media"
    path: str
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
    percent_used: float | None
    upm_owned_bytes: int = 0
    object_count: int = 0


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
        source_relative_path=media.source_relative_path,
        canonical_filename=media.canonical_filename,
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
    target: StorageTarget,
    observation: StorageObservation,
    *,
    owned_bytes: int = 0,
    object_count: int = 0,
    role: Literal["staging", "media"] = "media",
    path: str | None = None,
) -> StorageHealthResponse:
    return StorageHealthResponse(
        display_name=target.display_name,
        role=role,
        path=path or target.root_path,
        enabled=target.enabled,
        primary_media=target.primary_media,
        **{field: getattr(observation, field) for field in StorageObservation.__dataclass_fields__},
        percent_used=(observation.used_bytes * 100 / observation.total_bytes)
        if observation.used_bytes is not None and observation.total_bytes
        else None,
        upm_owned_bytes=owned_bytes,
        object_count=object_count,
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
        if not resources.get("auth_bootstrapped"):
            with cached.begin() as auth_session:
                bootstrap(
                    auth_session,
                    get_settings().bootstrap_admin_username,
                    get_settings().bootstrap_admin_password,
                )
            resources["auth_bootstrapped"] = True
        return cached  # type: ignore[return-value]

    @app.middleware("http")
    async def site_authentication(request: Request, call_next):
        if (
            request.url.path in {"/health", "/api/v1/auth/login"}
            or request.url.path.startswith("/api/v1/agent/")
            or not get_settings().auth_required
        ):
            return await call_next(request)
        token = request.cookies.get("upm_site_session")
        with get_factory()() as auth_session:
            item, user = resolve(auth_session, token)
            if not item or not user:
                return JSONResponse(status_code=401, content={"detail": "authentication required"})
            if request.method not in {"GET", "HEAD", "OPTIONS"} and not csrf_matches(
                item, request.headers.get("X-CSRF-Token")
            ):
                return JSONResponse(status_code=403, content={"detail": "invalid CSRF token"})
            permission = (
                "presentations.read"
                if request.method in {"GET", "HEAD", "OPTIONS"}
                else "presentations.write"
            )
            if "/users" in request.url.path:
                permission = (
                    "users.manage" if request.method not in {"GET", "HEAD"} else "users.read"
                )
            if not has_permission(user, permission):
                return JSONResponse(status_code=403, content={"detail": "permission denied"})
            request.state.site_user_id = str(user.user_id)
        return await call_next(request)

    def get_session() -> Iterator[Session]:
        with get_factory()() as session:
            yield session

    def transaction() -> Iterator[Session]:
        with get_factory().begin() as session:
            yield session

    @app.get("/health", response_model=HealthResponse, tags=["system"])
    def health() -> HealthResponse:
        return HealthResponse()

    @app.get("/api/v1/system/outbox-health", tags=["system"])
    def durable_outbox_health(
        session: Annotated[Session, Depends(get_session)],
    ) -> dict:
        return outbox_health(session)

    def auth_view(user, csrf=None):
        value = {
            "authenticated": True,
            "user": {
                "user_id": user.user_id,
                "username": user.username,
                "display_name": user.display_name,
                "roles": user.roles,
                "permissions": user.permissions,
                "user_type": user.user_type,
            },
        }
        if csrf:
            value["csrf_token"] = csrf
        return value

    @app.post("/api/v1/auth/login", tags=["authentication"])
    def login(
        payload: LoginRequest, response: Response, session: Annotated[Session, Depends(transaction)]
    ):
        user = authenticate(session, payload.username, payload.password)
        if not user:
            record_log(
                session,
                service="site-api",
                severity="warning",
                event_type="auth.login.failed",
                message="Site login failed",
                context={"username": payload.username[:255]},
            )
            raise HTTPException(401, "invalid username or password")
        _item, token, csrf = create_session(session, user, get_settings().session_hours)
        response.set_cookie(
            "upm_site_session",
            token,
            httponly=True,
            secure=get_settings().session_cookie_secure,
            samesite="lax",
            path="/",
            max_age=get_settings().session_hours * 3600,
        )
        record_log(
            session,
            service="site-api",
            event_type="auth.login.succeeded",
            message="Site user logged in",
            context={"user_id": str(user.user_id), "user_type": user.user_type},
        )
        return auth_view(user, csrf)

    @app.get("/api/v1/auth/session", tags=["authentication"])
    def current_session(
        upm_site_session: Annotated[str | None, Cookie()] = None,
        session: Annotated[Session, Depends(transaction)] = None,
    ):
        _item, user = resolve(session, upm_site_session)
        if not user:
            raise HTTPException(401, "not authenticated")
        return auth_view(user, rotate_csrf(_item))

    @app.post("/api/v1/auth/logout", status_code=204, tags=["authentication"])
    def logout(
        response: Response,
        upm_site_session: Annotated[str | None, Cookie()] = None,
        session: Annotated[Session, Depends(transaction)] = None,
    ):
        item, _user = resolve(session, upm_site_session)
        if item:
            item.revoked_at = utc_now()
            record_log(
                session,
                service="site-api",
                event_type="auth.logout",
                message="Site user logged out",
                context={"user_id": str(item.user_id)},
            )
        response.delete_cookie("upm_site_session", path="/")

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
        source_relative_path: Annotated[
            str | None, Header(alias="X-UPM-Source-Relative-Path")
        ] = None,
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
            get_factory(),
            max_upload_bytes=get_settings().max_upload_bytes,
            storage_client=AsyncMediaStorageClient(
                get_settings().media_storage_url, get_settings().media_storage_token
            ),
        )
        request = IngestionRequest(
            site_id=site_id,
            original_filename=unquote(original_filename),
            source_relative_path=unquote(source_relative_path) if source_relative_path else None,
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
            if getattr(error, "code", "") == "storage_service_unavailable":
                code = status.HTTP_503_SERVICE_UNAVAILABLE
            if getattr(error, "code", "") == "storage_write_error":
                code = status.HTTP_507_INSUFFICIENT_STORAGE
            raise HTTPException(code, detail=str(error)) from error
        with get_factory()() as session:
            media = session.get(MediaObject, result.media_object_id)
            if media is None:
                raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "media metadata missing")
            response = _media_response(session, media)
        with get_factory().begin() as session:
            record_log(
                session,
                service="presentation-intake",
                event_type="upload.staged",
                message="Presentation received into Site-local media",
                media_import_id=result.media_object_id,
                event_id=event_id,
                presentation_version_id=presentation_version_id,
                context={
                    "filename": unquote(original_filename),
                    "duplicate_retry": result.duplicate_retry,
                },
            )
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
        media_roots = [
            _health_response(
                target,
                observe_storage(target),
                owned_bytes=session.scalar(
                    select(func.coalesce(func.sum(MediaObject.size_bytes), 0)).where(
                        MediaObject.storage_target_id == target.storage_target_id,
                        MediaObject.availability == MediaAvailability.AVAILABLE,
                    )
                )
                or 0,
                object_count=session.scalar(
                    select(func.count())
                    .select_from(MediaObject)
                    .where(
                        MediaObject.storage_target_id == target.storage_target_id,
                        MediaObject.availability == MediaAvailability.AVAILABLE,
                    )
                )
                or 0,
            )
            for target in targets
        ]
        staging_roots = [
            _health_response(
                target,
                observe_storage(target),
                role="media",
                path=f"{target.root_path.rstrip('/')}/.ingestion-staging",
                owned_bytes=session.scalar(
                    select(func.coalesce(func.sum(MediaObject.size_bytes), 0)).where(
                        MediaObject.storage_target_id == target.storage_target_id,
                        MediaObject.availability.in_(
                            [MediaAvailability.STAGING, MediaAvailability.FINALIZING]
                        ),
                    )
                )
                or 0,
                object_count=session.scalar(
                    select(func.count())
                    .select_from(MediaObject)
                    .where(
                        MediaObject.storage_target_id == target.storage_target_id,
                        MediaObject.availability.in_(
                            [MediaAvailability.STAGING, MediaAvailability.FINALIZING]
                        ),
                    )
                )
                or 0,
            ).model_copy(update={"role": "staging", "display_name": "Temporary / Staging Storage"})
            for target in targets
            if target.primary_media
        ]
        return staging_roots + media_roots

    @app.get("/api/v1/media-storage", tags=["storage"])
    def media_storage_overview() -> dict:
        storage = MediaStorageClient(
            get_settings().media_storage_url, get_settings().media_storage_token
        )
        try:
            assignments = storage.assignments()
            roots = []
            for role, target in assignments.items():
                roots.append(
                    {
                        "storage_target_id": target["storage_target_id"],
                        "role": role,
                        "display_name": target["name"],
                        "path": target["internal_path"],
                        "available": target["health"] != "Unavailable",
                        "writable": target["writable"],
                        "health": target["health"],
                        "total_bytes": target["total_bytes"],
                        "used_bytes": target["used_bytes"],
                        "free_bytes": target["free_bytes"],
                        "percent_used": target["percent_used"],
                        "last_successful_check_at": target["checked_at"],
                        "detail": target["detail"],
                        "upm_owned_bytes": target.get("upm_owned_bytes"),
                        "object_count": target.get("object_count"),
                    }
                )
            return {"roots": roots, "targets": storage.targets(), "service_available": True}
        except MediaStorageUnavailable as error:
            return {
                "roots": [
                    {
                        "storage_target_id": f"unavailable-{role}",
                        "role": role,
                        "display_name": "Media Storage service unavailable",
                        "path": "",
                        "available": False,
                        "writable": False,
                        "health": "Unavailable",
                        "detail": str(error),
                    }
                    for role in ("staging", "media")
                ],
                "targets": [],
                "service_available": False,
                "detail": str(error),
            }

    @app.post("/api/v1/media-storage/{role}/test", tags=["storage"])
    def test_media_storage(role: str) -> dict:
        storage = MediaStorageClient(
            get_settings().media_storage_url, get_settings().media_storage_token
        )
        try:
            return storage.test(storage.assignments()[role]["storage_target_id"])
        except (MediaStorageUnavailable, KeyError) as error:
            raise HTTPException(503, "Media Storage service is unavailable.") from error

    @app.put("/api/v1/media-storage/{role}/{target_id}", tags=["storage"])
    def activate_media_storage(role: str, target_id: UUID) -> dict:
        if role not in {"staging", "media"}:
            raise HTTPException(404, "storage role not found")
        storage = MediaStorageClient(
            get_settings().media_storage_url, get_settings().media_storage_token
        )
        try:
            tested = storage.test(target_id)
            if not tested["writable"] or tested["health"] in {"Unavailable", "Critical"}:
                raise HTTPException(507, tested.get("detail") or "Storage target is not usable.")
            return storage.activate(role, target_id)
        except MediaStorageUnavailable as error:
            raise HTTPException(503, str(error)) from error

    @app.post(
        "/api/v1/storage-targets/{storage_target_id}/test",
        response_model=StorageHealthResponse,
        tags=["storage"],
    )
    def test_storage(
        storage_target_id: UUID, session: Annotated[Session, Depends(get_session)]
    ) -> StorageHealthResponse:
        target = session.get(StorageTarget, storage_target_id)
        if target is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "storage target not found")
        return _health_response(target, observe_storage(target))

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
            "last_connection_at": registration.last_connection_at,
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

    @app.post("/api/v1/central-registration/test", tags=["synchronization"])
    def test_central_endpoint(update: CentralEndpointUpdate) -> dict[str, object]:
        central_url = str(update.central_url).rstrip("/")
        try:
            response = httpx.get(f"{central_url}/health", timeout=5.0)
            response.raise_for_status()
            health = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise HTTPException(502, f"Central connection failed: {error}") from error
        if health.get("service") != "upm-central":
            raise HTTPException(409, "The endpoint did not identify itself as UPM Central.")
        return {
            "reachable": True,
            "central_url": central_url,
            "central_identity": health["service"],
            "status": health.get("status"),
        }

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

    @app.delete("/api/v1/events/{event_id}", status_code=202, tags=["events"])
    def delete_event(
        event_id: UUID,
        payload: EventDeletionRequest,
        session: Annotated[Session, Depends(transaction)],
    ) -> dict[str, object]:
        event = session.get(Event, event_id)
        if event is None:
            return {"event_id": event_id, "status": "deleted", "idempotent": True}
        if payload.confirmation != event.name:
            raise HTTPException(422, "type the exact event name to confirm global deletion")
        request_site_event_deletion(session, event_id)
        return {
            "event_id": event_id,
            "status": "deletion_requested",
            "scope": "global",
            "message": "Central-managed events are deleted from Central and every deployed Site.",
        }

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

    register_agent_control_routes(app, get_session, transaction)
    register_program_routes(app, get_session)
    register_operations_routes(app, get_session, transaction)
    register_rotation_routes(app, get_session, transaction)
    register_presentation_media_routes(app, get_session, transaction, get_settings)
    register_log_routes(app, get_session)
    register_user_routes(app, get_session, transaction, get_settings)
    return app
