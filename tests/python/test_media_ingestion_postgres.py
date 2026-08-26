"""PostgreSQL-backed real-file and API tests for Site media ingestion."""

from __future__ import annotations

import hashlib
import io
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from upm_shared.enums import AssetKind, MediaAvailability, MediaCategory, StorageHealth, StorageType
from upm_shared.identifiers import new_uuid7
from upm_site.api import create_app
from upm_site.config import SiteSettings
from upm_site.media.cleanup import cleanup_stale_ingestions
from upm_site.media.ingestion import IngestionError, IngestionRequest, MediaIngestionService
from upm_site.media.storage import generate_object_key, staging_path
from upm_site.persistence.models import (
    AuditRecord,
    Event,
    MediaObject,
    MediaReplicationSession,
    Presentation,
    PresentationAsset,
    PresentationVersion,
    ProcessingJob,
    Site,
    StorageTarget,
    TransferJob,
)
from upm_site.presentation_media_api import backfill_confirmed_original_assets

SITE_URL = os.getenv("UPM_SITE_DATABASE_URL")
pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(not SITE_URL, reason="a migrated Site PostgreSQL URL is required"),
]


class BoundedReader(io.BytesIO):
    def __init__(self, value: bytes, maximum_read: int) -> None:
        super().__init__(value)
        self.maximum_read = maximum_read
        self.read_calls = 0

    def read(self, size: int = -1) -> bytes:
        assert 0 < size <= self.maximum_read
        self.read_calls += 1
        return super().read(size)


class FailingReader(BoundedReader):
    def read(self, size: int = -1) -> bytes:
        if self.read_calls == 1:
            raise OSError("simulated interrupted upload")
        return super().read(size)


def test_site_confirmed_audit_backfills_original_asset(factory, media_context) -> None:
    media_id = new_uuid7()
    version_id = media_context["version_id"]
    with factory.begin() as session:
        session.add(
            MediaObject(
                media_object_id=media_id,
                site_id=media_context["site_id"],
                event_id=media_context["event_id"],
                storage_target_id=media_context["target_id"],
                object_key=f"objects/sha256/{media_id}",
                category=MediaCategory.PRESENTATION_VERSION,
                original_filename="generated.pptx",
                availability=MediaAvailability.AVAILABLE,
                size_bytes=9,
                content_hash="b" * 64,
                hash_algorithm="sha256",
            )
        )
        session.add(
            AuditRecord(
                actor_id="test",
                action="site.presentation_media.confirmed",
                target_type="media_object",
                target_id=media_id,
                site_id=media_context["site_id"],
                event_id=media_context["event_id"],
                after_context={
                    "presentation_version_id": str(version_id),
                    "presentation_id": str(media_context["presentation_id"]),
                },
            )
        )
    with factory.begin() as session:
        assert backfill_confirmed_original_assets(session, media_context["site_id"]) == 1
        assert backfill_confirmed_original_assets(session, media_context["site_id"]) == 0
        asset = session.scalar(
            select(PresentationAsset).where(PresentationAsset.presentation_version_id == version_id)
        )
        assert asset.kind is AssetKind.ORIGINAL
        assert asset.media_object_id == media_id


def test_adopt_committed_reuses_content_for_distinct_presentation_versions(
    factory: sessionmaker[Session], media_context: dict[str, object]
) -> None:
    version_two_id = new_uuid7()
    presentation_two_id = new_uuid7()
    media_id = new_uuid7()
    sha256 = "c" * 64
    key = f"objects/sha256/{sha256}"
    with factory.begin() as session:
        session.add(
            Presentation(
                presentation_id=presentation_two_id,
                event_id=media_context["event_id"],
                title="Second presentation",
            )
        )
        session.flush()
        session.add(
            PresentationVersion(
                presentation_version_id=version_two_id,
                presentation_id=presentation_two_id,
                version_number=1,
            )
        )
        session.add(
            MediaObject(
                media_object_id=media_id,
                site_id=media_context["site_id"],
                event_id=media_context["event_id"],
                storage_target_id=media_context["target_id"],
                object_key=key,
                category=MediaCategory.PRESENTATION_VERSION,
                original_filename="first-name.pptx",
                availability=MediaAvailability.AVAILABLE,
                size_bytes=12,
                content_hash=sha256,
                hash_algorithm="sha256",
            )
        )
    committed = {
        "storage_target_id": str(media_context["target_id"]),
        "storage_key": key,
        "internal_path": str(media_context["root"]),
    }
    ingestion = service(factory)
    requests = [
        request(
            media_context,
            category=MediaCategory.PRESENTATION_VERSION,
            presentation_version_id=media_context["version_id"],
            event_id=media_context["event_id"],
            original_filename="3365765-Slovak.pptx",
            idempotency_key="transfer:first",
        ),
        request(
            media_context,
            category=MediaCategory.PRESENTATION_VERSION,
            presentation_version_id=version_two_id,
            event_id=media_context["event_id"],
            original_filename="3420875-Brite.pptx",
            idempotency_key="transfer:second",
        ),
    ]
    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = executor.map(
            lambda item: ingestion.adopt_committed(item, committed, 12, sha256), requests
        )
    replay = ingestion.adopt_committed(
        request(
            media_context,
            category=MediaCategory.PRESENTATION_VERSION,
            presentation_version_id=version_two_id,
            event_id=media_context["event_id"],
            original_filename="3420875-Brite.pptx",
            idempotency_key="transfer:second",
        ),
        committed,
        12,
        sha256,
    )

    assert first.media_object_id == second.media_object_id == replay.media_object_id == media_id
    with factory() as session:
        assets = session.scalars(
            select(PresentationAsset)
            .where(PresentationAsset.media_object_id == media_id)
            .order_by(PresentationAsset.original_filename)
        ).all()
        assert {asset.presentation_version_id for asset in assets} == {
            media_context["version_id"],
            version_two_id,
        }
        assert {asset.original_filename for asset in assets} == {
            "3365765-Slovak.pptx",
            "3420875-Brite.pptx",
        }


def test_adopt_committed_finishes_interrupted_media_and_reuses_original_asset(
    factory: sessionmaker[Session], media_context: dict[str, object]
) -> None:
    media_id = new_uuid7()
    sha256 = "d" * 64
    key = f"objects/sha256/{sha256}"
    with factory.begin() as session:
        session.add(
            MediaObject(
                media_object_id=media_id,
                site_id=media_context["site_id"],
                event_id=media_context["event_id"],
                storage_target_id=media_context["target_id"],
                object_key=key,
                category=MediaCategory.PRESENTATION_VERSION,
                original_filename="session.jpg",
                availability=MediaAvailability.FINALIZING,
                ingestion_idempotency_key="transfer:interrupted",
            )
        )
        session.add(
            PresentationAsset(
                presentation_version_id=media_context["version_id"],
                media_object_id=media_id,
                original_filename="session.jpg",
                kind=AssetKind.ORIGINAL,
            )
        )
    committed = {
        "storage_target_id": str(media_context["target_id"]),
        "storage_key": key,
        "internal_path": str(media_context["root"]),
    }
    ingestion = service(factory)
    ingest_request = request(
        media_context,
        category=MediaCategory.PRESENTATION_VERSION,
        presentation_version_id=media_context["version_id"],
        event_id=media_context["event_id"],
        original_filename="session.jpg",
        idempotency_key="transfer:interrupted",
    )

    first = ingestion.adopt_committed(ingest_request, committed, 12, sha256)
    replay = ingestion.adopt_committed(ingest_request, committed, 12, sha256)

    assert first.media_object_id == replay.media_object_id == media_id
    with factory() as session:
        restored = session.scalar(
            select(MediaObject).where(MediaObject.media_object_id == media_id)
        )
        assert restored.availability is MediaAvailability.AVAILABLE
        assets = session.scalars(
            select(PresentationAsset).where(
                PresentationAsset.presentation_version_id == media_context["version_id"]
            )
        ).all()
        assert len(assets) == 1
        assert assets[0].media_object_id == media_id


def test_adopt_committed_repairs_original_asset_linked_to_incomplete_media(
    factory: sessionmaker[Session], media_context: dict[str, object]
) -> None:
    stale_id, canonical_id = new_uuid7(), new_uuid7()
    sha256 = "e" * 64
    key = f"objects/sha256/{sha256}"
    with factory.begin() as session:
        session.add_all(
            [
                MediaObject(
                    media_object_id=stale_id,
                    site_id=media_context["site_id"],
                    event_id=media_context["event_id"],
                    storage_target_id=media_context["target_id"],
                    object_key=f"staging/{stale_id}",
                    category=MediaCategory.PRESENTATION_VERSION,
                    original_filename="session.jpg",
                    availability=MediaAvailability.FINALIZING,
                ),
                MediaObject(
                    media_object_id=canonical_id,
                    site_id=media_context["site_id"],
                    event_id=media_context["event_id"],
                    storage_target_id=media_context["target_id"],
                    object_key=key,
                    category=MediaCategory.PRESENTATION_VERSION,
                    original_filename="session.jpg",
                    availability=MediaAvailability.AVAILABLE,
                    size_bytes=12,
                    content_hash=sha256,
                    hash_algorithm="sha256",
                ),
            ]
        )
        session.add(
            PresentationAsset(
                presentation_version_id=media_context["version_id"],
                media_object_id=stale_id,
                original_filename="session.jpg",
                kind=AssetKind.ORIGINAL,
            )
        )
    committed = {
        "storage_target_id": str(media_context["target_id"]),
        "storage_key": key,
        "internal_path": str(media_context["root"]),
    }
    ingestion = service(factory)
    ingest_request = request(
        media_context,
        category=MediaCategory.PRESENTATION_VERSION,
        presentation_version_id=media_context["version_id"],
        event_id=media_context["event_id"],
        original_filename="session.jpg",
        idempotency_key="transfer:repair",
    )

    first = ingestion.adopt_committed(ingest_request, committed, 12, sha256)
    replay = ingestion.adopt_committed(ingest_request, committed, 12, sha256)

    assert first.media_object_id == replay.media_object_id == canonical_id
    with factory() as session:
        assets = session.scalars(
            select(PresentationAsset).where(
                PresentationAsset.presentation_version_id == media_context["version_id"]
            )
        ).all()
        assert len(assets) == 1
        assert assets[0].media_object_id == canonical_id


@pytest.fixture
def factory() -> sessionmaker[Session]:
    engine = create_engine(SITE_URL)
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()


@pytest.fixture
def media_context(factory: sessionmaker[Session], tmp_path: Path) -> dict[str, object]:
    site_id = new_uuid7()
    event_id = new_uuid7()
    presentation_id = new_uuid7()
    version_id = new_uuid7()
    target_id = new_uuid7()
    with factory.begin() as session:
        session.add(Site(site_id=site_id, display_name=f"site-{site_id}"))
        session.flush()
        session.add(Event(event_id=event_id, site_id=site_id, name="Media Test"))
        session.flush()
        session.add(
            Presentation(
                presentation_id=presentation_id,
                event_id=event_id,
                title="Test presentation",
            )
        )
        session.flush()
        session.add(
            PresentationVersion(
                presentation_version_id=version_id,
                presentation_id=presentation_id,
                version_number=1,
            )
        )
        session.add(
            StorageTarget(
                storage_target_id=target_id,
                site_id=site_id,
                display_name="Primary",
                storage_type=StorageType.LOCAL_FILESYSTEM,
                root_path=str(tmp_path),
                enabled=True,
                primary_media=True,
                health=StorageHealth.UNKNOWN,
                warning_free_bytes=0,
                critical_free_bytes=0,
                safety_reserve_bytes=0,
            )
        )
    return {
        "site_id": site_id,
        "event_id": event_id,
        "presentation_id": presentation_id,
        "version_id": version_id,
        "target_id": target_id,
        "root": tmp_path,
    }


def service(factory: sessionmaker[Session]) -> MediaIngestionService:
    return MediaIngestionService(factory, max_upload_bytes=64 * 1024 * 1024, chunk_size=64 * 1024)


def request(context: dict[str, object], **overrides: object) -> IngestionRequest:
    values = {
        "site_id": context["site_id"],
        "original_filename": "deck.pptx",
        "category": MediaCategory.OPEN_FILE,
    }
    values.update(overrides)
    return IngestionRequest(**values)


def load_media(factory: sessionmaker[Session], media_id: UUID) -> MediaObject:
    with factory() as session:
        media = session.get(MediaObject, media_id)
        assert media is not None
        session.expunge(media)
        return media


def test_large_stream_is_chunked_hashed_finalized_and_enqueues_job(
    factory: sessionmaker[Session], media_context: dict[str, object]
) -> None:
    content = (b"large-test-block" * 8192) + b"tail"
    reader = BoundedReader(content, 64 * 1024)

    result = service(factory).ingest(
        request(media_context, expected_size=len(content), idempotency_key="large-upload"),
        reader,
    )
    media = load_media(factory, result.media_object_id)
    final_path = Path(media_context["root"]) / Path(media.object_key)

    assert reader.read_calls > 2
    assert result.availability == MediaAvailability.AVAILABLE
    assert result.content_hash == hashlib.sha256(content).hexdigest()
    assert media.hash_algorithm == "sha256"
    assert media.size_bytes == len(content)
    assert final_path.read_bytes() == content
    assert not (
        Path(media_context["root"]) / ".ingestion-staging" / f"{result.media_object_id}.upload"
    ).exists()
    with factory() as session:
        job = session.scalar(
            select(ProcessingJob).where(ProcessingJob.media_object_id == result.media_object_id)
        )
        assert job is not None
        assert job.job_type == "media.inspect"


def test_interrupted_upload_is_failed_and_staging_is_cleaned(
    factory: sessionmaker[Session], media_context: dict[str, object]
) -> None:
    with pytest.raises(IngestionError):
        service(factory).ingest(request(media_context), FailingReader(b"content", 64 * 1024))

    with factory() as session:
        media = session.scalars(
            select(MediaObject).where(MediaObject.site_id == media_context["site_id"])
        ).one()
        assert media.availability == MediaAvailability.FAILED
        assert (
            session.scalar(
                select(ProcessingJob).where(ProcessingJob.media_object_id == media.media_object_id)
            )
            is None
        )
    assert list((Path(media_context["root"]) / ".ingestion-staging").glob("*.upload")) == []


def test_size_mismatch_fails_without_publishing(
    factory: sessionmaker[Session], media_context: dict[str, object]
) -> None:
    with pytest.raises(IngestionError, match="expected size"):
        service(factory).ingest(
            request(media_context, expected_size=100), BoundedReader(b"short", 64 * 1024)
        )

    media = None
    with factory() as session:
        media = session.scalars(
            select(MediaObject).where(MediaObject.site_id == media_context["site_id"])
        ).one()
        assert media.availability == MediaAvailability.FAILED
        object_key = media.object_key
    assert not (Path(media_context["root"]) / Path(object_key)).exists()


def test_duplicate_names_and_hashes_remain_distinct(
    factory: sessionmaker[Session], media_context: dict[str, object]
) -> None:
    first = service(factory).ingest(request(media_context), io.BytesIO(b"same"))
    second = service(factory).ingest(request(media_context), io.BytesIO(b"same"))

    assert first.media_object_id != second.media_object_id
    assert first.content_hash == second.content_hash
    first_media = load_media(factory, first.media_object_id)
    second_media = load_media(factory, second.media_object_id)
    assert first_media.original_filename == second_media.original_filename == "deck.pptx"
    assert first_media.object_key != second_media.object_key


def test_idempotent_retry_returns_durable_identity(
    factory: sessionmaker[Session], media_context: dict[str, object]
) -> None:
    upload_request = request(media_context, idempotency_key="client-operation-1")
    first = service(factory).ingest(upload_request, io.BytesIO(b"original"))
    retry = service(factory).ingest(upload_request, io.BytesIO(b"must-not-be-written"))

    assert retry.media_object_id == first.media_object_id
    assert retry.duplicate_retry is True


def test_presentation_linked_and_open_file_ingestion_are_distinguishable(
    factory: sessionmaker[Session], media_context: dict[str, object]
) -> None:
    open_result = service(factory).ingest(request(media_context), io.BytesIO(b"open"))
    linked_result = service(factory).ingest(
        request(
            media_context,
            category=MediaCategory.PRESENTATION_VERSION,
            presentation_version_id=media_context["version_id"],
        ),
        io.BytesIO(b"linked"),
    )

    assert open_result.presentation_asset_id is None
    assert linked_result.presentation_asset_id is not None
    with factory() as session:
        assert (
            session.scalar(
                select(PresentationAsset).where(
                    PresentationAsset.media_object_id == linked_result.media_object_id
                )
            )
            is not None
        )
        replication = session.scalar(
            select(MediaReplicationSession).where(
                MediaReplicationSession.media_object_id == linked_result.media_object_id
            )
        )
        assert replication is not None
        assert replication.presentation_version_id == media_context["version_id"]
        transfer = session.get(TransferJob, replication.replication_session_id)
        assert transfer is not None
        assert transfer.transfer_type == "presentation_media.central_push"


def test_finalization_database_failure_is_reconciled(
    factory: sessionmaker[Session],
    media_context: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ingestion = service(factory)
    original_mark_available = ingestion._mark_available_and_enqueue
    monkeypatch.setattr(
        ingestion,
        "_mark_available_and_enqueue",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("simulated DB failure")),
    )

    with pytest.raises(IngestionError):
        ingestion.ingest(request(media_context), io.BytesIO(b"recoverable"))

    with factory() as session:
        media = session.scalars(
            select(MediaObject).where(MediaObject.site_id == media_context["site_id"])
        ).one()
        assert media.availability == MediaAvailability.FINALIZING
        media_id = media.media_object_id
        assert (Path(media_context["root"]) / Path(media.object_key)).exists()

    monkeypatch.setattr(ingestion, "_mark_available_and_enqueue", original_mark_available)
    result = ingestion.reconcile_finalizing(media_id)

    assert result.availability == MediaAvailability.AVAILABLE
    assert load_media(factory, media_id).availability == MediaAvailability.AVAILABLE


def test_cleanup_fails_abandoned_record_and_removes_staging_file(
    factory: sessionmaker[Session], media_context: dict[str, object]
) -> None:
    media_id = new_uuid7()
    old = datetime.now(UTC) - timedelta(days=2)
    with factory.begin() as session:
        target = session.get(StorageTarget, media_context["target_id"])
        assert target is not None
        session.add(
            MediaObject(
                media_object_id=media_id,
                site_id=media_context["site_id"],
                storage_target_id=target.storage_target_id,
                object_key=generate_object_key(MediaCategory.OPEN_FILE, media_id),
                category=MediaCategory.OPEN_FILE,
                original_filename="abandoned.pptx",
                availability=MediaAvailability.STAGING,
                created_at=old,
                updated_at=old,
            )
        )
        session.expunge(target)
    staged = staging_path(target, media_id)
    staged.write_bytes(b"partial")
    old_timestamp = old.timestamp()
    os.utime(staged, (old_timestamp, old_timestamp))

    removed = cleanup_stale_ingestions(factory, older_than=timedelta(days=1))

    assert str(staged) in removed
    assert not staged.exists()
    assert load_media(factory, media_id).availability == MediaAvailability.FAILED


def test_site_api_upload_metadata_status_and_storage_health_without_central(
    factory: sessionmaker[Session], media_context: dict[str, object]
) -> None:
    settings = SiteSettings(
        database_url=SITE_URL,
        media_mount_path=str(media_context["root"]),
        max_upload_bytes=1024 * 1024,
    )
    with TestClient(create_app(settings=settings, session_factory=factory)) as client:
        response = client.post(
            "/api/v1/media/ingestions",
            params={
                "site_id": str(media_context["site_id"]),
                "category": "open_file",
                "expected_size": "8",
            },
            content=b"%PDF-1.7",
            headers={
                "Content-Type": "text/plain",
                "X-UPM-Original-Filename": "walk-in.pdf",
                "X-UPM-Source-Relative-Path": "Event%20Slides%2Fday1%2Fwalk-in.pdf",
                "Idempotency-Key": "api-upload",
            },
        )
        assert response.status_code == 201, response.text
        payload = response.json()
        assert payload["availability"] == "available"
        assert payload["mime_type"] == "application/pdf"
        assert payload["original_filename"] == "walk-in.pdf"
        assert payload["source_relative_path"] == "Event Slides/day1/walk-in.pdf"

        metadata = client.get(f"/api/v1/media/{payload['media_object_id']}")
        assert metadata.status_code == 200
        assert metadata.json()["content_hash"] == hashlib.sha256(b"%PDF-1.7").hexdigest()

        ingestion_status = client.get(f"/api/v1/media/{payload['media_object_id']}/status")
        assert ingestion_status.status_code == 200
        assert ingestion_status.json()["availability"] == "available"

        health = client.get("/api/v1/storage-targets/health")
        assert health.status_code == 200
        assert health.json()[0]["available"] is True
