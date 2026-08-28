"""PostgreSQL regression tests for lifecycle request, persistence, and worker dispatch."""

import os
from collections.abc import Iterator
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateSchema, DropSchema

from upm_central.api import create_app
from upm_central.config import CentralDatabaseSettings
from upm_central.lifecycle import run_bulk_people_deletion
from upm_central.persistence.base import CentralBase
from upm_central.persistence.models import (
    AuditRecord,
    DeletionOperation,
    Event,
    EventDeployment,
    EventParticipation,
    MediaObjectReplica,
    OutboxEvent,
    Person,
    PersonIdentityLink,
    PersonIdentitySignal,
    ProcessingJob,
    RetainedPersonHistory,
    Site,
)
from upm_central.persistence.queue import CentralQueue
from upm_central.worker import execute_processing_job
from upm_shared.enums import EnrollmentState, EventDeploymentStatus, JobStatus, MediaCategory
from upm_shared.jobs import BulkPeopleDeletionJobPayload, LifecycleDeletionJobPayload

CENTRAL_URL = os.getenv("UPM_CENTRAL_DATABASE_URL")

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(not CENTRAL_URL, reason="Central PostgreSQL URL required"),
]


def _schema_url(raw: str, schema: str) -> str:
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
def lifecycle_database() -> Iterator[str]:
    schema = f"central_lifecycle_{uuid4().hex}"
    admin = create_engine(CENTRAL_URL)
    engine = None
    try:
        with admin.begin() as connection:
            connection.execute(CreateSchema(schema))
        scoped_url = _schema_url(CENTRAL_URL, schema)
        engine = create_engine(scoped_url)
        CentralBase.metadata.create_all(engine)
        yield scoped_url
    finally:
        if engine:
            engine.dispose()
        with admin.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True, if_exists=True))
        admin.dispose()


def _settings(database_url: str) -> CentralDatabaseSettings:
    return CentralDatabaseSettings(
        database_url=database_url,
        admin_token="test-administrator-token-at-least-32-characters",
        credential_issuer_key="test-credential-issuer-key-at-least-32-characters",
    )


def _claim_and_execute(engine, expected_type: str) -> tuple[UUID, UUID]:
    worker_id = f"test-worker-{uuid4()}"
    with Session(engine) as session, session.begin():
        queue = CentralQueue(session)
        job = queue.claim_processing(worker_id, set(), timedelta(seconds=30))
        assert job is not None
        assert job.job_type == expected_type
        payload = (
            BulkPeopleDeletionJobPayload.model_validate(job.payload)
            if expected_type == "lifecycle.delete_people_bulk"
            else LifecycleDeletionJobPayload.model_validate(job.payload)
        )
        operation_id = payload.data.deletion_operation_id
        target_id = session.get(DeletionOperation, operation_id).target_id
        assert execute_processing_job(session, queue, job, worker_id)
        queue.complete(job, worker_id)
    return operation_id, target_id


def test_event_and_person_requests_persist_typed_jobs_and_dispatch(
    lifecycle_database: str,
) -> None:
    settings = _settings(lifecycle_database)
    headers = {"X-UPM-Admin-Token": settings.admin_token}
    engine = create_engine(lifecycle_database)

    with TestClient(create_app(settings)) as client:
        event = client.post(
            "/api/v1/admin/events", headers=headers, json={"name": "Delete Me Event"}
        ).json()
        response = client.request(
            "DELETE",
            f"/api/v1/admin/events/{event['event_id']}",
            headers=headers,
            json={"confirmation": "Delete Me Event"},
        )
        assert response.status_code == 202
        event_operation_id = UUID(response.json()["deletion_operation_id"])

    with Session(engine) as session:
        job = session.scalar(
            select(ProcessingJob).where(ProcessingJob.job_type == "lifecycle.delete_event")
        )
        payload = LifecycleDeletionJobPayload.model_validate(job.payload)
        assert payload.data.deletion_operation_id == event_operation_id

    executed_operation_id, event_id = _claim_and_execute(engine, "lifecycle.delete_event")
    assert executed_operation_id == event_operation_id
    with Session(engine) as session, session.begin():
        operation = session.get(DeletionOperation, event_operation_id)
        assert operation.status == "completed"
        assert session.get(Event, event_id) is None
        # A recovered worker may safely call the handler again after the operation committed.
        from upm_central.lifecycle import run_deletion

        run_deletion(session, operation)
        assert operation.attempt_count == 1

    with Session(engine) as session, session.begin():
        person = Person(display_name="Delete Me Person", normalized_name="delete me person")
        session.add(person)
        session.flush()
        person_id = person.person_id
    with TestClient(create_app(settings)) as client:
        response = client.request(
            "DELETE",
            f"/api/v1/admin/people/{person_id}/lifecycle",
            headers=headers,
            json={"confirmation": "Delete Me Person"},
        )
        assert response.status_code == 202
        person_operation_id = UUID(response.json()["deletion_operation_id"])

    executed_operation_id, executed_person_id = _claim_and_execute(
        engine, "lifecycle.delete_person"
    )
    assert executed_operation_id == person_operation_id
    assert executed_person_id == person_id
    with Session(engine) as session:
        assert session.get(Person, person_id) is None
        job = session.scalar(
            select(ProcessingJob).where(ProcessingJob.job_type == "lifecycle.delete_person")
        )
        assert job.status == JobStatus.SUCCEEDED
    engine.dispose()


def test_queue_rejects_malformed_lifecycle_payload_but_accepts_unrelated_job(
    lifecycle_database: str,
) -> None:
    engine = create_engine(lifecycle_database)
    with pytest.raises(ValidationError), Session(engine) as session, session.begin():
        CentralQueue(session).enqueue_processing(
            job_type="lifecycle.delete_event",
            payload={"data": {"deletion_operation_id": "invalid"}},
            required_capabilities=[],
        )
    with Session(engine) as session, session.begin():
        job = CentralQueue(session).enqueue_processing(
            job_type="foundation.noop",
            payload={"data": {"existing": "payload"}},
            required_capabilities=[],
        )
        assert job.payload["data"] == {"existing": "payload"}
    engine.dispose()


def test_event_deletion_publishes_durable_site_tombstone_and_waits_for_ack(
    lifecycle_database: str,
) -> None:
    settings = _settings(lifecycle_database)
    engine = create_engine(lifecycle_database)
    with Session(engine) as session, session.begin():
        site = Site(
            display_name="Previously Deployed Site",
            enabled=True,
            enrollment_state=EnrollmentState.ACTIVE,
        )
        event = Event(name="Globally Deleted Event", timezone="UTC")
        session.add_all([site, event])
        session.flush()
        session.add(
            EventDeployment(
                event_id=event.event_id,
                site_id=site.site_id,
                status=EventDeploymentStatus.DEPLOYED,
                desired_revision=1,
                acknowledged_revision=1,
            )
        )
        event_id, site_id = event.event_id, site.site_id

    with TestClient(create_app(settings)) as client:
        response = client.request(
            "DELETE",
            f"/api/v1/admin/events/{event_id}",
            headers={"X-UPM-Admin-Token": settings.admin_token},
            json={"confirmation": "Globally Deleted Event"},
        )
        assert response.status_code == 202
        operation_id = UUID(response.json()["deletion_operation_id"])

    _claim_and_execute(engine, "lifecycle.delete_event")
    with Session(engine) as session:
        operation = session.get(DeletionOperation, operation_id)
        assert operation.status == "awaiting_sites"
        assert operation.stage == "site_deletion_pending"
        assert operation.site_statuses == [
            {
                "site_id": str(site_id),
                "status": "pending",
                "deletion_operation_id": str(operation_id),
            }
        ]
        tombstone = session.scalar(
            select(OutboxEvent).where(OutboxEvent.event_type == "central.event.deleted")
        )
        assert tombstone is not None
        assert tombstone.event_id is None
        assert tombstone.payload["event_id"] == str(event_id)
        assert tombstone.payload["deletion_operation_id"] == str(operation_id)
        assert session.get(Event, event_id) is None
    engine.dispose()


def test_bulk_people_deletion_targets_snapshot_and_preserves_audit(
    lifecycle_database: str,
) -> None:
    settings = _settings(lifecycle_database)
    headers = {"X-UPM-Admin-Token": settings.admin_token}
    engine = create_engine(lifecycle_database)
    with Session(engine) as session, session.begin():
        people = [
            Person(display_name=f"Bulk Person {number}", normalized_name=f"bulk person {number}")
            for number in range(3)
        ]
        session.add_all(people)
        session.flush()
        person_ids = {person.person_id for person in people}
        session.add(
            PersonIdentitySignal(
                person_id=people[0].person_id,
                signal_type="email",
                value="bulk@example.test",
                normalized_value="bulk@example.test",
            )
        )
        site = Site(
            display_name="Offline Site",
            enabled=True,
            enrollment_state=EnrollmentState.ACTIVE,
        )
        event = Event(name="Projected Event", timezone="UTC")
        session.add_all([site, event])
        session.flush()
        session.add(
            EventParticipation(
                event_id=event.event_id,
                person_id=people[0].person_id,
                display_name=people[0].display_name,
            )
        )
        session.add(
            EventDeployment(
                event_id=event.event_id,
                site_id=site.site_id,
                status=EventDeploymentStatus.DRAFT,
                desired_revision=0,
                acknowledged_revision=0,
            )
        )
        shared_media_id = uuid4()
        session.add(
            MediaObjectReplica(
                media_object_id=shared_media_id,
                authoritative_site_id=site.site_id,
                event_id=None,
                category=MediaCategory.OPEN_FILE,
                object_key="shared/unrelated.pdf",
                source_revision=1,
            )
        )
        event_id = event.event_id
        session.add(
            PersonIdentityLink(
                person_id=people[0].person_id,
                linked_person_id=people[1].person_id,
                link_type="related",
            )
        )
        session.add(
            RetainedPersonHistory(
                person_id=people[2].person_id,
                source_event_id=uuid4(),
                event_name="Retained Event",
                participation_summary={"role": "presenter"},
            )
        )
    with TestClient(create_app(settings)) as client:
        assert (
            client.post(
                "/api/v1/admin/people-bulk-deletion",
                json={"confirmation": "delete all"},
            ).status_code
            == 401
        )
        preview = client.get("/api/v1/admin/people-bulk-deletion/impact", headers=headers)
        assert preview.status_code == 200
        assert preview.json()["impact"]["people"] == 3
        assert (
            client.post(
                "/api/v1/admin/people-bulk-deletion",
                headers=headers,
                json={"confirmation": "DELETE ALL"},
            ).status_code
            == 422
        )
        response = client.post(
            "/api/v1/admin/people-bulk-deletion",
            headers=headers,
            json={"confirmation": "delete all"},
        )
        assert response.status_code == 202
        operation_id = UUID(response.json()["deletion_operation_id"])
        # Repeated submission resolves to the same active durable operation.
        repeated = client.post(
            "/api/v1/admin/people-bulk-deletion",
            headers=headers,
            json={"confirmation": "delete all"},
        )
        assert UUID(repeated.json()["deletion_operation_id"]) == operation_id

    with Session(engine) as session:
        jobs = session.scalars(
            select(ProcessingJob).where(ProcessingJob.job_type == "lifecycle.delete_people_bulk")
        ).all()
        assert len(jobs) == 1
        payload = BulkPeopleDeletionJobPayload.model_validate(jobs[0].payload)
        assert set(payload.data.person_ids) == person_ids
    executed_operation_id, _ = _claim_and_execute(engine, "lifecycle.delete_people_bulk")
    assert executed_operation_id == operation_id
    with Session(engine) as session:
        assert session.scalars(select(Person)).all() == []
        assert session.scalars(select(PersonIdentitySignal)).all() == []
        assert session.scalars(select(PersonIdentityLink)).all() == []
        assert session.scalars(select(RetainedPersonHistory)).all() == []
        assert session.get(Event, event_id) is not None
        assert session.get(MediaObjectReplica, shared_media_id) is not None
        assert session.scalars(select(EventParticipation)).all() == []
        # No Site acknowledgement is needed: the ADR-0007 update remains durable for offline poll.
        assert (
            session.scalar(
                select(OutboxEvent).where(
                    OutboxEvent.event_type == "central.event_deployment.requested"
                )
            )
            is not None
        )
        assert (
            session.scalar(
                select(OutboxEvent).where(OutboxEvent.event_type == "central.people.deleted")
            )
            is not None
        )
        operation = session.get(DeletionOperation, operation_id)
        assert operation.status == "completed"
        run_bulk_people_deletion(session, operation, list(person_ids))
        assert operation.attempt_count == 1
        assert (
            session.scalar(
                select(AuditRecord).where(AuditRecord.action == "central.people_bulk.deleted")
            )
            is not None
        )
    with TestClient(create_app(settings)) as client:
        assert (
            client.get("/api/v1/admin/people-bulk-deletion/impact", headers=headers).json()[
                "impact"
            ]["people"]
            == 0
        )
        assert (
            client.post(
                "/api/v1/admin/people-bulk-deletion",
                headers=headers,
                json={"confirmation": "delete all"},
            ).status_code
            == 409
        )
    engine.dispose()
