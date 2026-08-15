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
from upm_central.persistence.base import CentralBase
from upm_central.persistence.models import DeletionOperation, Event, Person, ProcessingJob
from upm_central.persistence.queue import CentralQueue
from upm_central.worker import execute_processing_job
from upm_shared.enums import JobStatus
from upm_shared.jobs import LifecycleDeletionJobPayload

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
        payload = LifecycleDeletionJobPayload.model_validate(job.payload)
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
