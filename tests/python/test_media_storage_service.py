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


def test_upm_usage_is_measured_separately_from_filesystem_usage(tmp_path):
    client = configured_client(tmp_path)
    payload = b"owned-by-upm"
    (tmp_path / "staging" / "one.upload").write_bytes(payload)

    target = client.get("/api/v1/storage/targets").json()["targets"][0]

    assert target["upm_owned_bytes"] == len(payload)
    assert target["object_count"] == 1
    assert target["used_bytes"] >= target["upm_owned_bytes"]


def test_target_activation_enforces_role_compatibility(tmp_path):
    client = configured_client(tmp_path)

    response = client.put(f"/api/v1/storage/assignments/staging/{MEDIA_ID}")

    assert response.status_code == 422
    assert "not compatible with staging" in response.json()["detail"]


def test_missing_mount_is_structured_unavailable(tmp_path):
    client = configured_client(tmp_path)
    (tmp_path / "staging").rmdir()
    result = client.post(f"/api/v1/storage/targets/{STAGING_ID}/test").json()
    assert result["health"] == "Unavailable"
    assert "not mounted" in result["detail"]


def test_permission_denied_is_structured_unavailable(tmp_path, monkeypatch):
    client = configured_client(tmp_path)
    monkeypatch.setattr("upm_media_storage.service.os.access", lambda *_args: False)

    result = client.post(f"/api/v1/storage/targets/{STAGING_ID}/test").json()

    assert result["health"] == "Unavailable"
    assert result["writable"] is False


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
    replay = client.post(
        "/api/v1/storage/objects/commit",
        json={
            "staging_target_id": allocation["storage_target_id"],
            "staging_key": allocation["storage_key"],
            "sha256": digest,
        },
    )
    assert replay.status_code == 200
    assert (
        client.get(
            f"/api/v1/storage/staging/{allocation['storage_target_id']}/{allocation['storage_key']}"
        ).content
        == payload
    )
    assert (
        client.get(
            f"/api/v1/storage/objects/{committed['storage_target_id']}/{committed['storage_key']}"
        ).content
        == payload
    )
    assert client.get(f"/api/v1/storage/staging/{STAGING_ID}/../escape").status_code in {404, 422}


def test_intake_publication_promotion_and_rejection_are_replay_safe(tmp_path):
    client = configured_client(tmp_path)

    def staged_payload(value: bytes):
        allocation = client.post("/api/v1/storage/staging/allocations").json()
        client.put(
            f"/api/v1/storage/staging/{allocation['storage_target_id']}/{allocation['storage_key']}",
            content=value,
        )
        return allocation, hashlib.sha256(value).hexdigest()

    allocation, digest = staged_payload(b"durable intake presentation")
    request = {
        "source_target_id": allocation["storage_target_id"],
        "source_key": allocation["storage_key"],
        "sha256": digest,
    }
    intake = client.post("/api/v1/storage/intake/publish", json=request).json()
    assert intake["storage_key"] == f"intake/sha256/{digest[:2]}/{digest}"
    assert client.post("/api/v1/storage/intake/publish", json=request).json() == intake

    promotion = client.post(
        "/api/v1/storage/intake/promote",
        json={
            "source_target_id": intake["storage_target_id"],
            "source_key": intake["storage_key"],
            "sha256": digest,
        },
    ).json()
    assert promotion["storage_key"] == f"objects/sha256/{digest[:2]}/{digest}"

    rejected_allocation, rejected_digest = staged_payload(b"retained rejected media")
    rejected_intake = client.post(
        "/api/v1/storage/intake/publish",
        json={
            "source_target_id": rejected_allocation["storage_target_id"],
            "source_key": rejected_allocation["storage_key"],
            "sha256": rejected_digest,
        },
    ).json()
    rejected_request = {
        "source_target_id": rejected_intake["storage_target_id"],
        "source_key": rejected_intake["storage_key"],
        "sha256": rejected_digest,
    }
    rejected = client.post("/api/v1/storage/intake/reject", json=rejected_request).json()
    assert rejected["storage_key"] == (f"rejected/sha256/{rejected_digest[:2]}/{rejected_digest}")
    assert client.post("/api/v1/storage/intake/reject", json=rejected_request).json() == rejected


def test_resumable_append_and_bounded_object_reads(tmp_path):
    client = configured_client(tmp_path)
    allocation = client.post("/api/v1/storage/staging/allocations").json()
    url = f"/api/v1/storage/staging/{allocation['storage_target_id']}/{allocation['storage_key']}"
    assert client.patch(url, params={"offset": 0}, content=b"first").json()["confirmed_offset"] == 5
    assert (
        client.patch(url, params={"offset": 5}, content=b"-second").json()["confirmed_offset"] == 12
    )
    assert client.patch(url, params={"offset": 0}, content=b"bad").status_code == 409
    digest = hashlib.sha256(b"first-second").hexdigest()
    committed = client.post(
        "/api/v1/storage/objects/commit",
        json={
            "staging_target_id": allocation["storage_target_id"],
            "staging_key": allocation["storage_key"],
            "sha256": digest,
        },
    ).json()
    response = client.get(
        f"/api/v1/storage/objects/{committed['storage_target_id']}/{committed['storage_key']}",
        params={"offset": 6, "limit": 3},
    )
    assert response.content == b"sec"


def test_assignment_persists_across_service_restart(tmp_path):
    client = configured_client(tmp_path)
    response = client.put(f"/api/v1/storage/assignments/staging/{STAGING_ID}")
    assert response.status_code == 200
    restarted = configured_client(tmp_path)
    assignments = restarted.get("/api/v1/storage/assignments").json()
    assert assignments["staging"]["storage_target_id"] == str(STAGING_ID)
