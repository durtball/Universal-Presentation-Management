"""PostgreSQL concurrency and recovery tests for durable jobs and outbox."""

import os
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from upm_central.persistence.models import OutboxEvent as CentralOutboxEvent
from upm_shared.enums import JobStatus, SourceSystem
from upm_shared.jobs import PRIORITY_VALUES, JobPriority, utc_now
from upm_site.persistence.models import (
    OutboxEvent,
    ProcessingJob,
    Site,
    TransferJob,
    WorkerIdentity,
)
from upm_site.persistence.queue import SiteQueue

CENTRAL_URL = os.getenv("UPM_CENTRAL_DATABASE_URL")
SITE_URL = os.getenv("UPM_SITE_DATABASE_URL")

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(
        not CENTRAL_URL or not SITE_URL,
        reason="independent Central and Site PostgreSQL URLs are required",
    ),
]


@pytest.fixture
def site_factory() -> sessionmaker[Session]:
    engine = create_engine(SITE_URL)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory.begin() as session:
        session.execute(delete(OutboxEvent))
        session.execute(delete(ProcessingJob))
        session.execute(delete(TransferJob))
        session.execute(delete(WorkerIdentity))
    yield factory
    engine.dispose()


@pytest.fixture
def site_id(site_factory: sessionmaker[Session]):
    value = uuid4()
    with site_factory.begin() as session:
        session.add(Site(site_id=value, display_name=f"queue-test-{value}"))
    return value


def enqueue_processing(factory: sessionmaker[Session], site_id, **overrides) -> ProcessingJob:
    values = {
        "site_id": site_id,
        "job_type": "foundation.noop",
        "payload": {},
        "required_capabilities": [],
    }
    values.update(overrides)
    with factory.begin() as session:
        return SiteQueue(session).enqueue_processing(**values)


def test_concurrent_claim_uses_skip_locked_without_double_claim(
    site_factory: sessionmaker[Session], site_id
) -> None:
    first = enqueue_processing(site_factory, site_id)
    second = enqueue_processing(site_factory, site_id)
    session_one = site_factory()
    session_two = site_factory()
    try:
        claim_one = SiteQueue(session_one).claim_processing("worker-1", {"cpu"}, timedelta(30))
        claim_two = SiteQueue(session_two).claim_processing("worker-2", {"cpu"}, timedelta(30))
        assert claim_one is not None and claim_two is not None
        assert {claim_one.processing_job_id, claim_two.processing_job_id} == {
            first.processing_job_id,
            second.processing_job_id,
        }
        session_one.commit()
        session_two.commit()
    finally:
        session_one.close()
        session_two.close()


def test_lease_expiration_reclaim_and_heartbeat(
    site_factory: sessionmaker[Session], site_id
) -> None:
    job = enqueue_processing(site_factory, site_id)
    with site_factory.begin() as session:
        queue = SiteQueue(session)
        claimed = queue.claim_processing("crashed", set(), timedelta(seconds=30))
        assert claimed is not None
        claimed.lease_expires_at = utc_now() - timedelta(seconds=1)
    with site_factory.begin() as session:
        queue = SiteQueue(session)
        reclaimed = queue.claim_processing("replacement", set(), timedelta(seconds=60))
        assert reclaimed is not None
        assert reclaimed.processing_job_id == job.processing_job_id
        assert reclaimed.attempt_count == 2
        previous = reclaimed.lease_expires_at
        queue.heartbeat(reclaimed, "replacement", timedelta(seconds=120))
        assert reclaimed.lease_expires_at > previous


def test_completion_retry_and_max_attempt_exhaustion(
    site_factory: sessionmaker[Session], site_id
) -> None:
    complete_job = enqueue_processing(site_factory, site_id)
    retry_job = enqueue_processing(site_factory, site_id, max_attempts=2, priority=300)
    with site_factory.begin() as session:
        queue = SiteQueue(session)
        claimed = queue.claim_processing("worker", set(), timedelta(30))
        assert claimed.processing_job_id == retry_job.processing_job_id
        queue.fail(
            claimed,
            "worker",
            error_code="temporary",
            message="retry me",
            retryable=True,
            base_delay_seconds=5,
        )
        assert claimed.status == JobStatus.RETRY_WAIT
        assert claimed.next_attempt_at > utc_now()
        claimed.next_attempt_at = utc_now() - timedelta(seconds=1)
    with site_factory.begin() as session:
        queue = SiteQueue(session)
        claimed = queue.claim_processing("worker", set(), timedelta(30))
        assert claimed.processing_job_id == retry_job.processing_job_id
        queue.fail(
            claimed,
            "worker",
            error_code="still-temporary",
            message="attempts used",
            retryable=True,
            base_delay_seconds=5,
        )
        assert claimed.status == JobStatus.EXHAUSTED
    with site_factory.begin() as session:
        queue = SiteQueue(session)
        claimed = queue.claim_processing("worker", set(), timedelta(30))
        assert claimed.processing_job_id == complete_job.processing_job_id
        queue.complete(claimed, "worker")
        assert claimed.status == JobStatus.SUCCEEDED
        assert claimed.completed_at is not None


def test_priority_order_and_capability_filtering(
    site_factory: sessionmaker[Session], site_id
) -> None:
    cpu = enqueue_processing(
        site_factory,
        site_id,
        priority=PRIORITY_VALUES[JobPriority.NORMAL],
        required_capabilities=["cpu"],
    )
    gpu = enqueue_processing(
        site_factory,
        site_id,
        priority=PRIORITY_VALUES[JobPriority.CRITICAL],
        required_capabilities=["gpu"],
    )
    with site_factory.begin() as session:
        claimed = SiteQueue(session).claim_processing("cpu-worker", {"cpu"}, timedelta(30))
        assert claimed.processing_job_id == cpu.processing_job_id
    with site_factory.begin() as session:
        claimed = SiteQueue(session).claim_processing("gpu-worker", {"cpu", "gpu"}, timedelta(30))
        assert claimed.processing_job_id == gpu.processing_job_id


def test_job_idempotency_is_unique_within_job_type(
    site_factory: sessionmaker[Session], site_id
) -> None:
    enqueue_processing(site_factory, site_id, idempotency_key="same-operation")
    with pytest.raises(IntegrityError), site_factory.begin() as session:
        SiteQueue(session).enqueue_processing(
            site_id=site_id,
            job_type="foundation.noop",
            payload={},
            required_capabilities=[],
            idempotency_key="same-operation",
        )


def test_outbox_atomicity_claim_retry_and_success(
    site_factory: sessionmaker[Session], site_id
) -> None:
    aggregate_id = uuid4()
    rolled_back_site_id = uuid4()
    session = site_factory()
    try:
        session.add(Site(site_id=rolled_back_site_id, display_name="rolled-back-domain-change"))
        SiteQueue(session).enqueue_outbox(
            event_type="site.changed",
            aggregate_type="site",
            aggregate_id=aggregate_id,
            site_id=site_id,
            idempotency_key="rolled-back-event",
            payload={"source_system": SourceSystem.SITE, "data": {"safe": True}},
        )
        session.rollback()
    finally:
        session.close()
    with site_factory() as session:
        assert session.get(Site, rolled_back_site_id) is None
        assert (
            session.scalar(
                select(OutboxEvent).where(OutboxEvent.idempotency_key == "rolled-back-event")
            )
            is None
        )

    with site_factory.begin() as session:
        event = SiteQueue(session).enqueue_outbox(
            event_type="site.changed",
            aggregate_type="site",
            aggregate_id=aggregate_id,
            site_id=site_id,
            idempotency_key="committed-event",
            payload={"source_system": SourceSystem.SITE, "data": {"safe": True}},
            max_attempts=2,
        )
    with site_factory.begin() as session:
        queue = SiteQueue(session)
        claimed = queue.claim_outbox("sync-1", timedelta(30))
        assert claimed.outbox_event_id == event.outbox_event_id
        queue.fail_outbox(
            claimed,
            "sync-1",
            error_code="offline",
            message="Central unavailable",
            retryable=True,
            base_delay_seconds=1,
        )
        assert claimed.status == JobStatus.RETRY_WAIT
        claimed.available_at = utc_now() - timedelta(seconds=1)
    with site_factory.begin() as session:
        queue = SiteQueue(session)
        claimed = queue.claim_outbox("sync-2", timedelta(30))
        queue.process_outbox(claimed, "sync-2")
        assert claimed.status == JobStatus.SUCCEEDED
        assert claimed.processed_at is not None


def test_transfer_jobs_keep_distinct_semantics(
    site_factory: sessionmaker[Session], site_id
) -> None:
    with site_factory.begin() as session:
        transfer = SiteQueue(session).enqueue_transfer(
            site_id=site_id,
            transfer_type="room-delivery",
            payload={"data": {"destination": "room-a"}},
            required_capabilities=["transfer"],
        )
    with site_factory.begin() as session:
        queue = SiteQueue(session)
        assert queue.claim_transfer("cpu-only", {"cpu"}, timedelta(30)) is None
        claimed = queue.claim_transfer("transfer-worker", {"cpu", "transfer"}, timedelta(30))
        assert claimed.transfer_job_id == transfer.transfer_job_id
        assert claimed.transfer_type == "room-delivery"


def test_central_and_site_queue_persistence_is_isolated(
    site_factory: sessionmaker[Session], site_id
) -> None:
    key = f"isolation-{uuid4()}"
    with site_factory.begin() as session:
        SiteQueue(session).enqueue_outbox(
            event_type="isolation.test",
            aggregate_type="site",
            aggregate_id=uuid4(),
            site_id=site_id,
            idempotency_key=key,
            payload={"source_system": SourceSystem.SITE, "data": {}},
        )
    central_engine = create_engine(CENTRAL_URL)
    try:
        with Session(central_engine) as session:
            assert (
                session.scalar(
                    select(CentralOutboxEvent).where(CentralOutboxEvent.idempotency_key == key)
                )
                is None
            )
    finally:
        central_engine.dispose()


def test_outbox_idempotency_is_unique(site_factory: sessionmaker[Session], site_id) -> None:
    values = {
        "event_type": "idempotency.test",
        "aggregate_type": "site",
        "aggregate_id": uuid4(),
        "site_id": site_id,
        "idempotency_key": "same-event",
        "payload": {"source_system": SourceSystem.SITE, "data": {}},
    }
    with site_factory.begin() as session:
        SiteQueue(session).enqueue_outbox(**values)
    with pytest.raises(IntegrityError), site_factory.begin() as session:
        SiteQueue(session).enqueue_outbox(**values)
