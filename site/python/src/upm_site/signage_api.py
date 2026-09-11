"""Authenticated, public-data-only Site projection for independent Signage stacks."""

import secrets
from collections.abc import Callable, Iterator
from typing import Annotated
from uuid import NAMESPACE_URL, UUID, uuid5

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from upm_site.persistence.models import (
    AgentChangeFeed,
    Event,
    LocalSiteIdentity,
    Room,
    RoomAssignment,
    User,
)
from upm_site.persistence.models import Session as ProgramSession

SCHEMA_VERSION = 1


def register_signage_routes(app: FastAPI, read_db: Callable[[], Iterator[Session]], settings):
    Read = Annotated[Session, Depends(read_db)]

    def authorize(value):
        configured = settings().signage_projection_token
        supplied = value[7:] if value and value.startswith("Bearer ") else ""
        if not configured or not secrets.compare_digest(configured, supplied):
            raise HTTPException(401, "invalid Signage service credential")

    @app.get("/api/v1/signage/status", tags=["signage"])
    async def status():
        """Resolve the Site-managed Signage entry point without exposing credentials."""
        configured = settings()
        if not configured.signage_internal_url or not configured.signage_public_url:
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
                "url": configured.signage_public_url,
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
        s: Read,
        cursor: Annotated[int, Query(ge=0)] = 0,
        authorization: Annotated[str | None, Header()] = None,
    ):
        authorize(authorization)
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
