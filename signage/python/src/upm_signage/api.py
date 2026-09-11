import hashlib
import hmac
import secrets
from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import Cookie, Depends, FastAPI, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .auth import authenticate, bootstrap, create_session, digest, hash_password, resolve, verify
from .config import Settings
from .db import factory
from .models import (
    Display,
    Layout,
    LayoutRevision,
    OperatorSession,
    OperatorUser,
    PlaybackState,
    Projection,
    Publication,
    Source,
)
from .scheduler import room_door

ELEMENT_TYPES = {
    "room_name",
    "current_session_title",
    "session_time",
    "presenter_names",
    "next_session_title",
    "next_session_time",
    "countdown",
    "event_logo",
    "room_logo",
    "custom_text",
    "date_time",
    "ticker",
    "qr_code",
    "image",
    "rotating_slides",
}


class Login(BaseModel):
    username: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1, max_length=1024)


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=1024)
    new_password: str = Field(min_length=8, max_length=1024)


class Pair(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    width: int = Field(ge=320, le=16384)
    height: int = Field(ge=240, le=16384)
    orientation: Literal["landscape", "portrait", "custom"]
    mode: Literal["room_door", "lobby", "playlist"]
    event_id: UUID | None = None
    room_id: UUID | None = None

    @model_validator(mode="after")
    def assignment(self):
        if self.mode == "room_door" and (not self.event_id or not self.room_id):
            raise ValueError("Room-door displays require an event and room selection")
        return self


class Assignment(BaseModel):
    event_id: UUID | None = None
    room_id: UUID | None = None
    mode: Literal["room_door", "lobby", "playlist"]


class Heartbeat(BaseModel):
    media_ready: bool
    current_item: str | None = None
    error: str | None = None


class Element(BaseModel):
    element_id: UUID
    type: str
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    z_index: int = 0
    visibility: bool = True
    locked: bool = False
    rotation: float = Field(default=0, ge=-360, le=360)
    opacity: float = Field(default=1, ge=0, le=1)
    style: dict = Field(default_factory=dict)
    data_binding: dict = Field(default_factory=dict)
    fallback_value: str = ""


class LayoutWrite(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str = Field(default="", max_length=4000)
    event_id: UUID | None = None
    mode: Literal["room_door", "lobby", "playlist", "template"] = "room_door"
    width: int = Field(ge=320, le=16384)
    height: int = Field(ge=240, le=16384)
    orientation: Literal["landscape", "portrait", "custom"]
    safe_area: dict = Field(default_factory=dict)
    background: dict = Field(default_factory=dict)
    elements: list[Element] = Field(default_factory=list, max_length=250)
    configuration: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def valid_elements(self):
        ids = set()
        for item in self.elements:
            if item.type not in ELEMENT_TYPES:
                raise ValueError(f"Unsupported element type: {item.type}")
            if item.element_id in ids:
                raise ValueError("Element identifiers must be unique")
            ids.add(item.element_id)
            if item.x + item.width > self.width or item.y + item.height > self.height:
                raise ValueError("Elements must remain inside the logical canvas")
        return self


def create_app(*, settings: Settings | None = None, session_factory=None):
    configured = settings or Settings()
    sessions = session_factory or factory(configured)
    with sessions.begin() as s:
        bootstrap(s, configured.bootstrap_admin_username, configured.bootstrap_admin_password)
    app = FastAPI(title="UPM Signage API", version="1")

    def read():
        with sessions() as s:
            yield s

    def write():
        with sessions.begin() as s:
            yield s

    @app.middleware("http")
    async def browser_auth(request: Request, call_next):
        path = request.url.path
        public = (
            path == "/health" or path == "/api/v1/auth/login" or path.startswith("/api/v1/player/")
        )
        if public:
            return await call_next(request)
        with sessions() as s:
            item, user = resolve(s, request.cookies.get("upm_signage_session"))
        if not item or not user:
            return Response(
                content='{"detail":"authentication required"}',
                status_code=401,
                media_type="application/json",
            )
        if request.method not in {"GET", "HEAD", "OPTIONS"} and not hmac.compare_digest(
            item.csrf_hash, digest(request.headers.get("X-CSRF-Token", ""))
        ):
            return Response(
                content='{"detail":"invalid CSRF token"}',
                status_code=403,
                media_type="application/json",
            )
        request.state.signage_user_id = user.user_id
        return await call_next(request)

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

    @app.get("/health")
    def health(s: Annotated[Session, Depends(read)]):
        source = s.scalar(select(Source))
        displays = list(s.scalars(select(Display)))
        online = (
            s.scalar(
                select(func.count())
                .select_from(PlaybackState)
                .where(PlaybackState.player_online.is_(True))
            )
            or 0
        )
        return {
            "service": "upm-signage",
            "status": "playback-ready",
            "source_connected": bool(source and source.last_sync_at and not source.last_error),
            "last_sync_at": source.last_sync_at if source else None,
            "displays": {"configured": len(displays), "online": online},
        }

    @app.post("/api/v1/auth/login")
    def login(body: Login, response: Response, s: Annotated[Session, Depends(write)]):
        user = authenticate(s, body.username, body.password)
        if not user:
            raise HTTPException(401, "invalid username or password")
        token, csrf = create_session(s, user, configured.session_hours)
        response.set_cookie(
            "upm_signage_session",
            token,
            httponly=True,
            secure=configured.session_cookie_secure,
            samesite="lax",
            path="/",
            max_age=configured.session_hours * 3600,
        )
        return {
            "authenticated": True,
            "csrf_token": csrf,
            "user": {
                "user_id": user.user_id,
                "username": user.username,
                "display_name": user.display_name,
                "roles": user.roles,
            },
        }

    @app.get("/api/v1/auth/session")
    def session_view(request: Request, s: Annotated[Session, Depends(write)]):
        item, user = resolve(s, request.cookies.get("upm_signage_session"))
        csrf = secrets.token_urlsafe(32)
        item.csrf_hash = digest(csrf)
        return {
            "authenticated": True,
            "csrf_token": csrf,
            "user": {
                "user_id": user.user_id,
                "username": user.username,
                "display_name": user.display_name,
                "roles": user.roles,
            },
        }

    @app.put("/api/v1/auth/password", status_code=204)
    def change_password(
        body: PasswordChange,
        request: Request,
        s: Annotated[Session, Depends(write)],
    ):
        user = s.get(OperatorUser, request.state.signage_user_id)
        if not user or not verify(body.current_password, user.password_hash):
            raise HTTPException(401, "current password is invalid")
        user.password_hash = hash_password(body.new_password)
        for item in s.scalars(
            select(OperatorSession).where(OperatorSession.user_id == user.user_id)
        ):
            item.revoked_at = datetime.now(UTC)

    @app.post("/api/v1/auth/logout", status_code=204)
    def logout(
        response: Response,
        token: Annotated[str | None, Cookie(alias="upm_signage_session")] = None,
        s: Annotated[Session, Depends(write)] = None,
    ):
        item, _ = resolve(s, token)
        if item:
            item.revoked_at = datetime.now(UTC)
        response.delete_cookie("upm_signage_session", path="/")

    @app.get("/api/v1/events")
    def events(s: Annotated[Session, Depends(read)]):
        rows = list(
            s.scalars(
                select(Projection).where(
                    Projection.entity_type == "session", Projection.tombstone.is_(False)
                )
            )
        )
        values = {
            str(x.event_id): {
                "event_id": x.event_id,
                "name": x.payload.get("event_name", "Unnamed event"),
                "timezone": x.payload.get("event_timezone"),
                "site_name": x.payload.get("site_name"),
            }
            for x in rows
        }
        return sorted(values.values(), key=lambda x: x["name"].casefold())

    @app.get("/api/v1/events/{event_id}/rooms")
    def rooms(event_id: UUID, s: Annotated[Session, Depends(read)]):
        return [
            dict(x.payload, room_id=x.entity_id)
            for x in s.scalars(
                select(Projection)
                .where(
                    Projection.event_id == event_id,
                    Projection.entity_type == "room",
                    Projection.tombstone.is_(False),
                )
                .order_by(Projection.entity_id)
            )
        ]

    @app.get("/api/v1/events/{event_id}/schedule")
    def schedule(event_id: UUID, s: Annotated[Session, Depends(read)]):
        return [
            x.payload
            for x in s.scalars(
                select(Projection).where(
                    Projection.event_id == event_id,
                    Projection.entity_type == "session",
                    Projection.tombstone.is_(False),
                )
            )
        ]

    def display_view(row):
        return {
            "display_id": row.display_id,
            "name": row.name,
            "event_id": row.event_id,
            "room_id": row.room_id,
            "mode": row.mode,
            "width": row.width,
            "height": row.height,
            "orientation": row.orientation,
            "enabled": row.enabled,
        }

    @app.get("/api/v1/displays")
    def displays(s: Annotated[Session, Depends(read)]):
        return [display_view(x) for x in s.scalars(select(Display).order_by(Display.name))]

    @app.post("/api/v1/displays", status_code=201)
    def create_display(body: Pair, s: Annotated[Session, Depends(write)]):
        token = secrets.token_urlsafe(48)
        row = Display(
            name=body.name,
            width=body.width,
            height=body.height,
            orientation=body.orientation,
            aspect_ratio=f"{body.width}:{body.height}",
            mode=body.mode,
            event_id=body.event_id,
            room_id=body.room_id,
            credential_hash=hashlib.sha256(token.encode()).hexdigest(),
        )
        s.add(row)
        s.flush()
        # Credential is returned exactly once to the authenticated native shell and never listed.
        return {**display_view(row), "player_credential": token}

    @app.put("/api/v1/displays/{display_id}/assignment")
    def assign(display_id: UUID, body: Assignment, s: Annotated[Session, Depends(write)]):
        row = s.get(Display, display_id)
        if not row:
            raise HTTPException(404, "display not found")
        if body.mode == "room_door" and (not body.event_id or not body.room_id):
            raise HTTPException(422, "Room-door displays require event and room selections")
        row.event_id, row.room_id, row.mode = body.event_id, body.room_id, body.mode
        return display_view(row)

    @app.post("/api/v1/displays/{display_id}/unpair", status_code=204)
    def unpair(display_id: UUID, s: Annotated[Session, Depends(write)]):
        row = s.get(Display, display_id)
        if not row:
            raise HTTPException(404, "display not found")
        row.enabled = False
        row.credential_hash = hashlib.sha256(secrets.token_bytes(64)).hexdigest()

    def layout_view(row):
        return {
            "layout_id": row.layout_id,
            "event_id": row.event_id,
            "name": row.name,
            "description": row.description,
            "mode": row.mode,
            "width": row.width,
            "height": row.height,
            "orientation": row.orientation,
            "safe_area": row.safe_area,
            "background": row.background,
            "elements": row.elements,
            "configuration": row.configuration,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
            "created_by": row.created_by,
            "draft_revision": row.draft_revision,
            "published_revision": row.published_revision,
            "status": row.status,
        }

    @app.get("/api/v1/layouts")
    def layouts(s: Annotated[Session, Depends(read)]):
        return [layout_view(x) for x in s.scalars(select(Layout).order_by(Layout.name))]

    @app.get("/api/v1/layouts/{layout_id}")
    def get_layout(layout_id: UUID, s: Annotated[Session, Depends(read)]):
        row = s.get(Layout, layout_id)
        if not row:
            raise HTTPException(404, "layout not found")
        return layout_view(row)

    def apply(row, body, user_id):
        now = datetime.now(UTC)
        values = body.model_dump(mode="json")
        for key, value in values.items():
            setattr(row, key, value)
        row.template = body.mode
        row.aspect_ratio = f"{body.width}:{body.height}"
        row.updated_at = now
        row.created_by = row.created_by or user_id

    @app.post("/api/v1/layouts", status_code=201)
    def create_layout(body: LayoutWrite, request: Request, s: Annotated[Session, Depends(write)]):
        now = datetime.now(UTC)
        row = Layout(
            name=body.name,
            template=body.mode,
            aspect_ratio=f"{body.width}:{body.height}",
            created_at=now,
            updated_at=now,
        )
        apply(row, body, request.state.signage_user_id)
        s.add(row)
        s.flush()
        return layout_view(row)

    @app.put("/api/v1/layouts/{layout_id}")
    def save_layout(
        layout_id: UUID, body: LayoutWrite, request: Request, s: Annotated[Session, Depends(write)]
    ):
        row = s.get(Layout, layout_id)
        if not row:
            raise HTTPException(404, "layout not found")
        apply(row, body, request.state.signage_user_id)
        row.draft_revision += 1
        row.status = "draft"
        s.flush()
        return layout_view(row)

    @app.post("/api/v1/layouts/{layout_id}/publish")
    def publish_layout(layout_id: UUID, request: Request, s: Annotated[Session, Depends(write)]):
        row = s.get(Layout, layout_id)
        if not row:
            raise HTTPException(404, "layout not found")
        revision = (row.published_revision or 0) + 1
        snapshot = layout_view(row)
        snapshot["published_revision"] = revision
        s.add(
            LayoutRevision(
                layout_id=row.layout_id,
                revision=revision,
                snapshot=snapshot,
                published_at=datetime.now(UTC),
                published_by=request.state.signage_user_id,
            )
        )
        row.published_revision = revision
        row.status = "published"
        row.updated_at = datetime.now(UTC)
        for display in s.scalars(
            select(Display).where(Display.event_id == row.event_id, Display.enabled.is_(True))
        ):
            publication = s.get(Publication, display.display_id) or Publication(
                display_id=display.display_id
            )
            s.add(publication)
            publication.previous_manifest = publication.active_manifest
            publication.revision += 1
            publication.active_manifest = {
                "schema_version": 1,
                "generation": publication.revision,
                "layout": snapshot,
                "display": display_view(display),
            }
            publication.activated_at = datetime.now(UTC)
        return layout_view(row)

    @app.post("/api/v1/layouts/{layout_id}/rollback")
    def rollback(layout_id: UUID, s: Annotated[Session, Depends(write)]):
        row = s.get(Layout, layout_id)
        if not row or not row.published_revision or row.published_revision < 2:
            raise HTTPException(409, "No previous published revision")
        target = s.scalar(
            select(LayoutRevision).where(
                LayoutRevision.layout_id == layout_id,
                LayoutRevision.revision == row.published_revision - 1,
            )
        )
        if not target:
            raise HTTPException(409, "Previous revision unavailable")
        row.published_revision = target.revision
        row.status = "published"
        for publication in s.scalars(
            select(Publication)
            .join(Display, Publication.display_id == Display.display_id)
            .where(Display.event_id == row.event_id)
        ):
            publication.active_manifest, publication.previous_manifest = (
                publication.previous_manifest,
                publication.active_manifest,
            )
            publication.revision += 1
        return layout_view(row)

    @app.get("/api/v1/player/manifest")
    def manifest(
        s: Annotated[Session, Depends(read)],
        authorization: Annotated[str | None, Header()] = None,
        at: datetime | None = None,
    ):
        display = player(s, authorization)
        publication = s.get(Publication, display.display_id)
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
        scheduled = (
            room_door(rows, at or datetime.now(UTC), str(display.room_id))
            if display.mode == "room_door"
            else {}
        )
        if publication:
            return {**publication.active_manifest, "schedule": scheduled}
        return {
            "schema_version": 1,
            "generation": 0,
            "mode": display.mode,
            "display": display_view(display),
            "schedule": scheduled,
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
