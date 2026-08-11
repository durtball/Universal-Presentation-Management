"""PostgreSQL integration coverage for the permanent event program domain."""

import io
import os
from collections.abc import Iterator
from datetime import datetime, time
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook
from sqlalchemy import create_engine, select
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateSchema, DropSchema

from upm_central.api import create_app
from upm_central.config import CentralDatabaseSettings
from upm_central.persistence.base import CentralBase
from upm_central.persistence.models import (
    Event,
    EventDeployment,
    EventParticipation,
    Person,
    Presentation,
    PresentationPresenter,
    PresentationSession,
    Site,
)
from upm_central.persistence.models import Session as ProgramSession
from upm_shared.enums import EnrollmentState

CENTRAL_URL = os.getenv("UPM_CENTRAL_DATABASE_URL")

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(not CENTRAL_URL, reason="Central PostgreSQL URL required"),
]


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
def program_database() -> Iterator[str]:
    schema = f"central_program_{uuid4().hex}"
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


def _xlsx_bytes() -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(
        [
            "Speaker Name",
            "Speaker Email",
            "Company",
            "Session Title",
            "Session Code",
            "Presentation Title",
            "Presentation Code",
            "Room",
        ]
    )
    sheet.append(
        [
            "XLSX Presenter",
            "xlsx.presenter@example.com",
            "Example Org",
            "Imported XLSX Session",
            "XLSX-S1",
            "Imported XLSX Presentation",
            "XLSX-P1",
            "Grand Ballroom",
        ]
    )
    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()


def _wide_program_xlsx_bytes(*, include_extra: bool = False) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(
        [
            "Presentation ID",
            "Presentation Title",
            "Date",
            "Start Time",
            "End Time",
            "Track",
            "Session Format",
            "Room",
            "First Name",
            "Last Name",
            "Email",
        ]
    )
    rows = [
        (
            3468947,
            "Cloud One",
            datetime(2026, 8, 4),
            time(16, 15),
            time(16, 35),
            "AI Agents",
            "Solo Talk",
            "Venetian Ballroom F",
            "Ada",
            "One",
            "ada@example.com",
        ),
        (
            3468947,
            "Cloud One",
            datetime(2026, 8, 4),
            time(16, 15),
            time(16, 35),
            "AI Agents",
            "Solo Talk",
            "Venetian Ballroom F",
            "Ben",
            "Two",
            "ben@example.com",
        ),
        (
            3468948,
            "Later Cloud",
            datetime(2026, 8, 4),
            time(17, 0),
            time(17, 20),
            "AI Agents",
            "Solo Talk",
            "Venetian Ballroom F",
            "Cara",
            "Three",
            "cara@example.com",
        ),
        (
            3468949,
            "Next Door",
            datetime(2026, 8, 4),
            time(16, 15),
            time(16, 35),
            "AI Agents",
            "Solo Talk",
            "Venetian Ballroom G",
            "Dev",
            "Four",
            "dev@example.com",
        ),
        (
            3468950,
            "Tomorrow",
            datetime(2026, 8, 5),
            time(16, 15),
            time(16, 35),
            "AI Agents",
            "Solo Talk",
            "Venetian Ballroom F",
            "Eve",
            "Five",
            "eve@example.com",
        ),
    ]
    if include_extra:
        rows.append(
            (
                3468951,
                "Cloud Three",
                datetime(2026, 8, 4),
                time(16, 15),
                time(16, 35),
                "AI Agents",
                "Solo Talk",
                "Venetian Ballroom F",
                "Finn",
                "Six",
                "finn@example.com",
            )
        )
    for row in rows:
        sheet.append(row)
    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()


def test_people_program_import_and_revision_workflow(program_database: str) -> None:
    token = "test-administrator-token-at-least-32-characters"
    settings = CentralDatabaseSettings(
        database_url=program_database,
        admin_token=token,
        credential_issuer_key="test-credential-issuer-key-at-least-32-characters",
    )
    headers = {"X-UPM-Admin-Token": token}
    engine = create_engine(program_database)
    site_id = uuid4()
    with Session(engine) as session, session.begin():
        session.add(
            Site(
                site_id=site_id,
                display_name="Program Site",
                enabled=True,
                enrollment_state=EnrollmentState.ACTIVE,
            )
        )
    with TestClient(create_app(settings)) as client:
        event = client.post(
            "/api/v1/admin/events",
            headers=headers,
            json={
                "name": "DST Conference",
                "timezone": "America/Chicago",
                "starts_at": "2027-03-14T01:00:00-06:00",
                "ends_at": "2027-03-14T18:00:00-05:00",
            },
        )
        assert event.status_code == 201
        event_id = event.json()["event_id"]
        second_event_id = client.post(
            "/api/v1/admin/events",
            headers=headers,
            json={"name": "Second Event", "timezone": "UTC"},
        ).json()["event_id"]

        people = []
        for email in ("jane.one@example.com", "jane.two@example.com"):
            response = client.post(
                "/api/v1/admin/people",
                headers=headers,
                json={
                    "given_name": "Jane",
                    "family_name": "Doe",
                    "display_name": "Jane Doe",
                    "primary_email": email,
                    "organization": "UPM Test",
                },
            )
            assert response.status_code == 201
            people.append(response.json())
        assert people[0]["person_id"] != people[1]["person_id"]

        participant_ids = []
        for person in people:
            response = client.post(
                f"/api/v1/admin/events/{event_id}/participants",
                headers=headers,
                json={"person_id": person["person_id"], "is_presenter": True},
            )
            assert response.status_code == 201
            participant_ids.append(response.json()["event_participation_id"])
        cross_event = client.post(
            f"/api/v1/admin/events/{second_event_id}/participants",
            headers=headers,
            json={"person_id": people[0]["person_id"]},
        )
        assert cross_event.status_code == 201

        program_session = client.post(
            f"/api/v1/admin/events/{event_id}/sessions",
            headers=headers,
            json={
                "title": "DST Session",
                "session_code": "S-1",
                "status": "scheduled",
                "starts_at": "2027-03-14T01:30:00-06:00",
                "ends_at": "2027-03-14T03:30:00-05:00",
            },
        )
        assert program_session.status_code == 201
        session_id = program_session.json()["session_id"]
        for order, participant_id in enumerate(participant_ids):
            assigned = client.post(
                f"/api/v1/admin/sessions/{session_id}/presenters",
                headers=headers,
                json={
                    "event_participation_id": participant_id,
                    "role": "presenter",
                    "presenter_order": order,
                    "primary_presenter": order == 0,
                },
            )
            assert assigned.status_code == 201

        presentation = client.post(
            f"/api/v1/admin/events/{event_id}/presentations",
            headers=headers,
            json={
                "title": "Logical Presentation",
                "presentation_code": "P-1",
                "workflow_status": "received",
                "processing_status": "queued",
                "preferred_session_id": session_id,
            },
        )
        assert presentation.status_code == 201
        presentation_id = presentation.json()["presentation_id"]
        for order, participant_id in enumerate(participant_ids):
            linked = client.post(
                f"/api/v1/admin/presentations/{presentation_id}/presenters",
                headers=headers,
                json={
                    "event_participation_id": participant_id,
                    "role": "presenter",
                    "presenter_order": order,
                    "primary_presenter": order == 0,
                },
            )
            assert linked.status_code == 201

        deployed = client.post(
            f"/api/v1/admin/events/{event_id}/deployments",
            headers=headers,
            json={"site_id": str(site_id)},
        )
        assert deployed.status_code == 201
        with Session(engine) as session:
            deployment = session.get(EventDeployment, UUID(deployed.json()["deployment_id"]))
            snapshot = deployment.snapshots[-1].snapshot
            assert snapshot["timezone"] == "America/Chicago"
            assert len(snapshot["sessions"][0]["participants"]) == 2
            assert len(snapshot["presentations"][0]["presenters"]) == 2
            assert len(snapshot["presentations"][0]["sessions"]) == 1

        csv_data = (
            b"entity_type,display_name,email,is_presenter,session_title,session_code,"
            b"presentation_title,presentation_code,presenter_email\n"
            b"participant,Jane Doe,jane.one@example.com,true,,,,,\n"
            b"participant,New Person,new.person@example.com,true,,,,,\n"
            b"participant,Jane Doe,,true,,,,,\n"
            b"session,,,,Imported Session,IMP-S1,,,jane.one@example.com\n"
            b"presentation,,,,,IMP-S1,Imported Presentation,IMP-P1,jane.one@example.com\n"
        )
        staged = client.post(
            f"/api/v1/admin/events/{event_id}/imports",
            headers=headers,
            files={"file": ("people.csv", csv_data, "text/csv")},
            data={"importer_type": "program"},
        )
        assert staged.status_code == 201
        batch_id = staged.json()["import_batch_id"]
        review = client.get(f"/api/v1/admin/imports/{batch_id}", headers=headers).json()
        assert review["rows"][0]["match_outcome"] == "exact"
        assert review["rows"][1]["match_outcome"] == "no_match"
        assert review["rows"][2]["match_outcome"] == "ambiguous"
        ambiguous_id = review["rows"][2]["import_row_id"]
        resolved = client.post(
            f"/api/v1/admin/import-rows/{ambiguous_id}/decision",
            headers=headers,
            json={"action": "create_person", "reason": "different Jane Doe"},
        )
        assert resolved.status_code == 200
        committed = client.post(f"/api/v1/admin/imports/{batch_id}/commit", headers=headers)
        assert committed.status_code == 200
        assert committed.json()["status"] == "committed"
        imported_sessions = client.get(
            f"/api/v1/admin/events/{event_id}/sessions", headers=headers
        ).json()
        imported_presentations = client.get(
            f"/api/v1/admin/events/{event_id}/presentations", headers=headers
        ).json()
        imported_session = next(
            item for item in imported_sessions if item["session_code"] == "IMP-S1"
        )
        imported_presentation = next(
            item for item in imported_presentations if item["presentation_code"] == "IMP-P1"
        )
        assert len(imported_session["presenters"]) == 1
        assert len(imported_presentation["sessions"]) == 1
        assert len(imported_presentation["presenters"]) == 1
        duplicate = client.post(
            f"/api/v1/admin/events/{event_id}/imports",
            headers=headers,
            files={"file": ("people.csv", csv_data, "text/csv")},
            data={"importer_type": "program"},
        )
        assert duplicate.json()["import_batch_id"] == batch_id

        xlsx = client.post(
            f"/api/v1/admin/events/{second_event_id}/imports",
            headers=headers,
            files={
                "file": (
                    "program.xlsx",
                    _xlsx_bytes(),
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
            data={"importer_type": "program"},
        )
        assert xlsx.status_code == 201
        assert xlsx.json()["row_count"] == 1
        xlsx_batch_id = xlsx.json()["import_batch_id"]
        xlsx_review = client.get(f"/api/v1/admin/imports/{xlsx_batch_id}", headers=headers).json()
        assert xlsx_review["preview_counts"]["people_or_presenters"] == 1
        assert xlsx_review["preview_counts"]["sessions"] == 1
        assert xlsx_review["preview_counts"]["presentations"] == 1
        assert xlsx_review["preview_counts"]["unresolved_room_mappings"] == 1
        xlsx_commit = client.post(f"/api/v1/admin/imports/{xlsx_batch_id}/commit", headers=headers)
        assert xlsx_commit.status_code == 200
        xlsx_sessions = client.get(
            f"/api/v1/admin/events/{second_event_id}/sessions", headers=headers
        ).json()
        xlsx_presentations = client.get(
            f"/api/v1/admin/events/{second_event_id}/presentations", headers=headers
        ).json()
        assert xlsx_sessions[0]["location_name"] == "Grand Ballroom"
        assert xlsx_sessions[0]["presenters"][0]["display_name"] == "XLSX Presenter"
        assert xlsx_presentations[0]["presenters"][0]["display_name"] == "XLSX Presenter"
        invalid_schedule = client.post(
            f"/api/v1/admin/events/{second_event_id}/imports",
            headers=headers,
            files={
                "file": (
                    "bad-time.csv",
                    b"entity_type,session_title,starts_at,ends_at\n"
                    b"session,Bad Time,2027-11-07T01:30:00,2027-11-07T01:00:00\n",
                    "text/csv",
                )
            },
            data={"importer_type": "program"},
        )
        invalid_review = client.get(
            f"/api/v1/admin/imports/{invalid_schedule.json()['import_batch_id']}",
            headers=headers,
        ).json()
        assert invalid_review["rows"][0]["validation_state"] == "error"
        assert any(
            issue["code"] == "invalid_schedule" for issue in invalid_review["rows"][0]["issues"]
        )

        impact = client.get(
            f"/api/v1/admin/people/{people[0]['person_id']}/deletion-impact", headers=headers
        ).json()["impact"]
        assert impact["event_participations"] == 2
        protected = client.request(
            "DELETE",
            f"/api/v1/admin/people/{people[0]['person_id']}",
            headers=headers,
            json={"confirmation": people[0]["person_id"]},
        )
        assert protected.status_code == 409

    with Session(engine) as session:
        person = session.get(Person, UUID(people[0]["person_id"]))
        assert len(person.event_participations) == 2
        assert (
            session.scalar(select(Event).where(Event.event_id == UUID(event_id))).timezone
            == "America/Chicago"
        )
        assert (
            session.scalar(
                select(EventParticipation).where(
                    EventParticipation.event_id == UUID(event_id),
                    EventParticipation.person_id == person.person_id,
                )
            )
            is not None
        )
    engine.dispose()


def test_wide_program_rows_create_deterministic_grouped_sessions(program_database: str) -> None:
    token = "test-administrator-token-at-least-32-characters"
    settings = CentralDatabaseSettings(
        database_url=program_database,
        admin_token=token,
        credential_issuer_key="test-credential-issuer-key-at-least-32-characters",
    )
    headers = {"X-UPM-Admin-Token": token}
    engine = create_engine(program_database)
    site_id = uuid4()
    with Session(engine) as session, session.begin():
        session.add(
            Site(
                site_id=site_id,
                display_name="Import Site",
                enabled=True,
                enrollment_state=EnrollmentState.ACTIVE,
            )
        )

    with TestClient(create_app(settings)) as client:
        event_id = client.post(
            "/api/v1/admin/events",
            headers=headers,
            json={"name": "Wide Program", "timezone": "UTC"},
        ).json()["event_id"]
        upload = client.post(
            f"/api/v1/admin/events/{event_id}/imports",
            headers=headers,
            files={
                "file": (
                    "program.xlsx",
                    _wide_program_xlsx_bytes(),
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
            data={"importer_type": "program"},
        )
        assert upload.status_code == 201
        batch_id = upload.json()["import_batch_id"]
        review_response = client.get(f"/api/v1/admin/imports/{batch_id}", headers=headers)
        assert review_response.status_code == 200
        review = review_response.json()
        assert review["preview_counts"]["people_or_presenters"] == 5
        assert review["preview_counts"]["sessions"] == 4
        assert review["preview_counts"]["presentations"] == 5
        assert review["preview_counts"]["unresolved_room_mappings"] == 4
        committed = client.post(f"/api/v1/admin/imports/{batch_id}/commit", headers=headers)
        assert committed.status_code == 200

        sessions = client.get(f"/api/v1/admin/events/{event_id}/sessions", headers=headers).json()
        presentations = client.get(
            f"/api/v1/admin/events/{event_id}/presentations", headers=headers
        ).json()
        assert len(sessions) == 4
        assert len(presentations) == 4
        first = next(
            item
            for item in sessions
            if item["starts_at"] == "2026-08-04T16:15:00Z"
            and item["location_name"] == "Venetian Ballroom F"
        )
        assert {item["display_name"] for item in first["presenters"]} == {"Ada One", "Ben Two"}
        assert {
            item["presentation_code"]
            for item in presentations
            if item["preferred_session_id"] == first["session_id"]
        } == {"3468947"}
        original_session_ids = {item["session_code"]: item["session_id"] for item in sessions}

        reupload = client.post(
            f"/api/v1/admin/events/{event_id}/imports",
            headers=headers,
            files={
                "file": (
                    "program-revised.xlsx",
                    _wide_program_xlsx_bytes(include_extra=True),
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
            data={"importer_type": "program"},
        )
        assert (
            client.post(
                f"/api/v1/admin/imports/{reupload.json()['import_batch_id']}/commit",
                headers=headers,
            ).status_code
            == 200
        )
        stable_sessions = client.get(
            f"/api/v1/admin/events/{event_id}/sessions", headers=headers
        ).json()
        assert {
            item["session_code"]: item["session_id"] for item in stable_sessions
        } == original_session_ids

        deployment = client.post(
            f"/api/v1/admin/events/{event_id}/deployments",
            headers=headers,
            json={"site_id": str(site_id)},
        )
        assert deployment.status_code == 201

    with Session(engine) as session:
        assert session.query(ProgramSession).filter_by(event_id=UUID(event_id)).count() == 4
        assert session.query(Presentation).filter_by(event_id=UUID(event_id)).count() == 5
        assert session.query(PresentationSession).count() == 5
        assert session.query(PresentationPresenter).count() == 6
        snapshot = (
            session.get(EventDeployment, UUID(deployment.json()["deployment_id"]))
            .snapshots[-1]
            .snapshot
        )
        assert len(snapshot["sessions"]) == 4
        assert {item["location_name"] for item in snapshot["sessions"]} == {
            "Venetian Ballroom F",
            "Venetian Ballroom G",
        }
    engine.dispose()
