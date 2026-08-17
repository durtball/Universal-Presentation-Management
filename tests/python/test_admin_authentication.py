"""Administrator password and PostgreSQL-backed browser session coverage."""

import os
import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateSchema, DropSchema

from upm_central.api import create_app
from upm_central.auth import hash_password, verify_password
from upm_central.config import CentralDatabaseSettings
from upm_central.persistence.base import CentralBase
from upm_central.persistence.models import AdminSession, AdminUser, utc_now
from upm_shared.identifiers import new_uuid7

CENTRAL_URL = os.getenv("UPM_CENTRAL_DATABASE_URL")


def test_password_hash_is_salted_and_not_plaintext() -> None:
    first = hash_password("admin")
    second = hash_password("admin")
    assert first != second
    assert first != "admin"
    assert verify_password("admin", first)
    assert not verify_password("wrong", first)


def _schema_url(raw: str, schema: str) -> str:
    url = make_url(raw)
    query = dict(url.query)
    query["options"] = f"-csearch_path={schema}"
    return URL.create(
        drivername=url.drivername,
        username=url.username,
        password=url.password,
        host=url.host,
        port=url.port,
        database=url.database,
        query=query,
    ).render_as_string(hide_password=False)


@pytest.fixture
def auth_database() -> Iterator[str]:
    if not CENTRAL_URL:
        pytest.skip("Central PostgreSQL URL required")
    schema = f"central_auth_{uuid4().hex}"
    admin = create_engine(CENTRAL_URL)
    engine = None
    try:
        with admin.begin() as connection:
            connection.execute(CreateSchema(schema))
        scoped_url = _schema_url(CENTRAL_URL, schema)
        engine = create_engine(scoped_url)
        CentralBase.metadata.create_all(engine)
        yield scoped_url
    finally:
        if engine:
            engine.dispose()
        with admin.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True, if_exists=True))
        admin.dispose()


@pytest.mark.postgres
def test_bootstrap_login_session_protection_and_logout(auth_database: str) -> None:
    settings = CentralDatabaseSettings(
        database_url=auth_database,
        admin_token="test-administrator-token-at-least-32-characters",
        credential_issuer_key="test-credential-issuer-key-at-least-32-characters",
    )
    app = create_app(settings)
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/api/v1/admin/events").status_code == 401
        assert (
            client.post(
                "/api/v1/auth/login", json={"username": "admin", "password": "wrong"}
            ).status_code
            == 401
        )
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
        assert login.status_code == 200
        assert login.cookies.get("upm_admin_session")
        csrf = login.json()["csrf_token"]
        assert client.get("/api/v1/admin/events").status_code == 200
        assert (
            client.post(
                "/api/v1/admin/events", json={"name": "No CSRF", "timezone": "UTC"}
            ).status_code
            == 403
        )
        created = client.post(
            "/api/v1/admin/events",
            headers={"X-CSRF-Token": csrf},
            json={"name": "Authenticated Event", "timezone": "UTC"},
        )
        assert created.status_code == 201
        changed = client.post(
            "/api/v1/auth/password",
            headers={"X-CSRF-Token": csrf},
            json={"current_password": "admin", "new_password": "new-test-password"},
        )
        assert changed.status_code == 200
        assert client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf}).status_code == 204
        assert client.get("/api/v1/admin/events").status_code == 401
        assert (
            client.post(
                "/api/v1/auth/login", json={"username": "admin", "password": "admin"}
            ).status_code
            == 401
        )
        assert (
            client.post(
                "/api/v1/auth/login",
                json={"username": "admin", "password": "new-test-password"},
            ).status_code
            == 200
        )

    engine = create_engine(auth_database)
    with Session(engine) as session:
        users = session.scalars(select(AdminUser)).all()
        sessions = session.scalars(select(AdminSession)).all()
        assert len(users) == 1
        assert verify_password("new-test-password", users[0].password_hash)
        assert users[0].password_hash != "admin"
        assert len(sessions) == 2
        assert any(item.revoked_at is not None for item in sessions)
    engine.dispose()


@pytest.mark.postgres
def test_twenty_concurrent_requests_share_session_without_activity_lock_storm(
    auth_database: str,
) -> None:
    settings = CentralDatabaseSettings(
        database_url=auth_database,
        admin_token="test-administrator-token-at-least-32-characters",
        credential_issuer_key="test-credential-issuer-key-at-least-32-characters",
    )
    app = create_app(settings)
    with TestClient(app) as client:
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
        assert login.status_code == 200
        engine = create_engine(auth_database)
        with Session(engine) as session, session.begin():
            stored = session.scalar(select(AdminSession))
            stored.last_seen_at = utc_now() - timedelta(minutes=5)

        with ThreadPoolExecutor(max_workers=20) as executor:
            responses = list(executor.map(lambda _: client.get("/api/v1/admin/events"), range(20)))
        assert all(response.status_code == 200 for response in responses)
        with Session(engine) as session:
            stored = session.scalar(select(AdminSession))
            assert stored.last_seen_at > utc_now() - timedelta(seconds=30)
        engine.dispose()


@pytest.mark.postgres
def test_slow_authenticated_upload_does_not_hold_auth_or_request_connection(
    auth_database: str, monkeypatch
) -> None:
    settings = CentralDatabaseSettings(
        database_url=auth_database,
        admin_token="test-administrator-token-at-least-32-characters",
        credential_issuer_key="test-credential-issuer-key-at-least-32-characters",
    )
    staging_id = new_uuid7()
    entered_storage = threading.Event()
    release_storage = threading.Event()

    async def allocate(_self):
        return {
            "storage_target_id": str(staging_id),
            "storage_key": "staging/slow-generated.upload",
            "name": "Slow generated staging",
            "internal_path": "/storage/staging",
        }

    async def write(_self, target_id, key, chunks):
        import hashlib

        content = b"".join([chunk async for chunk in chunks])
        entered_storage.set()
        assert release_storage.wait(timeout=5)
        return {
            "storage_target_id": str(target_id),
            "storage_key": key,
            "size_bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        }

    monkeypatch.setattr(
        "upm_shared.media_storage_client.AsyncMediaStorageClient.allocate_staging", allocate
    )
    monkeypatch.setattr(
        "upm_shared.media_storage_client.AsyncMediaStorageClient.write_staging", write
    )
    app = create_app(settings)
    with TestClient(app) as client:
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
        csrf = login.json()["csrf_token"]
        event = client.post(
            "/api/v1/admin/events",
            headers={"X-CSRF-Token": csrf},
            json={"name": "Slow upload event", "timezone": "UTC"},
        ).json()
        response_holder = []

        def upload():
            response_holder.append(
                client.post(
                    f"/api/v1/admin/events/{event['event_id']}/media-imports",
                    headers={
                        "X-CSRF-Token": csrf,
                        "X-UPM-Original-Filename": "generated-slow.pptx",
                        "Idempotency-Key": "generated-slow-upload",
                        "Content-Type": "application/octet-stream",
                    },
                    content=b"generated slow upload content",
                )
            )

        thread = threading.Thread(target=upload)
        thread.start()
        assert entered_storage.wait(timeout=5)
        assert client.get("/api/v1/admin/events").status_code == 200
        release_storage.set()
        thread.join(timeout=5)
        assert not thread.is_alive()
        assert response_holder[0].status_code == 201
