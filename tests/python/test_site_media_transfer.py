from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import httpx

from upm_shared.enums import JobStatus, MediaTransferState, SourceSystem
from upm_site.config import SiteSettings
from upm_site.media.ingestion import IngestionConflictError
from upm_site.media.transfer import (
    enqueue_transfer_progress,
    execute_central_pull,
    recover_exhausted_finalizations,
)
from upm_site.persistence.models import MediaTransferSession, OutboxEvent
from upm_site.worker import defer_orphaned_pull, fail_central_pull


def test_partial_transfer_reference_is_opaque_and_durable() -> None:
    target_id = UUID("018f0000-0000-7000-8000-000000000001")
    transfer = MediaTransferSession(
        transfer_session_id=UUID("018f0000-0000-7000-8000-000000000002"),
        site_id=UUID("018f0000-0000-7000-8000-000000000003"),
        event_id=UUID("018f0000-0000-7000-8000-000000000004"),
        presentation_id=UUID("018f0000-0000-7000-8000-000000000005"),
        presentation_version_id=UUID("018f0000-0000-7000-8000-000000000006"),
        original_filename="deck.pptx",
        canonical_filename="deck.pptx",
        expected_size=1,
        sha256="0" * 64,
        partial_key="staging/opaque.upload",
        storage_target_id=target_id,
    )
    assert transfer.storage_target_id == target_id
    assert transfer.partial_key == "staging/opaque.upload"


def test_site_runtime_does_not_mount_legacy_presentation_data_path() -> None:
    repository = Path(__file__).resolve().parents[2]
    compose = (repository / "docker-compose.site.yml").read_text()
    config = (repository / "site/python/src/upm_site/config.py").read_text()

    assert "${SITE_MEDIA_HOST_PATH" not in compose
    assert "/data/objects" not in compose
    assert "/data/staging" not in compose
    assert "media_mount_path" not in config
    assert "staging_mount_path" not in config


def transfer_session() -> MediaTransferSession:
    return MediaTransferSession(
        transfer_session_id=UUID("018f0000-0000-7000-8000-000000000002"),
        site_id=UUID("018f0000-0000-7000-8000-000000000003"),
        event_id=UUID("018f0000-0000-7000-8000-000000000004"),
        presentation_id=UUID("018f0000-0000-7000-8000-000000000005"),
        presentation_version_id=UUID("018f0000-0000-7000-8000-000000000006"),
        original_filename="deck.pptx",
        canonical_filename="deck.pptx",
        expected_size=8,
        sha256="0" * 64,
        partial_key="staging/opaque.upload",
        storage_target_id=UUID("018f0000-0000-7000-8000-000000000001"),
        confirmed_offset=0,
        retry_count=0,
        state=MediaTransferState.AVAILABLE,
    )


def test_central_pull_uses_configured_reachable_endpoint(monkeypatch) -> None:
    transfer = transfer_session()
    work = SimpleNamespace(transfer_job_id=transfer.transfer_session_id)

    class Session:
        def get(self, *_args, **_kwargs):
            return transfer

    class Storage:
        def __init__(self, *_args):
            pass

        def append_staging(self, _target, _key, offset, block):
            return {"confirmed_offset": offset + len(block)}

    requested = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(request)
        return httpx.Response(
            200,
            headers={
                "X-UPM-Transfer-Offset": "0",
                "X-UPM-Transfer-Next-Offset": "4",
            },
            content=b"data",
        )

    central_url = "http://192.168.100.10:8080"
    monkeypatch.setattr("upm_site.media.transfer.MediaStorageClient", Storage)
    monkeypatch.setattr("upm_site.media.transfer.enqueue_transfer_progress", lambda *_args: None)
    monkeypatch.setattr(
        "upm_site.media.transfer.auth_context",
        lambda *_args: (
            object(),
            SimpleNamespace(central_url=central_url),
            {"Authorization": "Bearer token"},
        ),
    )
    settings = SiteSettings(
        database_url="postgresql+psycopg://u:p@db/site",
        central_url=central_url,
        credential_encryption_key="x" * 32,
        transfer_block_bytes=65_536,
    )
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert not execute_central_pull(Session(), None, settings, work, client)

    assert requested[0].url.host == "192.168.100.10"
    assert requested[0].url.port == 8080
    assert requested[0].url.params["offset"] == "0"


def test_duplicate_transfer_progress_is_idempotent() -> None:
    transfer = transfer_session()
    transfer.state = MediaTransferState.RETRY_WAIT
    existing = OutboxEvent(
        event_type="site.media_transfer.progress",
        aggregate_type="media_transfer",
        aggregate_id=transfer.transfer_session_id,
        site_id=transfer.site_id,
        source_system=SourceSystem.SITE,
        payload={},
        idempotency_key=(
            f"media-progress:{transfer.transfer_session_id}:0:0:{MediaTransferState.RETRY_WAIT}"
        ),
        status=JobStatus.PENDING,
    )

    class Session:
        def scalar(self, _statement):
            return existing

    assert enqueue_transfer_progress(Session(), transfer) is existing
    transfer.error_detail = "second transient failure"
    assert enqueue_transfer_progress(Session(), transfer) is existing
    assert existing.payload["error_detail"] == "second transient failure"


def test_transient_pull_failure_remains_retryable_and_worker_handler_returns(monkeypatch) -> None:
    transfer = transfer_session()
    work = SimpleNamespace(
        transfer_job_id=transfer.transfer_session_id,
        status=JobStatus.RUNNING,
    )

    class Session:
        def get(self, *_args, **_kwargs):
            return transfer

    class Queue:
        calls = 0

        def fail(self, job, _worker_id, **values):
            self.calls += 1
            assert values["retryable"] is True
            job.status = JobStatus.RETRY_WAIT

    emitted = []
    monkeypatch.setattr(
        "upm_site.worker.enqueue_transfer_progress", lambda _session, item: emitted.append(item)
    )
    settings = SiteSettings(
        database_url="postgresql+psycopg://u:p@db/site",
        credential_encryption_key="x" * 32,
    )
    queue = Queue()

    fail_central_pull(
        Session(), queue, work, "worker-1", httpx.ConnectError("temporary DNS failure"), settings
    )

    assert queue.calls == 1
    assert work.status is JobStatus.RETRY_WAIT
    assert transfer.state is MediaTransferState.RETRY_WAIT
    assert transfer.retry_count == 1
    assert transfer.error_detail == "temporary DNS failure"
    assert emitted == [transfer]


def test_original_asset_identity_conflict_is_not_retried(monkeypatch) -> None:
    transfer = transfer_session()
    work = SimpleNamespace(
        transfer_job_id=transfer.transfer_session_id,
        status=JobStatus.RUNNING,
    )

    class Session:
        def get(self, *_args, **_kwargs):
            return transfer

    class Queue:
        def fail(self, job, _worker_id, **values):
            assert values["retryable"] is False
            assert values["error_code"] == "media_pull_identity_conflict"
            job.status = JobStatus.FAILED

    monkeypatch.setattr("upm_site.worker.enqueue_transfer_progress", lambda *_args: None)
    fail_central_pull(
        Session(),
        Queue(),
        work,
        "worker-1",
        IngestionConflictError("canonical asset identity is corrupt"),
        SiteSettings(
            database_url="postgresql+psycopg://u:p@db/site",
            credential_encryption_key="x" * 32,
        ),
    )
    assert work.status is JobStatus.FAILED
    assert transfer.state is MediaTransferState.FAILED


def test_orphaned_pull_is_deferred_without_burning_a_retry() -> None:
    work = SimpleNamespace(
        status=JobStatus.RUNNING,
        required_capabilities=["transfer"],
        claimed_by_worker_id="worker-1",
        lease_expires_at=object(),
        heartbeat_at=object(),
        error_code=None,
        last_error=None,
        attempt_count=7,
    )

    defer_orphaned_pull(work)

    assert work.status is JobStatus.PENDING
    assert work.required_capabilities == ["sync-dependencies"]
    assert work.claimed_by_worker_id is None
    assert work.lease_expires_at is None
    assert work.heartbeat_at is None
    assert work.error_code == "sync_dependency_materialization_required"
    assert work.attempt_count == 7


def test_site_worker_has_same_central_endpoint_and_egress_networks_as_sync() -> None:
    compose = (Path(__file__).resolve().parents[2] / "docker-compose.site.yml").read_text()
    worker = compose.split("\n  site-worker:", 1)[1].split("\n  site-media-storage:", 1)[0]

    assert "UPM_SITE_CENTRAL_URL: ${UPM_SITE_CENTRAL_URL:-http://upm-central}" in worker
    assert "- site-sync-egress" in worker
    assert "- upm-integration" in worker


def test_exhausted_full_download_is_requeued_for_idempotent_finalization() -> None:
    job = SimpleNamespace(
        status=JobStatus.EXHAUSTED,
        attempt_count=3,
        claimed_by_worker_id="old-worker",
        lease_expires_at=object(),
        error_code="media_pull_failed",
        last_error="duplicate object key",
    )

    class Session:
        def scalars(self, _statement):
            return SimpleNamespace(all=lambda: [job])

    assert recover_exhausted_finalizations(Session()) == 1
    assert job.status is JobStatus.PENDING
    assert job.attempt_count == 0
    assert job.claimed_by_worker_id is None
    assert job.lease_expires_at is None
    assert job.error_code is None
