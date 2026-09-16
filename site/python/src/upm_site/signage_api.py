"""Authenticated, public-data-only Site projection for independent Signage stacks."""

import hashlib
import secrets
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated
from uuid import NAMESPACE_URL, UUID, uuid5

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from upm_site.persistence.models import (
    AgentChangeFeed,
    Event,
    LocalSiteIdentity,
    Room,
    RoomAssignment,
    RotationAssignment,
    SignageInstallation,
    User,
)
from upm_site.persistence.models import Session as ProgramSession

SCHEMA_VERSION = 1


class SignageEnrollment(BaseModel):
    installation_id: UUID
    display_name: str = Field(min_length=1, max_length=255)
    version: str = Field(min_length=1, max_length=64)


def register_signage_routes(
    app: FastAPI,
    write_db: Callable[[], Iterator[Session]],
    settings,
):
    Write = Annotated[Session, Depends(write_db)]

    def authorize(s: Session, value):
        configured = settings().signage_projection_token
        supplied = value[7:] if value and value.startswith("Bearer ") else ""
        digest = hashlib.sha256(supplied.encode()).hexdigest() if supplied else ""
        installation = s.scalar(
            select(SignageInstallation).where(
                SignageInstallation.credential_hash == digest,
                SignageInstallation.revoked_at.is_(None),
            )
        )
        legacy = configured and secrets.compare_digest(configured, supplied)
        if not installation and not legacy:
            raise HTTPException(401, "invalid Signage service credential")
        if installation:
            installation.last_seen_at = datetime.now(UTC)
        return installation

    def enrollment_secret() -> str:
        path = settings().signage_enrollment_secret_file
        try:
            secret = Path(path or "").read_text().strip()
        except OSError as error:
            raise HTTPException(503, "automatic Signage enrollment is unavailable") from error
        if len(secret) < 32:
            raise HTTPException(503, "automatic Signage enrollment is unavailable")
        return secret

    @app.post("/api/v1/signage/service/enroll", tags=["signage"])
    def enroll(
        body: SignageEnrollment,
        s: Write,
        x_upm_signage_enrollment: Annotated[str | None, Header()] = None,
        authorization: Annotated[str | None, Header()] = None,
    ):
        local = s.scalar(select(LocalSiteIdentity))
        if not local:
            raise HTTPException(503, "Site identity is unavailable")
        existing = s.get(SignageInstallation, body.installation_id)
        supplied = (
            authorization[7:] if authorization and authorization.startswith("Bearer ") else ""
        )
        existing_proof = bool(
            existing
            and supplied
            and secrets.compare_digest(
                existing.credential_hash, hashlib.sha256(supplied.encode()).hexdigest()
            )
        )
        bootstrap_proof = secrets.compare_digest(
            x_upm_signage_enrollment or "", enrollment_secret()
        )
        if existing and existing.revoked_at:
            raise HTTPException(403, "Signage installation is revoked")
        if existing and not existing_proof and not bootstrap_proof:
            raise HTTPException(401, "existing Signage credential proof required")
        if not existing and not bootstrap_proof:
            raise HTTPException(401, "invalid Signage enrollment proof")
        credential = secrets.token_urlsafe(48)
        if existing is None:
            existing = SignageInstallation(
                installation_id=body.installation_id,
                site_id=local.site_id,
                display_name=body.display_name,
                credential_hash="",
            )
            s.add(existing)
        else:
            existing.credential_revision += 1
        existing.display_name = body.display_name
        existing.credential_hash = hashlib.sha256(credential.encode()).hexdigest()
        existing.last_seen_at = datetime.now(UTC)
        s.flush()
        return {
            "site_id": local.site_id,
            "site_name": local.display_name,
            "credential": credential,
            "credential_revision": existing.credential_revision,
        }

    @app.post("/api/v1/signage/installations/{installation_id}/revoke", tags=["signage"])
    def revoke(installation_id: UUID, s: Write):
        installation = s.get(SignageInstallation, installation_id)
        if not installation:
            raise HTTPException(404, "Signage installation not found")
        installation.revoked_at = datetime.now(UTC)
        return {"installation_id": installation_id, "revoked": True}

    @app.get("/api/v1/signage/service/events", tags=["signage"])
    def event_catalog(s: Write, authorization: Annotated[str | None, Header()] = None):
        authorize(s, authorization)
        return [
            {
                "event_id": event.event_id,
                "name": event.name,
                "starts_at": event.starts_at,
                "ends_at": event.ends_at,
                "timezone": event.timezone,
                "active": event.archived_at is None,
                "archived_at": event.archived_at,
                "revision": event.revision,
            }
            for event in s.scalars(select(Event).order_by(Event.starts_at, Event.name))
        ]

    @app.get("/api/v1/signage/status", tags=["signage"])
    async def status(request: Request):
        """Resolve the Site-managed Signage entry point without exposing credentials."""
        configured = settings()
        public_url = configured.signage_public_url or f"https://{request.url.hostname}:8445"
        if not configured.signage_internal_url:
            return {
                "installed": False,
                "healthy": False,
                "message": "Signage service is not installed on this Site.",
            }
        try:
            async with httpx.AsyncClient(timeout=3) as client:
                response = await client.get(configured.signage_internal_url.rstrip("/") + "/health")
                response.raise_for_status()
            health = response.json()
            return {
                "installed": True,
                "healthy": True,
                "url": public_url,
                "source_connected": bool(health.get("source_connected")),
            }
        except (httpx.HTTPError, ValueError):
            return {
                "installed": True,
                "healthy": False,
                "url": configured.signage_public_url,
                "message": "Signage service is installed but unavailable.",
            }

    @app.get("/api/v1/signage/projection", tags=["signage"])
    def projection(
        event_id: UUID,
        s: Write,
        cursor: Annotated[int, Query(ge=0)] = 0,
        authorization: Annotated[str | None, Header()] = None,
    ):
        authorize(s, authorization)
        identity = s.scalar(select(LocalSiteIdentity))
        event = s.get(Event, event_id)
        if not identity or not event or event.site_id != identity.site_id:
            raise HTTPException(404, "event not found")
        generation = s.scalar(select(func.max(AgentChangeFeed.sequence))) or 0
        rooms = {r.room_id: r for r in s.scalars(select(Room).where(Room.event_id == event_id))}
        assignments = {
            a.session_id: a
            for a in s.scalars(
                select(RoomAssignment)
                .join(ProgramSession)
                .where(ProgramSession.event_id == event_id, RoomAssignment.active.is_(True))
            )
        }
        items = []
        for user in s.scalars(select(User).where(User.active.is_(True), User.web_access.is_(True))):
            if user.web_password_hash and "administrator" in user.roles:
                items.append(
                    {
                        "type": "operator_user",
                        "id": user.central_user_id or user.user_id,
                        "event_id": event_id,
                        "tombstone": False,
                        "data": {
                            "user_id": str(user.central_user_id or user.user_id),
                            "username": user.username,
                            "normalized_username": user.normalized_username,
                            "display_name": user.display_name,
                            # A one-way scrypt verifier crosses only this authenticated
                            # service boundary; plaintext credentials never do.
                            "password_verifier": user.web_password_hash,
                            "roles": user.roles,
                        },
                    }
                )
        for room in rooms.values():
            items.append(
                {
                    "type": "room",
                    "id": room.room_id,
                    "event_id": event_id,
                    "tombstone": not room.enabled or room.archived_at is not None,
                    "data": {"room_id": str(room.room_id), "name": room.label},
                }
            )
        for rotation in s.scalars(
            select(RotationAssignment).where(RotationAssignment.event_id == event_id)
        ):
            items.append(
                {
                    "type": "rotation",
                    "id": rotation.rotation_assignment_id,
                    "event_id": event_id,
                    "tombstone": not rotation.active,
                    "data": {
                        "rotation_assignment_id": str(rotation.rotation_assignment_id),
                        "event_day": rotation.event_day.isoformat(),
                        "scope": rotation.scope,
                        "room_id": str(rotation.room_id) if rotation.room_id else None,
                        "session_id": str(rotation.session_id) if rotation.session_id else None,
                        "presentation_version_id": str(rotation.presentation_version_id)
                        if rotation.presentation_version_id
                        else None,
                        "source_authority": rotation.source_authority,
                    },
                }
            )
        for row in s.scalars(select(ProgramSession).where(ProgramSession.event_id == event_id)):
            assignment = assignments.get(row.session_id)
            room = rooms.get(assignment.room_id) if assignment else None
            presenters = [
                p.event_participation.display_name
                for p in row.participants
                if p.active and p.event_participation.display_name
            ]
            items.append(
                {
                    "type": "session",
                    "id": row.session_id,
                    "event_id": event_id,
                    "tombstone": not row.active or str(row.status) == "cancelled",
                    "data": {
                        "session_id": str(row.session_id),
                        "event_id": str(event_id),
                        "event_name": event.name,
                        "event_timezone": event.timezone,
                        "title": row.title,
                        "presenters": presenters,
                        "room_id": str(room.room_id) if room else None,
                        "room_name": room.label if room else None,
                        "starts_at": (assignment.starts_at or row.starts_at).isoformat()
                        if (assignment and assignment.starts_at) or row.starts_at
                        else None,
                        "ends_at": (assignment.ends_at or row.ends_at).isoformat()
                        if (assignment and assignment.ends_at) or row.ends_at
                        else None,
                        "cancelled": not row.active or str(row.status) == "cancelled",
                    },
                }
            )
        return {
            "schema_version": SCHEMA_VERSION,
            "source_site_id": identity.site_id,
            "source_instance_id": uuid5(
                NAMESPACE_URL,
                f"upm-site:{identity.site_id}:{event.created_at.isoformat()}",
            ),
            "event_id": event_id,
            "snapshot_generation": generation,
            "committed_cursor": generation,
            "full_resync": cursor == 0
            or cursor > generation
            or cursor < max(0, generation - 10000),
            "history_expires_before": max(0, generation - 10000),
            "items": items,
        }
