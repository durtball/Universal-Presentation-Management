from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from upm_shared.identifiers import new_uuid7

from .db import Base


def utc_now() -> datetime:
    return datetime.now(UTC)


class Source(Base):
    __tablename__ = "sources"
    source_id: Mapped[UUID] = mapped_column(PGUUID, primary_key=True, default=new_uuid7)
    site_id: Mapped[UUID] = mapped_column(PGUUID, unique=True)
    base_url: Mapped[str] = mapped_column(String(2048))
    committed_cursor: Mapped[int] = mapped_column(BigInteger, default=0)
    snapshot_generation: Mapped[int] = mapped_column(BigInteger, default=0)
    source_instance_id: Mapped[UUID | None] = mapped_column(PGUUID)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)


class Projection(Base):
    __tablename__ = "projections"
    __table_args__ = (UniqueConstraint("source_id", "entity_type", "entity_id"),)
    projection_id: Mapped[UUID] = mapped_column(PGUUID, primary_key=True, default=new_uuid7)
    source_id: Mapped[UUID] = mapped_column(PGUUID)
    event_id: Mapped[UUID] = mapped_column(PGUUID, index=True)
    entity_type: Mapped[str] = mapped_column(String(32))
    entity_id: Mapped[UUID] = mapped_column(PGUUID)
    generation: Mapped[int] = mapped_column(BigInteger)
    payload: Mapped[dict] = mapped_column(JSONB)
    tombstone: Mapped[bool] = mapped_column(Boolean, default=False)


class Display(Base):
    __tablename__ = "displays"
    display_id: Mapped[UUID] = mapped_column(PGUUID, primary_key=True, default=new_uuid7)
    name: Mapped[str] = mapped_column(String(255))
    aspect_ratio: Mapped[str] = mapped_column(String(16), default="9:16")
    width: Mapped[int] = mapped_column(Integer, default=1080)
    height: Mapped[int] = mapped_column(Integer, default=1920)
    orientation: Mapped[str] = mapped_column(String(16), default="portrait")
    event_id: Mapped[UUID | None] = mapped_column(PGUUID)
    room_id: Mapped[UUID | None] = mapped_column(PGUUID)
    mode: Mapped[str] = mapped_column(String(32), default="room_door")
    credential_hash: Mapped[str] = mapped_column(String(64))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class Layout(Base):
    __tablename__ = "layouts"
    layout_id: Mapped[UUID] = mapped_column(PGUUID, primary_key=True, default=new_uuid7)
    name: Mapped[str] = mapped_column(String(255))
    event_id: Mapped[UUID | None] = mapped_column(PGUUID, index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    mode: Mapped[str] = mapped_column(String(32), default="room_door")
    template: Mapped[str] = mapped_column(String(64), default="room_door")
    aspect_ratio: Mapped[str] = mapped_column(String(16))
    width: Mapped[int] = mapped_column(Integer, default=1920)
    height: Mapped[int] = mapped_column(Integer, default=1080)
    orientation: Mapped[str] = mapped_column(String(16), default="landscape")
    safe_area: Mapped[dict] = mapped_column(JSONB, default=dict)
    background: Mapped[dict] = mapped_column(JSONB, default=dict)
    elements: Mapped[list] = mapped_column(JSONB, default=list)
    configuration: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    created_by: Mapped[UUID | None] = mapped_column(PGUUID)
    draft_revision: Mapped[int] = mapped_column(BigInteger, default=1)
    published_revision: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(32), default="draft")


class LayoutRevision(Base):
    __tablename__ = "layout_revisions"
    __table_args__ = (UniqueConstraint("layout_id", "revision"),)
    layout_revision_id: Mapped[UUID] = mapped_column(PGUUID, primary_key=True, default=new_uuid7)
    layout_id: Mapped[UUID] = mapped_column(PGUUID, index=True)
    revision: Mapped[int] = mapped_column(BigInteger)
    snapshot: Mapped[dict] = mapped_column(JSONB)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    published_by: Mapped[UUID | None] = mapped_column(PGUUID)


class OperatorUser(Base):
    __tablename__ = "operator_users"
    user_id: Mapped[UUID] = mapped_column(PGUUID, primary_key=True, default=new_uuid7)
    username: Mapped[str] = mapped_column(String(255), unique=True)
    normalized_username: Mapped[str] = mapped_column(String(255), unique=True)
    display_name: Mapped[str] = mapped_column(String(255))
    password_hash: Mapped[str] = mapped_column(Text)
    roles: Mapped[list] = mapped_column(JSONB, default=list)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class OperatorSession(Base):
    __tablename__ = "operator_sessions"
    session_id: Mapped[UUID] = mapped_column(PGUUID, primary_key=True, default=new_uuid7)
    user_id: Mapped[UUID] = mapped_column(PGUUID, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    csrf_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Playlist(Base):
    __tablename__ = "playlists"
    playlist_id: Mapped[UUID] = mapped_column(PGUUID, primary_key=True, default=new_uuid7)
    name: Mapped[str] = mapped_column(String(255))
    items: Mapped[list] = mapped_column(JSONB, default=list)


class Asset(Base):
    __tablename__ = "assets"
    asset_id: Mapped[UUID] = mapped_column(PGUUID, primary_key=True)
    source_id: Mapped[UUID] = mapped_column(PGUUID)
    media_id: Mapped[UUID] = mapped_column(PGUUID)
    mime_type: Mapped[str] = mapped_column(String(255))
    size: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(32), default="pending")
    confirmed_offset: Mapped[int] = mapped_column(BigInteger, default=0)
    local_path: Mapped[str | None] = mapped_column(Text)


class Override(Base):
    __tablename__ = "overrides"
    override_id: Mapped[UUID] = mapped_column(PGUUID, primary_key=True, default=new_uuid7)
    display_id: Mapped[UUID] = mapped_column(PGUUID)
    payload: Mapped[dict] = mapped_column(JSONB)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Publication(Base):
    __tablename__ = "publications"
    display_id: Mapped[UUID] = mapped_column(PGUUID, primary_key=True)
    revision: Mapped[int] = mapped_column(BigInteger, default=0)
    active_manifest: Mapped[dict] = mapped_column(JSONB, default=dict)
    previous_manifest: Mapped[dict] = mapped_column(JSONB, default=dict)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PlaybackState(Base):
    __tablename__ = "playback_state"
    display_id: Mapped[UUID] = mapped_column(PGUUID, primary_key=True)
    player_online: Mapped[bool] = mapped_column(Boolean, default=False)
    media_ready: Mapped[bool] = mapped_column(Boolean, default=False)
    current_item: Mapped[str | None] = mapped_column(String(255))
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)


class Job(Base):
    __tablename__ = "jobs"
    job_id: Mapped[UUID] = mapped_column(PGUUID, primary_key=True, default=new_uuid7)
    kind: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(32), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
