"""Focused validation coverage for lifecycle durable-job contracts."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from upm_shared.jobs import (
    BulkPeopleDeletionJobPayload,
    JobPayload,
    LifecycleDeletionJobPayload,
)


def test_lifecycle_payload_requires_typed_operation_identity() -> None:
    operation_id = uuid4()
    payload = LifecycleDeletionJobPayload.model_validate(
        {"schema_version": 1, "data": {"deletion_operation_id": str(operation_id)}}
    )
    assert payload.data.deletion_operation_id == operation_id

    with pytest.raises(ValidationError):
        LifecycleDeletionJobPayload.model_validate(
            {"schema_version": 1, "data": {"deletion_operation_id": "not-a-uuid"}}
        )
    with pytest.raises(ValidationError):
        LifecycleDeletionJobPayload.model_validate(
            {
                "schema_version": 1,
                "data": {"deletion_operation_id": str(operation_id), "unexpected": True},
            }
        )


def test_unrelated_job_payload_contract_remains_compatible() -> None:
    payload = JobPayload.model_validate(
        {"schema_version": 2, "data": {"media_object_id": str(uuid4()), "mode": "inspect"}}
    )
    assert payload.schema_version == 2
    assert payload.data["mode"] == "inspect"


def test_bulk_people_payload_requires_a_nonempty_typed_target_snapshot() -> None:
    operation_id, person_id = uuid4(), uuid4()
    payload = BulkPeopleDeletionJobPayload.model_validate(
        {
            "data": {
                "deletion_operation_id": str(operation_id),
                "person_ids": [str(person_id)],
            }
        }
    )
    assert payload.data.deletion_operation_id == operation_id
    assert payload.data.person_ids == [person_id]
    with pytest.raises(ValidationError):
        BulkPeopleDeletionJobPayload.model_validate(
            {"data": {"deletion_operation_id": str(operation_id), "person_ids": []}}
        )
