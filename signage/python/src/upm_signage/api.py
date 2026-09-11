import hashlib
import secrets
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import Settings
from .db import factory
from .models import Display, PlaybackState, Projection, Publication, Source
from .scheduler import room_door


class Pair(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    aspect_ratio: str = Field(pattern=r"^(9:16|16:9)$")
    mode: str = Field(pattern=r"^(room_door|playlist)$")
    event_id: UUID | None = None
    room_id: UUID | None = None


class Heartbeat(BaseModel):
    media_ready: bool
    current_item: str | None = None
    error: str | None = None


def create_app():
    settings = Settings()
    sessions = factory(settings)
    app = FastAPI(title="UPM Signage API", version="1")

    def read():
        with sessions() as s:
            yield s

    def write():
        with sessions.begin() as s:
            yield s

    def player(s: Session, authorization: str | None):
        token = authorization[7:] if authorization and authorization.startswith("Bearer ") else ""
        row = (
            s.scalar(
                select(Display).where(
                    Display.credential_hash == hashlib.sha256(token.encode()).hexdigest(),
                    Display.enabled.is_(True),
                )
            )
            if token
            else None
        )
        if not row:
            raise HTTPException(401, "invalid player credential")
        return row

    def operator(password: str | None):
        if not secrets.compare_digest(password or "", settings.operator_password):
            raise HTTPException(401, "operator password required")

    @app.get("/health")
    def health(s: Annotated[Session, Depends(read)]):
        source = s.scalar(select(Source))
        return {
            "service": "upm-signage",
            "status": "playback-ready",
            "source_connected": bool(source and source.last_sync_at and not source.last_error),
        }

    @app.post("/api/v1/displays/pair", status_code=201)
    def pair(
        body: Pair,
        s: Annotated[Session, Depends(write)],
        x_upm_operator_password: Annotated[str | None, Header()] = None,
    ):
        operator(x_upm_operator_password)
        token = secrets.token_urlsafe(48)
        row = Display(
            name=body.name,
            aspect_ratio=body.aspect_ratio,
            mode=body.mode,
            event_id=body.event_id,
            room_id=body.room_id,
            credential_hash=hashlib.sha256(token.encode()).hexdigest(),
        )
        s.add(row)
        s.flush()
        return {"display_id": row.display_id, "player_credential": token}

    @app.get("/api/v1/player/manifest")
    def manifest(
        s: Annotated[Session, Depends(read)],
        authorization: Annotated[str | None, Header()] = None,
        at: datetime | None = None,
    ):
        display = player(s, authorization)
        publication = s.get(Publication, display.display_id)
        if publication:
            return publication.active_manifest
        if display.mode == "playlist":
            return {"schema_version": 1, "mode": "playlist", "items": []}
        rows = [
            x.payload
            for x in s.scalars(
                select(Projection).where(
                    Projection.event_id == display.event_id,
                    Projection.entity_type == "session",
                    Projection.tombstone.is_(False),
                )
            )
        ]
        scheduled = room_door(rows, at or datetime.now(UTC), str(display.room_id))
        return {
            "schema_version": 1,
            "mode": "room_door",
            "aspect_ratio": display.aspect_ratio,
            "event_id": display.event_id,
            "room_id": display.room_id,
            **scheduled,
        }

    @app.post("/api/v1/player/heartbeat")
    def heartbeat(
        body: Heartbeat,
        s: Annotated[Session, Depends(write)],
        authorization: Annotated[str | None, Header()] = None,
    ):
        display = player(s, authorization)
        row = s.get(PlaybackState, display.display_id) or PlaybackState(
            display_id=display.display_id
        )
        s.add(row)
        row.player_online = True
        row.media_ready = body.media_ready
        row.current_item = body.current_item
        row.last_error = body.error
        row.last_heartbeat_at = datetime.now(UTC)
        return {"received": True}

    return app
