from pathlib import Path
from threading import Event, Lock
from time import monotonic, sleep
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import Integer

from upm_central.api import create_app
from upm_central.config import CentralDatabaseSettings
from upm_central.persistence.models import StorageRoot
from upm_central.presentation_media import MediaStagingError, _safe_staging_path
from upm_central.worker import PresentationMediaJobPool, execute_processing_job


def test_staging_key_cannot_escape_configured_root(tmp_path: Path) -> None:
    assert _safe_staging_path(tmp_path, "018f.upload") == tmp_path / "018f.upload"
    with pytest.raises(MediaStagingError, match="invalid staging key"):
        _safe_staging_path(tmp_path, "../outside")
    with pytest.raises(MediaStagingError, match="invalid staging key"):
        _safe_staging_path(tmp_path, "nested/file")


def test_central_media_routes_are_authenticated_and_versioned() -> None:
    routes = {
        (method, route.path): route
        for route in create_app().routes
        for method in getattr(route, "methods", set())
    }
    expected = {
        ("POST", "/api/v1/admin/events/{event_id}/media-imports"),
        ("GET", "/api/v1/admin/events/{event_id}/media-imports"),
        ("GET", "/api/v1/admin/media-imports/{media_import_id}"),
        ("GET", "/api/v1/admin/presentation-versions/{presentation_version_id}/download"),
        ("POST", "/api/v1/admin/events/{event_id}/media-imports/rescan"),
        ("GET", "/api/v1/admin/media-rescans/{operation_id}"),
        (
            "GET",
            "/api/v1/admin/events/{event_id}/presentation-match-candidates",
        ),
        ("POST", "/api/v1/admin/media-imports/{media_import_id}/match"),
        (
            "POST",
            "/api/v1/admin/events/{event_id}/presentation-materialization",
        ),
        (
            "PUT",
            "/api/v1/admin/media-imports/{media_import_id}/assignment/{presentation_id}",
        ),
        ("POST", "/api/v1/admin/media-imports/{media_import_id}/retry"),
        ("POST", "/api/v1/admin/media-imports/{media_import_id}/cancel"),
    }
    assert expected <= routes.keys()
    for key in expected:
        assert routes[key].dependencies, f"{key} must require Central administrator authorization"
    assert ("GET", "/api/v1/media-transfers/{transfer_session_id}") in routes
    assert ("GET", "/api/v1/media-transfers/{transfer_session_id}/content") in routes
    assert ("POST", "/api/v1/media-replications") in routes
    assert ("GET", "/api/v1/media-replications/{replication_session_id}") in routes
    assert ("PUT", "/api/v1/media-replications/{replication_session_id}/content") in routes
    assert (
        "POST",
        "/api/v1/media-replications/{replication_session_id}/finalize",
    ) in routes


def test_storage_root_uses_standard_central_record_revision() -> None:
    revision = StorageRoot.__table__.columns["revision"]

    assert isinstance(revision.type, Integer)
    assert revision.nullable is False
    assert revision.default is not None
    assert revision.default.arg == 1


def test_worker_dispatches_dedicated_presentation_media_rescan_job() -> None:
    job_id = uuid4()

    class Processor:
        called_with = None

        def rescan(self, processing_job_id, worker_id):
            self.called_with = (processing_job_id, worker_id)

    processor = Processor()
    work = SimpleNamespace(
        job_type="presentation_media.rescan",
        processing_job_id=job_id,
        payload={"data": {"event_id": str(uuid4())}},
    )

    assert execute_processing_job(None, None, work, "worker-1", processor)
    assert processor.called_with == (job_id, "worker-1")


def test_presentation_media_concurrency_setting_defaults_and_validates(monkeypatch) -> None:
    monkeypatch.setenv("UPM_CENTRAL_DATABASE_URL", "postgresql+psycopg://db/test")
    monkeypatch.setenv("UPM_CENTRAL_ADMIN_TOKEN", "a" * 32)
    monkeypatch.setenv("UPM_CENTRAL_CREDENTIAL_ISSUER_KEY", "b" * 32)
    assert CentralDatabaseSettings().presentation_media_concurrency == 4
    monkeypatch.setenv("UPM_CENTRAL_PRESENTATION_MEDIA_CONCURRENCY", "16")
    assert CentralDatabaseSettings().presentation_media_concurrency == 16
    monkeypatch.setenv("UPM_CENTRAL_PRESENTATION_MEDIA_CONCURRENCY", "17")
    with pytest.raises(ValueError):
        CentralDatabaseSettings()


def test_media_pool_one_serializes_and_rejects_duplicate_processing() -> None:
    pool = PresentationMediaJobPool(1)
    release = Event()
    started = Event()
    job_id = uuid4()

    def work() -> None:
        started.set()
        release.wait(2)

    try:
        assert pool.submit(job_id, work)
        assert started.wait(1)
        assert pool.available == 0
        assert not pool.submit(uuid4(), work)
        assert not pool.submit(job_id, work)
        release.set()
        deadline = monotonic() + 2
        while not pool.reap() and monotonic() < deadline:
            sleep(0.01)
        assert pool.available == 1
    finally:
        release.set()
        pool.shutdown()


def test_media_pool_four_run_in_parallel_fifth_waits_and_failure_is_isolated() -> None:
    pool = PresentationMediaJobPool(4)
    release = Event()
    four_started = Event()
    lock = Lock()
    in_flight = 0
    peak = 0

    def work(*, fail: bool = False) -> None:
        nonlocal in_flight, peak
        with lock:
            in_flight += 1
            peak = max(peak, in_flight)
            if in_flight == 4:
                four_started.set()
        release.wait(2)
        with lock:
            in_flight -= 1
        if fail:
            raise RuntimeError("synthetic failure")

    ids = [uuid4() for _ in range(5)]
    try:
        for index, job_id in enumerate(ids[:4]):
            assert pool.submit(job_id, lambda fail=index == 0: work(fail=fail))
        assert four_started.wait(1)
        assert peak == 4
        assert pool.available == 0
        assert not pool.submit(ids[4], work)
        release.set()
        completed = []
        deadline = monotonic() + 2
        while len(completed) < 4 and monotonic() < deadline:
            completed.extend(pool.reap())
            sleep(0.01)
        assert len(completed) == 4
        assert sum(error is not None for _, error in completed) == 1
        assert pool.submit(ids[4], lambda: None)
    finally:
        release.set()
        pool.shutdown()


def test_central_presentation_ingestion_has_no_legacy_data_path_dependency() -> None:
    repository = Path(__file__).resolve().parents[2]
    config = (repository / "central/python/src/upm_central/config.py").read_text()
    compose = (repository / "docker-compose.central.yml").read_text()
    staging = (repository / "central/python/src/upm_central/presentation_media.py").read_text()

    assert "media_staging_path" not in config
    assert "media_objects_path" not in config
    assert "central-media-data" not in compose
    assert "/data/staging" not in staging
    assert "write_staging" in staging
    assert "storage.publish_intake" in staging
    assert "storage.promote_intake" in staging
