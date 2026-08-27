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
import upm_site.sync as site_sync_module
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
    PresentationVersionSnapshot,
    SessionParticipantSnapshot,
    SessionSnapshot,
)
from upm_shared.contracts.media_transfer import MediaTransferManifest
from upm_shared.contracts.sync import SyncEventEnvelope
from upm_shared.enums import (
    AssetKind,
    AuthorityScope,
    EnrollmentState,
    EventDeploymentStatus,
    JobStatus,
    MediaAvailability,
    MediaCategory,
    MediaTransferState,
    ParticipantStatus,
    PresentationProcessingStatus,
    PresentationWorkflowStatus,
    SessionStatus,
    SourceSystem,
    StorageType,
)
from upm_shared.identifiers import new_uuid7
from upm_site.config import SiteSettings
from upm_site.persistence.base import SiteBase
from upm_site.persistence.models import (
    CentralRegistration,
    EventDeploymentProjection,
    EventParticipation,
    MediaObject,
    MediaTransferSession,
    Presentation,
    PresentationAsset,
    PresentationPresenter,
    PresentationSession,
    PresentationVersion,
    ProgramRoomMapping,
    Room,
    RoomAssignment,
    SessionParticipant,
    StorageTarget,
    TransferJob,
)
from upm_site.persistence.models import Event as SiteEvent
from upm_site.sync import (
    apply_central_event,
    bootstrap_identity,
    decrypt_secret,
    reconcile_deferred_media_transfers,
    recover_media_transfer_manifests,
)
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
        preview = central_client.get(
            f"/api/v1/admin/events/{event_id}/deployment-preview",
            headers=headers,
            params={"site_id": str(site_id)},
        )
        assert preview.status_code == 200
        assert preview.json()["site_name"]
        assert preview.json()["counts"] == {
            "rooms": 0,
            "sessions": 0,
            "presenters": 0,
            "presentations": 0,
        }
        assert preview.json()["deployable"] is True
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
            json={
                "name": "  UPM Expo Updated  ",
                "timezone": "America/New_York",
                "starts_at": "2027-04-10T00:00:00Z",
                "ends_at": "2027-04-12T00:00:00Z",
            },
        )
        assert updated.status_code == 200
        assert updated.json()["event_id"] == event_id
        update_status = central_client.get(
            f"/api/v1/admin/events/{event_id}/deployments", headers=headers
        ).json()[0]
        assert update_status["update_available"] is True
        assert update_status["desired_revision"] == 2
        synchronize_once(site_factory, site_settings, "site-sync-test", adapter)
        synchronize_once(site_factory, site_settings, "site-sync-test", adapter)
        with site_factory() as session:
            assert session.get(EventDeploymentProjection, deployment_id).applied_revision == 2
            site_event = session.get(SiteEvent, event_id)
            assert site_event.name == "UPM Expo Updated"
            assert site_event.timezone == "America/New_York"
            assert site_event.starts_at.isoformat().startswith("2027-04-10")

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

            # Each offline edit durably publishes a complete update; the Site may skip ahead.
            for revision in range(3, 6):
                response = central_client.put(
                    f"/api/v1/admin/events/{event_id}",
                    headers=headers,
                    json={"name": f"Offline revision {revision}", "timezone": "America/New_York"},
                )
                assert response.status_code == 200
            pending_update = central_client.get(
                f"/api/v1/admin/events/{event_id}/deployments", headers=headers
            ).json()[0]
            assert pending_update["update_available"] is True
            assert pending_update["desired_revision"] == 5
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


def test_media_transfer_waits_for_presentation_version_dependencies(
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

    event_id = new_uuid7()
    deployment_id = new_uuid7()
    presentation_id = new_uuid7()
    version_id = new_uuid7()
    transfer_id = new_uuid7()
    manifest = MediaTransferManifest(
        transfer_session_id=transfer_id,
        origin_system=SourceSystem.CENTRAL,
        destination_site_id=site_id,
        event_id=event_id,
        presentation_id=presentation_id,
        presentation_version_id=version_id,
        presentation_version_number=1,
        presentation_identifier="UPM-HOTFIX-1",
        original_filename="deck.pptx",
        canonical_filename="UPM-HOTFIX-1_v01.pptx",
        expected_size=12,
        sha256="a" * 64,
        created_at=datetime.now(UTC),
    )
    transfer_event = SyncEventEnvelope(
        event_id=new_uuid7(),
        event_type="central.media_transfer.available",
        protocol_version=1,
        source="central",
        source_sequence=1,
        authority=AuthorityScope.CENTRAL,
        entity_type="media_transfer",
        entity_id=transfer_id,
        occurred_at=datetime.now(UTC),
        payload=manifest.model_dump(mode="json"),
    )

    with site_factory.begin() as session:
        result = apply_central_event(session, transfer_event)
        assert result.accepted
    with site_factory() as session:
        deferred = session.get(TransferJob, transfer_id)
        assert deferred.required_capabilities == ["sync-dependencies"]
        assert session.get(MediaTransferSession, transfer_id) is None

    snapshot = EventDeploymentSnapshot(
        deployment_id=deployment_id,
        deployment_revision=1,
        event_id=event_id,
        site_id=site_id,
        event_name="Transfer dependency event",
        presentations=[
            PresentationSnapshot(
                presentation_id=presentation_id,
                title="Transfer dependency presentation",
                presentation_identifier="UPM-HOTFIX-1",
                central_revision=1,
                versions=[
                    PresentationVersionSnapshot(
                        presentation_version_id=version_id,
                        version_number=1,
                    )
                ],
                version_numbers=[1],
            )
        ],
    )
    deployment_event = SyncEventEnvelope(
        event_id=new_uuid7(),
        event_type="central.event_deployment.updated",
        protocol_version=1,
        source="central",
        source_sequence=2,
        authority=AuthorityScope.CENTRAL,
        entity_type="event_deployment",
        entity_id=deployment_id,
        occurred_at=datetime.now(UTC),
        payload=snapshot.model_dump(mode="json"),
    )
    with site_factory.begin() as session:
        assert apply_central_event(session, deployment_event).accepted
    with site_factory() as session:
        assert session.get(PresentationVersion, version_id).presentation_id == presentation_id
        transfer = session.get(MediaTransferSession, transfer_id)
        assert transfer.presentation_version_id == version_id
        assert session.get(TransferJob, transfer_id).required_capabilities == ["transfer"]


def test_deferred_transfer_converges_legacy_version_uuid_and_preserves_assets(
    deployment_databases: tuple[str, sessionmaker[Session]],
) -> None:
    _, site_factory = deployment_databases
    settings = SiteSettings(
        database_url=SITE_URL,
        credential_encryption_key="test-only-encryption-key-with-32-characters",
    )
    event_id, deployment_id, presentation_id = new_uuid7(), new_uuid7(), new_uuid7()
    canonical_version_id, transfer_id = new_uuid7(), new_uuid7()
    with site_factory.begin() as session:
        site, _ = bootstrap_identity(session, settings)
        site_id = site.site_id
        old_snapshot = EventDeploymentSnapshot(
            deployment_id=deployment_id,
            deployment_revision=1,
            event_id=event_id,
            site_id=site_id,
            event_name="Legacy version identity",
            presentations=[
                PresentationSnapshot(
                    presentation_id=presentation_id,
                    title="Legacy presentation",
                    presentation_identifier="UPM-LEGACY-1",
                    central_revision=1,
                    version_numbers=[1],
                )
            ],
        )
        old_event = SyncEventEnvelope(
            event_id=new_uuid7(),
            event_type="central.event_deployment.updated",
            protocol_version=1,
            source="central",
            source_sequence=1,
            authority=AuthorityScope.CENTRAL,
            entity_type="event_deployment",
            entity_id=deployment_id,
            occurred_at=datetime.now(UTC),
            payload=old_snapshot.model_dump(mode="json"),
        )
        assert apply_central_event(session, old_event).accepted
        legacy = session.scalar(
            select(PresentationVersion).where(
                PresentationVersion.presentation_id == presentation_id,
                PresentationVersion.version_number == 1,
            )
        )
        legacy_version_id = legacy.presentation_version_id
        assert legacy_version_id != canonical_version_id

        storage = StorageTarget(
            site_id=site_id,
            display_name="legacy-media",
            storage_type=StorageType.LOCAL_FILESYSTEM,
            root_path="/tmp/upm-test-media",
        )
        session.add(storage)
        session.flush()
        media = MediaObject(
            site_id=site_id,
            event_id=event_id,
            storage_target_id=storage.storage_target_id,
            object_key="legacy/deck.pptx",
            category=MediaCategory.PRESENTATION_VERSION,
            original_filename="deck.pptx",
        )
        session.add(media)
        session.flush()
        asset = PresentationAsset(
            presentation_version_id=legacy_version_id,
            media_object_id=media.media_object_id,
            original_filename="deck.pptx",
            kind=AssetKind.ORIGINAL,
        )
        session.add(asset)
        manifest = MediaTransferManifest(
            transfer_session_id=transfer_id,
            origin_system=SourceSystem.CENTRAL,
            destination_site_id=site_id,
            event_id=event_id,
            presentation_id=presentation_id,
            presentation_version_id=canonical_version_id,
            presentation_version_number=1,
            presentation_identifier="UPM-LEGACY-1",
            original_filename="deck.pptx",
            canonical_filename="UPM-LEGACY-1_v01.pptx",
            expected_size=12,
            sha256="b" * 64,
            created_at=datetime.now(UTC),
        )
        session.add(
            TransferJob(
                transfer_job_id=transfer_id,
                site_id=site_id,
                transfer_type="presentation_media.central_pull",
                payload=manifest.model_dump(mode="json"),
                required_capabilities=["sync-dependencies"],
                idempotency_key=f"central-pull:{transfer_id}",
            )
        )
        session.flush()
        asset_id = asset.presentation_asset_id

    with site_factory.begin() as session:
        reconcile_deferred_media_transfers(session)
    with site_factory() as session:
        canonical = session.get(PresentationVersion, canonical_version_id)
        assert canonical.presentation_id == presentation_id
        assert canonical.version_number == 1
        assert session.get(PresentationVersion, legacy_version_id) is None
        assert (
            session.get(PresentationAsset, asset_id).presentation_version_id == canonical_version_id
        )
        assert session.get(MediaTransferSession, transfer_id).presentation_version_id == (
            canonical_version_id
        )
        assert session.get(TransferJob, transfer_id).required_capabilities == ["transfer"]

    # Replaying reconciliation is a no-op and preserves the converged graph.
    with site_factory.begin() as session:
        reconcile_deferred_media_transfers(session)
    with site_factory() as session:
        assert (
            session.get(PresentationAsset, asset_id).presentation_version_id == canonical_version_id
        )


def test_acknowledged_manifest_retains_intent_and_inventory_repairs_missing_job(
    deployment_databases: tuple[str, sessionmaker[Session]], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, site_factory = deployment_databases
    settings = SiteSettings(
        database_url=SITE_URL,
        credential_encryption_key="test-only-encryption-key-with-32-characters",
    )
    event_id, presentation_id, version_id, transfer_id = [new_uuid7() for _ in range(4)]
    with site_factory.begin() as session:
        site, _ = bootstrap_identity(session, settings)
        site_id = site.site_id
        session.add(SiteEvent(event_id=event_id, site_id=site_id, name="Recovery event"))
        session.flush()
        session.add(
            Presentation(
                presentation_id=presentation_id,
                event_id=event_id,
                title="Recovery presentation",
            )
        )
        session.flush()
        session.add(
            PresentationVersion(
                presentation_version_id=version_id,
                presentation_id=presentation_id,
                version_number=1,
            )
        )
    manifest = MediaTransferManifest(
        transfer_session_id=transfer_id,
        origin_system=SourceSystem.CENTRAL,
        destination_site_id=site_id,
        event_id=event_id,
        presentation_id=presentation_id,
        presentation_version_id=version_id,
        presentation_version_number=1,
        presentation_identifier="UPM-RECOVERY-1",
        original_filename="recovery.pptx",
        canonical_filename="UPM-RECOVERY-1_v01.pptx",
        expected_size=12,
        sha256="d" * 64,
        created_at=datetime.now(UTC),
    )
    transfer_event = SyncEventEnvelope(
        event_id=new_uuid7(),
        event_type="central.media_transfer.available",
        protocol_version=1,
        source="central",
        source_sequence=1,
        authority=AuthorityScope.CENTRAL,
        entity_type="media_transfer",
        entity_id=transfer_id,
        occurred_at=datetime.now(UTC),
        payload=manifest.model_dump(mode="json"),
    )
    original_materialize = site_sync_module._materialize_media_transfer
    monkeypatch.setattr(
        site_sync_module,
        "_materialize_media_transfer",
        lambda *_args: (_ for _ in ()).throw(ValueError("simulated dependency failure")),
    )
    with site_factory.begin() as session:
        assert apply_central_event(session, transfer_event).accepted
    with site_factory() as session:
        intent = session.get(TransferJob, transfer_id)
        assert intent.required_capabilities == ["sync-dependencies"]
        assert intent.error_code == "sync_dependency_materialization_failed"
        assert session.get(MediaTransferSession, transfer_id) is None

    monkeypatch.setattr(site_sync_module, "_materialize_media_transfer", original_materialize)
    with site_factory.begin() as session:
        session.delete(session.get(TransferJob, transfer_id))
    with site_factory.begin() as session:
        assert recover_media_transfer_manifests(session, [manifest.model_dump(mode="json")]) == 1
    with site_factory() as session:
        assert session.get(TransferJob, transfer_id).required_capabilities == ["transfer"]
        assert session.get(MediaTransferSession, transfer_id) is not None
    with site_factory.begin() as session:
        completed = session.get(TransferJob, transfer_id)
        completed.status = JobStatus.SUCCEEDED
        completed.required_capabilities = ["transfer"]
    with site_factory.begin() as session:
        assert recover_media_transfer_manifests(session, [manifest.model_dump(mode="json")]) == 0
    with site_factory() as session:
        completed = session.get(TransferJob, transfer_id)
        assert completed.status is JobStatus.PENDING
        assert completed.required_capabilities == ["transfer"]

    target_id, media_id = new_uuid7(), new_uuid7()
    with site_factory.begin() as session:
        session.add(
            StorageTarget(
                storage_target_id=target_id,
                site_id=site_id,
                display_name="Redeployment media",
                storage_type=StorageType.LOCAL_FILESYSTEM,
                root_path="/tmp/redeployment-media",
                enabled=True,
            )
        )
        session.add(
            MediaObject(
                media_object_id=media_id,
                site_id=site_id,
                event_id=event_id,
                storage_target_id=target_id,
                object_key=f"objects/sha256/{manifest.sha256}",
                category=MediaCategory.PRESENTATION_VERSION,
                original_filename=manifest.original_filename,
                availability=MediaAvailability.AVAILABLE,
                content_hash=manifest.sha256,
                hash_algorithm="sha256",
                size_bytes=manifest.expected_size,
            )
        )
        session.add(
            PresentationAsset(
                presentation_version_id=version_id,
                media_object_id=media_id,
                original_filename=manifest.original_filename,
                kind=AssetKind.ORIGINAL,
            )
        )
        completed = session.get(TransferJob, transfer_id)
        completed.status = JobStatus.SUCCEEDED
        local = session.get(MediaTransferSession, transfer_id)
        local.state = MediaTransferState.COMPLETED
        local.confirmed_offset = manifest.expected_size
        local.media_object_id = media_id

    with site_factory.begin() as session:
        recover_media_transfer_manifests(session, [manifest.model_dump(mode="json")])
        assert session.get(TransferJob, transfer_id).status is JobStatus.SUCCEEDED

    with site_factory.begin() as session:
        session.get(MediaObject, media_id).content_hash = "0" * 64
    with site_factory.begin() as session:
        recover_media_transfer_manifests(session, [manifest.model_dump(mode="json")])
        assert session.get(TransferJob, transfer_id).status is JobStatus.PENDING
        local = session.get(MediaTransferSession, transfer_id)
        assert local.state is MediaTransferState.AVAILABLE
        assert local.confirmed_offset == 0

    # Simulate the authoritative retry completing, then verify a later soft
    # deletion independently causes the same durable intent to be revived.
    with site_factory.begin() as session:
        session.get(MediaObject, media_id).content_hash = manifest.sha256
        completed = session.get(TransferJob, transfer_id)
        completed.status = JobStatus.SUCCEEDED
        local = session.get(MediaTransferSession, transfer_id)
        local.state = MediaTransferState.COMPLETED
        local.confirmed_offset = manifest.expected_size
        local.media_object_id = media_id
    with site_factory.begin() as session:
        session.get(MediaObject, media_id).deleted_at = datetime.now(UTC)
    with site_factory.begin() as session:
        recover_media_transfer_manifests(session, [manifest.model_dump(mode="json")])
        assert session.get(TransferJob, transfer_id).status is JobStatus.PENDING
        local = session.get(MediaTransferSession, transfer_id)
        assert local.state is MediaTransferState.AVAILABLE
        assert local.confirmed_offset == 0
        assert local.media_object_id is None

    # A worker restart/failure during the replacement transfer preserves its
    # resumable offset while making terminal work claimable again.
    partial_target = new_uuid7()
    with site_factory.begin() as session:
        transfer = session.get(TransferJob, transfer_id)
        transfer.status = JobStatus.FAILED
        local = session.get(MediaTransferSession, transfer_id)
        local.state = MediaTransferState.TRANSFERRING
        local.storage_target_id = partial_target
        local.confirmed_offset = 5
    with site_factory.begin() as session:
        recover_media_transfer_manifests(session, [manifest.model_dump(mode="json")])
        assert session.get(TransferJob, transfer_id).status is JobStatus.PENDING
        local = session.get(MediaTransferSession, transfer_id)
        assert local.state is MediaTransferState.TRANSFERRING
        assert local.storage_target_id == partial_target
        assert local.confirmed_offset == 5


def test_manifest_waits_for_snapshot_dependencies_before_becoming_runnable(
    deployment_databases: tuple[str, sessionmaker[Session]],
) -> None:
    _, site_factory = deployment_databases
    settings = SiteSettings(
        database_url=SITE_URL,
        credential_encryption_key="test-only-encryption-key-with-32-characters",
    )
    event_id, presentation_id, version_id, transfer_id = [new_uuid7() for _ in range(4)]
    with site_factory.begin() as session:
        site, _ = bootstrap_identity(session, settings)
        site_id = site.site_id
    manifest = MediaTransferManifest(
        transfer_session_id=transfer_id,
        origin_system=SourceSystem.CENTRAL,
        destination_site_id=site_id,
        event_id=event_id,
        presentation_id=presentation_id,
        presentation_version_id=version_id,
        presentation_version_number=1,
        presentation_identifier="UPM-DEFERRED-1",
        original_filename="deferred.pptx",
        canonical_filename="UPM-DEFERRED-1_v01.pptx",
        expected_size=12,
        sha256="f" * 64,
        created_at=datetime.now(UTC),
    )

    with site_factory.begin() as session:
        assert recover_media_transfer_manifests(
            session, [manifest.model_dump(mode="json")]
        ) == 1
    with site_factory() as session:
        transfer = session.get(TransferJob, transfer_id)
        assert transfer.status is JobStatus.PENDING
        assert transfer.required_capabilities == ["sync-dependencies"]
        assert session.get(MediaTransferSession, transfer_id) is None

    with site_factory.begin() as session:
        session.add(SiteEvent(event_id=event_id, site_id=site_id, name="Deferred event"))
        session.flush()
        session.add(
            Presentation(
                presentation_id=presentation_id,
                event_id=event_id,
                title="Deferred presentation",
            )
        )
        session.flush()
        session.add(
            PresentationVersion(
                presentation_version_id=version_id,
                presentation_id=presentation_id,
                version_number=1,
            )
        )
        summary = reconcile_deferred_media_transfers(session)
        assert summary["materialized"] == 1
    with site_factory() as session:
        transfer = session.get(TransferJob, transfer_id)
        assert transfer.required_capabilities == ["transfer"]
        assert session.get(MediaTransferSession, transfer_id) is not None
        assert len(
            session.scalars(
                select(TransferJob).where(TransferJob.transfer_job_id == transfer_id)
            ).all()
        ) == 1


def test_bulk_orphan_transfers_are_fenced_from_workers(
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
        for index in range(501):
            transfer_id = new_uuid7()
            manifest = MediaTransferManifest(
                transfer_session_id=transfer_id,
                origin_system=SourceSystem.CENTRAL,
                destination_site_id=site_id,
                event_id=new_uuid7(),
                presentation_id=new_uuid7(),
                presentation_version_id=new_uuid7(),
                presentation_version_number=1,
                presentation_identifier=f"UPM-ORPHAN-{index}",
                original_filename=f"orphan-{index}.pptx",
                canonical_filename=f"UPM-ORPHAN-{index}_v01.pptx",
                expected_size=12,
                sha256=f"{index:064x}",
                created_at=datetime.now(UTC),
            )
            session.add(
                TransferJob(
                    transfer_job_id=transfer_id,
                    site_id=site_id,
                    transfer_type="presentation_media.central_pull",
                    payload=manifest.model_dump(mode="json"),
                    status=JobStatus.PENDING,
                    required_capabilities=["transfer"],
                    idempotency_key=f"central-pull:{transfer_id}",
                )
            )
        session.flush()
        summary = reconcile_deferred_media_transfers(session)
        assert summary["orphan_jobs_repaired"] == 501
        assert summary["deferred"] == 501
    with site_factory() as session:
        jobs = session.scalars(
            select(TransferJob).where(
                TransferJob.transfer_type == "presentation_media.central_pull"
            )
        ).all()
        assert len(jobs) == 501
        assert all(job.required_capabilities == ["sync-dependencies"] for job in jobs)
        assert session.scalars(select(MediaTransferSession)).all() == []


def test_deployment_materializes_unmapped_rooms_and_preserves_site_overrides(
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
        existing_room = Room(site_id=site_id, label="  BALLROOM   A ")
        session.add(existing_room)
        session.flush()
        existing_room_id = existing_room.room_id

    deployment_id, event_id = new_uuid7(), new_uuid7()
    ballroom_session_id, expo_session_id = new_uuid7(), new_uuid7()

    def envelope(revision: int) -> SyncEventEnvelope:
        snapshot = EventDeploymentSnapshot(
            deployment_id=deployment_id,
            deployment_revision=revision,
            event_id=event_id,
            site_id=site_id,
            event_name="Automatic room materialization",
            sessions=[
                SessionSnapshot(
                    session_id=ballroom_session_id,
                    title="Keynote",
                    location_name="Ballroom A",
                    status=SessionStatus.SCHEDULED,
                    central_revision=revision,
                ),
                SessionSnapshot(
                    session_id=expo_session_id,
                    title="Expo",
                    location_name="Expo Hall",
                    status=SessionStatus.SCHEDULED,
                    central_revision=revision,
                ),
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
        assert apply_central_event(session, envelope(1)).accepted
    with site_factory() as session:
        rooms = session.scalars(select(Room).where(Room.site_id == site_id)).all()
        assert len(rooms) == 2
        mappings = {
            item.normalized_imported_label: item
            for item in session.scalars(
                select(ProgramRoomMapping).where(ProgramRoomMapping.event_id == event_id)
            )
        }
        assert mappings["ballroom a"].room_id == existing_room_id
        assert mappings["expo hall"].room_id in {item.room_id for item in rooms}
        assert all(
            item.confirmed_by == "deployment-auto-materialization" for item in mappings.values()
        )
        expo_room_id = mappings["expo hall"].room_id

    # A Site operator's deliberate unmap remains authoritative across a newer snapshot.
    with site_factory.begin() as session:
        expo_mapping = session.scalar(
            select(ProgramRoomMapping).where(
                ProgramRoomMapping.event_id == event_id,
                ProgramRoomMapping.normalized_imported_label == "expo hall",
            )
        )
        expo_mapping.room_id = None
        expo_mapping.confirmed_by = "site-operator"
        expo_mapping.revision += 1
    with site_factory.begin() as session:
        assert apply_central_event(session, envelope(2)).accepted
    with site_factory() as session:
        assert session.scalar(select(Room).where(Room.room_id == expo_room_id)) is not None
        assert len(session.scalars(select(Room).where(Room.site_id == site_id)).all()) == 2
        expo_mapping = session.scalar(
            select(ProgramRoomMapping).where(
                ProgramRoomMapping.event_id == event_id,
                ProgramRoomMapping.normalized_imported_label == "expo hall",
            )
        )
        assert expo_mapping.room_id is None
        assert expo_mapping.confirmed_by == "site-operator"
        assert (
            session.scalar(
                select(RoomAssignment).where(
                    RoomAssignment.session_id == expo_session_id,
                    RoomAssignment.active.is_(True),
                )
            )
            is None
        )


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
