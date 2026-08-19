"""Focused SMB Incoming discovery and source-lifecycle coverage."""

import hashlib
import os
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from upm_media_storage.api import create_app
from upm_media_storage.config import Settings
from upm_shared.media_storage_client import (
    MediaStorageOperationError,
    MediaStorageUnavailable,
)
from upm_shared.smb_intake import event_and_filename, incoming_identity, intake_candidate

ROOT = Path(__file__).resolve().parents[2]


def storage_client(tmp_path: Path) -> tuple[TestClient, Path, dict[str, str]]:
    incoming = tmp_path / "incoming"
    incoming.mkdir()
    token = "test-media-storage-token"
    app = create_app(Settings(service_token=token, smb_incoming_path=incoming))
    return TestClient(app), incoming, {"Authorization": f"Bearer {token}"}


def test_temp_files_are_ignored_and_supported_no_match_files_are_candidates() -> None:
    event_id = uuid4()
    assert intake_candidate(f"{event_id}/No Matching Roster Entry.pptx")
    assert intake_candidate(f"{event_id}/walk-in.pdf")
    assert intake_candidate("walk-in.pdf")
    assert not intake_candidate(f"{event_id}/~$walk-in.pptx")
    assert not intake_candidate(f"{event_id}/deck.pptx.partial")
    assert not intake_candidate(f"{event_id}/desktop.ini")
    assert event_and_filename(f"{event_id}/walk-in.pdf") == (event_id, "walk-in.pdf")


def test_identity_is_idempotent_but_changed_file_evidence_is_new() -> None:
    first = incoming_identity("event/deck.pptx", 100, 200)
    assert first == incoming_identity("event/deck.pptx", 100, 200)
    assert first != incoming_identity("event/deck.pptx", 101, 201)


def test_media_storage_lists_and_streams_incoming_after_restart(tmp_path: Path) -> None:
    client, incoming, headers = storage_client(tmp_path)
    event_id = uuid4()
    source = incoming / str(event_id) / "large deck.pptx"
    source.parent.mkdir()
    source.write_bytes(b"PK\x03\x04" + b"slides" * 100)

    listed = client.get("/api/v1/storage/smb/incoming", headers=headers).json()["files"]
    assert listed == [
        {
            "relative_path": f"{event_id}/large deck.pptx",
            "size_bytes": source.stat().st_size,
            "modified_ns": source.stat().st_mtime_ns,
        }
    ]
    response = client.get(
        f"/api/v1/storage/smb/incoming/{event_id}/large%20deck.pptx",
        headers=headers,
        params={"offset": 4, "limit": 12},
    )
    assert response.content == source.read_bytes()[4:16]


def test_source_is_removed_only_after_matching_stable_evidence(tmp_path: Path) -> None:
    client, incoming, headers = storage_client(tmp_path)
    event_id = uuid4()
    source = incoming / str(event_id) / "deck.pdf"
    source.parent.mkdir()
    source.write_bytes(b"%PDF-generated-at-runtime")
    evidence = {
        "size_bytes": source.stat().st_size,
        "modified_ns": source.stat().st_mtime_ns,
        "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
    }

    changed = client.post(
        f"/api/v1/storage/smb/incoming/{event_id}/deck.pdf/complete",
        headers=headers,
        json={**evidence, "size_bytes": evidence["size_bytes"] + 1},
    )
    assert changed.status_code == 409
    assert source.exists()

    completed = client.post(
        f"/api/v1/storage/smb/incoming/{event_id}/deck.pdf/complete",
        headers=headers,
        json=evidence,
    )
    assert completed.status_code == 200
    assert not source.exists()


def test_same_size_and_timestamp_with_changed_checksum_is_retained(tmp_path: Path) -> None:
    client, incoming, headers = storage_client(tmp_path)
    source = incoming / "deck.pdf"
    source.write_bytes(b"first-content")
    stat = source.stat()
    evidence = {
        "size_bytes": stat.st_size,
        "modified_ns": stat.st_mtime_ns,
        "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
    }
    source.write_bytes(b"other-content")
    os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns))

    response = client.post(
        "/api/v1/storage/smb/incoming/deck.pdf/complete", headers=headers, json=evidence
    )
    assert response.status_code == 409
    assert source.exists()


def test_retirement_permission_failure_has_specific_safe_error(tmp_path: Path, monkeypatch) -> None:
    client, incoming, headers = storage_client(tmp_path)
    source = incoming / "deck.pdf"
    source.write_bytes(b"%PDF-permission-test")
    evidence = {
        "size_bytes": source.stat().st_size,
        "modified_ns": source.stat().st_mtime_ns,
        "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
    }

    def deny_unlink(_path):
        raise PermissionError("host filesystem detail must not escape")

    monkeypatch.setattr(Path, "unlink", deny_unlink)
    response = client.post(
        "/api/v1/storage/smb/incoming/deck.pdf/complete", headers=headers, json=evidence
    )
    assert response.status_code == 500
    assert response.json() == {
        "detail": "Media Storage does not have permission to retire the SMB source.",
        "code": "smb_retirement_permission_denied",
    }
    assert source.exists()


class FakeStorage:
    def __init__(self, relative_path: str, content: bytes, modified_ns: int = 42):
        self.relative_path = relative_path
        self.content = content
        self.modified_ns = modified_ns
        self.completed = False

    def list_smb_incoming(self):
        return [
            {
                "relative_path": self.relative_path,
                "size_bytes": len(self.content),
                "modified_ns": self.modified_ns,
            }
        ]

    def read_smb_incoming(self, relative_path: str, offset: int, limit: int):
        assert relative_path == self.relative_path
        return self.content[offset : offset + limit]

    def complete_smb_incoming(
        self, relative_path: str, *, size_bytes: int, modified_ns: int, sha256: str
    ):
        assert (relative_path, size_bytes, modified_ns, sha256) == (
            self.relative_path,
            len(self.content),
            self.modified_ns,
            hashlib.sha256(self.content).hexdigest(),
        )
        self.completed = True


class FakeWork:
    def __init__(self, event_id, relative_path: str, content: bytes, modified_ns: int = 42):
        self.idempotency_key = incoming_identity(relative_path, len(content), modified_ns)
        self.payload = {
            "data": {
                "event_id": str(event_id),
                "relative_path": relative_path,
                "original_filename": Path(relative_path).name,
                "size_bytes": len(content),
                "modified_ns": modified_ns,
                "sha256": hashlib.sha256(content).hexdigest(),
                "source_share": "Incoming",
            }
        }


def test_central_ingest_commits_before_separate_source_retirement() -> None:
    from upm_central.smb_intake import ingest, retire

    event_id = uuid4()
    content = b"PK\x03\x04runtime-pptx"
    storage = FakeStorage("deck.pptx", content)
    work = FakeWork(event_id, "deck.pptx", content)

    class Staging:
        calls = 0

        async def stage(self, **values):
            self.calls += 1
            assert values["origin"] == "smb"
            assert values["source_share"] == "Incoming"
            assert values["idempotency_key"] == work.idempotency_key
            assert b"".join([chunk async for chunk in values["chunks"]]) == content
            return object()

    staging = Staging()
    ingest(work, staging, storage, chunk_bytes=5)
    assert staging.calls == 1
    assert not storage.completed
    retire(work, storage)
    assert storage.completed


def test_changed_source_is_not_ingested_or_removed() -> None:
    from upm_central.smb_intake import ingest

    event_id = uuid4()
    original = b"PK\x03\x04first"
    work = FakeWork(event_id, "deck.pptx", original)
    storage = FakeStorage("deck.pptx", original + b"changed")

    class Staging:
        async def stage(self, **_values):
            raise AssertionError("unstable source must not be staged")

    with pytest.raises(RuntimeError, match="changed before"):
        ingest(work, Staging(), storage)
    assert not storage.completed


def test_site_ingest_is_local_and_does_not_call_central() -> None:
    from upm_site.smb_intake import ingest, retire

    event_id, site_id = uuid4(), uuid4()
    content = b"%PDF-runtime-site"
    storage = FakeStorage("deck.pdf", content)
    work = FakeWork(event_id, "deck.pdf", content)
    work.payload["data"]["site_id"] = str(site_id)

    class Ingestion:
        async def ingest_async(self, request, chunks):
            assert request.site_id == site_id
            assert request.event_id == event_id
            assert request.intake_origin == "smb"
            assert request.replicate_to_central is True
            assert b"".join([chunk async for chunk in chunks]) == content
            return object()

    ingest(work, Ingestion(), storage, chunk_bytes=4)
    assert not storage.completed
    retire(work, storage)
    assert storage.completed


def test_retirement_failure_does_not_repeat_successful_intake() -> None:
    from upm_central.smb_intake import ingest, retire

    event_id = uuid4()
    content = b"PK\x03\x04durably-staged"
    work = FakeWork(event_id, "deck.pptx", content)

    class Storage(FakeStorage):
        def complete_smb_incoming(self, *_args, **_kwargs):
            raise MediaStorageUnavailable("temporary retirement failure")

    class Staging:
        calls = 0

        async def stage(self, **values):
            self.calls += 1
            assert b"".join([chunk async for chunk in values["chunks"]]) == content
            return object()

    storage = Storage("deck.pptx", content)
    staging = Staging()
    ingest(work, staging, storage)
    with pytest.raises(MediaStorageUnavailable, match="retirement failure"):
        retire(work, storage)
    assert staging.calls == 1
    assert not storage.completed


def test_changed_source_is_retained_during_retirement() -> None:
    from upm_site.smb_intake import retire

    event_id, site_id = uuid4(), uuid4()
    content = b"%PDF-original"
    work = FakeWork(event_id, "deck.pdf", content)
    work.payload["data"]["site_id"] = str(site_id)

    class ChangedStorage(FakeStorage):
        def complete_smb_incoming(self, *_args, **_kwargs):
            raise MediaStorageOperationError(
                "Incoming file changed during intake", 409, "incoming_source_changed"
            )

    storage = ChangedStorage("deck.pdf", content)
    assert retire(work, storage) == {"removed": False, "source_changed": True}
    assert not storage.completed


def test_workers_persist_retirement_jobs_after_intake() -> None:
    central = (ROOT / "central/python/src/upm_central/worker.py").read_text()
    site = (ROOT / "site/python/src/upm_site/worker.py").read_text()
    assert "session, media_work, sha256=smb_ingested_sha256" in central
    assert "enqueue_smb_retirement(session, work, sha256=result.content_hash)" in site
    assert 'error_code="smb_retirement_failed"' in site
