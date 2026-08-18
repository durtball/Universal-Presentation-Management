from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from upm_site.worker import enqueue_startup_maintenance


@pytest.mark.parametrize("role", ["site-worker", "site-sync"])
def test_startup_maintenance_always_enqueues_with_site_identity(role: str) -> None:
    site_id = uuid4()
    session = MagicMock()
    session.scalar.return_value = None
    queued = SimpleNamespace(site_id=site_id)

    with patch("upm_site.worker.SiteQueue") as queue_type:
        queue_type.return_value.enqueue_processing.return_value = queued
        result = enqueue_startup_maintenance(session, site_id=site_id, retention_days=30)

    assert result is queued, role
    values = queue_type.return_value.enqueue_processing.call_args.kwargs
    assert values["site_id"] == site_id
    assert values["site_id"] is not None
    assert values["idempotency_key"].startswith("operational-logs-prune:")
