"""Focused regression coverage for deployment-aware Central presentation media."""

import os

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from upm_central.event_deployments import create_deployment, push_deployment
from upm_central.persistence.models import (
    Event,
    EventDeployment,
    OutboxEvent,
    Presentation,
    PresentationMediaImport,
    PresentationVersion,
    Site,
    TransferJob,
)
from upm_central.presentation_media import CentralMediaStagingService
from upm_shared.enums import (
    EnrollmentState,
    EventDeploymentStatus,
    MediaImportState,
    MediaMatchState,
)
from upm_shared.identifiers import new_uuid7

CENTRAL_URL = os.getenv("UPM_CENTRAL_DATABASE_URL")
pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(not CENTRAL_URL, reason="a migrated Central PostgreSQL URL is required"),
]


def _seed_event(session: Session, filename: str = "deployment-deck.pptx"):
    event_id, site_id, presentation_id, version_id, import_id = [new_uuid7() for _ in range(5)]
    session.add(
        Site(
            site_id=site_id,
            display_name="Media destination",
            enabled=True,
            enrollment_state=EnrollmentState.ACTIVE,
        )
    )
    session.add(Event(event_id=event_id, name="Media deployment", timezone="UTC"))
    presentation = Presentation(
        presentation_id=presentation_id,
        event_id=event_id,
        title="Deployment deck",
    )
    session.add(presentation)
    session.add(
        PresentationVersion(
            presentation_version_id=version_id,
            presentation_id=presentation_id,
            version_number=1,
        )
    )
    record = PresentationMediaImport(
        media_import_id=import_id,
        event_id=event_id,
        presentation_id=presentation_id,
        presentation_version_id=version_id,
        presentation_identifier=presentation.presentation_identifier,
        original_filename=filename,
        canonical_filename=f"deployment-deck--v001{filename[filename.rfind('.'):]}",
        staging_key=f"staging/{import_id}",
        committed_storage_key=f"objects/sha256/aa/{'a' * 64}",
        size_bytes=12,
        sha256="a" * 64,
        match_state=MediaMatchState.CONFIRMED,
        import_state=MediaImportState.ASSIGNED,
    )
    session.add(record)
    session.flush()
    return event_id, site_id, presentation_id, record


def test_media_confirmed_before_event_deployment_is_targeted_and_queued() -> None:
    engine = create_engine(CENTRAL_URL)
    connection = engine.connect()
    transaction = connection.begin()
    factory = sessionmaker(bind=connection, expire_on_commit=False)
    try:
        with factory() as session:
            event_id, site_id, _, record = _seed_event(session)
            deployment = create_deployment(session, event_id, site_id)
            session.flush()

            assert record.destination_site_id == site_id
            assert record.import_state is MediaImportState.TRANSFER_QUEUED
            transfer_id = record.transfer_job_id
            transfer = session.get(TransferJob, transfer_id)
            assert transfer.transfer_type == "presentation_media.central_to_site"
            assert transfer.owning_site_id == site_id
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(OutboxEvent)
                    .where(
                        OutboxEvent.aggregate_id == transfer_id,
                        OutboxEvent.event_type == "central.media_transfer.available",
                    )
                )
                == 1
            )

            push_deployment(session, deployment)
            session.flush()
            assert record.transfer_job_id == transfer_id
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(TransferJob)
                    .where(TransferJob.idempotency_key == f"central-media:{record.media_import_id}")
                )
                == 1
            )
    finally:
        transaction.rollback()
        connection.close()
        engine.dispose()


@pytest.mark.parametrize("filename", ["session-graphic.jpg", "speaker-show.ppsx"])
def test_supported_media_confirmed_after_deployment_queues_transfer(filename: str) -> None:
    engine = create_engine(CENTRAL_URL)
    connection = engine.connect()
    transaction = connection.begin()
    factory = sessionmaker(bind=connection, expire_on_commit=False)
    try:
        with factory() as session:
            event_id, site_id, presentation_id, record = _seed_event(session, filename)
            record.presentation_id = None
            record.presentation_version_id = None
            record.presentation_identifier = None
            record.canonical_filename = None
            record.match_state = MediaMatchState.UNMATCHED
            record.import_state = MediaImportState.NEEDS_REVIEW
            session.add(
                EventDeployment(
                    event_id=event_id,
                    site_id=site_id,
                    status=EventDeploymentStatus.DEPLOYED,
                    desired_revision=1,
                    acknowledged_revision=1,
                )
            )
            session.flush()

            CentralMediaStagingService(factory, None, 1024).assign(
                session, record, presentation_id, manual=True, actor="test-operator"
            )
            session.flush()

            assert record.destination_site_id == site_id
            assert record.import_state is MediaImportState.TRANSFER_QUEUED
            transfer = session.get(TransferJob, record.transfer_job_id)
            assert transfer.transfer_type == "presentation_media.central_to_site"
            assert transfer.owning_site_id == site_id
            assert record.canonical_filename.endswith(filename[filename.rfind(".") :])
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(OutboxEvent)
                    .where(
                        OutboxEvent.aggregate_id == transfer.transfer_job_id,
                        OutboxEvent.event_type == "central.media_transfer.available",
                    )
                )
                == 1
            )
    finally:
        transaction.rollback()
        connection.close()
        engine.dispose()
