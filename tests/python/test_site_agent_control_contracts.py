from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi import FastAPI
from pydantic import ValidationError

from upm_site.agent_control import (
    CommandCreate,
    CommandUpdate,
    Heartbeat,
    LocalChanges,
    register_agent_control_routes,
)


def test_command_contract_rejects_unknown_fields_and_accepts_idempotency():
    value = CommandCreate(
        device_id=uuid4(),
        command_type="push_and_open",
        payload={"presentation_version_id": str(uuid4())},
        idempotency_key="manager:batch:item:push-open",
    )
    assert value.idempotency_key.endswith("push-open")
    with pytest.raises(ValidationError):
        CommandCreate(
            device_id=uuid4(), command_type="push", payload={}, idempotency_key="x", unexpected=True
        )


def test_agent_state_contract_only_permits_protocol_states():
    with pytest.raises(ValidationError):
        CommandUpdate(status="pending")
    assert CommandUpdate(status="running").status == "running"


def test_heartbeat_does_not_accept_online_boolean():
    with pytest.raises(ValidationError):
        Heartbeat(hostname="ROOM-01", agent_version="1.0", online=True)


def test_local_changes_requires_sha256_not_office_temp_signal():
    with pytest.raises(ValidationError):
        LocalChanges(
            filename="~$deck.pptx", size_bytes=5, sha256="bad", modified_at=datetime.now(UTC)
        )


def test_review_collection_registers_get_and_post_together():
    app = FastAPI()

    def unused_db():
        raise AssertionError("route registration must not access the database")

    register_agent_control_routes(app, unused_db, unused_db)
    methods = {
        method
        for route in app.routes
        if getattr(route, "path", None) == "/api/v1/review-sessions"
        for method in getattr(route, "methods", set())
    }

    assert methods == {"GET", "POST"}
