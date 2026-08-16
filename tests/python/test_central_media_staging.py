from pathlib import Path

import pytest

from upm_central.api import create_app
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
