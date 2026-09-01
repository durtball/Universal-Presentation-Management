from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import FastAPI
from pydantic import ValidationError

from upm_site.agent_control import (
    AgentConfigurationWrite,
    CommandCreate,
    CommandUpdate,
    EventBrandingWrite,
    Heartbeat,
    LocalChanges,
    discovery_signature,
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


def test_agent_configuration_has_closed_role_vocabulary():
    event_id = uuid4()
    assert (
        AgentConfigurationWrite(event_id=event_id, agent_role="room_agent_kiosk").event_id
        == event_id
    )
    with pytest.raises(ValidationError):
        AgentConfigurationWrite(event_id=event_id, agent_role="signage")


def test_room_agent_sync_and_media_routes_are_registered():
    app = FastAPI()

    def unused_db():
        raise AssertionError("route registration must not access the database")

    register_agent_control_routes(app, unused_db, unused_db)
    paths = {getattr(route, "path", None) for route in app.routes}
    assert "/api/v1/agent/bootstrap" in paths
    assert "/api/v1/agent/discovery-metadata" in paths
    assert "/api/v1/agent/enroll" in paths
    assert "/api/v1/agent/changes" in paths
    assert "/api/v1/agent/presentation-versions/{version_id}/download" in paths
    assert "/api/v1/agent/branding-assets/{asset_id}/download" in paths
    assert "/api/v1/devices/{device_id}/room-agent-assignment" in paths


def test_room_agent_assignment_supports_put_and_clear_without_reenrollment():
    app = FastAPI()

    def unused_db():
        raise AssertionError("route registration must not access the database")

    register_agent_control_routes(app, unused_db, unused_db)
    methods = {
        method
        for route in app.routes
        if getattr(route, "path", None)
        == "/api/v1/devices/{device_id}/room-agent-assignment"
        for method in getattr(route, "methods", set())
    }
    assert methods == {"PUT", "DELETE"}


def test_agent_change_feed_repair_uses_jsonb_and_tracks_device_assignments():
    source = Path(
        "database/site/migrations/versions/"
        "a73c5e91f204_repair_agent_change_feed_assignments.py"
    ).read_text()
    assert "to_jsonb(OLD)" in source
    assert "to_jsonb(NEW)" in source
    assert '"device_assignments": "device_assignment_id"' in source
    assert "tr_device_assignments_agent_change" in source
    assert "row_record.session_id" not in source


def test_branding_contract_rejects_unknown_asset_slots():
    with pytest.raises(ValidationError):
        EventBrandingWrite(source="site_managed", assets={"executable": uuid4()})


def test_discovery_ticket_signature_is_stable_and_binds_endpoint():
    site_id = uuid4()
    first = discovery_signature("s" * 32, site_id, "http://10.0.0.8:9080/", 123, "nonce-value")
    assert first == discovery_signature(
        "s" * 32, site_id, "http://10.0.0.8:9080/", 123, "nonce-value"
    )
    assert first != discovery_signature(
        "s" * 32, site_id, "http://10.0.0.9:9080/", 123, "nonce-value"
    )
