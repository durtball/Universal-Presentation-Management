from pathlib import Path

import pytest
from sqlalchemy import Integer

from upm_central.api import create_app
from upm_central.persistence.models import StorageRoot
from upm_central.presentation_media import MediaStagingError, _safe_staging_path


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
    assert "storage.commit" in staging
