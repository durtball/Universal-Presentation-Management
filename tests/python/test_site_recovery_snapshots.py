"""Central application of complete Site-originated Event recovery snapshots."""

import os
from collections.abc import Iterator
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateSchema, DropSchema

from upm_central.persistence.base import CentralBase
from upm_central.persistence.models import (
    Event,
    EventDeployment,
    Presentation,
    Site,
    SiteEventRecoverySnapshot,
)
from upm_central.site_recovery import apply_site_recovery_snapshot
from upm_shared.contracts.deployments import (
    EventDeploymentSnapshot,
    PresentationSnapshot,
    SessionSnapshot,
)
from upm_shared.enums import EnrollmentState

CENTRAL_URL = os.getenv("UPM_CENTRAL_DATABASE_URL")


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
def recovery_database() -> Iterator[str]:
    if not CENTRAL_URL:
        pytest.skip("Central PostgreSQL URL required")
    schema = f"site_recovery_{uuid4().hex}"
    admin = create_engine(CENTRAL_URL)
    engine = None
    try:
        with admin.begin() as connection:
            connection.execute(CreateSchema(schema))
        scoped = _schema_url(CENTRAL_URL, schema)
        engine = create_engine(scoped)
        CentralBase.metadata.create_all(engine)
        yield scoped
    finally:
        if engine:
            engine.dispose()
        with admin.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True, if_exists=True))
        admin.dispose()


@pytest.mark.postgres
def test_complete_site_snapshot_creates_recoverable_event_and_is_idempotent(
    recovery_database: str,
) -> None:
    engine = create_engine(recovery_database)
    site_id = uuid4()
    event_id = uuid4()
    session_id = uuid4()
    presentation_ids = [uuid4(), uuid4(), uuid4()]
    snapshot = EventDeploymentSnapshot(
        deployment_id=event_id,
        deployment_revision=2,
        central_event_revision=2,
        event_id=event_id,
        site_id=site_id,
        event_name="Site-created show",
        timezone="UTC",
        sessions=[
            SessionSnapshot(
                session_id=session_id,
                title="Future of AI",
                session_code="AI-101",
                central_revision=1,
            )
        ],
        presentations=[
            PresentationSnapshot(
                presentation_id=presentation_id,
                session_id=session_id,
                title=f"Presenter Entry {index}",
                presentation_identifier=f"SITE-{index}",
                central_revision=1,
            )
            for index, presentation_id in enumerate(presentation_ids, 1)
        ],
        extensions={"site_recovery": {"origin_site_id": str(site_id)}},
    )
    with Session(engine) as session, session.begin():
        site = Site(
            site_id=site_id,
            display_name="Origin Site",
            enabled=True,
            enrollment_state=EnrollmentState.ACTIVE,
        )
        session.add(site)
        session.flush()
        first = apply_site_recovery_snapshot(session, site, snapshot)
        second = apply_site_recovery_snapshot(session, site, snapshot)
        assert first.recovery_snapshot_id == second.recovery_snapshot_id
    with Session(engine) as session:
        event = session.get(Event, event_id)
        assert event is not None and event.owning_site_id == site_id
        assert session.scalar(select(func.count()).select_from(Presentation)) == 3
        assert session.scalar(select(func.count()).select_from(EventDeployment)) == 1
        assert session.scalar(select(func.count()).select_from(SiteEventRecoverySnapshot)) == 1
    engine.dispose()
