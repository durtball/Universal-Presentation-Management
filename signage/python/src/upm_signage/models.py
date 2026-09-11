from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from upm_shared.identifiers import new_uuid7

from .db import Base


class Source(Base):
    __tablename__ = "sources"
    source_id: Mapped[UUID] = mapped_column(PGUUID, primary_key=True, default=new_uuid7)
    site_id: Mapped[UUID] = mapped_column(PGUUID, unique=True)
    base_url: Mapped[str] = mapped_column(String(2048))
    credential: Mapped[str] = mapped_column(Text)
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
    event_id: Mapped[UUID | None] = mapped_column(PGUUID)
    room_id: Mapped[UUID | None] = mapped_column(PGUUID)
    mode: Mapped[str] = mapped_column(String(32), default="room_door")
    credential_hash: Mapped[str] = mapped_column(String(64))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class Layout(Base):
    __tablename__ = "layouts"
    layout_id: Mapped[UUID] = mapped_column(PGUUID, primary_key=True, default=new_uuid7)
    name: Mapped[str] = mapped_column(String(255))
    template: Mapped[str] = mapped_column(String(64))
    aspect_ratio: Mapped[str] = mapped_column(String(16))
    configuration: Mapped[dict] = mapped_column(JSONB, default=dict)


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
