"""PostgreSQL tests for permanent identity and idempotent bidirectional sync."""

import os
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.schema import CreateSchema, DropSchema

from upm_central.api import create_app as create_central_app
from upm_central.config import CentralDatabaseSettings
from upm_central.persistence.models import (
    AuditRecord as CentralAuditRecord,
)
from upm_central.persistence.models import (
    OutboxEvent as CentralOutboxEvent,
)
from upm_central.persistence.models import Site as CentralSite
from upm_central.persistence.models import (
    SiteCredential,
    SiteEnrollmentClaim,
    SiteManagedSetting,
)
from upm_central.persistence.models import SyncCursor as CentralCursor
from upm_central.persistence.models import SyncReceipt as CentralReceipt
from upm_central.persistence.models import (
    SyncSequence as CentralSequence,
)
from upm_central.sync import apply_site_event
from upm_shared.contracts.sync import UPM_SYNC_PROTOCOL_VERSION, SyncEventEnvelope
from upm_shared.enums import AuthorityScope, EnrollmentState
from upm_shared.identifiers import new_uuid7
from upm_site.config import SiteSettings
from upm_site.persistence.base import SiteBase
from upm_site.persistence.models import (
    CentralRegistration,
    LocalSiteIdentity,
    ManagedSetting,
    Site,
    SyncCursor,
    SyncReceipt,
)
from upm_site.sync import apply_central_event, bootstrap_identity

CENTRAL_URL = os.getenv("UPM_CENTRAL_DATABASE_URL")
SITE_URL = os.getenv("UPM_SITE_DATABASE_URL")

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(
        not CENTRAL_URL or not SITE_URL, reason="independent PostgreSQL URLs required"
    ),
]


def site_settings() -> SiteSettings:
    return SiteSettings(
        database_url=SITE_URL,
        credential_encryption_key="test-only-encryption-key-with-32-characters",
    )


@pytest.fixture
def isolated_site_factory() -> Iterator[sessionmaker[Session]]:
    admin_engine = create_engine(SITE_URL)
    engine = None
    schema = f"site_identity_{uuid4().hex}"
    try:
        with admin_engine.begin() as connection:
            connection.execute(CreateSchema(schema))
        engine = create_engine(
            SITE_URL,
            connect_args={"options": f"-csearch_path={schema}"},
            pool_size=8,
            max_overflow=0,
        )
        SiteBase.metadata.create_all(engine)
        yield sessionmaker(engine, expire_on_commit=False)
    finally:
        if engine is not None:
            engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True, if_exists=True))
        admin_engine.dispose()


def test_site_identity_bootstrap_creates_one_stable_singleton(
    isolated_site_factory: sessionmaker[Session],
) -> None:
    with isolated_site_factory.begin() as session:
        first, _ = bootstrap_identity(session, site_settings())
        first_id = first.site_id
    with isolated_site_factory.begin() as session:
        assert session.scalar(select(func.count()).select_from(LocalSiteIdentity)) == 1
        assert session.scalar(select(func.count()).select_from(Site)) == 1
    with isolated_site_factory.begin() as session:
        second, _ = bootstrap_identity(session, site_settings())
        assert second.site_id == first_id
        assert session.scalar(select(func.count()).select_from(LocalSiteIdentity)) == 1
        assert session.scalar(select(func.count()).select_from(Site)) == 1


def test_concurrent_site_identity_bootstrap_resolves_to_one_site(
    isolated_site_factory: sessionmaker[Session],
) -> None:
    caller_count = 8
    start = Barrier(caller_count)

    def initialize() -> UUID:
        with isolated_site_factory.begin() as session:
            start.wait(timeout=10)
            site, _ = bootstrap_identity(session, site_settings())
            return site.site_id

    with ThreadPoolExecutor(max_workers=caller_count) as executor:
        site_ids = list(executor.map(lambda _: initialize(), range(caller_count)))

    assert len(set(site_ids)) == 1
    with isolated_site_factory.begin() as session:
        assert session.scalar(select(func.count()).select_from(LocalSiteIdentity)) == 1
        assert session.scalar(select(func.count()).select_from(Site)) == 1
        assert session.scalar(select(func.count()).select_from(CentralRegistration)) == 1


def test_concurrent_bootstrap_preserves_existing_site_identity(
    isolated_site_factory: sessionmaker[Session],
) -> None:
    with isolated_site_factory.begin() as session:
        existing, _ = bootstrap_identity(session, site_settings())
        existing_id = existing.site_id

    caller_count = 8
    start = Barrier(caller_count)
    changed_settings = site_settings().model_copy(
        update={"default_display_name": "Must not replace existing identity"}
    )

    def initialize() -> UUID:
        with isolated_site_factory.begin() as session:
            start.wait(timeout=10)
            site, _ = bootstrap_identity(session, changed_settings)
            return site.site_id

    with ThreadPoolExecutor(max_workers=caller_count) as executor:
        site_ids = list(executor.map(lambda _: initialize(), range(caller_count)))

    assert set(site_ids) == {existing_id}
    with isolated_site_factory.begin() as session:
        identity = session.scalar(
            select(LocalSiteIdentity).where(LocalSiteIdentity.singleton_key == 1)
        )
        assert identity is not None
        assert identity.site_id == existing_id
        assert identity.display_name != changed_settings.default_display_name
        assert session.scalar(select(func.count()).select_from(LocalSiteIdentity)) == 1


def test_site_to_central_event_is_idempotent_and_advances_cursor_after_apply() -> None:
    engine = create_engine(CENTRAL_URL)
    site_id = new_uuid7()
    event_id = new_uuid7()
    try:
        with Session(engine) as session, session.begin():
            site = CentralSite(
                site_id=site_id,
                display_name="Sync test",
                enabled=True,
                enrollment_state=EnrollmentState.ACTIVE,
            )
            session.add(site)
        envelope = SyncEventEnvelope(
            event_id=event_id,
            event_type="site.heartbeat",
            protocol_version=1,
            source="site",
            source_site_id=site_id,
            source_sequence=1,
            authority=AuthorityScope.SITE,
            entity_type="site_operational_status",
            entity_id=site_id,
            occurred_at=datetime.now(UTC),
            payload={
                "application_version": "test",
                "capabilities": ["sync-v1"],
                "site_health": "healthy",
            },
        )
        with Session(engine) as session, session.begin():
            session.execute(
                CentralReceipt.__table__.delete().where(CentralReceipt.site_id == site_id)
            )
            session.execute(
                CentralCursor.__table__.delete().where(CentralCursor.site_id == site_id)
            )
            site = session.get(CentralSite, site_id)
            first = apply_site_event(session, site, envelope)
            assert first.accepted and not first.duplicate
        with Session(engine) as session, session.begin():
            site = session.get(CentralSite, site_id)
            duplicate = apply_site_event(session, site, envelope)
            assert duplicate.accepted and duplicate.duplicate
            assert session.get(CentralCursor, (site_id, "site_to_central")).last_sequence == 1
    finally:
        with Session(engine) as session, session.begin():
            session.execute(
                CentralReceipt.__table__.delete().where(CentralReceipt.site_id == site_id)
            )
            session.execute(
                CentralCursor.__table__.delete().where(CentralCursor.site_id == site_id)
            )
            site = session.get(CentralSite, site_id)
            if site:
                session.delete(site)
        engine.dispose()


def test_enrollment_authentication_protocol_and_revocation_api() -> None:
    site_id = new_uuid7()
    admin_token = "test-administrator-token-at-least-32-characters"
    settings = CentralDatabaseSettings(
        database_url=CENTRAL_URL,
        admin_token=admin_token,
        credential_issuer_key="test-credential-issuer-key-at-least-32-characters",
    )
    request = {
        "site_id": str(site_id),
        "display_name": "Enrollment integration test",
        "application_version": "test",
        "protocol_version": 1,
        "claim_secret": "claim-secret-with-at-least-thirty-two-characters",
        "capabilities": ["sync-v1"],
    }
    engine = create_engine(CENTRAL_URL)
    try:
        with TestClient(create_central_app(settings)) as client:
            first = client.post("/api/v1/sites/enrollment-requests", json=request)
            duplicate = client.post("/api/v1/sites/enrollment-requests", json=request)
            assert first.status_code == duplicate.status_code == 202
            poll_token = duplicate.json()["poll_token"]

            assert (
                client.post(
                    f"/api/v1/admin/sites/{site_id}/approve",
                    headers={"X-UPM-Admin-Token": "wrong-token-value-with-at-least-32-characters"},
                    json={},
                ).status_code
                == 401
            )
            assert (
                client.post(
                    f"/api/v1/admin/sites/{site_id}/approve",
                    headers={"X-UPM-Admin-Token": admin_token},
                    json={},
                ).status_code
                == 200
            )
            status_response = client.get(
                f"/api/v1/sites/{site_id}/enrollment-status",
                headers={"X-UPM-Poll-Token": poll_token},
            )
            credential = status_response.json()["credential"]
            recovered = client.get(
                f"/api/v1/sites/{site_id}/enrollment-status",
                headers={"X-UPM-Poll-Token": poll_token},
            )
            assert recovered.json()["credential"] == credential
            headers = {"X-UPM-Site-ID": str(site_id), "Authorization": f"Bearer {credential}"}
            assert (
                client.get(
                    "/api/v1/sync/central-events",
                    headers={**headers, "Authorization": "Bearer wrong"},
                ).status_code
                == 401
            )
            assert client.get("/api/v1/sync/central-events", headers=headers).status_code == 200
            assert (
                client.post(
                    "/api/v1/sync/site-events",
                    headers=headers,
                    json={"protocol_version": 2, "events": []},
                ).status_code
                == 409
            )
            assert (
                client.post(
                    f"/api/v1/admin/sites/{site_id}/revoke",
                    headers={"X-UPM-Admin-Token": admin_token},
                    json={},
                ).status_code
                == 200
            )
            assert client.get("/api/v1/sync/central-events", headers=headers).status_code in {
                401,
                403,
            }
    finally:
        with Session(engine) as session, session.begin():
            session.execute(
                CentralAuditRecord.__table__.delete().where(CentralAuditRecord.site_id == site_id)
            )
            session.execute(
                CentralOutboxEvent.__table__.delete().where(
                    CentralOutboxEvent.owning_site_id == site_id
                )
            )
            session.execute(
                SiteManagedSetting.__table__.delete().where(SiteManagedSetting.site_id == site_id)
            )
            session.execute(
                CentralReceipt.__table__.delete().where(CentralReceipt.site_id == site_id)
            )
            session.execute(
                CentralCursor.__table__.delete().where(CentralCursor.site_id == site_id)
            )
            session.execute(
                SiteCredential.__table__.delete().where(SiteCredential.site_id == site_id)
            )
            session.execute(
                SiteEnrollmentClaim.__table__.delete().where(SiteEnrollmentClaim.site_id == site_id)
            )
            session.execute(
                CentralSequence.__table__.delete().where(CentralSequence.site_id == site_id)
            )
            session.execute(CentralSite.__table__.delete().where(CentralSite.site_id == site_id))
        engine.dispose()


def test_central_to_site_setting_is_persisted_and_duplicate_safe() -> None:
    engine = create_engine(SITE_URL)
    event_id = new_uuid7()
    envelope = SyncEventEnvelope(
        event_id=event_id,
        event_type="site.configuration.updated",
        protocol_version=UPM_SYNC_PROTOCOL_VERSION,
        source="central",
        source_sequence=1,
        authority=AuthorityScope.CENTRAL,
        entity_type="site_managed_setting",
        entity_id=new_uuid7(),
        occurred_at=datetime.now(UTC),
        payload={"setting_key": "sync-proof", "value": {"enabled": True}, "revision": 1},
    )
    try:
        with Session(engine) as session, session.begin():
            session.execute(
                ManagedSetting.__table__.delete().where(ManagedSetting.setting_key == "sync-proof")
            )
            session.execute(
                SyncCursor.__table__.delete().where(SyncCursor.direction == "central_to_site")
            )
        with Session(engine) as session, session.begin():
            assert apply_central_event(session, envelope).accepted
        with Session(engine) as session, session.begin():
            duplicate = apply_central_event(session, envelope)
            assert duplicate.accepted and duplicate.duplicate
            assert session.get(ManagedSetting, "sync-proof").value == {"enabled": True}
    finally:
        with Session(engine) as session, session.begin():
            session.execute(
                ManagedSetting.__table__.delete().where(ManagedSetting.setting_key == "sync-proof")
            )
            session.execute(SyncReceipt.__table__.delete().where(SyncReceipt.event_id == event_id))
            session.execute(
                SyncCursor.__table__.delete().where(SyncCursor.direction == "central_to_site")
            )
        engine.dispose()
