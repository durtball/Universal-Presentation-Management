"""Focused validation coverage for lifecycle durable-job contracts."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from upm_shared.jobs import JobPayload, LifecycleDeletionJobPayload


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
