"""Independent UPM Central API and minimal browser administration."""

import base64
import hashlib
import hmac
from collections.abc import Iterator
from datetime import datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID

from fastapi import Cookie, Depends, FastAPI, Header, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from upm_central.auth import (
    authenticate,
    bootstrap_administrator,
    create_browser_session,
    csrf_matches,
    hash_password,
    resolve_browser_session,
    rotate_csrf,
    verify_password,
)
from upm_central.config import CentralDatabaseSettings
from upm_central.event_deployments import (
    create_deployment,
    push_deployment,
    retry_deployment,
    revoke_deployment,
)
from upm_central.lifecycle_api import register_lifecycle_routes
from upm_central.persistence.database import create_central_engine, create_central_session_factory
from upm_central.persistence.models import (
    AdminSession,
    AuditRecord,
    Event,
    EventDeployment,
    OutboxEvent,
    Site,
    SiteCredential,
    SiteEnrollmentClaim,
    SiteManagedSetting,
    SiteRoomMapping,
    SyncCursor,
    SyncSequence,
    utc_now,
)
from upm_central.persistence.models import Session as ProgramSession
from upm_central.program import (
    normalize_text,
    require_aware,
    touch_event_program,
    validate_timezone,
)
from upm_central.program_api import register_program_routes
from upm_central.sync import (
    apply_site_event,
    authenticate_site,
    create_setting_event,
    envelope,
    issue_poll_token,
    secret_hash,
    secrets_match,
)
from upm_shared.contracts.sync import (
    UPM_SYNC_PROTOCOL_VERSION,
    EnrollmentRequest,
    EnrollmentRequestResponse,
    EnrollmentStatusResponse,
    EventAckRequest,
    OutboundSyncResponse,
    SyncBatchRequest,
    SyncBatchResponse,
)
from upm_shared.enums import EnrollmentState, EventDeploymentStatus, JobStatus


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    service: Literal["upm-central"] = "upm-central"
    status: Literal["foundation-ready"] = "foundation-ready"


class StateChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: Annotated[str | None, Field(max_length=512)] = None


class SettingUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: dict[str, object]


class EventCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Annotated[str, Field(min_length=1, max_length=255)]
    description: str | None = None
    timezone: Annotated[str, Field(min_length=1, max_length=100)] = "UTC"
    starts_at: datetime | None = None
    ends_at: datetime | None = None


class EventUpdate(EventCreate):
    pass


class DeploymentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    site_id: UUID


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: Annotated[str, Field(min_length=1, max_length=255)]
    password: Annotated[str, Field(min_length=1, max_length=1024)]


class RoomMappingWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    site_id: UUID
    imported_label: Annotated[str, Field(min_length=1, max_length=255)]
    target_room_id: UUID | None = None
    target_room_label: Annotated[str | None, Field(max_length=255)] = None
    mapping_status: Literal["mapped", "unmapped", "conflict"] = "mapped"


class PasswordChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    current_password: Annotated[str, Field(min_length=1, max_length=1024)]
    new_password: Annotated[str, Field(min_length=12, max_length=1024)]


def bearer_token(authorization: str | None) -> str | None:
    if not authorization or not authorization.startswith("Bearer "):
        return None
    return authorization[7:]


def create_app(settings: CentralDatabaseSettings | None = None) -> FastAPI:
    app = FastAPI(
        title="UPM Central API",
        version="0.2.0",
        description="UPM Central registry and synchronization API",
    )
    resources: dict[str, object] = {}

    @app.middleware("http")
    async def limit_sync_requests(request: Request, call_next):
        if request.method in {"POST", "PUT"} and request.url.path.startswith("/api/v1/sync"):
            body = await request.body()
            if len(body) > get_settings().sync_max_payload_bytes:
                return JSONResponse(status_code=413, content={"detail": "request_too_large"})
        return await call_next(request)

    def get_settings() -> CentralDatabaseSettings:
        configured = settings
        if configured is None:
            configured = resources.get("settings")  # type: ignore[assignment]
        if configured is None:
            configured = CentralDatabaseSettings()
            resources["settings"] = configured
        return configured

    def get_factory():
        cached = resources.get("factory")
        if cached is None:
            engine = create_central_engine(get_settings())
            cached = create_central_session_factory(engine)
            resources["engine"] = engine
            resources["factory"] = cached
        if not resources.get("administrator_bootstrapped"):
            with cached.begin() as bootstrap_session:
                bootstrap_administrator(
                    bootstrap_session,
                    get_settings().bootstrap_admin_username,
                    get_settings().bootstrap_admin_password,
                )
            resources["administrator_bootstrapped"] = True
        return cached

    def db() -> Iterator[Session]:
        with get_factory().begin() as session:
            yield session

    DbSession = Annotated[Session, Depends(db)]

    def require_admin(
        request: Request,
        session: DbSession,
        upm_admin_session: Annotated[str | None, Cookie()] = None,
        x_csrf_token: Annotated[str | None, Header()] = None,
        x_upm_admin_token: Annotated[str | None, Header()] = None,
    ) -> None:
        # Retain the documented automation credential without exposing it to the browser UI.
        if x_upm_admin_token and hmac.compare_digest(x_upm_admin_token, get_settings().admin_token):
            request.state.admin_actor = "central-automation"
            return
        browser_session = resolve_browser_session(session, upm_admin_session)
        if browser_session is None:
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED, detail="administrator authentication required"
            )
        if request.method not in {"GET", "HEAD", "OPTIONS"} and not csrf_matches(
            browser_session, x_csrf_token
        ):
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="invalid CSRF token")
        request.state.admin_actor = str(browser_session.user.admin_user_id)

    def session_view(item: AdminSession, csrf_token: str | None = None) -> dict[str, object]:
        result: dict[str, object] = {
            "authenticated": True,
            "user": {
                "user_id": item.user.admin_user_id,
                "username": item.user.username,
                "display_name": item.user.display_name,
                "roles": item.user.roles,
            },
            "expires_at": item.expires_at,
        }
        if csrf_token:
            result["csrf_token"] = csrf_token
        return result

    @app.post("/api/v1/auth/login", tags=["authentication"])
    def login(payload: LoginRequest, request: Request, response: Response, session: DbSession):
        user = authenticate(session, payload.username, payload.password)
        if user is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="invalid username or password")
        item, token, csrf_token = create_browser_session(
            session,
            user,
            lifetime=timedelta(hours=get_settings().admin_session_hours),
            remote_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
        )
        response.set_cookie(
            "upm_admin_session",
            token,
            httponly=True,
            secure=get_settings().admin_cookie_secure,
            samesite="lax",
            max_age=get_settings().admin_session_hours * 3600,
            path="/",
        )
        return session_view(item, csrf_token)

    @app.get("/api/v1/auth/session", tags=["authentication"])
    def current_session(
        session: DbSession, upm_admin_session: Annotated[str | None, Cookie()] = None
    ) -> dict[str, object]:
        item = resolve_browser_session(session, upm_admin_session)
        if item is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="not authenticated")
        return session_view(item, rotate_csrf(item))

    @app.post("/api/v1/auth/logout", tags=["authentication"])
    def logout(
        response: Response,
        session: DbSession,
        upm_admin_session: Annotated[str | None, Cookie()] = None,
        x_csrf_token: Annotated[str | None, Header()] = None,
    ) -> Response:
        item = resolve_browser_session(session, upm_admin_session)
        if item is not None:
            if not csrf_matches(item, x_csrf_token):
                raise HTTPException(status.HTTP_403_FORBIDDEN, detail="invalid CSRF token")
            item.revoked_at = utc_now()
        response.delete_cookie("upm_admin_session", path="/")
        response.status_code = status.HTTP_204_NO_CONTENT
        return response

    @app.post(
        "/api/v1/auth/password",
        dependencies=[Depends(require_admin)],
        tags=["authentication"],
    )
    def change_password(
        payload: PasswordChange,
        session: DbSession,
        upm_admin_session: Annotated[str | None, Cookie()] = None,
    ) -> dict[str, object]:
        item = resolve_browser_session(session, upm_admin_session)
        if item is None or not verify_password(payload.current_password, item.user.password_hash):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="current password is invalid")
        item.user.password_hash = hash_password(payload.new_password)
        item.user.password_changed_at = utc_now()
        item.user.revision += 1
        for other in item.user.sessions:
            if other.admin_session_id != item.admin_session_id and other.revoked_at is None:
                other.revoked_at = utc_now()
        return {"password_changed": True}

    @app.get("/health", response_model=HealthResponse, tags=["system"])
    def health() -> HealthResponse:
        return HealthResponse()

    @app.post(
        "/api/v1/sites/enrollment-requests",
        response_model=EnrollmentRequestResponse,
        status_code=202,
    )
    def request_enrollment(
        payload: EnrollmentRequest, request: Request, session: DbSession
    ) -> EnrollmentRequestResponse:
        site = session.get(Site, payload.site_id)
        now = utc_now()
        if site is None:
            site = Site(
                site_id=payload.site_id,
                display_name=payload.display_name,
                enabled=True,
                enrollment_state=EnrollmentState.PENDING,
                first_registered_at=now,
            )
            session.add(site)
        elif site.enrollment_state in {EnrollmentState.REVOKED, EnrollmentState.DISABLED}:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, detail="site enrollment is revoked or disabled"
            )
        site.display_name = payload.display_name
        site.last_registered_at = now
        site.application_version = payload.application_version
        site.protocol_version = payload.protocol_version
        site.reported_hostname = payload.reported_hostname
        site.reported_address = request.client.host if request.client else None
        site.capabilities = payload.capabilities
        site.protocol_error = (
            None
            if payload.protocol_version == UPM_SYNC_PROTOCOL_VERSION
            else "incompatible_sync_protocol"
        )
        if site.enrollment_state not in {EnrollmentState.ACTIVE, EnrollmentState.REJECTED}:
            site.enrollment_state = EnrollmentState.PENDING
        if session.get(SyncSequence, payload.site_id) is None:
            session.add(SyncSequence(site_id=payload.site_id, next_value=1))
        claim = session.get(SiteEnrollmentClaim, payload.site_id)
        if claim is None:
            claim = SiteEnrollmentClaim(
                site_id=payload.site_id,
                claim_secret_hash=secret_hash(payload.claim_secret),
                poll_token_hash="",
                expires_at=now,
            )
            session.add(claim)
        elif claim.claim_secret_hash and not secrets_match(
            payload.claim_secret, claim.claim_secret_hash
        ):
            raise HTTPException(
                status.HTTP_409_CONFLICT, detail="site_id already has a different enrollment claim"
            )
        poll_token = issue_poll_token(claim)
        return EnrollmentRequestResponse(
            site_id=site.site_id, state=site.enrollment_state, poll_token=poll_token
        )

    @app.get("/api/v1/sites/{site_id}/enrollment-status", response_model=EnrollmentStatusResponse)
    def enrollment_status(
        site_id: UUID,
        session: DbSession,
        x_upm_poll_token: Annotated[str | None, Header()] = None,
    ) -> EnrollmentStatusResponse:
        site = session.get(Site, site_id)
        claim = session.get(SiteEnrollmentClaim, site_id)
        if (
            site is None
            or claim is None
            or not x_upm_poll_token
            or not secrets_match(x_upm_poll_token, claim.poll_token_hash)
            or claim.expires_at < utc_now()
        ):
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED, detail="invalid or expired enrollment poll token"
            )
        credential = None
        if site.enrollment_state == EnrollmentState.ACTIVE:
            digest = hmac.new(
                get_settings().credential_issuer_key.encode(),
                f"upm-site-credential-v1:{site_id}:{claim.claim_secret_hash}".encode(),
                hashlib.sha256,
            ).digest()
            credential = base64.urlsafe_b64encode(digest).decode().rstrip("=")
            token_digest = secret_hash(credential)
            existing = session.scalar(
                select(SiteCredential).where(SiteCredential.token_hash == token_digest)
            )
            if existing is None:
                session.add(SiteCredential(site_id=site_id, token_hash=token_digest))
            claim.credential_delivered_at = utc_now()
        return EnrollmentStatusResponse(
            site_id=site_id,
            state=site.enrollment_state,
            protocol_version=UPM_SYNC_PROTOCOL_VERSION,
            credential=credential,
            reason=claim.rejection_reason,
        )

    def change_state(
        site_id: UUID, target: EnrollmentState, change: StateChange, session: Session
    ) -> dict[str, object]:
        site = session.get(Site, site_id)
        if site is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="site not found")
        before = site.enrollment_state
        allowed = {
            EnrollmentState.ACTIVE: {
                EnrollmentState.PENDING,
                EnrollmentState.REJECTED,
                EnrollmentState.DISABLED,
            },
            EnrollmentState.PENDING: {EnrollmentState.REVOKED},
            EnrollmentState.REJECTED: {EnrollmentState.PENDING},
            EnrollmentState.REVOKED: {EnrollmentState.ACTIVE, EnrollmentState.DISABLED},
            EnrollmentState.DISABLED: {EnrollmentState.ACTIVE},
        }
        if before not in allowed[target]:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail=f"invalid enrollment transition {before} -> {target}",
            )
        site.enrollment_state = target
        site.enabled = target not in {EnrollmentState.DISABLED, EnrollmentState.REVOKED}
        claim = session.get(SiteEnrollmentClaim, site_id)
        if claim:
            claim.rejection_reason = change.reason
        if target == EnrollmentState.REVOKED:
            for credential in session.scalars(
                select(SiteCredential).where(
                    SiteCredential.site_id == site_id, SiteCredential.revoked_at.is_(None)
                )
            ):
                credential.revoked_at = utc_now()
        session.add(
            AuditRecord(
                actor_id="central-admin",
                action=f"site.enrollment.{target}",
                target_type="site",
                target_id=site_id,
                site_id=site_id,
                before_context={"state": before},
                after_context={"state": target, "reason": change.reason},
            )
        )
        return {"site_id": site_id, "state": target}

    @app.post("/api/v1/admin/sites/{site_id}/approve", dependencies=[Depends(require_admin)])
    def approve(site_id: UUID, change: StateChange, session: DbSession):
        return change_state(site_id, EnrollmentState.ACTIVE, change, session)

    @app.post("/api/v1/admin/sites/{site_id}/reject", dependencies=[Depends(require_admin)])
    def reject(site_id: UUID, change: StateChange, session: DbSession):
        return change_state(site_id, EnrollmentState.REJECTED, change, session)

    @app.post("/api/v1/admin/sites/{site_id}/revoke", dependencies=[Depends(require_admin)])
    def revoke(site_id: UUID, change: StateChange, session: DbSession):
        return change_state(site_id, EnrollmentState.REVOKED, change, session)

    @app.post("/api/v1/admin/sites/{site_id}/disable", dependencies=[Depends(require_admin)])
    def disable(site_id: UUID, change: StateChange, session: DbSession):
        return change_state(site_id, EnrollmentState.DISABLED, change, session)

    @app.post("/api/v1/admin/sites/{site_id}/reenroll", dependencies=[Depends(require_admin)])
    def reenroll(site_id: UUID, change: StateChange, session: DbSession):
        result = change_state(site_id, EnrollmentState.PENDING, change, session)
        claim = session.get(SiteEnrollmentClaim, site_id)
        if claim is not None:
            session.delete(claim)
        return result

    @app.get("/api/v1/admin/sites", dependencies=[Depends(require_admin)])
    def list_sites(session: DbSession) -> list[dict[str, object]]:
        now = utc_now()
        result = []
        for site in session.scalars(select(Site).order_by(Site.display_name)):
            age = (now - site.last_seen_at).total_seconds() if site.last_seen_at else None
            connectivity = (
                "online"
                if age is not None and age < 90
                else "degraded"
                if age is not None and age < 300
                else "offline"
            )
            counts = dict(
                session.execute(
                    select(OutboxEvent.status, func.count())
                    .where(
                        OutboxEvent.owning_site_id == site.site_id,
                        OutboxEvent.source_sequence.is_not(None),
                    )
                    .group_by(OutboxEvent.status)
                ).all()
            )
            outbound_sequence = (
                session.scalar(
                    select(func.max(OutboxEvent.source_sequence)).where(
                        OutboxEvent.owning_site_id == site.site_id
                    )
                )
                or 0
            )
            central_to_site = session.get(SyncCursor, (site.site_id, "central_to_site"))
            site_to_central = session.get(SyncCursor, (site.site_id, "site_to_central"))
            deployments = session.scalars(
                select(EventDeployment).where(EventDeployment.site_id == site.site_id)
            ).all()
            result.append(
                {
                    "site_id": site.site_id,
                    "display_name": site.display_name,
                    "enrollment_state": site.enrollment_state,
                    "connectivity": connectivity,
                    "last_seen_at": site.last_seen_at,
                    "last_successful_sync_at": site.last_successful_sync_at,
                    "application_version": site.application_version,
                    "protocol_version": site.protocol_version,
                    "health": site.health_summary,
                    "pending_sync": counts.get(JobStatus.PENDING, 0)
                    + counts.get(JobStatus.RETRY_WAIT, 0)
                    + counts.get(JobStatus.RUNNING, 0),
                    "failed_sync": counts.get(JobStatus.FAILED, 0)
                    + counts.get(JobStatus.EXHAUSTED, 0),
                    "outbound_central_sequence": outbound_sequence,
                    "site_acknowledged_through": (
                        central_to_site.last_sequence if central_to_site else 0
                    ),
                    "inbound_site_sequence": site_to_central.last_sequence
                    if site_to_central
                    else 0,
                    "deployments": [deployment_view(item) for item in deployments],
                }
            )
        return result

    @app.put(
        "/api/v1/admin/sites/{site_id}/settings/{setting_key}",
        dependencies=[Depends(require_admin)],
    )
    def update_setting(
        site_id: UUID, setting_key: str, update: SettingUpdate, session: DbSession
    ) -> dict[str, object]:
        site = session.get(Site, site_id)
        if site is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="site not found")
        setting = session.scalar(
            select(SiteManagedSetting).where(
                SiteManagedSetting.site_id == site_id, SiteManagedSetting.setting_key == setting_key
            )
        )
        if setting is None:
            setting = SiteManagedSetting(
                site_id=site_id, setting_key=setting_key, value=update.value, revision=1
            )
            session.add(setting)
            session.flush()
        else:
            setting.value = update.value
            setting.revision += 1
        event = create_setting_event(session, setting)
        return {
            "setting_key": setting_key,
            "revision": setting.revision,
            "event_id": event.outbox_event_id,
        }

    def deployment_view(deployment: EventDeployment) -> dict[str, object]:
        synchronization_state = (
            "failed"
            if deployment.status == EventDeploymentStatus.FAILED
            else "current"
            if deployment.acknowledged_revision == deployment.desired_revision
            else "synchronizing"
        )
        return {
            "deployment_id": deployment.deployment_id,
            "event_id": deployment.event_id,
            "site_id": deployment.site_id,
            "status": deployment.status,
            "desired_revision": deployment.desired_revision,
            "applied_revision": deployment.acknowledged_revision,
            "synchronization_state": synchronization_state,
            "site_status": deployment.site_status,
            "last_synchronization_at": deployment.last_synchronization_at,
            "successfully_deployed_at": deployment.successfully_deployed_at,
            "failure_at": deployment.failure_at,
            "failure_reason": deployment.failure_reason,
            "summary_counts": deployment.summary_counts,
        }

    @app.post("/api/v1/admin/events", status_code=201, dependencies=[Depends(require_admin)])
    def create_event(payload: EventCreate, session: DbSession) -> dict[str, object]:
        validate_timezone(payload.timezone)
        require_aware(payload.starts_at, "starts_at")
        require_aware(payload.ends_at, "ends_at")
        if payload.starts_at and payload.ends_at and payload.ends_at <= payload.starts_at:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="invalid event dates")
        event = Event(**payload.model_dump())
        session.add(event)
        session.flush()
        return {"event_id": event.event_id, "name": event.name, "revision": event.revision}

    @app.get("/api/v1/admin/events", dependencies=[Depends(require_admin)])
    def list_events(session: DbSession) -> list[dict[str, object]]:
        result = []
        for event in session.scalars(select(Event).order_by(Event.name)):
            deployments = session.scalars(
                select(EventDeployment).where(EventDeployment.event_id == event.event_id)
            ).all()
            result.append(
                {
                    "event_id": event.event_id,
                    "name": event.name,
                    "description": event.description,
                    "timezone": event.timezone,
                    "starts_at": event.starts_at,
                    "ends_at": event.ends_at,
                    "revision": event.revision,
                    "deployments": [deployment_view(item) for item in deployments],
                }
            )
        return result

    @app.put("/api/v1/admin/events/{event_id}", dependencies=[Depends(require_admin)])
    def update_event(event_id: UUID, payload: EventUpdate, session: DbSession) -> dict[str, object]:
        event = session.get(Event, event_id)
        if event is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="event not found")
        validate_timezone(payload.timezone)
        require_aware(payload.starts_at, "starts_at")
        require_aware(payload.ends_at, "ends_at")
        if payload.starts_at and payload.ends_at and payload.ends_at <= payload.starts_at:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="invalid event dates")
        event.name = payload.name
        event.description = payload.description
        event.timezone = payload.timezone
        event.starts_at = payload.starts_at
        event.ends_at = payload.ends_at
        event.revision += 1
        pushed = []
        deployments = session.scalars(
            select(EventDeployment).where(
                EventDeployment.event_id == event_id,
                EventDeployment.status.notin_(
                    [EventDeploymentStatus.REVOKED, EventDeploymentStatus.ARCHIVED]
                ),
            )
        ).all()
        for deployment in deployments:
            push_deployment(session, deployment)
            pushed.append(deployment.deployment_id)
        return {"event_id": event_id, "revision": event.revision, "deployments_pushed": pushed}

    @app.get(
        "/api/v1/admin/events/{event_id}/deployments",
        dependencies=[Depends(require_admin)],
    )
    def list_deployments(event_id: UUID, session: DbSession) -> list[dict[str, object]]:
        if session.get(Event, event_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="event not found")
        return [
            deployment_view(item)
            for item in session.scalars(
                select(EventDeployment)
                .where(EventDeployment.event_id == event_id)
                .order_by(EventDeployment.created_at)
            )
        ]

    @app.get(
        "/api/v1/admin/events/{event_id}/room-mappings",
        dependencies=[Depends(require_admin)],
        tags=["program"],
    )
    def list_room_mappings(
        event_id: UUID, site_id: UUID, session: DbSession
    ) -> list[dict[str, object]]:
        if session.get(Event, event_id) is None or session.get(Site, site_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="event or site not found")
        labels = list(
            dict.fromkeys(
                value
                for value in session.scalars(
                    select(ProgramSession.location_name)
                    .where(
                        ProgramSession.event_id == event_id,
                        ProgramSession.location_name.is_not(None),
                    )
                    .order_by(ProgramSession.location_name)
                )
                if value
            )
        )
        existing = {
            item.normalized_imported_label: item
            for item in session.scalars(
                select(SiteRoomMapping).where(SiteRoomMapping.site_id == site_id)
            )
        }
        result = []
        for label in labels:
            normalized = normalize_text(label) or ""
            mapping = existing.get(normalized)
            result.append(
                {
                    "imported_label": label,
                    "normalized_imported_label": normalized,
                    "mapping_status": mapping.mapping_status if mapping else "unmapped",
                    "target_room_id": mapping.target_room_id if mapping else None,
                    "target_room_label": mapping.target_room_label if mapping else None,
                    "revision": mapping.revision if mapping else None,
                }
            )
        return result

    @app.put(
        "/api/v1/admin/room-mappings",
        dependencies=[Depends(require_admin)],
        tags=["program"],
    )
    def save_room_mapping(payload: RoomMappingWrite, session: DbSession) -> dict[str, object]:
        if session.get(Site, payload.site_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="site not found")
        if payload.mapping_status == "mapped" and not (
            payload.target_room_id and payload.target_room_label
        ):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="mapped rooms require the Site room UUID and label",
            )
        normalized = normalize_text(payload.imported_label)
        mapping = session.scalar(
            select(SiteRoomMapping).where(
                SiteRoomMapping.site_id == payload.site_id,
                SiteRoomMapping.normalized_imported_label == normalized,
            )
        )
        if mapping is None:
            mapping = SiteRoomMapping(
                site_id=payload.site_id,
                imported_label=payload.imported_label,
                normalized_imported_label=normalized,
            )
            session.add(mapping)
        else:
            mapping.revision += 1
        mapping.target_room_id = payload.target_room_id
        mapping.target_room_label = payload.target_room_label
        mapping.mapping_status = payload.mapping_status
        mapping.confirmed_by = "central-admin"
        session.flush()
        affected_events = session.scalars(
            select(Event)
            .join(ProgramSession, ProgramSession.event_id == Event.event_id)
            .where(func.lower(ProgramSession.location_name) == normalized)
            .distinct()
        ).all()
        for event in affected_events:
            touch_event_program(session, event)
        return {
            "site_room_mapping_id": mapping.site_room_mapping_id,
            "site_id": mapping.site_id,
            "imported_label": mapping.imported_label,
            "mapping_status": mapping.mapping_status,
            "target_room_id": mapping.target_room_id,
            "target_room_label": mapping.target_room_label,
        }

    @app.post(
        "/api/v1/admin/events/{event_id}/deployments",
        status_code=201,
        dependencies=[Depends(require_admin)],
    )
    def deploy_event(
        event_id: UUID, payload: DeploymentCreate, session: DbSession
    ) -> dict[str, object]:
        return deployment_view(create_deployment(session, event_id, payload.site_id))

    def required_deployment(session: Session, deployment_id: UUID) -> EventDeployment:
        deployment = session.get(EventDeployment, deployment_id)
        if deployment is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="deployment not found")
        return deployment

    @app.post(
        "/api/v1/admin/event-deployments/{deployment_id}/push",
        dependencies=[Depends(require_admin)],
    )
    def push_event_deployment(deployment_id: UUID, session: DbSession) -> dict[str, object]:
        return deployment_view(
            push_deployment(session, required_deployment(session, deployment_id))
        )

    @app.post(
        "/api/v1/admin/event-deployments/{deployment_id}/retry",
        dependencies=[Depends(require_admin)],
    )
    def retry_event_deployment(deployment_id: UUID, session: DbSession) -> dict[str, object]:
        return deployment_view(
            retry_deployment(session, required_deployment(session, deployment_id))
        )

    @app.post(
        "/api/v1/admin/event-deployments/{deployment_id}/revoke",
        dependencies=[Depends(require_admin)],
    )
    def revoke_event_deployment(
        deployment_id: UUID, change: StateChange, session: DbSession
    ) -> dict[str, object]:
        return deployment_view(
            revoke_deployment(session, required_deployment(session, deployment_id), change.reason)
        )

    @app.post("/api/v1/sync/site-events", response_model=SyncBatchResponse)
    def receive_site_events(
        payload: SyncBatchRequest,
        session: DbSession,
        authorization: Annotated[str | None, Header()] = None,
        x_upm_site_id: Annotated[UUID | None, Header()] = None,
    ) -> SyncBatchResponse:
        if x_upm_site_id is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="missing site identity")
        site = authenticate_site(session, x_upm_site_id, bearer_token(authorization))
        if payload.protocol_version != UPM_SYNC_PROTOCOL_VERSION:
            site.protocol_error = "incompatible_sync_protocol"
            raise HTTPException(status.HTTP_409_CONFLICT, detail="incompatible_sync_protocol")
        acknowledgements = [apply_site_event(session, site, event) for event in payload.events]
        cursor = session.get(SyncCursor, (site.site_id, "site_to_central"))
        return SyncBatchResponse(
            acknowledgements=acknowledgements,
            checkpoint_sequence=cursor.last_sequence if cursor else 0,
        )

    @app.post("/api/v1/sites/{site_id}/heartbeat", response_model=SyncBatchResponse)
    def heartbeat(
        site_id: UUID,
        payload: SyncBatchRequest,
        session: DbSession,
        authorization: Annotated[str | None, Header()] = None,
    ) -> SyncBatchResponse:
        site = authenticate_site(session, site_id, bearer_token(authorization))
        acknowledgements = [apply_site_event(session, site, event) for event in payload.events]
        cursor = session.get(SyncCursor, (site.site_id, "site_to_central"))
        return SyncBatchResponse(
            acknowledgements=acknowledgements,
            checkpoint_sequence=cursor.last_sequence if cursor else 0,
        )

    @app.get("/api/v1/sync/central-events", response_model=OutboundSyncResponse)
    def outbound_events(
        session: DbSession,
        authorization: Annotated[str | None, Header()] = None,
        x_upm_site_id: Annotated[UUID | None, Header()] = None,
    ) -> OutboundSyncResponse:
        if x_upm_site_id is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="missing site identity")
        authenticate_site(session, x_upm_site_id, bearer_token(authorization))
        events = session.scalars(
            select(OutboxEvent)
            .where(
                OutboxEvent.owning_site_id == x_upm_site_id,
                OutboxEvent.status.in_(
                    [JobStatus.PENDING, JobStatus.RETRY_WAIT, JobStatus.RUNNING]
                ),
            )
            .order_by(OutboxEvent.source_sequence)
            .limit(get_settings().sync_batch_count)
        ).all()
        return OutboundSyncResponse(events=[envelope(event) for event in events])

    @app.post("/api/v1/sync/central-events/ack")
    def acknowledge_central_events(
        payload: EventAckRequest,
        session: DbSession,
        authorization: Annotated[str | None, Header()] = None,
        x_upm_site_id: Annotated[UUID | None, Header()] = None,
    ) -> dict[str, object]:
        if x_upm_site_id is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="missing site identity")
        site = authenticate_site(session, x_upm_site_id, bearer_token(authorization))
        if payload.protocol_version != UPM_SYNC_PROTOCOL_VERSION:
            raise HTTPException(status.HTTP_409_CONFLICT, detail="incompatible_sync_protocol")
        events = session.scalars(
            select(OutboxEvent).where(
                OutboxEvent.owning_site_id == site.site_id,
                OutboxEvent.outbox_event_id.in_(payload.event_ids),
            )
        ).all()
        for event in events:
            event.status = JobStatus.SUCCEEDED
            event.processed_at = utc_now()
            event.claimed_by_worker_id = None
            event.lease_expires_at = None
        cursor = session.get(SyncCursor, (site.site_id, "central_to_site"))
        if cursor is None:
            cursor = SyncCursor(site_id=site.site_id, direction="central_to_site", last_sequence=0)
            session.add(cursor)
        highest_owned_sequence = max(
            (event.source_sequence or 0 for event in events), default=cursor.last_sequence
        )
        if payload.checkpoint_sequence > highest_owned_sequence:
            raise HTTPException(status.HTTP_409_CONFLICT, detail="invalid checkpoint sequence")
        cursor.last_sequence = max(cursor.last_sequence, payload.checkpoint_sequence)
        site.last_successful_sync_at = utc_now()
        return {"acknowledged": len(events), "checkpoint_sequence": cursor.last_sequence}

    @app.get("/admin/sites", response_class=HTMLResponse)
    def sites_page() -> str:
        return """<!doctype html>
<html><head><title>UPM Central Sites</title></head><body>
<nav><a href="/admin/sites">Sites</a> | <a href="/admin/events">Events</a> |
<a href="/admin/program">Program</a> | <a href="/admin/people">People</a></nav>
<h1>Sites</h1><p>Enter the configured administrator token.</p>
<input id="t" type="password"><button onclick="load()">Refresh</button><div id="o"></div>
<script>async function load(){const t=document.querySelector('#t').value;
const r=await fetch('/api/v1/admin/sites',{headers:{'X-UPM-Admin-Token':t}});
const sites=await r.json(),out=document.querySelector('#o');out.replaceChildren();
for(const s of sites){const pre=document.createElement('pre');
pre.textContent=JSON.stringify(s,null,2);out.append(pre);
for(const action of ['approve','reject']){const button=document.createElement('button');
button.textContent=action;button.onclick=()=>act(s.site_id,action);out.append(button)}}}
async function act(id,a){await fetch(`/api/v1/admin/sites/${id}/${a}`,{method:'POST',
headers:{'Content-Type':'application/json','X-UPM-Admin-Token':document.querySelector('#t').value},
body:'{}'});load()}</script>
</body></html>"""

    @app.get("/admin/events", response_class=HTMLResponse)
    def events_page() -> str:
        return """<!doctype html>
<html><head><title>UPM Central Events</title></head><body>
<nav><a href="/admin/sites">Sites</a> | <a href="/admin/events">Events</a> |
<a href="/admin/program">Program</a> | <a href="/admin/people">People</a></nav>
<h1>Event deployments</h1><p>Enter the configured administrator token.</p>
<input id="t" type="password"><button onclick="load()">Refresh</button>
<p><input id="event-name" placeholder="Event name"><button onclick="createEvent()">
Create Event</button></p><div id="o"></div>
<script>async function load(){const t=document.querySelector('#t').value;
const headers={'X-UPM-Admin-Token':t};
const [er,sr]=await Promise.all([fetch('/api/v1/admin/events',{headers}),
fetch('/api/v1/admin/sites',{headers})]);
const events=await er.json(),sites=await sr.json(),out=document.querySelector('#o');
out.replaceChildren();
for(const e of events){const h=document.createElement('h2');
h.textContent=`${e.name} (${e.event_id})`;
out.append(h);const select=document.createElement('select');
for(const s of sites.filter(x=>x.enrollment_state==='active')){const option=new Option(
s.display_name,s.site_id);select.add(option)}out.append(select);
const deploy=document.createElement('button');deploy.textContent='Deploy to Site';
deploy.onclick=()=>deployTo(e.event_id,select.value);out.append(deploy);
for(const d of e.deployments){const pre=document.createElement('pre');
pre.textContent=JSON.stringify(d,null,2);out.append(pre);for(const a of ['push','retry','revoke']){
const b=document.createElement('button');b.textContent=a;b.onclick=()=>act(d.deployment_id,a);
out.append(b)}}}}async function act(id,a){await fetch(`/api/v1/admin/event-deployments/${id}/${a}`,
{method:'POST',headers:{'Content-Type':'application/json','X-UPM-Admin-Token':
document.querySelector('#t').value},body:JSON.stringify({})});load()}
async function deployTo(eventId,siteId){if(!siteId)return;await fetch(
`/api/v1/admin/events/${eventId}/deployments`,{method:'POST',headers:{
'Content-Type':'application/json','X-UPM-Admin-Token':document.querySelector('#t').value},
body:JSON.stringify({site_id:siteId})});load()}
async function createEvent(){await fetch('/api/v1/admin/events',{method:'POST',headers:{
'Content-Type':'application/json','X-UPM-Admin-Token':document.querySelector('#t').value},
body:JSON.stringify({name:document.querySelector('#event-name').value})});load()}
</script></body></html>"""

    register_program_routes(app, db, require_admin)
    register_lifecycle_routes(app, db, require_admin)
    return app
