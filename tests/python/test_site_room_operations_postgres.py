"""PostgreSQL integration coverage for the Site room operational workspace."""

import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.schema import CreateSchema, DropSchema

from upm_shared.enums import (
    AssetKind,
    JobStatus,
    MediaAvailability,
    MediaCategory,
    PresentationProcessingStatus,
    PresentationWorkflowStatus,
    SessionStatus,
    StorageHealth,
    StorageType,
)
from upm_shared.identifiers import new_uuid7
from upm_site.api import create_app
from upm_site.config import SiteSettings
from upm_site.persistence.base import SiteBase
from upm_site.persistence.models import (
    Device,
    DeviceAssignment,
    Event,
    MediaObject,
    Presentation,
    PresentationAsset,
    PresentationVersion,
    ProcessingJob,
    ProgramRoomMapping,
    Room,
    StorageTarget,
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


@pytest.fixture
def room_factory() -> Iterator[tuple[sessionmaker[Session], SiteSettings, dict[str, object]]]:
    schema = f"site_rooms_{uuid4().hex}"
    admin = create_engine(SITE_URL)
    engine = None
    try:
        with admin.begin() as connection:
            connection.execute(CreateSchema(schema))
        scoped_url = schema_url(SITE_URL, schema)
        engine = create_engine(scoped_url)
        SiteBase.metadata.create_all(engine)
        factory = sessionmaker(engine, expire_on_commit=False)
        settings = SiteSettings(
            database_url=scoped_url,
            central_url="http://central-unavailable.test",
            credential_encryption_key="test-only-encryption-key-with-32-characters",
        )
        now = datetime.now(UTC)
        context: dict[str, object] = {}
        with factory.begin() as session:
            site, _ = bootstrap_identity(session, settings)
            event_id = new_uuid7()
            first_session_id, second_session_id, unmapped_session_id = (
                new_uuid7(),
                new_uuid7(),
                new_uuid7(),
            )
            session.add(
                Event(
                    event_id=event_id,
                    site_id=site.site_id,
                    name="Site Room Operations",
                    timezone="America/Chicago",
                )
            )
            session.flush()
            session.add_all(
                [
                    ProgramSession(
                        session_id=second_session_id,
                        event_id=event_id,
                        title="AI Infrastructure",
                        starts_at=now + timedelta(hours=2),
                        ends_at=now + timedelta(hours=3),
                        location_name="Room 101",
                        status=SessionStatus.SCHEDULED,
                        active=True,
                    ),
                    ProgramSession(
                        session_id=first_session_id,
                        event_id=event_id,
                        title="Opening Session",
                        starts_at=now + timedelta(hours=1),
                        ends_at=now + timedelta(hours=2),
                        location_name="Room 101",
                        status=SessionStatus.SCHEDULED,
                        active=True,
                    ),
                    ProgramSession(
                        session_id=unmapped_session_id,
                        event_id=event_id,
                        title="Expo Hall",
                        starts_at=now + timedelta(hours=1),
                        location_name="Expo Hall",
                        status=SessionStatus.SCHEDULED,
                        active=True,
                    ),
                ]
            )
            missing_presentation_id, ready_presentation_id = new_uuid7(), new_uuid7()
            version_id, target_id, media_id = new_uuid7(), new_uuid7(), new_uuid7()
            session.add_all(
                [
                    Presentation(
                        presentation_id=missing_presentation_id,
                        event_id=event_id,
                        session_id=first_session_id,
                        title="Opening deck",
                        workflow_status=PresentationWorkflowStatus.EXPECTED,
                        processing_status=PresentationProcessingStatus.NOT_STARTED,
                        scheduled_at=now + timedelta(hours=1),
                        active=True,
                    ),
                    Presentation(
                        presentation_id=ready_presentation_id,
                        event_id=event_id,
                        session_id=second_session_id,
                        title="Infrastructure deck",
                        workflow_status=PresentationWorkflowStatus.READY,
                        processing_status=PresentationProcessingStatus.SUCCEEDED,
                        scheduled_at=now + timedelta(hours=2),
                        active=True,
                    ),
                ]
            )
            session.flush()
            session.add(
                PresentationVersion(
                    presentation_version_id=version_id,
                    presentation_id=ready_presentation_id,
                    version_number=2,
                )
            )
            session.add(
                StorageTarget(
                    storage_target_id=target_id,
                    site_id=site.site_id,
                    display_name="Room operations media",
                    storage_type=StorageType.LOCAL_FILESYSTEM,
                    root_path="/tmp/upm-room-operations",
                    enabled=True,
                    primary_media=True,
                    health=StorageHealth.UNKNOWN,
                    safety_reserve_bytes=0,
                )
            )
            session.flush()
            session.add(
                MediaObject(
                    media_object_id=media_id,
                    site_id=site.site_id,
                    event_id=event_id,
                    storage_target_id=target_id,
                    object_key=f"presentation/2026/08/{media_id}.pptx",
                    category=MediaCategory.PRESENTATION_VERSION,
                    original_filename="infrastructure-v2.pptx",
                    content_hash="a" * 64,
                    hash_algorithm="sha256",
                    size_bytes=2048,
                    mime_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
                    availability=MediaAvailability.AVAILABLE,
                    disposition="authoritative",
                )
            )
            session.flush()
            session.add(
                PresentationAsset(
                    presentation_version_id=version_id,
                    media_object_id=media_id,
                    kind=AssetKind.ORIGINAL,
                )
            )
            session.add(
                ProcessingJob(
                    site_id=site.site_id,
                    media_object_id=media_id,
                    job_type="media.inspect",
                    status=JobStatus.SUCCEEDED,
                    progress=100,
                )
            )
            failed_job_id = new_uuid7()
            session.add(
                ProcessingJob(
                    processing_job_id=failed_job_id,
                    site_id=site.site_id,
                    job_type="preview.generate",
                    status=JobStatus.FAILED,
                    last_error="test processing failure",
                )
            )
            device_ids = [new_uuid7(), new_uuid7(), new_uuid7(), new_uuid7()]
            session.add_all(
                [
                    Device(
                        device_id=device_ids[0],
                        site_id=site.site_id,
                        display_name="Agent Primary A",
                        enrolled_at=now,
                    ),
                    Device(
                        device_id=device_ids[1],
                        site_id=site.site_id,
                        display_name="Agent Backup",
                        enrolled_at=now,
                    ),
                    Device(
                        device_id=device_ids[2],
                        site_id=site.site_id,
                        display_name="Unenrolled Agent",
                    ),
                    Device(
                        device_id=device_ids[3],
                        site_id=site.site_id,
                        display_name="Agent Primary B",
                        enrolled_at=now,
                        agent_token_hash="a" * 64,
                    ),
                ]
            )
            context.update(
                site_id=site.site_id,
                event_id=event_id,
                media_id=media_id,
                target_id=target_id,
                device_ids=device_ids,
            )
        yield factory, settings, context
    finally:
        if engine is not None:
            engine.dispose()
        with admin.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True, if_exists=True))
        admin.dispose()


def test_room_program_media_device_dashboard_and_restart_workflow(
    room_factory: tuple[sessionmaker[Session], SiteSettings, dict[str, object]],
) -> None:
    factory, settings, context = room_factory
    device_ids = context["device_ids"]
    with TestClient(create_app(settings=settings, session_factory=factory)) as client:
        created = client.post("/api/v1/rooms", json={"label": "Room 101"})
        assert created.status_code == 201, created.text
        room_id = created.json()["room_id"]
        assert client.get("/api/v1/rooms").json()[0]["room_id"] == room_id

        edited = client.patch(
            f"/api/v1/rooms/{room_id}",
            json={"label": "Room 101 - Main", "enabled": True, "revision": 1},
        )
        assert edited.status_code == 200, edited.text
        assert edited.json()["room_id"] == room_id
        assert edited.json()["revision"] == 2

        before_mapping = client.get(f"/api/v1/events/{context['event_id']}/program-room-locations")
        assert before_mapping.status_code == 200
        assert {
            item["imported_label"]: item["mapping_status"] for item in before_mapping.json()
        } == {"Expo Hall": "unmapped", "Room 101": "unmapped"}

        mapped = client.put(
            f"/api/v1/events/{context['event_id']}/program-room-mappings",
            json={"imported_label": "Room 101", "room_id": room_id},
        )
        assert mapped.status_code == 200, mapped.text
        assert mapped.json()["reconciliation"] == {
            "mapped_sessions": 2,
            "unmapped_sessions": 1,
        }

        detail = client.get(f"/api/v1/rooms/{room_id}").json()
        assert [item["title"] for item in detail["sessions"]] == [
            "Opening Session",
            "AI Infrastructure",
        ]
        presentations = {
            item["title"]: item
            for program_session in detail["sessions"]
            for item in program_session["presentations"]
        }
        assert presentations["Opening deck"]["operational_status"] == "missing"
        assert presentations["Infrastructure deck"]["operational_status"] == "ready"
        assert presentations["Infrastructure deck"]["media"][0]["filename"] == (
            "infrastructure-v2.pptx"
        )
        assert presentations["Infrastructure deck"]["media"][0]["checksum"] == "a" * 64

        primary = client.put(
            f"/api/v1/rooms/{room_id}/device-assignments/primary",
            json={"device_id": str(device_ids[0])},
        )
        assert primary.status_code == 200, primary.text
        backup = client.put(
            f"/api/v1/rooms/{room_id}/device-assignments/backup",
            json={"device_id": str(device_ids[1])},
        )
        assert backup.status_code == 200, backup.text
        assert backup.json()["endpoints"]["primary"]["status"] == "unknown"
        assert backup.json()["endpoints"]["primary"]["telemetry_available"] is False
        invalid = client.put(
            f"/api/v1/rooms/{room_id}/device-assignments/primary",
            json={"device_id": str(device_ids[2])},
        )
        assert invalid.status_code == 422
        moved_role = client.put(
            f"/api/v1/rooms/{room_id}/device-assignments/primary",
            json={"device_id": str(device_ids[1])},
        )
        assert moved_role.status_code == 200, moved_role.text
        assert moved_role.json()["endpoints"]["primary"]["device_id"] == str(device_ids[1])
        assert "backup" not in moved_role.json()["endpoints"]
        changed = client.put(
            f"/api/v1/rooms/{room_id}/device-assignments/primary",
            json={"device_id": str(device_ids[3])},
        )
        assert changed.status_code == 200, changed.text
        assert changed.json()["endpoints"]["primary"]["device_id"] == str(device_ids[3])

        media = client.get("/api/v1/media")
        assert media.status_code == 200
        assert media.json()[0]["presentation"]["title"] == "Infrastructure deck"
        assert media.json()[0]["size_bytes"] == 2048

        dashboard = client.get("/api/v1/operations/dashboard")
        assert dashboard.status_code == 200
        assert dashboard.json()["failed_processing_jobs"] == 1
        assert {item["kind"] for item in dashboard.json()["attention"]} >= {
            "media_missing",
            "processing_jobs_failed",
        }

    # Recreate the application boundary without Central; PostgreSQL retains identity and
    # assignments.
    with TestClient(create_app(settings=settings, session_factory=factory)) as restarted:
        detail = restarted.get(f"/api/v1/rooms/{room_id}")
        assert detail.status_code == 200
        assert detail.json()["room_id"] == room_id
        assert detail.json()["label"] == "Room 101 - Main"
        assert detail.json()["endpoints"]["primary"]["device_id"] == str(device_ids[3])
        assert len(detail.json()["sessions"]) == 2
        with factory() as session:
            assigned = session.get(Device, device_ids[3])
            assert assigned is not None
            assert assigned.event_id == context["event_id"]
            assert assigned.enrollment_state == "assigned"
            assert assigned.agent_token_hash == "a" * 64
        cleared = restarted.put(
            f"/api/v1/rooms/{room_id}/device-assignments/primary",
            json={"device_id": None},
        )
        assert cleared.status_code == 200, cleared.text
        assert "primary" not in cleared.json()["endpoints"]
    with factory() as session:
        room = session.get(Room, room_id)
        assert room is not None and str(room.room_id) == room_id
        assignments = session.scalars(
            select(DeviceAssignment).where(DeviceAssignment.room_id == room.room_id)
        ).all()
        assert len([item for item in assignments if item.active]) == 0
        cleared_device = session.get(Device, device_ids[3])
        assert cleared_device is not None
        assert cleared_device.event_id is None
        assert cleared_device.enrollment_state == "unassigned"
        assert cleared_device.agent_token_hash == "a" * 64


def test_room_readiness_uses_canonical_media_and_counts_presentations_once(
    room_factory: tuple[sessionmaker[Session], SiteSettings, dict[str, object]],
) -> None:
    factory, settings, context = room_factory
    expected = {
        "Bellini Ballroom 2102": (14, 5),
        "Bellini Ballroom 2104": (12, 5),
        "Delfino Ballroom 4103": (22, 11),
        "Delfino Ballroom 4104": (27, 19),
        "Lando Ballroom 4203": (29, 15),
        "Marcello Ballroom 4403": (25, 15),
    }
    now = datetime.now(UTC)
    with factory.begin() as session:
        for room_index, (label, (presentation_count, ready_count)) in enumerate(expected.items()):
            room = Room(
                site_id=context["site_id"],
                event_id=context["event_id"],
                label=label,
            )
            session.add(room)
            session.flush()
            session.add(
                ProgramRoomMapping(
                    site_id=context["site_id"],
                    event_id=context["event_id"],
                    imported_label=label,
                    normalized_imported_label=label.casefold(),
                    room_id=room.room_id,
                    confirmed_by="test",
                )
            )
            for item_index in range(presentation_count):
                session_id, presentation_id = new_uuid7(), new_uuid7()
                session.add(
                    ProgramSession(
                        session_id=session_id,
                        event_id=context["event_id"],
                        title=f"{label} Session {item_index}",
                        starts_at=now + timedelta(minutes=room_index * 60 + item_index),
                        location_name=label,
                        status=SessionStatus.SCHEDULED,
                        active=True,
                    )
                )
                session.add(
                    Presentation(
                        presentation_id=presentation_id,
                        event_id=context["event_id"],
                        session_id=session_id,
                        title=f"{label} Presentation {item_index}",
                        active=True,
                    )
                )
                if item_index >= ready_count:
                    continue
                # Two canonical versions prove readiness is counted per
                # presentation rather than per version or asset.
                for version_number in (1, 2):
                    version_id, media_id = new_uuid7(), new_uuid7()
                    session.add(
                        PresentationVersion(
                            presentation_version_id=version_id,
                            presentation_id=presentation_id,
                            version_number=version_number,
                        )
                    )
                    session.add(
                        MediaObject(
                            media_object_id=media_id,
                            site_id=context["site_id"],
                            event_id=context["event_id"],
                            storage_target_id=context["target_id"],
                            object_key=f"objects/room-readiness/{media_id}.pptx",
                            category=MediaCategory.PRESENTATION_VERSION,
                            original_filename=f"deck-v{version_number}.pptx",
                            content_hash=f"{media_id.int:064x}"[-64:],
                            hash_algorithm="sha256",
                            size_bytes=1024,
                            availability=MediaAvailability.AVAILABLE,
                            disposition="authoritative",
                        )
                    )
                    session.add(
                        PresentationAsset(
                            presentation_version_id=version_id,
                            media_object_id=media_id,
                            kind=AssetKind.ORIGINAL,
                        )
                    )

    with TestClient(create_app(settings=settings, session_factory=factory)) as client:
        rooms = {item["label"]: item for item in client.get("/api/v1/rooms").json()}
        for label, (presentation_count, ready_count) in expected.items():
            summary = rooms[label]["summary"]
            assert summary["presentation_count"] == presentation_count
            assert summary["ready_count"] == ready_count
            assert summary["missing_count"] == presentation_count - ready_count
            detail = client.get(f"/api/v1/rooms/{rooms[label]['room_id']}").json()
            assert detail["summary"] == summary
