from upm_central.api import create_app as create_central_app
from upm_site.api import create_app as create_site_app


def methods(app, path):
    return {
        method
        for route in app.routes
        if getattr(route, "path", None) == path
        for method in getattr(route, "methods", set())
    }


def test_central_and_site_expose_consistent_rotation_lifecycle_routes():
    central = create_central_app()
    site = create_site_app()

    assert methods(central, "/api/v1/admin/events/{event_id}/rotating-slides") == {"GET", "POST"}
    assert methods(central, "/api/v1/admin/rotating-slides/{assignment_id}") == {"DELETE"}
    assert methods(site, "/api/v1/events/{event_id}/rotating-slides") == {"GET"}
    assert methods(site, "/api/v1/events/{event_id}/rotating-slides/overrides") == {"POST"}
    assert methods(site, "/api/v1/rotating-slides/overrides/{assignment_id}") == {"DELETE"}


def test_site_intake_supports_create_with_media_and_reassignment_without_upload():
    site = create_site_app()

    assert methods(site, "/api/v1/events/{event_id}/presentations") == {"POST"}
    assert methods(site, "/api/v1/media/{media_id}/reassignment") == {"POST"}
    assert methods(site, "/api/v1/media/{media_id}") == {"DELETE"}
