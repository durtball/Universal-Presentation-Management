from __future__ import annotations

import hashlib
import json
from uuid import UUID

from fastapi.testclient import TestClient

from upm_media_storage.api import create_app
from upm_media_storage.config import Settings

TOKEN = "test-storage-service-token"
STAGING_ID = UUID("0198b8d0-63e0-7000-8000-000000000011")
MEDIA_ID = UUID("0198b8d0-63e0-7000-8000-000000000012")


def configured_client(tmp_path) -> TestClient:
    staging = tmp_path / "staging"
    media = tmp_path / "media"
    staging.mkdir(exist_ok=True)
    media.mkdir(exist_ok=True)
    targets = [
        {
            "storage_target_id": str(STAGING_ID),
            "name": "Temporary SSD",
            "internal_path": str(staging),
            "roles": ["staging"],
        },
        {
            "storage_target_id": str(MEDIA_ID),
            "name": "RAID Media",
            "internal_path": str(media),
            "roles": ["media"],
        },
    ]
    settings = Settings(
        service_token=TOKEN,
        targets_json=json.dumps(targets),
        state_path=tmp_path / "state" / "assignments.json",
    )
    return TestClient(create_app(settings), headers={"Authorization": f"Bearer {TOKEN}"})


def test_health_is_public_and_private_api_requires_authentication(tmp_path):
    client = configured_client(tmp_path)
    assert client.get("/health", headers={}).json()["status"] == "ok"
    anonymous = TestClient(client.app)
    assert anonymous.get("/api/v1/storage/targets").status_code == 401


def test_targets_report_capacity_and_probe_real_storage(tmp_path):
    client = configured_client(tmp_path)
    targets = client.get("/api/v1/storage/targets").json()["targets"]
    assert [target["name"] for target in targets] == ["Temporary SSD", "RAID Media"]
    result = client.post(f"/api/v1/storage/targets/{STAGING_ID}/test").json()
    assert result["health"] == "Healthy"
    assert result["readable"] is True and result["writable"] is True
    assert result["total_bytes"] > 0 and result["free_bytes"] > 0


def test_missing_mount_is_structured_unavailable(tmp_path):
    client = configured_client(tmp_path)
    (tmp_path / "staging").rmdir()
    result = client.post(f"/api/v1/storage/targets/{STAGING_ID}/test").json()
    assert result["health"] == "Unavailable"
    assert "not mounted" in result["detail"]


def test_staging_commit_is_content_addressed_and_rejects_traversal(tmp_path):
    client = configured_client(tmp_path)
    allocation = client.post("/api/v1/storage/staging/allocations").json()
    payload = b"programmatically generated presentation bytes"
    written = client.put(
        f"/api/v1/storage/staging/{allocation['storage_target_id']}/{allocation['storage_key']}",
        content=payload,
    ).json()
    digest = hashlib.sha256(payload).hexdigest()
    assert written["sha256"] == digest
    committed = client.post(
        "/api/v1/storage/objects/commit",
        json={
            "staging_target_id": allocation["storage_target_id"],
            "staging_key": allocation["storage_key"],
            "sha256": digest,
        },
    ).json()
    assert committed["storage_key"] == f"objects/sha256/{digest[:2]}/{digest}"
    assert (
        client.get(
            f"/api/v1/storage/objects/{committed['storage_target_id']}/{committed['storage_key']}"
        ).content
        == payload
    )
    assert client.get(f"/api/v1/storage/staging/{STAGING_ID}/../escape").status_code in {404, 422}


def test_assignment_persists_across_service_restart(tmp_path):
    client = configured_client(tmp_path)
    response = client.put(f"/api/v1/storage/assignments/staging/{STAGING_ID}")
    assert response.status_code == 200
    restarted = configured_client(tmp_path)
    assignments = restarted.get("/api/v1/storage/assignments").json()
    assert assignments["staging"]["storage_target_id"] == str(STAGING_ID)
