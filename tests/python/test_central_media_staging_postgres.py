"""Regression coverage for the StorageRoot revision/upload schema mismatch."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from upm_central.persistence.models import (
    AuditRecord,
    Event,
    PresentationMediaImport,
    ProcessingJob,
    StorageRoot,
)
from upm_central.presentation_media import CentralMediaStagingService
from upm_shared.enums import MediaImportState
from upm_shared.identifiers import new_uuid7

CENTRAL_URL = os.getenv("UPM_CENTRAL_DATABASE_URL")
pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(not CENTRAL_URL, reason="a migrated Central PostgreSQL URL is required"),
]


@pytest.mark.anyio
async def test_migrated_storage_root_can_be_queried_and_upload_staged(tmp_path: Path) -> None:
    engine = create_engine(CENTRAL_URL)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    event_id = new_uuid7()
    root_id = new_uuid7()
    media_root_id = new_uuid7()
    payload = b"generated central presentation upload"
    previously_enabled: list = []

    class StorageClient:
        writes = 0
        commits = 0

        async def allocate_staging(self):
            return {
                "storage_target_id": str(root_id),
                "storage_key": f"staging/{event_id}.upload",
                "name": "Regression staging",
                "internal_path": "/storage/staging",
            }

        async def write_staging(self, target_id, key, chunks):
            self.writes += 1
            content = b"".join([chunk async for chunk in chunks])
            (tmp_path / "staged").write_bytes(content)
            import hashlib

            return {
                "storage_target_id": str(target_id),
                "storage_key": key,
                "size_bytes": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            }

        async def commit(self, target_id, key, sha256):
            self.commits += 1
            (tmp_path / "committed").write_bytes((tmp_path / "staged").read_bytes())
            return {
                "storage_target_id": str(media_root_id),
                "storage_key": f"objects/sha256/{sha256[:2]}/{sha256}",
                "name": "Regression media",
                "internal_path": "/storage/media",
            }

    async def chunks():
        yield payload[:12]
        yield payload[12:]

    try:
        with factory.begin() as session:
            previously_enabled = list(
                session.scalars(
                    select(StorageRoot.storage_root_id).where(StorageRoot.enabled.is_(True))
                )
            )
            session.execute(
                StorageRoot.__table__.update()
                .where(StorageRoot.enabled.is_(True))
                .values(enabled=False)
            )
            session.add(
                Event(event_id=event_id, name="Storage revision regression", timezone="UTC")
            )
            session.add(
                StorageRoot(
                    storage_root_id=root_id,
                    role="staging",
                    display_name="Regression staging",
                    backend_type="filesystem",
                    path=str(tmp_path),
                    enabled=True,
                )
            )

        storage = StorageClient()
        result = await CentralMediaStagingService(factory, storage, 1024).stage(
            event_id=event_id,
            destination_site_id=None,
            original_filename="unmatched-deck.pptx",
            source_relative_path=None,
            content_type="application/vnd.ms-powerpoint",
            idempotency_key=f"storage-revision-{event_id}",
            chunks=chunks(),
            actor="regression-test",
        )

        with factory() as session:
            active = session.scalar(
                select(StorageRoot).where(
                    StorageRoot.role == "staging", StorageRoot.enabled.is_(True)
                )
            )
            persisted = session.get(PresentationMediaImport, result.media_import_id)
            assert active is not None and active.revision == 1
            assert persisted is not None
            assert persisted.import_state == MediaImportState.STAGED
            assert persisted.staging_storage_root_id == root_id
            assert persisted.committed_storage_root_id is None
            assert persisted.committed_storage_key is None
            queued = session.scalar(
                select(ProcessingJob).where(
                    ProcessingJob.idempotency_key == str(result.media_import_id)
                )
            )
            assert queued is not None
            assert storage.writes == 1
            assert storage.commits == 0
            assert (tmp_path / "staged").read_bytes() == payload
        replay = await CentralMediaStagingService(factory, storage, 1024).stage(
            event_id=event_id,
            destination_site_id=None,
            original_filename="unmatched-deck.pptx",
            source_relative_path=None,
            content_type="application/vnd.ms-powerpoint",
            idempotency_key=f"storage-revision-{event_id}",
            chunks=chunks(),
            actor="regression-test",
        )
        assert replay.media_import_id == result.media_import_id
        assert storage.writes == 1
        processed = CentralMediaStagingService(factory, storage, 1024).analyze(
            result.media_import_id
        )
        assert processed.import_state == MediaImportState.NEEDS_REVIEW
        assert storage.commits == 0
        assert not (tmp_path / "committed").exists()
    finally:
        with factory.begin() as session:
            session.query(ProcessingJob).filter(
                ProcessingJob.idempotency_key == str(result.media_import_id)
            ).delete(synchronize_session=False)
            session.query(PresentationMediaImport).filter(
                PresentationMediaImport.event_id == event_id
            ).delete(synchronize_session=False)
            session.query(Event).filter(Event.event_id == event_id).delete(
                synchronize_session=False
            )
            session.query(StorageRoot).filter(StorageRoot.storage_root_id == root_id).delete(
                synchronize_session=False
            )
            session.query(StorageRoot).filter(StorageRoot.storage_root_id == media_root_id).delete(
                synchronize_session=False
            )
            if previously_enabled:
                session.execute(
                    StorageRoot.__table__.update()
                    .where(StorageRoot.storage_root_id.in_(previously_enabled))
                    .values(enabled=True)
                )
        engine.dispose()


@pytest.mark.anyio
async def test_generated_500_file_batch_remains_staged_before_confirmation() -> None:
    engine = create_engine(CENTRAL_URL)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    event_id = new_uuid7()
    staging_id = new_uuid7()
    import_ids = []

    class StorageClient:
        commits = 0
        sequence = 0

        async def allocate_staging(self):
            self.sequence += 1
            return {
                "storage_target_id": str(staging_id),
                "storage_key": f"staging/generated-{self.sequence}.upload",
                "name": "Generated staging",
                "internal_path": "/storage/staging",
            }

        async def write_staging(self, target_id, key, chunks):
            import hashlib

            content = b"".join([chunk async for chunk in chunks])
            return {
                "storage_target_id": str(target_id),
                "storage_key": key,
                "size_bytes": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            }

        async def commit(self, *_args):
            self.commits += 1
            raise AssertionError("initial intake must not commit canonical media")

    try:
        with factory.begin() as session:
            session.add(Event(event_id=event_id, name="Generated 500 batch", timezone="UTC"))
        storage = StorageClient()
        service = CentralMediaStagingService(factory, storage, 1024)
        for index in range(500):
            payload = f"generated-presentation-{index}".encode()

            async def chunks(content=payload):
                yield content

            item = await service.stage(
                event_id=event_id,
                destination_site_id=None,
                original_filename=f"unknown-{index}.pptx",
                source_relative_path=f"batch/unknown-{index}.pptx",
                content_type="application/vnd.ms-powerpoint",
                idempotency_key=f"generated-500-{event_id}-{index}",
                chunks=chunks(),
                actor="batch-regression-test",
            )
            import_ids.append(item.media_import_id)
        with factory() as session:
            imports = session.scalars(
                select(PresentationMediaImport).where(PresentationMediaImport.event_id == event_id)
            ).all()
            assert len(imports) == 500
            assert all(item.import_state == MediaImportState.STAGED for item in imports)
            assert all(item.staging_key and item.staging_storage_root_id for item in imports)
            assert all(item.committed_storage_key is None for item in imports)
            assert all(item.presentation_version_id is None for item in imports)
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(ProcessingJob)
                    .where(ProcessingJob.idempotency_key.in_([str(value) for value in import_ids]))
                )
                == 500
            )
            assert storage.commits == 0
    finally:
        with factory.begin() as session:
            session.query(ProcessingJob).filter(
                ProcessingJob.idempotency_key.in_([str(value) for value in import_ids])
            ).delete(synchronize_session=False)
            session.query(PresentationMediaImport).filter(
                PresentationMediaImport.event_id == event_id
            ).delete(synchronize_session=False)
            session.query(AuditRecord).filter(AuditRecord.event_id == event_id).delete(
                synchronize_session=False
            )
            session.query(Event).filter(Event.event_id == event_id).delete(
                synchronize_session=False
            )
            session.query(StorageRoot).filter(StorageRoot.storage_root_id == staging_id).delete(
                synchronize_session=False
            )
        engine.dispose()
