from types import SimpleNamespace

import pytest

from upm_site.api import create_app
from upm_site.config import SiteSettings


def endpoint(app, path: str, method: str):
    return next(
        route.endpoint
        for route in app.routes
        if getattr(route, "path", None) == path and method in getattr(route, "methods", set())
    )


def test_connection_test_validates_upm_central_identity(monkeypatch) -> None:
    app = create_app(settings=SiteSettings(database_url="postgresql+psycopg://u:p@db/site"))
    requested = []

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"service": "upm-central", "status": "foundation-ready"}

    monkeypatch.setattr(
        "upm_site.api.httpx.get",
        lambda url, timeout: requested.append((url, timeout)) or Response(),
    )
    result = endpoint(app, "/api/v1/central-registration/test", "POST")(
        SimpleNamespace(central_url="https://central.example.com/")
    )

    assert requested == [("https://central.example.com/health", 5.0)]
    assert result["reachable"] is True
    assert result["central_identity"] == "upm-central"


def test_connection_test_rejects_non_central_endpoint(monkeypatch) -> None:
    app = create_app(settings=SiteSettings(database_url="postgresql+psycopg://u:p@db/site"))

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"service": "not-central", "status": "ok"}

    monkeypatch.setattr("upm_site.api.httpx.get", lambda *_args, **_kwargs: Response())
    with pytest.raises(Exception) as error:
        endpoint(app, "/api/v1/central-registration/test", "POST")(
            SimpleNamespace(central_url="https://wrong.example.com")
        )
    assert getattr(error.value, "status_code", None) == 409
