"""PostgreSQL regression coverage for Room Agent assignment invalidation."""

import hashlib
import os
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.schema import CreateSchema, DropSchema

from upm_shared.enums import DeviceRole, SessionStatus
from upm_site.api import create_app
from upm_site.config import SiteSettings
from upm_site.persistence.models import (
    AgentChangeFeed,
    Device,
    DeviceAssignment,
    Event,
    Room,
    RoomAssignment,
    utc_now,
)
from upm_site.persistence.models import Session as ProgramSession
from upm_site.sync import bootstrap_identity

SITE_URL = os.getenv("UPM_SITE_DATABASE_URL")
pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(not SITE_URL, reason="a Site PostgreSQL URL is required"),
]


def schema_url(raw: str, schema: str) -> str:
    url = make_url(raw)
    query = dict(url.query)
    query["options"] = f"-csearch_path={schema}"
    return URL.create(
        drivername=url.drivername,
        username=url.username,
        password=url.password,
        host=url.host,
        port=url.port,
        database=url.database,
        query=query,
    ).render_as_string(hide_password=False)


@contextmanager
def migrated_schema(start_revision: str | None):
    schema = f"agent_feed_{uuid4().hex}"
    admin = create_engine(SITE_URL)
    with admin.begin() as connection:
        connection.execute(CreateSchema(schema))
    url = schema_url(SITE_URL, schema)
    previous = os.environ.get("UPM_SITE_DATABASE_URL")
    os.environ["UPM_SITE_DATABASE_URL"] = url
    config = Config("database/site/alembic.ini")
    try:
        if start_revision:
            command.upgrade(config, start_revision)
        command.upgrade(config, "head")
        yield url
    finally:
        if previous is None:
            os.environ.pop("UPM_SITE_DATABASE_URL", None)
        else:
            os.environ["UPM_SITE_DATABASE_URL"] = previous
        with admin.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True, if_exists=True))
        admin.dispose()


@pytest.mark.parametrize("start_revision", [None, "d12a9f73bc21"])
def test_fresh_and_existing_site_install_assignment_change_feed(start_revision: str | None):
    with migrated_schema(start_revision) as url:
        engine = create_engine(url)
        settings = SiteSettings(
            database_url=url,
            credential_encryption_key="test-only-encryption-key-with-32-characters",
        )
        with Session(engine) as session, session.begin():
            site, _ = bootstrap_identity(session, settings)
            event = Event(site_id=site.site_id, name="Agent Feed Event", timezone="UTC")
            session.add(event)
            session.flush()
            room = Room(site_id=site.site_id, event_id=event.event_id, label="Room 101")
            device = Device(site_id=site.site_id, display_name="ROOM-101", enrolled_at=utc_now())
            session.add_all([room, device])
            session.flush()
            before = session.scalar(select(func.max(AgentChangeFeed.sequence))) or 0
            session.add(
                DeviceAssignment(
                    device_id=device.device_id,
                    room_id=room.room_id,
                    role=DeviceRole.PRIMARY,
                    active=True,
                )
            )
            session.flush()
            after = session.scalar(select(func.max(AgentChangeFeed.sequence))) or 0
            assert after > before
            latest = session.scalar(
                select(AgentChangeFeed)
                .where(AgentChangeFeed.entity_type == "device_assignments")
                .order_by(AgentChangeFeed.sequence.desc())
            )
            assert latest is not None
            assert latest.operation == "insert"
        engine.dispose()


def test_assignment_move_clear_incremental_sync_and_heartbeat():
    with migrated_schema(None) as url:
        engine = create_engine(url)
        factory = sessionmaker(engine, expire_on_commit=False)
        settings = SiteSettings(
            database_url=url,
            credential_encryption_key="test-only-encryption-key-with-32-characters",
        )
        token = "durable-agent-credential"
        now = datetime.now(UTC)
        with factory.begin() as session:
            site, _ = bootstrap_identity(session, settings)
            event = Event(site_id=site.site_id, name="Agent Event", timezone="UTC")
            session.add(event)
            session.flush()
            rooms = [
                Room(site_id=site.site_id, event_id=event.event_id, label="Room A"),
                Room(site_id=site.site_id, event_id=event.event_id, label="Room B"),
            ]
            device = Device(
                site_id=site.site_id,
                display_name="ROOM-PC",
                enrolled_at=now,
                enrollment_state="unassigned",
                agent_token_hash=hashlib.sha256(token.encode()).hexdigest(),
            )
            session.add_all([*rooms, device])
            session.flush()
            for index, room in enumerate(rooms):
                program_session = ProgramSession(
                    event_id=event.event_id,
                    title=f"Session {index + 1}",
                    starts_at=now + timedelta(hours=index + 1),
                    ends_at=now + timedelta(hours=index + 2),
                    location_name=room.label,
                    status=SessionStatus.SCHEDULED,
                    active=True,
                )
                session.add(program_session)
                session.flush()
                session.add(
                    RoomAssignment(
                        session_id=program_session.session_id,
                        room_id=room.room_id,
                        starts_at=program_session.starts_at,
                        ends_at=program_session.ends_at,
                        active=True,
                    )
                )

        headers = {"Authorization": f"Bearer {token}"}
        with TestClient(create_app(settings=settings, session_factory=factory)) as client:
            initial = client.get("/api/v1/agent/changes", headers=headers)
            assert initial.status_code == 200, initial.text
            assert initial.json()["assigned"] is False
            assert initial.json()["sessions"] == []
            initial_revision = initial.json()["revisions"]["schedule"]

            assigned = client.put(
                f"/api/v1/rooms/{rooms[0].room_id}/device-assignments/primary",
                json={"device_id": str(device.device_id)},
            )
            assert assigned.status_code == 200, assigned.text
            first = client.get(
                "/api/v1/agent/changes",
                params={
                    "schedule": initial_revision,
                    "presentations": initial_revision,
                    "branding": initial_revision,
                    "rotating_slides": initial_revision,
                },
                headers=headers,
            ).json()
            assert first["room_name"] == "Room A"
            assert first["event_name"] == "Agent Event"
            assert [item["title"] for item in first["sessions"]] == ["Session 1"]
            assert first["revisions"]["schedule"] > initial_revision

            heartbeat = client.post(
                "/api/v1/agent/heartbeat",
                headers=headers,
                json={
                    "hostname": "ROOM-PC",
                    "agent_version": "1.0.0",
                    "windows_version": "Windows 11",
                    "free_disk_bytes": 1024,
                    "local_cache_bytes": 512,
                    "powerpoint_available": True,
                    "metadata": {
                        "event_id": str(event.event_id),
                        "room_id": str(rooms[0].room_id),
                        "role": "RoomAgent",
                        "last_sync": now.isoformat(),
                        "failed_transfers": 0,
                    },
                },
            )
            assert heartbeat.status_code == 200, heartbeat.text
            room_detail = client.get(f"/api/v1/rooms/{rooms[0].room_id}").json()
            assert room_detail["endpoints"]["primary"]["telemetry_available"] is True
            assert room_detail["endpoints"]["primary"]["status"] == "online"

            moved = client.put(
                f"/api/v1/rooms/{rooms[1].room_id}/device-assignments/primary",
                json={"device_id": str(device.device_id)},
            )
            assert moved.status_code == 200, moved.text
            second_revision = first["revisions"]["schedule"]
            second = client.get(
                "/api/v1/agent/changes",
                params={
                    "schedule": second_revision,
                    "presentations": second_revision,
                    "branding": second_revision,
                    "rotating_slides": second_revision,
                },
                headers=headers,
            ).json()
            assert second["room_name"] == "Room B"
            assert [item["title"] for item in second["sessions"]] == ["Session 2"]

            cleared = client.put(
                f"/api/v1/rooms/{rooms[1].room_id}/device-assignments/primary",
                json={"device_id": None},
            )
            assert cleared.status_code == 200, cleared.text
            cleared_revision = second["revisions"]["schedule"]
            final = client.get(
                "/api/v1/agent/changes",
                params={
                    "schedule": cleared_revision,
                    "presentations": cleared_revision,
                    "branding": cleared_revision,
                    "rotating_slides": cleared_revision,
                },
                headers=headers,
            ).json()
            assert final["assigned"] is False
            assert final["room_id"] is None
            assert final["sessions"] == []
        with factory() as session:
            persisted = session.get(Device, device.device_id)
            assert persisted is not None
            assert persisted.enrollment_state == "unassigned"
            assert persisted.event_id is None
            assert persisted.agent_token_hash == hashlib.sha256(token.encode()).hexdigest()
        engine.dispose()
