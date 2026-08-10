"""Unit tests for deployment contracts and explicit lifecycle rules."""

from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from upm_central.event_deployments import transition
from upm_central.persistence.models import EventDeployment
from upm_shared.contracts.deployments import EventDeploymentSnapshot
from upm_shared.enums import EventDeploymentStatus


def snapshot(**overrides) -> EventDeploymentSnapshot:
    person_id = uuid4()
    participation_id = uuid4()
    values = {
        "deployment_id": uuid4(),
        "deployment_revision": 1,
        "event_id": uuid4(),
        "site_id": uuid4(),
        "event_name": "UPM Expo",
        "people": [
            {
                "person_id": person_id,
                "display_name": "Presenter",
                "central_revision": 1,
            }
        ],
        "participations": [
            {
                "event_participation_id": participation_id,
                "person_id": person_id,
                "role": "presenter",
                "central_revision": 1,
            }
        ],
    }
    values.update(overrides)
    return EventDeploymentSnapshot.model_validate(values)


def test_snapshot_is_explicit_versioned_and_uuid_based() -> None:
    contract = snapshot()
    dumped = contract.model_dump(mode="json")
    assert dumped["schema_version"] == 1
    assert dumped["deployment_revision"] == 1
    assert isinstance(dumped["event_id"], str)


def test_snapshot_rejects_dangling_identity_relationship() -> None:
    with pytest.raises(ValidationError, match="undeployed person"):
        snapshot(
            participations=[
                {
                    "event_participation_id": uuid4(),
                    "person_id": uuid4(),
                    "central_revision": 1,
                }
            ]
        )


def test_lifecycle_rejects_invalid_transition() -> None:
    deployment = EventDeployment(
        event_id=uuid4(),
        site_id=uuid4(),
        status=EventDeploymentStatus.DRAFT,
    )
    transition(deployment, EventDeploymentStatus.PENDING)
    assert deployment.status == EventDeploymentStatus.PENDING
    with pytest.raises(HTTPException, match="invalid deployment transition"):
        transition(deployment, EventDeploymentStatus.ARCHIVED)
