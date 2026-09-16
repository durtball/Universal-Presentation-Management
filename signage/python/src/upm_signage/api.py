import base64
import hashlib
import hmac
import io
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

import qrcode
from fastapi import (
    Cookie,
    Depends,
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    Request,
    Response,
    UploadFile,
)
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from upm_shared.identifiers import new_uuid7

from .auth import authenticate, bootstrap, create_session, digest, hash_password, resolve, verify
from .config import Settings
from .db import factory
from .models import (
    Asset,
    Display,
    Installation,
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
    width: int | None = Field(default=None, ge=320, le=16384)
    height: int | None = Field(default=None, ge=240, le=16384)
    orientation: Literal["landscape", "portrait", "custom"] | None = None
    layout_id: UUID | None = None


class Heartbeat(BaseModel):
    media_ready: bool
    current_item: str | None = None
    error: str | None = None
    player_version: str | None = Field(default=None, max_length=64)


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
            asset_id = item.data_binding.get("asset_id")
            if asset_id:
                try:
                    UUID(str(asset_id))
                except ValueError as error:
                    raise ValueError("Asset binding must contain a valid identifier") from error
        return self


class TemplateApply(BaseModel):
    event_id: UUID
    room_id: UUID | None = None
    name: str | None = Field(default=None, max_length=255)


def create_app(*, settings: Settings | None = None, session_factory=None):
    configured = settings or Settings()
    sessions = session_factory or factory(configured)
    with sessions.begin() as s:
        bootstrap(s, configured.bootstrap_admin_username, configured.bootstrap_admin_password)
        if not s.scalar(select(Installation).where(Installation.singleton_key == 1)):
            s.add(Installation(singleton_key=1))
        if not s.scalar(select(Layout).where(Layout.mode == "template")):
            administrator = s.scalar(select(OperatorUser).order_by(OperatorUser.username))
            now = datetime.now(UTC)
            for name, width, height in [
                ("Room Door Portrait", 1080, 1920),
                ("Room Schedule Landscape", 1920, 1080),
            ]:
                elements = [
                    {
                        "element_id": str(new_uuid7()),
                        "type": element_type,
                        "x": 60,
                        "y": y,
                        "width": width - 120,
                        "height": 150,
                        "z_index": index,
                        "visibility": True,
                        "locked": False,
                        "rotation": 0,
                        "opacity": 1,
                        "style": {
                            "font_family": "Segoe UI",
                            "font_size": size,
                            "font_weight": 700,
                            "color": "#ffffff",
                            "text_align": "left",
                            "line_height": 1.15,
                        },
                        "data_binding": {"field": element_type},
                        "fallback_value": label,
                    }
                    for index, (element_type, label, y, size) in enumerate(
                        [
                            ("room_name", "Room name", 80, 72),
                            ("current_session_title", "Current session", 360, 60),
                            ("presenter_names", "Presenter names", 560, 40),
                            ("next_session_title", "Next session", height - 260, 38),
                            ("date_time", "Date and time", height - 100, 28),
                        ]
                    )
                ]
                s.add(
                    Layout(
                        name=name,
                        description="Editable UPM starter template",
                        mode="template",
                        template="template",
                        aspect_ratio=f"{width}:{height}",
                        width=width,
                        height=height,
                        orientation="portrait" if height > width else "landscape",
                        safe_area={"top": 50, "right": 50, "bottom": 50, "left": 50},
                        background={"color": "#101a2c"},
                        elements=elements,
                        configuration={"grid": True, "snap": True, "guides": True, "grid_size": 20},
                        created_at=now,
                        updated_at=now,
                        created_by=administrator.user_id if administrator else None,
                    )
                )
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
        public = path in {"/health", "/api/v1/auth/login", "/api/v1/discovery"} or path.startswith(
            "/api/v1/player/"
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
            "source_connected": bool(
                source
                and source.last_sync_at
                and datetime.now(UTC) - source.last_sync_at
                < timedelta(seconds=max(30, configured.poll_seconds * 3))
                and not source.last_error
            ),
            "last_sync_at": source.last_sync_at if source else None,
            "displays": {"configured": len(displays), "online": online},
        }

    @app.get("/api/v1/discovery")
    def discovery(s: Annotated[Session, Depends(read)]):
        identity = s.scalar(select(Installation).where(Installation.singleton_key == 1))
        return {
            "product": "UPM Signage",
            "installation_id": identity.installation_id if identity else None,
            "site_id": identity.site_id if identity else None,
            "site_name": identity.site_name if identity and identity.site_name else "UPM Site",
            "version": configured.application_version,
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
            "default_password": body.username.strip().casefold() == "admin"
            and body.password == "admin",
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
    def events(s: Annotated[Session, Depends(read)], include_archived: bool = False):
        query = select(Projection).where(Projection.entity_type == "event")
        if not include_archived:
            query = query.where(Projection.tombstone.is_(False))
        rows = s.scalars(query)
        return sorted(
            [dict(row.payload, event_id=row.event_id) for row in rows],
            key=lambda value: ((value.get("starts_at") or ""), value["name"].casefold()),
        )

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

    def display_view(s: Session, row):
        state = s.get(PlaybackState, row.display_id)
        publication = s.get(Publication, row.display_id)
        event = (
            s.scalar(
                select(Projection).where(
                    Projection.entity_type == "event", Projection.entity_id == row.event_id
                )
            )
            if row.event_id
            else None
        )
        room = (
            s.scalar(
                select(Projection).where(
                    Projection.entity_type == "room", Projection.entity_id == row.room_id
                )
            )
            if row.room_id
            else None
        )
        online = bool(
            state
            and state.last_heartbeat_at
            and datetime.now(UTC) - state.last_heartbeat_at < timedelta(seconds=45)
        )
        manifest_layout = publication.active_manifest.get("layout", {}) if publication else {}
        referenced_assets = {
            value
            for element in manifest_layout.get("elements", [])
            if (value := element.get("data_binding", {}).get("asset_id"))
        }
        asset_missing = any(s.get(Asset, UUID(value)) is None for value in referenced_assets)
        canonical_layout = (
            s.get(Layout, UUID(manifest_layout["layout_id"]))
            if manifest_layout.get("layout_id")
            else None
        )
        update_available = bool(
            canonical_layout
            and canonical_layout.published_revision != manifest_layout.get("published_revision")
        )
        status = (
            "needs_assignment"
            if not row.event_id
            else "asset_missing"
            if asset_missing
            else "layout_missing"
            if not publication
            else "update_available"
            if update_available
            else "online"
            if online
            else "playback_ready"
            if state and state.media_ready
            else "offline"
        )
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
            "status": status,
            "event_name": event.payload.get("name") if event else None,
            "room_name": room.payload.get("name") if room else None,
            "current_layout": publication.active_manifest.get("layout", {}).get("name")
            if publication
            else None,
            "last_seen_at": state.last_heartbeat_at if state else None,
            "player_version": state.player_version if state else None,
        }

    @app.get("/api/v1/displays")
    def displays(s: Annotated[Session, Depends(read)]):
        return [display_view(s, x) for x in s.scalars(select(Display).order_by(Display.name))]

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
        return {**display_view(s, row), "player_credential": token}

    @app.put("/api/v1/displays/{display_id}/assignment")
    def assign(display_id: UUID, body: Assignment, s: Annotated[Session, Depends(write)]):
        row = s.get(Display, display_id)
        if not row:
            raise HTTPException(404, "display not found")
        if body.mode == "room_door" and (not body.event_id or not body.room_id):
            raise HTTPException(422, "Room-door displays require event and room selections")
        row.event_id, row.room_id, row.mode = body.event_id, body.room_id, body.mode
        row.width = body.width or row.width
        row.height = body.height or row.height
        row.orientation = body.orientation or row.orientation
        if body.layout_id:
            layout = s.get(Layout, body.layout_id)
            if not layout or not layout.published_revision:
                raise HTTPException(409, "Select a published layout")
            publication = s.get(Publication, row.display_id) or Publication(
                display_id=row.display_id
            )
            s.add(publication)
            publication.previous_manifest = publication.active_manifest
            publication.revision += 1
            publication.active_manifest = {
                "schema_version": 1,
                "generation": publication.revision,
                "layout": layout_view(layout),
                "display": display_view(s, row),
            }
            publication.activated_at = datetime.now(UTC)
        return display_view(s, row)

    @app.post("/api/v1/displays/{display_id}/unpair", status_code=204)
    def unpair(display_id: UUID, s: Annotated[Session, Depends(write)]):
        row = s.get(Display, display_id)
        if not row:
            raise HTTPException(404, "display not found")
        row.enabled = False
        row.credential_hash = hashlib.sha256(secrets.token_bytes(64)).hexdigest()

    @app.delete("/api/v1/displays/{display_id}", status_code=204)
    def delete_display(display_id: UUID, s: Annotated[Session, Depends(write)]):
        row = s.get(Display, display_id)
        if not row:
            raise HTTPException(404, "display not found")
        if row.enabled:
            raise HTTPException(409, "Unpair the display before deleting it")
        publication = s.get(Publication, display_id)
        playback = s.get(PlaybackState, display_id)
        if publication:
            s.delete(publication)
        if playback:
            s.delete(playback)
        s.delete(row)

    def asset_usage(s: Session, asset_id: UUID) -> int:
        value = str(asset_id)
        return sum(
            1
            for layout in s.scalars(select(Layout))
            for element in layout.elements
            if value
            in {
                str(element.get("data_binding", {}).get("asset_id")),
                str(element.get("style", {}).get("asset_id")),
            }
        )

    def asset_view(s: Session, row: Asset):
        return {
            "asset_id": row.asset_id,
            "event_id": row.event_id,
            "name": row.name,
            "original_filename": row.original_filename,
            "mime_type": row.mime_type,
            "size": row.size,
            "sha256": row.sha256,
            "created_at": row.created_at,
            "usage_count": asset_usage(s, row.asset_id),
            "content_url": f"/api/v1/assets/{row.asset_id}/content",
        }

    @app.get("/api/v1/assets")
    def assets(
        s: Annotated[Session, Depends(read)], event_id: UUID | None = None, search: str = ""
    ):
        query = select(Asset).where(Asset.state == "ready")
        if event_id:
            query = query.where((Asset.event_id == event_id) | (Asset.event_id.is_(None)))
        rows = s.scalars(query.order_by(Asset.name)).all()
        term = search.strip().casefold()
        return [
            asset_view(s, row)
            for row in rows
            if not term or term in row.name.casefold() or term in row.original_filename.casefold()
        ]

    async def store_asset(
        upload: UploadFile,
        event_id: UUID | None,
        name: str,
        s: Session,
        existing: Asset | None = None,
    ):
        allowed = {"image/png", "image/jpeg", "image/webp", "image/gif", "image/svg+xml"}
        if upload.content_type not in allowed:
            raise HTTPException(415, "Only PNG, JPEG, WebP, GIF, and SVG images are supported")
        content = await upload.read(10 * 1024 * 1024 + 1)
        if not content or len(content) > 10 * 1024 * 1024:
            raise HTTPException(413, "Asset must be between 1 byte and 10 MiB")
        sha256 = hashlib.sha256(content).hexdigest()
        duplicate = s.scalar(select(Asset).where(Asset.sha256 == sha256, Asset.state == "ready"))
        root = Path(configured.media_root) / "assets" / sha256[:2]
        root.mkdir(parents=True, exist_ok=True)
        path = root / sha256
        if not path.exists():
            temporary = path.with_suffix(".new")
            temporary.write_bytes(content)
            temporary.replace(path)
        identity = s.scalar(select(Installation).where(Installation.singleton_key == 1))
        source = s.scalar(select(Source))
        row = existing or Asset(
            asset_id=new_uuid7(),
            source_id=source.source_id if source else identity.installation_id,
            media_id=duplicate.media_id if duplicate else new_uuid7(),
            mime_type=upload.content_type,
            size=len(content),
            sha256=sha256,
        )
        row.event_id = event_id
        row.name = name.strip() or upload.filename or "Asset"
        row.original_filename = upload.filename or "asset"
        row.mime_type = upload.content_type
        row.size = len(content)
        row.sha256 = sha256
        row.state = "ready"
        row.local_path = str(path)
        s.add(row)
        s.flush()
        return asset_view(s, row)

    @app.post("/api/v1/assets", status_code=201)
    async def upload_asset(
        s: Annotated[Session, Depends(write)],
        file: Annotated[UploadFile, File()],
        event_id: Annotated[UUID | None, Form()] = None,
        name: Annotated[str, Form()] = "",
    ):
        return await store_asset(file, event_id, name, s)

    @app.put("/api/v1/assets/{asset_id}")
    async def replace_asset(
        asset_id: UUID,
        s: Annotated[Session, Depends(write)],
        file: Annotated[UploadFile, File()],
        name: Annotated[str, Form()] = "",
    ):
        row = s.get(Asset, asset_id)
        if not row:
            raise HTTPException(404, "asset not found")
        return await store_asset(file, row.event_id, name or row.name, s, row)

    @app.get("/api/v1/assets/{asset_id}/content")
    def asset_content(asset_id: UUID, s: Annotated[Session, Depends(read)]):
        row = s.get(Asset, asset_id)
        if (
            not row
            or row.state != "ready"
            or not row.local_path
            or not Path(row.local_path).is_file()
        ):
            raise HTTPException(404, "asset content not found")
        return FileResponse(
            row.local_path, media_type=row.mime_type, filename=row.original_filename
        )

    @app.delete("/api/v1/assets/{asset_id}", status_code=204)
    def delete_asset(asset_id: UUID, s: Annotated[Session, Depends(write)]):
        row = s.get(Asset, asset_id)
        if not row:
            raise HTTPException(404, "asset not found")
        if asset_usage(s, asset_id):
            raise HTTPException(409, "asset is used by a layout")
        s.delete(row)

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

    @app.post("/api/v1/templates/{template_id}/duplicate", status_code=201)
    def duplicate_template(
        template_id: UUID, request: Request, s: Annotated[Session, Depends(write)]
    ):
        source = s.get(Layout, template_id)
        if not source or source.mode != "template":
            raise HTTPException(404, "template not found")
        now = datetime.now(UTC)
        row = Layout(
            name=f"{source.name} copy",
            description=source.description,
            mode="template",
            template="template",
            aspect_ratio=source.aspect_ratio,
            width=source.width,
            height=source.height,
            orientation=source.orientation,
            safe_area=source.safe_area,
            background=source.background,
            elements=source.elements,
            configuration=source.configuration,
            created_at=now,
            updated_at=now,
            created_by=request.state.signage_user_id,
        )
        s.add(row)
        s.flush()
        return layout_view(row)

    @app.post("/api/v1/templates/{template_id}/apply", status_code=201)
    def apply_template(
        template_id: UUID,
        body: TemplateApply,
        request: Request,
        s: Annotated[Session, Depends(write)],
    ):
        source = s.get(Layout, template_id)
        if not source or source.mode != "template":
            raise HTTPException(404, "template not found")
        now = datetime.now(UTC)
        row = Layout(
            name=body.name or source.name,
            description=source.description,
            event_id=body.event_id,
            mode="room_door" if body.room_id else "lobby",
            template=source.name,
            aspect_ratio=source.aspect_ratio,
            width=source.width,
            height=source.height,
            orientation=source.orientation,
            safe_area=source.safe_area,
            background=source.background,
            elements=source.elements,
            configuration={
                **source.configuration,
                "applied_room_id": str(body.room_id) if body.room_id else None,
            },
            created_at=now,
            updated_at=now,
            created_by=request.state.signage_user_id,
        )
        s.add(row)
        s.flush()
        return layout_view(row)

    @app.delete("/api/v1/templates/{template_id}", status_code=204)
    def delete_template(template_id: UUID, s: Annotated[Session, Depends(write)]):
        row = s.get(Layout, template_id)
        if not row or row.mode != "template":
            raise HTTPException(404, "template not found")
        if row.published_revision:
            raise HTTPException(409, "published templates cannot be deleted")
        s.delete(row)

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
        referenced = {
            UUID(value)
            for element in row.elements
            if element["type"] in {"image", "event_logo", "room_logo", "rotating_slides"}
            and (value := element.get("data_binding", {}).get("asset_id"))
        }
        missing = [asset_id for asset_id in referenced if not s.get(Asset, asset_id)]
        if missing:
            raise HTTPException(409, "Layout contains missing assets")
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
                "display": display_view(s, display),
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
            manifest = {**publication.active_manifest, "schedule": scheduled}
            layout = manifest.get("layout", {})
            inventory = []
            for element in layout.get("elements", []):
                if element["type"] == "qr_code":
                    value = element.get("data_binding", {}).get("value") or element.get(
                        "fallback_value", ""
                    )
                    image = qrcode.make(value)
                    output = io.BytesIO()
                    image.save(output, format="PNG")
                    element["render_data"] = (
                        "data:image/png;base64," + base64.b64encode(output.getvalue()).decode()
                    )
                asset_value = element.get("data_binding", {}).get("asset_id")
                if asset_value:
                    asset = s.get(Asset, UUID(asset_value))
                    if asset and asset.local_path and Path(asset.local_path).is_file():
                        data = base64.b64encode(Path(asset.local_path).read_bytes()).decode()
                        element["render_data"] = f"data:{asset.mime_type};base64,{data}"
                        inventory.append(
                            {"asset_id": asset.asset_id, "sha256": asset.sha256, "size": asset.size}
                        )
            manifest["assets"] = inventory
            return manifest
        return {
            "schema_version": 1,
            "generation": 0,
            "mode": display.mode,
            "display": display_view(s, display),
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
        row.player_version = body.player_version
        row.last_heartbeat_at = datetime.now(UTC)
        return {"received": True}

    return app
