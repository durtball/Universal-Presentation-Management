"""Site-local program parsing/routes and PostgreSQL materialization coverage."""

import io
import os
from collections.abc import Iterator
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook
from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.schema import CreateSchema, DropSchema

from upm_shared.program_import import normalize_row, parse_program_source
from upm_site.api import create_app
from upm_site.config import SiteSettings
from upm_site.persistence.base import SiteBase
from upm_site.persistence.models import (
    Event,
    OutboxEvent,
    Presentation,
    PresentationPresenter,
    ProgramImportBatch,
)
from upm_site.persistence.models import Session as ProgramSession
from upm_site.sync import bootstrap_identity

SITE_URL = os.getenv("UPM_SITE_DATABASE_URL")


def test_shared_program_parser_normalizes_csv_and_xlsx_consistently() -> None:
    csv = (
        b"Session Title,Presentation Title,Presenter Name,Room,Date,Start Time\n"
        b"Future of AI,Responsible Agents,Jane Smith,Venetian G,2026-08-28,10:15 AM\n"
    )
    source_type, rows = parse_program_source("program.csv", csv)
    assert source_type == "csv"
    assert normalize_row(rows[0])["display_name"] == "Jane Smith"
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Session Title", "Presentation Title", "Presenter Name"])
    sheet.append(["Future of AI", "Responsible Agents", "Jane Smith"])
    output = io.BytesIO()
    workbook.save(output)
    source_type, rows = parse_program_source("program.xlsx", output.getvalue())
    assert source_type == "xlsx"
    assert normalize_row(rows[0])["presentation_title"] == "Responsible Agents"


def test_site_program_and_event_routes_are_registered() -> None:
    methods = {
        (route.path, method)
        for route in create_app().routes
        for method in getattr(route, "methods", set())
    }
    assert ("/api/v1/events", "POST") in methods
    assert ("/api/v1/events/{event_id}/program-imports", "POST") in methods
    assert ("/api/v1/program-imports/{batch_id}", "GET") in methods
    assert ("/api/v1/program-imports/{batch_id}/commit", "POST") in methods


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
def site_program_database() -> Iterator[str]:
    if not SITE_URL:
        pytest.skip("Site PostgreSQL URL required")
    schema = f"site_program_import_{uuid4().hex}"
    admin = create_engine(SITE_URL)
    engine = None
    try:
        with admin.begin() as connection:
            connection.execute(CreateSchema(schema))
        scoped = _schema_url(SITE_URL, schema)
        engine = create_engine(scoped)
        SiteBase.metadata.create_all(engine)
        yield scoped
    finally:
        if engine:
            engine.dispose()
        with admin.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True, if_exists=True))
        admin.dispose()


@pytest.mark.postgres
def test_site_import_creates_one_shared_session_and_three_row_presentations(
    site_program_database: str,
) -> None:
    engine = create_engine(site_program_database)
    factory = sessionmaker(engine, expire_on_commit=False)
    settings = SiteSettings(
        database_url=site_program_database,
        credential_encryption_key="test-only-encryption-key-with-32-characters",
    )
    with factory.begin() as session:
        bootstrap_identity(session, settings)
    csv = (
        b"Session ID,Session Title,Presentation Title,Presenter Name,Presenter Email,"
        b"Room,Date,Start Time\n"
        b"AI-101,Future of AI,Jane's Talk,Jane Smith,jane@example.com,Venetian G,"
        b"2026-08-28,10:15 AM\n"
        b"AI-101,Future of AI,Bob's Talk,Bob Jones,bob@example.com,Venetian G,2026-08-28,10:15 AM\n"
        b"AI-101,Future of AI,Sarah's Talk,Sarah Lee,sarah@example.com,Venetian G,"
        b"2026-08-28,10:15 AM\n"
    )
    with TestClient(create_app(settings=settings, session_factory=factory)) as client:
        created = client.post("/api/v1/events", json={"name": "Offline Show", "timezone": "UTC"})
        assert created.status_code == 201
        event_id = created.json()["event_id"]
        staged = client.post(
            f"/api/v1/events/{event_id}/program-imports",
            files={"file": ("program.csv", csv, "text/csv")},
        )
        assert staged.status_code == 201, staged.text
        assert staged.json()["valid_count"] == 3
        batch_id = staged.json()["import_batch_id"]
        committed = client.post(f"/api/v1/program-imports/{batch_id}/commit")
        assert committed.status_code == 200, committed.text
        presentation_ids = {
            row["committed_entity_ids"]["presentation_id"] for row in committed.json()["rows"]
        }
        assert len(presentation_ids) == 3
        duplicate = client.post(
            f"/api/v1/events/{event_id}/program-imports",
            files={"file": ("program.csv", csv, "text/csv")},
        )
        assert duplicate.status_code == 201
        assert duplicate.json()["duplicate"] is True
        revised = client.post(
            f"/api/v1/events/{event_id}/program-imports",
            files={"file": ("program.csv", csv + b"\n", "text/csv")},
        )
        assert revised.status_code == 201
        assert revised.json()["error_count"] == 3
        assert all(
            "re-import requires explicit presentation_id" in row["validation_messages"][0]
            for row in revised.json()["rows"]
        )
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(Event)) == 1
        assert session.scalar(select(func.count()).select_from(ProgramSession)) == 1
        assert session.scalar(select(func.count()).select_from(Presentation)) == 3
        assert session.scalar(select(func.count()).select_from(PresentationPresenter)) == 3
        assert session.scalar(select(func.count()).select_from(ProgramImportBatch)) == 2
        recovery = session.scalars(
            select(OutboxEvent).where(
                OutboxEvent.event_type == "site.event_recovery_snapshot.upserted"
            )
        ).all()
        assert len(recovery) == 2  # Event creation plus one complete post-import revision.
        latest = recovery[-1].payload
        assert len(latest["sessions"]) == 1
        assert len(latest["presentations"]) == 3
    engine.dispose()
