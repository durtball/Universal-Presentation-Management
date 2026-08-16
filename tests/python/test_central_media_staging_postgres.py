"""Regression coverage for the StorageRoot revision/upload schema mismatch."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from upm_central.persistence.models import Event, PresentationMediaImport, StorageRoot
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
    payload = b"generated central presentation upload"
    previously_enabled: list = []

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

        result = await CentralMediaStagingService(factory, str(tmp_path), 1024).stage(
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
            assert persisted.import_state in {
                MediaImportState.STAGED,
                MediaImportState.NEEDS_REVIEW,
            }
            assert persisted.staging_storage_root_id == root_id
            assert (tmp_path / persisted.staging_key).read_bytes() == payload
    finally:
        with factory.begin() as session:
            session.query(PresentationMediaImport).filter(
                PresentationMediaImport.event_id == event_id
            ).delete(synchronize_session=False)
            session.query(Event).filter(Event.event_id == event_id).delete(
                synchronize_session=False
            )
            session.query(StorageRoot).filter(StorageRoot.storage_root_id == root_id).delete(
                synchronize_session=False
            )
            if previously_enabled:
                session.execute(
                    StorageRoot.__table__.update()
                    .where(StorageRoot.storage_root_id.in_(previously_enabled))
                    .values(enabled=True)
                )
        engine.dispose()
