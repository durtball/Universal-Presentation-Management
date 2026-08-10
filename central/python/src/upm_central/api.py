"""Independent UPM Central API and minimal browser administration."""

import base64
import hashlib
import hmac
from collections.abc import Iterator
from typing import Annotated, Literal
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from upm_central.config import CentralDatabaseSettings
from upm_central.persistence.database import create_central_engine, create_central_session_factory
from upm_central.persistence.models import (
    AuditRecord,
    OutboxEvent,
    Site,
    SiteCredential,
    SiteEnrollmentClaim,
    SiteManagedSetting,
    SyncCursor,
    SyncSequence,
    utc_now,
)
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
from upm_shared.enums import EnrollmentState, JobStatus


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
        if request.method in {"POST", "PUT"} and request.url.path.startswith("/api/v1/"):
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
        return cached

    def db() -> Iterator[Session]:
        with get_factory().begin() as session:
            yield session

    DbSession = Annotated[Session, Depends(db)]

    def require_admin(x_upm_admin_token: Annotated[str | None, Header()] = None) -> None:
        if not x_upm_admin_token or not hmac.compare_digest(
            x_upm_admin_token, get_settings().admin_token
        ):
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED, detail="administrator authentication required"
            )

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
        cursor.last_sequence = max(cursor.last_sequence, payload.checkpoint_sequence)
        site.last_successful_sync_at = utc_now()
        return {"acknowledged": len(events), "checkpoint_sequence": cursor.last_sequence}

    @app.get("/admin/sites", response_class=HTMLResponse)
    def sites_page() -> str:
        return """<!doctype html>
<html><head><title>UPM Central Sites</title></head><body>
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

    return app
