"""PostgreSQL integration tests for Central-to-Site event deployment convergence."""

import os
from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.schema import CreateSchema, DropSchema

import upm_site.event_deployments as site_deployment_module
from upm_central.api import create_app as create_central_app
from upm_central.config import CentralDatabaseSettings
from upm_central.persistence.base import CentralBase
from upm_central.persistence.models import EventDeployment as CentralDeployment
from upm_central.persistence.models import OutboxEvent as CentralOutboxEvent
from upm_central.persistence.models import Site as CentralSite
from upm_shared.contracts.deployments import (
    EventDeploymentSnapshot,
    ParticipationSnapshot,
    PersonProfile,
    PresentationPresenterSnapshot,
    PresentationSessionSnapshot,
    PresentationSnapshot,
    SessionParticipantSnapshot,
    SessionSnapshot,
)
from upm_shared.contracts.sync import SyncEventEnvelope
from upm_shared.enums import (
    AuthorityScope,
    EnrollmentState,
    EventDeploymentStatus,
    JobStatus,
    ParticipantStatus,
    PresentationProcessingStatus,
    PresentationWorkflowStatus,
    SessionStatus,
)
from upm_shared.identifiers import new_uuid7
from upm_site.config import SiteSettings
from upm_site.persistence.base import SiteBase
from upm_site.persistence.models import (
    CentralRegistration,
    EventDeploymentProjection,
    EventParticipation,
    Presentation,
    PresentationPresenter,
    PresentationSession,
    SessionParticipant,
)
from upm_site.persistence.models import Event as SiteEvent
from upm_site.sync import apply_central_event, bootstrap_identity, decrypt_secret
from upm_site.sync_transport import synchronize_once

CENTRAL_URL = os.getenv("UPM_CENTRAL_DATABASE_URL")
SITE_URL = os.getenv("UPM_SITE_DATABASE_URL")

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(
        not CENTRAL_URL or not SITE_URL, reason="independent PostgreSQL URLs required"
    ),
]


def schema_url(raw: str, schema: str) -> str:
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
def deployment_databases() -> Iterator[tuple[str, sessionmaker[Session]]]:
    central_schema = f"central_deployment_{uuid4().hex}"
    site_schema = f"site_deployment_{uuid4().hex}"
    central_admin = create_engine(CENTRAL_URL)
    site_admin = create_engine(SITE_URL)
    central_engine = site_engine = None
    try:
        with central_admin.begin() as connection:
            connection.execute(CreateSchema(central_schema))
        with site_admin.begin() as connection:
            connection.execute(CreateSchema(site_schema))
        central_scoped_url = schema_url(CENTRAL_URL, central_schema)
        site_scoped_url = schema_url(SITE_URL, site_schema)
        central_engine = create_engine(central_scoped_url)
        site_engine = create_engine(site_scoped_url)
        CentralBase.metadata.create_all(central_engine)
        SiteBase.metadata.create_all(site_engine)
        yield central_scoped_url, sessionmaker(site_engine, expire_on_commit=False)
    finally:
        if central_engine:
            central_engine.dispose()
        if site_engine:
            site_engine.dispose()
        with central_admin.begin() as connection:
            connection.execute(DropSchema(central_schema, cascade=True, if_exists=True))
        with site_admin.begin() as connection:
            connection.execute(DropSchema(site_schema, cascade=True, if_exists=True))
        central_admin.dispose()
        site_admin.dispose()


class CentralClientAdapter:
    def __init__(self, client: TestClient) -> None:
        self.client = client

    def get(self, url: str, **kwargs) -> httpx.Response:
        return self.client.get(httpx.URL(url).raw_path.decode(), **kwargs)

    def post(self, url: str, **kwargs) -> httpx.Response:
        return self.client.post(httpx.URL(url).raw_path.decode(), **kwargs)


def test_enrollment_deployment_update_duplicate_outage_and_recovery(
    deployment_databases: tuple[str, sessionmaker[Session]],
) -> None:
    central_url, site_factory = deployment_databases
    admin_token = "test-administrator-token-at-least-32-characters"
    central_settings = CentralDatabaseSettings(
        database_url=central_url,
        admin_token=admin_token,
        credential_issuer_key="test-credential-issuer-key-at-least-32-characters",
    )
    site_settings = SiteSettings(
        database_url=SITE_URL,
        central_url="http://central.test",
        credential_encryption_key="test-only-encryption-key-with-32-characters",
        heartbeat_interval_seconds=3600,
    )
    with site_factory.begin() as session:
        site, _ = bootstrap_identity(session, site_settings)
        site_id = site.site_id

    headers = {"X-UPM-Admin-Token": admin_token}
    with TestClient(create_central_app(central_settings)) as central_client:
        adapter = CentralClientAdapter(central_client)
        synchronize_once(site_factory, site_settings, "site-sync-test", adapter)
        approved = central_client.post(
            f"/api/v1/admin/sites/{site_id}/approve", headers=headers, json={}
        )
        assert approved.status_code == 200
        synchronize_once(site_factory, site_settings, "site-sync-test", adapter)
        with site_factory() as session:
            registration = session.get(CentralRegistration, site_id)
            credential = decrypt_secret(site_settings, registration.credential_encrypted)
        cross_site = central_client.get(
            "/api/v1/sync/central-events",
            headers={
                "X-UPM-Site-ID": str(uuid4()),
                "Authorization": f"Bearer {credential}",
            },
        )
        assert cross_site.status_code == 401
        assert (
            central_client.get(
                "/api/v1/admin/events", headers={"X-UPM-Admin-Token": "wrong"}
            ).status_code
            == 401
        )

        created = central_client.post(
            "/api/v1/admin/events",
            headers=headers,
            json={"name": "UPM Expo 2027"},
        )
        assert created.status_code == 201
        event_id = created.json()["event_id"]
        deployed = central_client.post(
            f"/api/v1/admin/events/{event_id}/deployments",
            headers=headers,
            json={"site_id": str(site_id)},
        )
        assert deployed.status_code == 201
        deployment_id = deployed.json()["deployment_id"]
        assert (
            central_client.post(
                f"/api/v1/admin/events/{event_id}/deployments",
                headers=headers,
                json={"site_id": str(uuid4())},
            ).status_code
            == 404
        )
        second_site_id = uuid4()
        central_seed_engine = create_engine(central_url)
        try:
            with Session(central_seed_engine) as session, session.begin():
                session.add(
                    CentralSite(
                        site_id=second_site_id,
                        display_name="Second independent Site",
                        enabled=True,
                        enrollment_state=EnrollmentState.ACTIVE,
                    )
                )
            second_deployment = central_client.post(
                f"/api/v1/admin/events/{event_id}/deployments",
                headers=headers,
                json={"site_id": str(second_site_id)},
            )
            assert second_deployment.status_code == 201
            assert second_deployment.json()["deployment_id"] != deployment_id
        finally:
            central_seed_engine.dispose()

        synchronize_once(site_factory, site_settings, "site-sync-test", adapter)
        synchronize_once(site_factory, site_settings, "site-sync-test", adapter)
        status = central_client.get(
            f"/api/v1/admin/events/{event_id}/deployments", headers=headers
        ).json()[0]
        assert status["desired_revision"] == status["applied_revision"] == 1
        assert status["status"] == "deployed"
        with site_factory() as session:
            projection = session.get(EventDeploymentProjection, deployment_id)
            assert projection.applied_revision == 1
            assert session.get(SiteEvent, event_id).name == "UPM Expo 2027"

        updated = central_client.put(
            f"/api/v1/admin/events/{event_id}",
            headers=headers,
            json={"name": "UPM Expo Updated"},
        )
        assert updated.status_code == 200
        synchronize_once(site_factory, site_settings, "site-sync-test", adapter)
        synchronize_once(site_factory, site_settings, "site-sync-test", adapter)
        with site_factory() as session:
            assert session.get(EventDeploymentProjection, deployment_id).applied_revision == 2
            assert session.get(SiteEvent, event_id).name == "UPM Expo Updated"

        central_engine = create_engine(central_url)
        try:
            with Session(central_engine) as session, session.begin():
                deployment = session.get(CentralDeployment, deployment_id)
                duplicate = session.scalar(
                    select(CentralOutboxEvent)
                    .where(
                        CentralOutboxEvent.aggregate_id == deployment.deployment_id,
                        CentralOutboxEvent.source_sequence.is_not(None),
                    )
                    .order_by(CentralOutboxEvent.source_sequence.desc())
                )
                duplicate.status = JobStatus.PENDING
            synchronize_once(site_factory, site_settings, "site-sync-test", adapter)

            # Simulate a disconnected Site while Central generates three complete revisions.
            for revision in range(3, 6):
                response = central_client.put(
                    f"/api/v1/admin/events/{event_id}",
                    headers=headers,
                    json={"name": f"Offline revision {revision}"},
                )
                assert response.status_code == 200
            synchronize_once(site_factory, site_settings, "site-sync-test", adapter)
            synchronize_once(site_factory, site_settings, "site-sync-test", adapter)
            with site_factory() as session:
                projection = session.get(EventDeploymentProjection, deployment_id)
                assert projection.applied_revision == 5
                assert session.get(SiteEvent, event_id).name == "Offline revision 5"
            with Session(central_engine) as session:
                deployment = session.get(CentralDeployment, deployment_id)
                assert deployment.desired_revision == deployment.acknowledged_revision == 5
                assert deployment.status == EventDeploymentStatus.DEPLOYED
        finally:
            central_engine.dispose()

        revoked = central_client.post(
            f"/api/v1/admin/event-deployments/{deployment_id}/revoke",
            headers=headers,
            json={"reason": "assignment moved"},
        )
        assert revoked.status_code == 200
        synchronize_once(site_factory, site_settings, "site-sync-test", adapter)
        synchronize_once(site_factory, site_settings, "site-sync-test", adapter)
        with site_factory() as session:
            projection = session.get(EventDeploymentProjection, deployment_id)
            assert projection.status == EventDeploymentStatus.REVOKED
            assert session.get(SiteEvent, event_id) is not None


def test_site_revision_order_schema_failure_and_identity_security(
    deployment_databases: tuple[str, sessionmaker[Session]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, site_factory = deployment_databases
    settings = SiteSettings(
        database_url=SITE_URL,
        credential_encryption_key="test-only-encryption-key-with-32-characters",
    )
    with site_factory.begin() as session:
        site, _ = bootstrap_identity(session, settings)
        site_id = site.site_id
    deployment_id = uuid4()
    event_id = uuid4()

    def event(sequence: int, revision: int, name: str) -> SyncEventEnvelope:
        snapshot = EventDeploymentSnapshot(
            deployment_id=deployment_id,
            deployment_revision=revision,
            event_id=event_id,
            site_id=site_id,
            event_name=name,
        )
        return SyncEventEnvelope(
            event_id=uuid4(),
            event_type="central.event_deployment.updated",
            protocol_version=1,
            source="central",
            source_sequence=sequence,
            authority=AuthorityScope.CENTRAL,
            entity_type="event_deployment",
            entity_id=deployment_id,
            occurred_at=datetime.now(UTC),
            payload=snapshot.model_dump(mode="json"),
        )

    # Complete snapshots may jump directly to a newer revision.
    with site_factory.begin() as session:
        assert apply_central_event(session, event(1, 2, "Revision 2")).accepted
    with site_factory.begin() as session:
        stale = apply_central_event(session, event(2, 1, "Must not roll back"))
        assert stale.accepted
    with site_factory() as session:
        assert session.get(EventDeploymentProjection, deployment_id).applied_revision == 2
        assert session.get(SiteEvent, event_id).name == "Revision 2"

    # A different envelope carrying the same complete revision is also idempotent.
    with site_factory.begin() as session:
        duplicate_revision = apply_central_event(session, event(3, 2, "Revision 2"))
        assert duplicate_revision.accepted

    unsupported = event(4, 3, "Unsupported")
    unsupported.payload_schema_version = 2
    with site_factory.begin() as session:
        result = apply_central_event(session, unsupported)
        assert result.accepted and result.error_code == "application_failed"
    with site_factory() as session:
        projection = session.get(EventDeploymentProjection, deployment_id)
        assert projection.applied_revision == 2
        assert projection.status == EventDeploymentStatus.FAILED
        assert session.get(SiteEvent, event_id).name == "Revision 2"

    wrong_site = event(5, 4, "Wrong Site")
    wrong_site.payload["site_id"] = str(uuid4())
    with site_factory.begin() as session:
        rejected = apply_central_event(session, wrong_site)
        assert not rejected.accepted and rejected.error_code == "invalid_authority"
    with site_factory() as session:
        # The rejected cross-Site event cannot advance the authenticated Site cursor.
        assert session.get(EventDeploymentProjection, deployment_id).applied_revision == 2

    def database_failure(*_args, **_kwargs):
        raise RuntimeError("simulated database application failure")

    monkeypatch.setattr(site_deployment_module, "_upsert_snapshot", database_failure)
    with pytest.raises(RuntimeError, match="simulated database"):
        with site_factory.begin() as session:
            apply_central_event(session, event(5, 4, "Must roll back"))
    with site_factory() as session:
        assert session.get(EventDeploymentProjection, deployment_id).applied_revision == 2
        assert session.get(SiteEvent, event_id).name == "Revision 2"


def test_complete_program_projection_replaces_removed_relationships(
    deployment_databases: tuple[str, sessionmaker[Session]],
) -> None:
    _, site_factory = deployment_databases
    settings = SiteSettings(
        database_url=SITE_URL,
        credential_encryption_key="test-only-encryption-key-with-32-characters",
    )
    with site_factory.begin() as session:
        site, _ = bootstrap_identity(session, settings)
        site_id = site.site_id
    deployment_id, event_id = new_uuid7(), new_uuid7()
    person_id, participant_id = new_uuid7(), new_uuid7()
    session_id, session_participant_id = new_uuid7(), new_uuid7()
    presentation_id = new_uuid7()
    presentation_session_id, presentation_presenter_id = new_uuid7(), new_uuid7()

    def envelope(revision: int, include_links: bool) -> SyncEventEnvelope:
        snapshot = EventDeploymentSnapshot(
            deployment_id=deployment_id,
            deployment_revision=revision,
            event_id=event_id,
            site_id=site_id,
            event_name="Program projection",
            timezone="America/Chicago",
            people=[
                PersonProfile(person_id=person_id, display_name="Presenter One", central_revision=1)
            ],
            participations=[
                ParticipationSnapshot(
                    event_participation_id=participant_id,
                    person_id=person_id,
                    participant_status=ParticipantStatus.ACTIVE,
                    is_presenter=True,
                    central_revision=1,
                )
            ],
            sessions=[
                SessionSnapshot(
                    session_id=session_id,
                    title="Session One",
                    status=SessionStatus.SCHEDULED,
                    central_revision=1,
                    participants=[
                        SessionParticipantSnapshot(
                            session_participant_id=session_participant_id,
                            event_participation_id=participant_id,
                            role="presenter",
                            presenter_order=0,
                            primary_presenter=True,
                            central_revision=1,
                        )
                    ]
                    if include_links
                    else [],
                )
            ],
            presentations=[
                PresentationSnapshot(
                    presentation_id=presentation_id,
                    session_id=session_id,
                    title="Presentation One",
                    workflow_status=PresentationWorkflowStatus.READY,
                    processing_status=PresentationProcessingStatus.SUCCEEDED,
                    central_revision=1,
                    sessions=[
                        PresentationSessionSnapshot(
                            presentation_session_id=presentation_session_id,
                            session_id=session_id,
                            primary_session=True,
                            central_revision=1,
                        )
                    ]
                    if include_links
                    else [],
                    presenters=[
                        PresentationPresenterSnapshot(
                            presentation_presenter_id=presentation_presenter_id,
                            event_participation_id=participant_id,
                            primary_presenter=True,
                            central_revision=1,
                        )
                    ]
                    if include_links
                    else [],
                )
            ],
        )
        return SyncEventEnvelope(
            event_id=new_uuid7(),
            event_type="central.event_deployment.updated",
            protocol_version=1,
            source="central",
            source_sequence=revision,
            authority=AuthorityScope.CENTRAL,
            entity_type="event_deployment",
            entity_id=deployment_id,
            occurred_at=datetime.now(UTC),
            payload=snapshot.model_dump(mode="json"),
        )

    with site_factory.begin() as session:
        assert apply_central_event(session, envelope(1, True)).accepted
    with site_factory() as session:
        assert session.get(SiteEvent, event_id).timezone == "America/Chicago"
        assert session.get(EventParticipation, participant_id).active
        assert session.get(SessionParticipant, session_participant_id).active
        assert session.get(Presentation, presentation_id).active
        assert session.get(PresentationSession, presentation_session_id).active
        assert session.get(PresentationPresenter, presentation_presenter_id).active
    with site_factory.begin() as session:
        assert apply_central_event(session, envelope(2, False)).accepted
    with site_factory() as session:
        assert not session.get(SessionParticipant, session_participant_id).active
        assert not session.get(PresentationSession, presentation_session_id).active
        assert not session.get(PresentationPresenter, presentation_presenter_id).active
