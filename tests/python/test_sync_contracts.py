"""Synchronization contract, ownership, and retry classification tests."""

from datetime import UTC, datetime

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from upm_shared.contracts.sync import (
    MAX_SYNC_BATCH_EVENTS,
    UPM_SYNC_PROTOCOL_VERSION,
    SyncBatchRequest,
    SyncEventEnvelope,
)
from upm_shared.enums import AuthorityScope
from upm_shared.identifiers import new_uuid7
from upm_site.api import create_app as create_site_app
from upm_site.config import SiteSettings
from upm_site.sync_transport import DeliveryFailure, checked


def event(**overrides) -> SyncEventEnvelope:
    values = {
        "event_id": new_uuid7(),
        "event_type": "site.heartbeat",
        "protocol_version": UPM_SYNC_PROTOCOL_VERSION,
        "source": "site",
        "source_site_id": new_uuid7(),
        "source_sequence": 1,
        "authority": AuthorityScope.SITE,
        "entity_type": "site_operational_status",
        "entity_id": new_uuid7(),
        "occurred_at": datetime.now(UTC),
        "payload": {},
    }
    values.update(overrides)
    return SyncEventEnvelope(**values)


def test_protocol_version_is_independent_and_explicit() -> None:
    assert UPM_SYNC_PROTOCOL_VERSION == 1
    assert event(protocol_version=1).protocol_version == 1


def test_site_source_requires_permanent_site_identity() -> None:
    with pytest.raises(ValidationError):
        event(source_site_id=None)


def test_batch_count_is_bounded() -> None:
    with pytest.raises(ValidationError):
        SyncBatchRequest(
            protocol_version=UPM_SYNC_PROTOCOL_VERSION,
            events=[event(source_sequence=index + 1) for index in range(MAX_SYNC_BATCH_EVENTS + 1)],
        )


@pytest.mark.parametrize(
    ("status_code", "retryable"), [(503, True), (401, False), (409, False), (422, False)]
)
def test_http_failure_classification(status_code: int, retryable: bool) -> None:
    response = httpx.Response(
        status_code, request=httpx.Request("POST", "https://central.example/sync")
    )
    with pytest.raises(DeliveryFailure) as captured:
        checked(response)
    assert captured.value.retryable is retryable


def test_site_sync_request_size_is_bounded_before_database_work() -> None:
    settings = SiteSettings(
        database_url="postgresql+psycopg://unused:unused@invalid/site",
        sync_max_payload_bytes=1024,
    )
    with TestClient(create_site_app(settings=settings)) as client:
        response = client.post(
            "/api/v1/sync/central-events",
            content=b"x" * 1025,
            headers={"Content-Type": "application/json"},
        )
    assert response.status_code == 413
