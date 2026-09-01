"""Durable Site/Agent command, telemetry, review, and saveback HTTP contracts."""

import hashlib
import hmac
import secrets
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Annotated, Literal
from urllib.parse import quote
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from upm_shared.enums import AssetKind, MediaAvailability, SyncState
from upm_shared.enums import DeviceRole as EndpointRole
from upm_shared.identifiers import new_uuid7
from upm_shared.media_storage_client import AsyncMediaStorageClient
from upm_site.persistence.models import (
    AgentChangeFeed,
    AuditRecord,
    Device,
    DeviceAssignment,
    DeviceCommand,
    DeviceCommandAttempt,
    DeviceRuntimeState,
    Event,
    EventBranding,
    EventBrandingAsset,
    LocalSiteIdentity,
    MediaObject,
    Presentation,
    PresentationAsset,
    PresentationReviewSession,
    PresentationVersion,
    Room,
    RoomAssignment,
    RotationAssignment,
    utc_now,
)
from upm_site.persistence.models import (
    Session as ProgramSession,
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


def discovery_signature(secret: str, site_id: UUID, endpoint: str, issued_at: int, nonce: str):
    signed = f"{site_id}|{endpoint}|{issued_at}|{nonce}".encode()
    return hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()


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


class AgentConfigurationWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: UUID
    agent_role: Literal["room_agent", "upload_kiosk", "room_agent_kiosk"]
    configuration: dict[str, object] = Field(default_factory=dict)


class EventBrandingWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: Literal["central_default", "site_managed", "site_local_override"]
    local_override: bool = False
    configuration: dict[str, object] = Field(default_factory=dict)
    assets: dict[
        Literal[
            "event-logo",
            "client-logo",
            "kiosk-logo",
            "kiosk-background",
            "room-client-background",
            "sponsor",
        ],
        UUID,
    ] = Field(default_factory=dict)


class AutomaticAgentEnrollment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent_id: UUID
    machine_name: str = Field(min_length=1, max_length=255)
    device_name: str = Field(min_length=1, max_length=255)
    agent_version: str = Field(min_length=1, max_length=64)
    windows_version: str = Field(min_length=1, max_length=255)
    capabilities: list[str] = Field(max_length=32)
    supported_roles: list[str] = Field(max_length=16)
    discovery_issued_at: int
    discovery_nonce: str = Field(min_length=16, max_length=128)
    discovery_signature: str = Field(pattern=r"^[0-9a-f]{64}$")
    discovered_endpoint: str = Field(min_length=8, max_length=2048)


class RoomAgentAssignmentWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: UUID
    room_id: UUID | None = None
    role: Literal["room_agent", "upload_kiosk", "room_agent_kiosk"]


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
    app: FastAPI,
    read_db,
    transaction_db,
    heartbeat_timeout_seconds: int = 90,
    settings: Callable[[], object] | None = None,
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

    def discovery_settings():
        if settings is None:
            raise HTTPException(503, "Site discovery is not configured")
        configured = settings()
        if not configured.discovery_secret:
            raise HTTPException(503, "Site discovery is disabled")
        return configured

    @app.get("/api/v1/agent/discovery-metadata", tags=["agent"])
    def discovery_metadata(s: Read, x_upm_discovery_secret: Annotated[str | None, Header()] = None):
        configured = discovery_settings()
        if not secrets.compare_digest(x_upm_discovery_secret or "", configured.discovery_secret):
            raise HTTPException(401, "invalid discovery service credential")
        local = identity(s)
        return {"site_id": local.site_id, "site_name": local.display_name}

    @app.post("/api/v1/agent/enroll", tags=["agent"])
    def automatic_enrollment(payload: AutomaticAgentEnrollment, request: Request, s: Write):
        configured = discovery_settings()
        local = identity(s)
        now = int(utc_now().timestamp())
        if abs(now - payload.discovery_issued_at) > configured.discovery_ticket_seconds:
            raise HTTPException(401, "discovery ticket expired")
        expected = discovery_signature(
            configured.discovery_secret,
            local.site_id,
            payload.discovered_endpoint,
            payload.discovery_issued_at,
            payload.discovery_nonce,
        )
        if not secrets.compare_digest(expected, payload.discovery_signature):
            raise HTTPException(401, "invalid discovery ticket")
        device = s.scalar(select(Device).where(Device.agent_identity == payload.agent_id))
        if device is None:
            exact = s.scalars(
                select(Device).where(
                    func.lower(Device.display_name) == payload.machine_name.casefold(),
                    Device.agent_identity.is_(None),
                )
            ).all()
            device = exact[0] if len(exact) == 1 else None
        if device is None:
            device = Device(
                site_id=local.site_id,
                display_name=payload.device_name,
                enrollment_state="unassigned",
            )
            s.add(device)
            s.flush()
        device.agent_identity = payload.agent_id
        device.machine_name = payload.machine_name
        device.enrolled_at = device.enrolled_at or utc_now()
        device.revoked_at = None
        token = secrets.token_urlsafe(48)
        device.agent_token_hash = hashlib.sha256(token.encode()).hexdigest()
        assignment = s.scalar(
            select(DeviceAssignment).where(
                DeviceAssignment.device_id == device.device_id,
                DeviceAssignment.active.is_(True),
            )
        )
        room = s.get(Room, assignment.room_id) if assignment else None
        assigned = device.event_id is not None and (
            device.agent_role == "upload_kiosk" or room is not None
        )
        device.enrollment_state = "assigned" if assigned else "unassigned"
        runtime = s.get(DeviceRuntimeState, device.device_id)
        metadata = {
            "capabilities": payload.capabilities,
            "supported_roles": payload.supported_roles,
            "enrollment_remote_address": request.client.host if request.client else None,
        }
        if runtime is None:
            runtime = DeviceRuntimeState(
                device_id=device.device_id,
                connected_at=utc_now(),
                last_heartbeat_at=utc_now(),
                hostname=payload.machine_name,
                display_name=payload.device_name,
                windows_version=payload.windows_version,
                agent_version=payload.agent_version,
                metadata_json=metadata,
            )
            s.add(runtime)
        return {
            "site_id": local.site_id,
            "site_name": local.display_name,
            "device_id": device.device_id,
            "agent_credential": token,
            "assigned": assigned,
            "event_id": device.event_id,
            "event_name": s.get(Event, device.event_id).name if device.event_id else None,
            "room_id": room.room_id if room else None,
            "room_name": room.label if room else None,
            "role": {"room_agent": 1, "upload_kiosk": 2, "room_agent_kiosk": 3}.get(
                device.agent_role, 1
            ),
        }

    def envelope(s: Session, d: Device, after: int = -1):
        sequence = s.scalar(select(func.max(AgentChangeFeed.sequence))) or 0
        assignment = s.scalar(
            select(DeviceAssignment).where(
                DeviceAssignment.device_id == d.device_id, DeviceAssignment.active.is_(True)
            )
        )
        room = s.get(Room, assignment.room_id) if assignment else None
        event_id = d.event_id or (room.event_id if room else None)
        event = s.get(Event, event_id) if event_id else None
        if event is None:
            return {
                "site_id": d.site_id,
                "site_name": identity(s).display_name,
                "event_id": None,
                "event_name": None,
                "room_id": None,
                "room_name": None,
                "role": {"room_agent": 1, "upload_kiosk": 2, "room_agent_kiosk": 3}.get(
                    d.agent_role, 1
                ),
                "revisions": {
                    "schedule": sequence,
                    "presentations": sequence,
                    "branding": sequence,
                    "rotating_slides": sequence,
                },
                "sessions": [],
                "assets": [],
                "branding": {
                    "revision": 0,
                    "source": "Site Managed",
                    "event_name": "",
                    "assets": [],
                },
                "settings": None,
                "assigned": False,
            }
        changed = after < 0 or sequence > after
        room_rows = s.scalars(
            select(RoomAssignment)
            .join(ProgramSession, ProgramSession.session_id == RoomAssignment.session_id)
            .where(
                ProgramSession.event_id == event.event_id,
                RoomAssignment.active.is_(True),
                *([RoomAssignment.room_id == room.room_id] if room else []),
            )
        ).all()
        session_ids = {item.session_id for item in room_rows}
        sessions = (
            s.scalars(
                select(ProgramSession).where(
                    ProgramSession.event_id == event.event_id,
                    ProgramSession.session_id.in_(session_ids),
                )
            ).all()
            if session_ids
            else []
        )
        room_by_session = {item.session_id: item for item in room_rows}
        session_views = []
        for item in sessions if changed else []:
            presenters = [
                part.event_participation.display_name
                for part in item.participants
                if part.active and part.event_participation.display_name
            ]
            row_assignment = room_by_session[item.session_id]
            session_room = room or s.get(Room, row_assignment.room_id)
            session_views.append(
                {
                    "session_id": item.session_id,
                    "session_identifier": item.session_code,
                    "title": item.title,
                    "presenter": ", ".join(presenters) or None,
                    "room_id": session_room.room_id,
                    "room_name": session_room.label,
                    "starts_at": row_assignment.starts_at or item.starts_at,
                    "ends_at": row_assignment.ends_at or item.ends_at,
                    "cancelled": not item.active or str(item.status) == "cancelled",
                    "revision": item.revision,
                }
            )
        assets = []
        presentations = (
            s.scalars(
                select(Presentation).where(
                    Presentation.event_id == event.event_id,
                    Presentation.session_id.in_(session_ids),
                    Presentation.active.is_(True),
                )
            ).all()
            if session_ids and changed
            else []
        )
        for presentation in presentations:
            if not presentation.versions:
                continue
            version = max(presentation.versions, key=lambda value: value.version_number)
            asset = next(
                (value for value in version.assets if value.kind == AssetKind.ORIGINAL), None
            )
            media = s.get(MediaObject, asset.media_object_id) if asset else None
            if not asset or not media or media.availability != MediaAvailability.AVAILABLE:
                continue
            assets.append(
                {
                    "asset_id": asset.presentation_asset_id,
                    "kind": 0,
                    "version_id": version.presentation_version_id,
                    "presentation_id": presentation.presentation_id,
                    "session_id": presentation.session_id,
                    "room_id": room_by_session[presentation.session_id].room_id,
                    "event_day": None,
                    "rotation_scope": None,
                    "title": presentation.title,
                    "original_filename": asset.original_filename or media.original_filename,
                    "sha256": media.content_hash,
                    "size": media.size_bytes,
                    "download_uri": (
                        "/api/v1/agent/presentation-versions/"
                        f"{version.presentation_version_id}/download"
                    ),
                    "revision": version.revision,
                }
            )
        rotations = (
            s.scalars(
                select(RotationAssignment).where(
                    RotationAssignment.event_id == event.event_id,
                    RotationAssignment.active.is_(True),
                )
            ).all()
            if changed
            else []
        )
        for rotation in rotations:
            if rotation.scope == "room_day" and rotation.room_id != (
                room.room_id if room else None
            ):
                continue
            if rotation.scope == "session" and rotation.session_id not in session_ids:
                continue
            version = (
                s.get(PresentationVersion, rotation.presentation_version_id)
                if rotation.presentation_version_id
                else None
            )
            asset = (
                next((value for value in version.assets if value.kind == AssetKind.ORIGINAL), None)
                if version
                else None
            )
            media = s.get(MediaObject, asset.media_object_id) if asset else None
            if not asset or not media or media.availability != MediaAvailability.AVAILABLE:
                continue
            assets.append(
                {
                    "asset_id": rotation.rotation_assignment_id,
                    "kind": 1,
                    "version_id": version.presentation_version_id,
                    "presentation_id": version.presentation_id,
                    "session_id": rotation.session_id,
                    "room_id": rotation.room_id,
                    "event_day": rotation.event_day,
                    "rotation_scope": {"event_day": 0, "room_day": 1, "session": 2}[rotation.scope],
                    "title": "Rotating Slides",
                    "original_filename": asset.original_filename or media.original_filename,
                    "sha256": media.content_hash,
                    "size": media.size_bytes,
                    "download_uri": (
                        "/api/v1/agent/presentation-versions/"
                        f"{version.presentation_version_id}/download"
                    ),
                    "revision": rotation.revision,
                }
            )
        branding = s.get(EventBranding, event.event_id)
        branding_assets = []
        if branding and changed:
            for item in s.scalars(
                select(EventBrandingAsset).where(EventBrandingAsset.event_id == event.event_id)
            ):
                media = s.get(MediaObject, item.media_object_id)
                if media and media.availability == MediaAvailability.AVAILABLE:
                    branding_assets.append(
                        {
                            "slot": item.slot,
                            "original_filename": media.original_filename,
                            "sha256": media.content_hash,
                            "size": media.size_bytes,
                            "download_uri": (
                                "/api/v1/agent/branding-assets/"
                                f"{item.event_branding_asset_id}/download"
                            ),
                        }
                    )
        config = branding.configuration if branding else {}
        return {
            "site_id": d.site_id,
            "site_name": identity(s).display_name,
            "event_id": event.event_id,
            "event_name": event.name,
            "room_id": room.room_id if room else None,
            "room_name": room.label if room else None,
            "role": {"room_agent": 1, "upload_kiosk": 2, "room_agent_kiosk": 3}.get(
                d.agent_role, 1
            ),
            "revisions": {
                "schedule": sequence,
                "presentations": sequence,
                "branding": sequence,
                "rotating_slides": sequence,
            },
            "sessions": session_views,
            "assets": assets,
            "branding": {
                "revision": branding.revision if branding else 0,
                "source": "Site Local Override"
                if branding and branding.local_override
                else "Site Managed",
                "event_name": event.name,
                "accent_color": config.get("accent_color"),
                "primary_color": config.get("primary_color"),
                "welcome_message": config.get("welcome_message"),
                "upload_instructions": config.get("upload_instructions"),
                "footer": config.get("footer"),
                "assets": branding_assets,
            },
            "settings": None,
            "assigned": True,
        }

    @app.get("/api/v1/agent/bootstrap", tags=["agent"])
    def bootstrap_agent(s: Read, authorization: Annotated[str | None, Header()] = None):
        return envelope(s, agent(s, authorization))

    @app.get("/api/v1/agent/changes", tags=["agent"])
    def agent_changes(
        s: Read,
        schedule: int = 0,
        presentations: int = 0,
        branding: int = 0,
        rotating_slides: int = 0,
        authorization: Annotated[str | None, Header()] = None,
    ):
        return envelope(
            s, agent(s, authorization), min(schedule, presentations, branding, rotating_slides)
        )

    def stream_media(media: MediaObject):
        if settings is None:
            raise HTTPException(503, "Agent media transfer is not configured")
        config = settings()
        storage = AsyncMediaStorageClient(config.media_storage_url, config.media_storage_token)
        return StreamingResponse(
            storage.stream_object(
                media.storage_target_id, media.object_key, 0, media.size_bytes or 0
            ),
            media_type=media.mime_type or "application/octet-stream",
            headers={
                "Content-Disposition": (
                    f"attachment; filename*=UTF-8''{quote(media.original_filename)}"
                ),
                "Content-Length": str(media.size_bytes),
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.get("/api/v1/agent/presentation-versions/{version_id}/download", tags=["agent"])
    def agent_presentation_download(
        version_id: UUID, s: Read, authorization: Annotated[str | None, Header()] = None
    ):
        d = agent(s, authorization)
        version = s.get(PresentationVersion, version_id)
        presentation = s.get(Presentation, version.presentation_id) if version else None
        if not presentation or presentation.event_id != d.event_id:
            raise HTTPException(404, "presentation not assigned to Agent event")
        asset = next((item for item in version.assets if item.kind == AssetKind.ORIGINAL), None)
        media = s.get(MediaObject, asset.media_object_id) if asset else None
        if not media or media.availability != MediaAvailability.AVAILABLE:
            raise HTTPException(404, "presentation media unavailable")
        return stream_media(media)

    @app.get("/api/v1/agent/branding-assets/{asset_id}/download", tags=["agent"])
    def agent_branding_download(
        asset_id: UUID, s: Read, authorization: Annotated[str | None, Header()] = None
    ):
        d = agent(s, authorization)
        item = s.get(EventBrandingAsset, asset_id)
        if not item or item.event_id != d.event_id:
            raise HTTPException(404, "branding asset not assigned to Agent event")
        media = s.get(MediaObject, item.media_object_id)
        if not media or media.availability != MediaAvailability.AVAILABLE:
            raise HTTPException(404, "branding media unavailable")
        return stream_media(media)

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

    @app.put("/api/v1/devices/{device_id}/agent-configuration", tags=["devices"])
    def configure_agent(
        device_id: UUID, payload: AgentConfigurationWrite, request: Request, s: Write
    ):
        d = s.get(Device, device_id)
        event = s.get(Event, payload.event_id)
        if not d:
            raise HTTPException(404, "device not found")
        if not event or event.site_id != d.site_id:
            raise HTTPException(422, "event is not owned by this Site")
        d.event_id = event.event_id
        d.agent_role = payload.agent_role
        d.agent_configuration = payload.configuration
        d.revision += 1
        audit(
            s,
            d.site_id,
            request.state.site_user_id,
            "device.agent_configuration.updated",
            "device",
            d.device_id,
            {
                "event_id": d.event_id,
                "agent_role": d.agent_role,
                "configuration": d.agent_configuration,
            },
            event.event_id,
        )
        return {
            "device_id": d.device_id,
            "event_id": d.event_id,
            "agent_role": d.agent_role,
            "revision": d.revision,
        }

    @app.put("/api/v1/devices/{device_id}/room-agent-assignment", tags=["devices"])
    def assign_room_agent(
        device_id: UUID, payload: RoomAgentAssignmentWrite, request: Request, s: Write
    ):
        device = s.get(Device, device_id)
        event = s.get(Event, payload.event_id)
        room = s.get(Room, payload.room_id) if payload.room_id else None
        if device is None:
            raise HTTPException(404, "device not found")
        if event is None or event.site_id != device.site_id:
            raise HTTPException(422, "event is not owned by this Site")
        if payload.role != "upload_kiosk" and room is None:
            raise HTTPException(422, "Room Agent roles require a room")
        if room and (room.site_id != device.site_id or room.event_id not in {None, event.event_id}):
            raise HTTPException(422, "room is not available for this event")
        prior = s.scalar(
            select(DeviceAssignment).where(
                DeviceAssignment.device_id == device_id,
                DeviceAssignment.active.is_(True),
            )
        )
        if prior and (room is None or prior.room_id != room.room_id):
            prior.active = False
            prior.ends_at = utc_now()
            prior.revision += 1
        if room and (prior is None or prior.room_id != room.room_id):
            occupied = s.scalar(
                select(DeviceAssignment).where(
                    DeviceAssignment.room_id == room.room_id,
                    DeviceAssignment.role == EndpointRole.PRIMARY,
                    DeviceAssignment.active.is_(True),
                )
            )
            if occupied:
                raise HTTPException(409, "room already has a primary Agent")
            s.add(
                DeviceAssignment(
                    device_id=device_id,
                    room_id=room.room_id,
                    role=EndpointRole.PRIMARY,
                    starts_at=utc_now(),
                    active=True,
                )
            )
        device.event_id = event.event_id
        device.agent_role = payload.role
        device.enrollment_state = "assigned"
        device.revision += 1
        audit(
            s,
            device.site_id,
            request.state.site_user_id,
            "device.room_agent_assignment.updated",
            "device",
            device.device_id,
            {
                "event_id": event.event_id,
                "room_id": room.room_id if room else None,
                "role": payload.role,
            },
            event.event_id,
        )
        return {
            "device_id": device.device_id,
            "event_id": event.event_id,
            "room_id": room.room_id if room else None,
            "role": payload.role,
        }

    @app.put("/api/v1/events/{event_id}/branding", tags=["branding"])
    def update_branding(event_id: UUID, payload: EventBrandingWrite, request: Request, s: Write):
        event = s.get(Event, event_id)
        if not event:
            raise HTTPException(404, "event not found")
        item = s.get(EventBranding, event_id)
        if item is None:
            item = EventBranding(event_id=event_id, revision=1)
            s.add(item)
        else:
            item.revision += 1
        item.source = payload.source
        item.local_override = payload.local_override
        item.configuration = payload.configuration
        existing = {
            value.slot: value
            for value in s.scalars(
                select(EventBrandingAsset).where(EventBrandingAsset.event_id == event_id)
            )
        }
        for slot, media_id in payload.assets.items():
            media = s.get(MediaObject, media_id)
            if not media or media.event_id != event_id:
                raise HTTPException(422, f"branding asset {slot} is not event media")
            asset = existing.pop(slot, None)
            if asset is None:
                s.add(EventBrandingAsset(event_id=event_id, slot=slot, media_object_id=media_id))
            else:
                asset.media_object_id = media_id
                asset.revision += 1
        for removed in existing.values():
            s.delete(removed)
        audit(
            s,
            event.site_id,
            request.state.site_user_id,
            "event.branding.updated",
            "event_branding",
            event.event_id,
            {
                "source": item.source,
                "local_override": item.local_override,
                "revision": item.revision,
            },
            event.event_id,
        )
        return {"event_id": event_id, "revision": item.revision, "source": item.source}

    @app.get("/api/v1/events/{event_id}/branding", tags=["branding"])
    def get_branding(event_id: UUID, s: Read):
        event = s.get(Event, event_id)
        if not event:
            raise HTTPException(404, "event not found")
        item = s.get(EventBranding, event_id)
        assets = s.scalars(
            select(EventBrandingAsset).where(EventBrandingAsset.event_id == event_id)
        ).all()
        return {
            "event_id": event_id,
            "revision": item.revision if item else 0,
            "source": item.source if item else "central_default",
            "local_override": item.local_override if item else False,
            "configuration": item.configuration if item else {},
            "assets": {asset.slot: asset.media_object_id for asset in assets},
        }

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

    @app.get("/api/v1/review-sessions", tags=["reviews"])
    def review_sessions(
        s: Read,
        limit: Annotated[int, Query(ge=1, le=500)] = 200,
    ) -> dict[str, object]:
        rows = s.scalars(
            select(PresentationReviewSession)
            .order_by(PresentationReviewSession.opened_at.desc())
            .limit(limit)
        ).all()
        presentations = {
            item.presentation_id: item
            for item in s.scalars(
                select(Presentation).where(
                    Presentation.presentation_id.in_([row.presentation_id for row in rows])
                )
            )
        }
        rooms = {
            item.room_id: item
            for item in s.scalars(
                select(Room).where(Room.room_id.in_([row.room_id for row in rows]))
            )
        }
        devices = {
            item.device_id: item
            for item in s.scalars(
                select(Device).where(Device.device_id.in_([row.device_id for row in rows]))
            )
        }
        runtimes = {
            item.device_id: item
            for item in s.scalars(
                select(DeviceRuntimeState).where(
                    DeviceRuntimeState.device_id.in_([row.device_id for row in rows])
                )
            )
        }
        return {
            "items": [
                {
                    "review_session_id": row.review_session_id,
                    "presentation_id": row.presentation_id,
                    "presentation_title": presentations.get(row.presentation_id).title
                    if presentations.get(row.presentation_id)
                    else None,
                    "base_presentation_version_id": row.base_presentation_version_id,
                    "device_id": row.device_id,
                    "device_name": devices.get(row.device_id).display_name
                    if devices.get(row.device_id)
                    else None,
                    "device_hostname": runtimes.get(row.device_id).hostname
                    if runtimes.get(row.device_id)
                    else None,
                    "room_id": row.room_id,
                    "room_label": rooms.get(row.room_id).label if rooms.get(row.room_id) else None,
                    "state": row.state,
                    "opened_at": row.opened_at,
                    "local_changes_at": row.local_changes_at,
                    "working_filename": row.working_filename,
                    "working_size_bytes": row.working_size_bytes,
                    "working_sha256": row.working_sha256,
                    "working_modified_at": row.working_modified_at,
                    "saveback_version_id": row.saveback_version_id,
                    "conflict_version_id": row.conflict_version_id,
                    "completed_at": row.completed_at,
                    "last_error": runtimes.get(row.device_id).last_error
                    if runtimes.get(row.device_id)
                    else None,
                }
                for row in rows
            ],
            "total": len(rows),
        }

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
