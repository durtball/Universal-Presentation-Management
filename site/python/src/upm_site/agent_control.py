"""Durable Site/Agent command, telemetry, review, and saveback HTTP contracts."""

import hashlib
import secrets
from datetime import datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from upm_shared.enums import AssetKind, MediaAvailability, SyncState
from upm_shared.identifiers import new_uuid7
from upm_site.persistence.models import (
    AuditRecord,
    Device,
    DeviceAssignment,
    DeviceCommand,
    DeviceCommandAttempt,
    DeviceRuntimeState,
    LocalSiteIdentity,
    MediaObject,
    Presentation,
    PresentationAsset,
    PresentationReviewSession,
    PresentationVersion,
    utc_now,
)

COMMAND_STATES = {
    "pending",
    "delivered",
    "acknowledged",
    "running",
    "succeeded",
    "failed",
    "expired",
    "cancelled",
}
TERMINAL = {"succeeded", "failed", "expired", "cancelled"}


class CommandCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    device_id: UUID
    room_id: UUID | None = None
    command_type: str = Field(min_length=1, max_length=64)
    payload: dict[str, object]
    idempotency_key: str = Field(min_length=1, max_length=255)
    expires_at: datetime | None = None
    correlation_id: UUID | None = None


class CommandUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["acknowledged", "running", "succeeded", "failed"]
    error_code: str | None = Field(None, max_length=100)
    error_message: str | None = Field(None, max_length=2048)
    detail: dict[str, object] = Field(default_factory=dict)


class Heartbeat(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hostname: str = Field(min_length=1, max_length=255)
    display_name: str | None = Field(None, max_length=255)
    windows_version: str | None = Field(None, max_length=255)
    agent_version: str = Field(min_length=1, max_length=64)
    interactive_session_available: bool = False
    powerpoint_available: bool | None = None
    powerpoint_version: str | None = None
    free_disk_bytes: int | None = Field(None, ge=0)
    local_cache_bytes: int | None = Field(None, ge=0)
    current_presentation_id: UUID | None = None
    current_review_session_id: UUID | None = None
    current_command_id: UUID | None = None
    last_error: str | None = Field(None, max_length=2048)
    metadata: dict[str, object] = Field(default_factory=dict)


class ReviewCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    presentation_id: UUID
    base_presentation_version_id: UUID
    device_id: UUID
    room_id: UUID
    idempotency_key: str = Field(min_length=1, max_length=255)


class LocalChanges(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filename: str = Field(min_length=1, max_length=1024)
    size_bytes: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-fA-F]{64}$")
    modified_at: datetime

    @field_validator("filename")
    @classmethod
    def reject_office_temporary_files(cls, value: str) -> str:
        if value.startswith("~$"):
            raise ValueError("Office lock/temp files are not saveback candidates")
        return value


class SavebackComplete(BaseModel):
    model_config = ConfigDict(extra="forbid")
    media_object_id: UUID
    force_new_revision: bool = False


def view(c: DeviceCommand):
    return {
        x: getattr(c, x)
        for x in (
            "command_id",
            "site_id",
            "device_id",
            "room_id",
            "command_type",
            "payload",
            "idempotency_key",
            "requested_by",
            "requested_at",
            "status",
            "available_at",
            "delivered_at",
            "acknowledged_at",
            "started_at",
            "completed_at",
            "failed_at",
            "expires_at",
            "attempt_count",
            "error_code",
            "error_message",
            "correlation_id",
        )
    }


def audit(s, site, actor, action, target, target_id, after, event_id=None):
    s.add(
        AuditRecord(
            site_id=site,
            actor_id=actor,
            action=action,
            target_type=target,
            target_id=target_id,
            event_id=event_id,
            after_context=after,
        )
    )


def identity(s):
    i = s.scalar(select(LocalSiteIdentity).where(LocalSiteIdentity.singleton_key == 1))
    if not i:
        raise HTTPException(409, "Site identity is not configured")
    return i


def register_agent_control_routes(
    app: FastAPI, read_db, transaction_db, heartbeat_timeout_seconds: int = 90
):
    Read = Annotated[Session, Depends(read_db)]
    Write = Annotated[Session, Depends(transaction_db)]

    def agent(s: Session, authorization: str | None):
        token = authorization[7:] if authorization and authorization.startswith("Bearer ") else ""
        d = (
            s.scalar(
                select(Device).where(
                    Device.agent_token_hash == hashlib.sha256(token.encode()).hexdigest()
                )
            )
            if token
            else None
        )
        if not d or d.revoked_at:
            raise HTTPException(401, "invalid Agent credential")
        return d

    @app.post("/api/v1/devices/{device_id}/agent-credential", tags=["devices"])
    def credential(device_id: UUID, request: Request, s: Write):
        d = s.get(Device, device_id)
        if not d:
            raise HTTPException(404, "device not found")
        token = secrets.token_urlsafe(48)
        d.agent_token_hash = hashlib.sha256(token.encode()).hexdigest()
        d.enrolled_at = d.enrolled_at or utc_now()
        audit(
            s,
            d.site_id,
            request.state.site_user_id,
            "device.agent_credential.rotated",
            "device",
            d.device_id,
            {},
        )
        return {"device_id": d.device_id, "agent_token": token}  # returned once; only hash persists

    @app.post("/api/v1/device-commands", status_code=201, tags=["device-control"])
    def create_command(p: CommandCreate, request: Request, s: Write):
        i = identity(s)
        existing = s.scalar(
            select(DeviceCommand).where(
                DeviceCommand.site_id == i.site_id,
                DeviceCommand.idempotency_key == p.idempotency_key,
            )
        )
        if existing:
            return view(existing)
        d = s.get(Device, p.device_id)
        if not d or d.site_id != i.site_id:
            raise HTTPException(422, "invalid device")
        if p.room_id:
            assignment = s.scalar(
                select(DeviceAssignment).where(
                    DeviceAssignment.device_id == d.device_id,
                    DeviceAssignment.room_id == p.room_id,
                    DeviceAssignment.active.is_(True),
                )
            )
            if not assignment:
                raise HTTPException(409, "device is not actively assigned to room")
        c = DeviceCommand(
            site_id=i.site_id,
            device_id=d.device_id,
            room_id=p.room_id,
            command_type=p.command_type,
            payload=p.payload,
            idempotency_key=p.idempotency_key,
            requested_by=request.state.site_user_id,
            expires_at=p.expires_at,
            correlation_id=p.correlation_id or new_uuid7(),
        )
        s.add(c)
        s.flush()
        audit(
            s,
            i.site_id,
            c.requested_by,
            "device.command.created",
            "device_command",
            c.command_id,
            view(c),
        )
        return view(c)

    @app.get("/api/v1/device-commands/{command_id}", tags=["device-control"])
    def command(command_id: UUID, s: Read):
        c = s.get(DeviceCommand, command_id)
        if not c:
            raise HTTPException(404, "command not found")
        return view(c)

    @app.get("/api/v1/device-commands", tags=["device-control"])
    def list_commands(
        s: Read,
        device_id: UUID | None = None,
        command_status: str | None = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ):
        query = select(DeviceCommand).order_by(DeviceCommand.requested_at.desc()).limit(limit)
        if device_id is not None:
            query = query.where(DeviceCommand.device_id == device_id)
        if command_status is not None:
            if command_status not in COMMAND_STATES:
                raise HTTPException(422, "invalid command status")
            query = query.where(DeviceCommand.status == command_status)
        return [view(command) for command in s.scalars(query).all()]

    @app.get("/api/v1/devices/{device_id}/runtime", tags=["devices"])
    def runtime(device_id: UUID, s: Read):
        d = s.get(Device, device_id)
        r = s.get(DeviceRuntimeState, device_id)
        if not d:
            raise HTTPException(404, "device not found")
        if not r:
            return {"device_id": device_id, "online_state": "unknown", "last_heartbeat_at": None}
        online = r.last_heartbeat_at >= utc_now() - timedelta(seconds=heartbeat_timeout_seconds)
        return {
            "device_id": device_id,
            "online_state": "online" if online else "offline",
            **{
                x: getattr(r, x)
                for x in (
                    "last_heartbeat_at",
                    "hostname",
                    "display_name",
                    "ip_address",
                    "windows_version",
                    "agent_version",
                    "interactive_session_available",
                    "powerpoint_available",
                    "powerpoint_version",
                    "free_disk_bytes",
                    "local_cache_bytes",
                    "current_presentation_id",
                    "current_review_session_id",
                    "current_command_id",
                    "last_error",
                )
            },
        }

    @app.post("/api/v1/agent/heartbeat", tags=["agent"])
    def heartbeat(
        p: Heartbeat,
        request: Request,
        s: Write,
        authorization: Annotated[str | None, Header()] = None,
    ):
        d = agent(s, authorization)
        now = utc_now()
        r = s.get(DeviceRuntimeState, d.device_id)
        values = p.model_dump(exclude={"metadata"})
        values.update(
            ip_address=request.client.host if request.client else None,
            metadata_json=p.metadata,
            last_heartbeat_at=now,
            updated_at=now,
        )
        if r:
            for k, v in values.items():
                setattr(r, k, v)
        else:
            r = DeviceRuntimeState(device_id=d.device_id, connected_at=now, **values)
            s.add(r)
        return {
            "device_id": d.device_id,
            "server_time": now,
            "heartbeat_expires_in_seconds": heartbeat_timeout_seconds,
        }

    @app.get("/api/v1/agent/commands", tags=["agent"])
    def poll(
        s: Write,
        authorization: Annotated[str | None, Header()] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
    ):
        d = agent(s, authorization)
        now = utc_now()
        expired = s.scalars(
            select(DeviceCommand).where(
                DeviceCommand.device_id == d.device_id,
                DeviceCommand.status.in_(["pending", "delivered"]),
                DeviceCommand.expires_at.is_not(None),
                DeviceCommand.expires_at <= now,
            )
        ).all()
        for c in expired:
            c.status = "expired"
            c.updated_at = now
        rows = s.scalars(
            select(DeviceCommand)
            .where(
                DeviceCommand.device_id == d.device_id,
                DeviceCommand.status.in_(["pending", "delivered"]),
                DeviceCommand.available_at <= now,
            )
            .order_by(DeviceCommand.available_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        ).all()
        for c in rows:
            c.status = "delivered"
            c.delivered_at = now
            c.updated_at = now
            c.attempt_count += 1
            s.add(DeviceCommandAttempt(command_id=c.command_id, attempt_number=c.attempt_count))
        return [view(c) for c in rows]

    @app.post("/api/v1/agent/commands/{command_id}/state", tags=["agent"])
    def update(
        command_id: UUID,
        p: CommandUpdate,
        s: Write,
        authorization: Annotated[str | None, Header()] = None,
    ):
        d = agent(s, authorization)
        c = s.get(DeviceCommand, command_id)
        if not c or c.device_id != d.device_id:
            raise HTTPException(404, "command not found")
        allowed = {
            "delivered": {"acknowledged", "running", "succeeded", "failed"},
            "acknowledged": {"running", "succeeded", "failed"},
            "running": {"succeeded", "failed"},
        }
        if p.status not in allowed.get(c.status, set()):
            raise HTTPException(409, f"invalid transition {c.status} -> {p.status}")
        now = utc_now()
        c.status = p.status
        c.updated_at = now
        c.error_code = p.error_code
        c.error_message = p.error_message
        setattr(
            c,
            {
                "acknowledged": "acknowledged_at",
                "running": "started_at",
                "succeeded": "completed_at",
                "failed": "failed_at",
            }[p.status],
            now,
        )
        a = s.scalar(
            select(DeviceCommandAttempt).where(
                DeviceCommandAttempt.command_id == c.command_id,
                DeviceCommandAttempt.attempt_number == c.attempt_count,
            )
        )
        if a:
            a.result_status = p.status
            a.result_at = now
            a.detail = p.detail
        audit(
            s,
            c.site_id,
            f"agent:{d.device_id}",
            f"device.command.{p.status}",
            "device_command",
            c.command_id,
            {"error_code": p.error_code},
        )
        return view(c)

    @app.post("/api/v1/review-sessions", status_code=201, tags=["reviews"])
    def create_review(p: ReviewCreate, request: Request, s: Write):
        i = identity(s)
        base = s.get(PresentationVersion, p.base_presentation_version_id)
        if not base or base.presentation_id != p.presentation_id:
            raise HTTPException(422, "invalid base version")
        cmd_key = f"review:{p.idempotency_key}"
        existing = s.scalar(
            select(DeviceCommand).where(
                DeviceCommand.site_id == i.site_id, DeviceCommand.idempotency_key == cmd_key
            )
        )
        if existing:
            return {
                "review_session_id": existing.payload["review_session_id"],
                "command": view(existing),
            }
        review = PresentationReviewSession(
            site_id=i.site_id,
            presentation_id=p.presentation_id,
            base_presentation_version_id=base.presentation_version_id,
            device_id=p.device_id,
            room_id=p.room_id,
            operator_id=request.state.site_user_id,
        )
        s.add(review)
        s.flush()
        c = DeviceCommand(
            site_id=i.site_id,
            device_id=p.device_id,
            room_id=p.room_id,
            command_type="open_review",
            payload={
                "review_session_id": str(review.review_session_id),
                "presentation_id": str(p.presentation_id),
                "presentation_version_id": str(base.presentation_version_id),
                "action": "open",
            },
            idempotency_key=cmd_key,
            requested_by=request.state.site_user_id,
            correlation_id=review.correlation_id,
        )
        s.add(c)
        s.flush()
        audit(
            s,
            i.site_id,
            request.state.site_user_id,
            "presentation.review.created",
            "presentation_review",
            review.review_session_id,
            {"base_version_id": str(base.presentation_version_id)},
            s.get(Presentation, p.presentation_id).event_id,
        )
        return {"review_session_id": review.review_session_id, "command": view(c)}

    @app.get("/api/v1/review-sessions/{review_id}", tags=["reviews"])
    def review_detail(review_id: UUID, s: Read):
        review = s.get(PresentationReviewSession, review_id)
        if review is None:
            raise HTTPException(404, "review not found")
        return {
            name: getattr(review, name)
            for name in (
                "review_session_id",
                "presentation_id",
                "base_presentation_version_id",
                "device_id",
                "room_id",
                "operator_id",
                "state",
                "opened_at",
                "local_changes_at",
                "working_filename",
                "working_size_bytes",
                "working_sha256",
                "working_modified_at",
                "saveback_media_object_id",
                "saveback_version_id",
                "conflict_version_id",
                "correlation_id",
                "completed_at",
            )
        }

    @app.post("/api/v1/agent/review-sessions/{review_id}/local-changes", tags=["agent"])
    def changes(
        review_id: UUID,
        p: LocalChanges,
        s: Write,
        authorization: Annotated[str | None, Header()] = None,
    ):
        d = agent(s, authorization)
        r = s.get(PresentationReviewSession, review_id)
        if not r or r.device_id != d.device_id:
            raise HTTPException(404, "review not found")
        r.state = "local_changes"
        r.local_changes_at = utc_now()
        r.working_filename = p.filename
        r.working_size_bytes = p.size_bytes
        r.working_sha256 = p.sha256.lower()
        r.working_modified_at = p.modified_at
        r.updated_at = utc_now()
        return {"review_session_id": r.review_session_id, "state": r.state}

    @app.post("/api/v1/agent/review-sessions/{review_id}/saveback-complete", tags=["agent"])
    def saveback(
        review_id: UUID,
        p: SavebackComplete,
        s: Write,
        authorization: Annotated[str | None, Header()] = None,
    ):
        d = agent(s, authorization)
        r = s.get(PresentationReviewSession, review_id)
        media = s.get(MediaObject, p.media_object_id)
        if not r or r.device_id != d.device_id:
            raise HTTPException(404, "review not found")
        if (
            not media
            or media.site_id != r.site_id
            or media.availability != MediaAvailability.AVAILABLE
        ):
            raise HTTPException(422, "saveback media is not verified and available")
        latest = s.scalar(
            select(PresentationVersion)
            .where(PresentationVersion.presentation_id == r.presentation_id)
            .order_by(PresentationVersion.version_number.desc())
            .with_for_update()
        )
        if (
            latest.presentation_version_id != r.base_presentation_version_id
            and not p.force_new_revision
        ):
            r.state = "conflict"
            r.conflict_version_id = latest.presentation_version_id
            r.saveback_media_object_id = media.media_object_id
            r.updated_at = utc_now()
            return {
                "review_session_id": r.review_session_id,
                "state": "conflict",
                "base_version_id": r.base_presentation_version_id,
                "current_version_id": latest.presentation_version_id,
            }
        version = PresentationVersion(
            presentation_id=r.presentation_id,
            version_number=latest.version_number + 1,
            sync_state=SyncState.LOCAL,
        )
        s.add(version)
        s.flush()
        s.add(
            PresentationAsset(
                presentation_version_id=version.presentation_version_id,
                media_object_id=media.media_object_id,
                original_filename=media.original_filename,
                kind=AssetKind.ORIGINAL,
            )
        )
        r.state = "saveback_complete"
        r.saveback_media_object_id = media.media_object_id
        r.saveback_version_id = version.presentation_version_id
        r.completed_at = utc_now()
        r.updated_at = utc_now()
        presentation = s.get(Presentation, r.presentation_id)
        audit(
            s,
            r.site_id,
            f"agent:{d.device_id}",
            "presentation.saveback.created",
            "presentation_version",
            version.presentation_version_id,
            {
                "review_session_id": str(r.review_session_id),
                "base_version_id": str(r.base_presentation_version_id),
            },
            presentation.event_id,
        )
        return {
            "review_session_id": r.review_session_id,
            "state": r.state,
            "presentation_version_id": version.presentation_version_id,
            "version_number": version.version_number,
        }
