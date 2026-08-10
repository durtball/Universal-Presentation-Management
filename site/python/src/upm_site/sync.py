"""Site-local identity, protected credential storage, and sync application."""

import base64
import hashlib
import hmac

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.orm import Session

from upm_shared.contracts.sync import (
    UPM_SYNC_PROTOCOL_VERSION,
    EventAcknowledgement,
    SyncEventEnvelope,
)
from upm_shared.enums import AuthorityScope, EnrollmentState, SourceSystem
from upm_shared.jobs import OutboxPayload
from upm_site.config import SiteSettings
from upm_site.persistence.models import (
    CentralRegistration,
    LocalSiteIdentity,
    ManagedSetting,
    OutboxEvent,
    Site,
    SyncCursor,
    SyncReceipt,
    SyncSequence,
)
from upm_site.persistence.queue import SiteQueue


def cipher(settings: SiteSettings) -> Fernet:
    if not settings.credential_encryption_key:
        raise RuntimeError("UPM_SITE_CREDENTIAL_ENCRYPTION_KEY is required for synchronization")
    key = base64.urlsafe_b64encode(
        hashlib.sha256(settings.credential_encryption_key.encode()).digest()
    )
    return Fernet(key)


def encrypt_secret(settings: SiteSettings, value: str) -> bytes:
    return cipher(settings).encrypt(value.encode())


def decrypt_secret(settings: SiteSettings, value: bytes | None) -> str | None:
    if value is None:
        return None
    try:
        return cipher(settings).decrypt(value).decode()
    except InvalidToken as exc:
        raise RuntimeError("Site credential encryption key does not match stored data") from exc


def bootstrap_identity(
    session: Session, settings: SiteSettings
) -> tuple[Site, CentralRegistration]:
    identity = session.scalar(select(LocalSiteIdentity).limit(1))
    site = session.get(Site, identity.site_id) if identity else None
    if identity is None:
        site = Site(display_name=settings.default_display_name)
        session.add(site)
        session.flush()
        identity = LocalSiteIdentity(
            site_id=site.site_id,
            singleton_key=1,
            display_name=site.display_name,
            installation_version=settings.application_version,
        )
        session.add(identity)
        session.flush()
    if site is None:
        raise RuntimeError("local Site identity refers to a missing Site record")
    registration = session.get(CentralRegistration, site.site_id)
    if registration is None:
        registration = CentralRegistration(
            site_id=site.site_id,
            central_url=settings.central_url,
            state=EnrollmentState.UNREGISTERED,
        )
        session.add(registration)
        session.flush()
    elif settings.central_url and not registration.central_url:
        registration.central_url = settings.central_url
    return site, registration


def next_sequence(session: Session) -> int:
    sequence = session.get(SyncSequence, 1, with_for_update=True)
    if sequence is None:
        sequence = SyncSequence(singleton_key=1, next_value=2)
        session.add(sequence)
        session.flush()
        return 1
    value = sequence.next_value
    sequence.next_value += 1
    return value


def enqueue_heartbeat(session: Session, site: Site, payload: dict[str, object]) -> OutboxEvent:
    sequence = next_sequence(session)
    return SiteQueue(session).enqueue_outbox(
        event_type="site.heartbeat",
        aggregate_type="site_operational_status",
        aggregate_id=site.site_id,
        site_id=site.site_id,
        protocol_version=UPM_SYNC_PROTOCOL_VERSION,
        source_sequence=sequence,
        idempotency_key=f"heartbeat:{site.site_id}:{sequence}",
        payload=OutboxPayload(source_system=SourceSystem.SITE, data=payload),
    )


def envelope(event: OutboxEvent) -> SyncEventEnvelope:
    return SyncEventEnvelope(
        event_id=event.outbox_event_id,
        event_type=event.event_type,
        protocol_version=event.protocol_version,
        source="site",
        source_site_id=event.site_id,
        source_sequence=event.source_sequence or 0,
        authority=AuthorityScope.SITE,
        entity_type=event.aggregate_type,
        entity_id=event.aggregate_id,
        occurred_at=event.created_at,
        payload=event.payload,
        payload_schema_version=event.payload_schema_version,
        correlation_id=event.correlation_id,
        causation_id=event.causation_id,
    )


def apply_central_event(session: Session, event: SyncEventEnvelope) -> EventAcknowledgement:
    if event.protocol_version != UPM_SYNC_PROTOCOL_VERSION:
        return EventAcknowledgement(
            event_id=event.event_id, accepted=False, error_code="incompatible_protocol"
        )
    if event.source != "central" or event.authority != AuthorityScope.CENTRAL:
        return EventAcknowledgement(
            event_id=event.event_id, accepted=False, error_code="invalid_authority"
        )
    if session.scalar(select(SyncReceipt).where(SyncReceipt.event_id == event.event_id)):
        return EventAcknowledgement(event_id=event.event_id, accepted=True, duplicate=True)
    cursor = session.get(SyncCursor, "central_to_site")
    last = cursor.last_sequence if cursor else 0
    if event.source_sequence != last + 1:
        return EventAcknowledgement(
            event_id=event.event_id,
            accepted=False,
            error_code="sequence_gap",
            detail=f"expected {last + 1}",
        )
    if event.event_type != "site.configuration.updated":
        return EventAcknowledgement(
            event_id=event.event_id, accepted=False, error_code="unsupported_event_type"
        )
    key = str(event.payload["setting_key"])
    revision = int(event.payload["revision"])
    setting = session.get(ManagedSetting, key)
    if setting is None:
        setting = ManagedSetting(
            setting_key=key, value=dict(event.payload["value"]), central_revision=revision
        )
        session.add(setting)
    elif revision > setting.central_revision:
        setting.value = dict(event.payload["value"])
        setting.central_revision = revision
    session.add(
        SyncReceipt(
            event_id=event.event_id,
            source_sequence=event.source_sequence,
            event_type=event.event_type,
        )
    )
    if cursor is None:
        cursor = SyncCursor(direction="central_to_site", last_sequence=0)
        session.add(cursor)
    cursor.last_sequence = event.source_sequence
    cursor.last_event_id = event.event_id
    return EventAcknowledgement(event_id=event.event_id, accepted=True)


def credential_matches(
    settings: SiteSettings, registration: CentralRegistration, bearer: str | None
) -> bool:
    expected = decrypt_secret(settings, registration.credential_encrypted)
    return bool(expected and bearer and hmac.compare_digest(expected, bearer))
