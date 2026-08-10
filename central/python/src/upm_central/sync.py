"""Central-owned enrollment, authentication, and durable sync application."""

import hashlib
import hmac
import secrets
from datetime import timedelta
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from upm_central.persistence.models import (
    OutboxEvent,
    Site,
    SiteCredential,
    SiteEnrollmentClaim,
    SiteManagedSetting,
    SyncCursor,
    SyncReceipt,
    SyncSequence,
    utc_now,
)
from upm_central.persistence.queue import CentralQueue
from upm_shared.contracts.sync import (
    UPM_SYNC_PROTOCOL_VERSION,
    EventAcknowledgement,
    SyncEventEnvelope,
)
from upm_shared.enums import AuthorityScope, EnrollmentState, SourceSystem
from upm_shared.jobs import OutboxPayload


def secret_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def secrets_match(value: str, expected_hash: str) -> bool:
    return hmac.compare_digest(secret_hash(value), expected_hash)


def require_protocol(version: int) -> None:
    if version != UPM_SYNC_PROTOCOL_VERSION:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="incompatible_sync_protocol")


def authenticate_site(session: Session, site_id: UUID, bearer: str | None) -> Site:
    if not bearer:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="missing_site_credential")
    credential = session.scalar(
        select(SiteCredential).where(
            SiteCredential.site_id == site_id,
            SiteCredential.revoked_at.is_(None),
        )
    )
    if credential is None or not secrets_match(bearer, credential.token_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="invalid_site_credential")
    site = session.get(Site, site_id)
    if site is None or site.enrollment_state != EnrollmentState.ACTIVE or not site.enabled:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="site_not_active")
    return site


def next_sequence(session: Session, site_id: UUID) -> int:
    sequence = session.get(SyncSequence, site_id, with_for_update=True)
    if sequence is None:
        sequence = SyncSequence(site_id=site_id, next_value=2)
        session.add(sequence)
        session.flush()
        return 1
    value = sequence.next_value
    sequence.next_value += 1
    session.flush()
    return value


def envelope(event: OutboxEvent) -> SyncEventEnvelope:
    return SyncEventEnvelope(
        event_id=event.outbox_event_id,
        event_type=event.event_type,
        protocol_version=event.protocol_version,
        source="central",
        source_sequence=event.source_sequence or 0,
        authority=AuthorityScope.CENTRAL,
        entity_type=event.aggregate_type,
        entity_id=event.aggregate_id,
        occurred_at=event.created_at,
        payload=event.payload,
        payload_schema_version=event.payload_schema_version,
        correlation_id=event.correlation_id,
        causation_id=event.causation_id,
    )


def apply_site_event(
    session: Session, site: Site, event: SyncEventEnvelope
) -> EventAcknowledgement:
    if event.protocol_version != UPM_SYNC_PROTOCOL_VERSION:
        return EventAcknowledgement(
            event_id=event.event_id, accepted=False, error_code="incompatible_protocol"
        )
    if (
        event.source != "site"
        or event.source_site_id != site.site_id
        or event.authority != AuthorityScope.SITE
    ):
        return EventAcknowledgement(
            event_id=event.event_id, accepted=False, error_code="invalid_authority"
        )
    existing = session.scalar(
        select(SyncReceipt).where(
            SyncReceipt.site_id == site.site_id, SyncReceipt.event_id == event.event_id
        )
    )
    if existing:
        return EventAcknowledgement(event_id=event.event_id, accepted=True, duplicate=True)
    cursor = session.get(SyncCursor, (site.site_id, "site_to_central"))
    last = cursor.last_sequence if cursor else 0
    if event.source_sequence != last + 1:
        return EventAcknowledgement(
            event_id=event.event_id,
            accepted=False,
            error_code="sequence_gap",
            detail=f"expected {last + 1}",
        )
    if event.event_type in {"site.heartbeat", "site.metadata.updated"}:
        payload = event.payload
        site.last_seen_at = utc_now()
        site.last_successful_sync_at = utc_now()
        site.application_version = str(
            payload.get("application_version", site.application_version or "")
        )[:64]
        site.protocol_version = event.protocol_version
        site.reported_hostname = str(payload.get("hostname") or "")[:255] or None
        site.capabilities = list(payload.get("capabilities", []))
        site.health_summary = dict(payload)
        site.protocol_error = None
    else:
        return EventAcknowledgement(
            event_id=event.event_id, accepted=False, error_code="unsupported_event_type"
        )
    session.add(
        SyncReceipt(
            site_id=site.site_id,
            event_id=event.event_id,
            source_sequence=event.source_sequence,
            event_type=event.event_type,
        )
    )
    if cursor is None:
        cursor = SyncCursor(site_id=site.site_id, direction="site_to_central", last_sequence=0)
        session.add(cursor)
    cursor.last_sequence = event.source_sequence
    cursor.last_event_id = event.event_id
    return EventAcknowledgement(event_id=event.event_id, accepted=True)


def create_setting_event(session: Session, setting: SiteManagedSetting) -> OutboxEvent:
    return CentralQueue(session).enqueue_outbox(
        event_type="site.configuration.updated",
        aggregate_type="site_managed_setting",
        aggregate_id=setting.setting_id,
        owning_site_id=setting.site_id,
        source_sequence=next_sequence(session, setting.site_id),
        protocol_version=UPM_SYNC_PROTOCOL_VERSION,
        idempotency_key=f"setting:{setting.site_id}:{setting.setting_key}:{setting.revision}",
        payload=OutboxPayload(
            source_system=SourceSystem.CENTRAL,
            data={
                "setting_key": setting.setting_key,
                "value": setting.value,
                "revision": setting.revision,
            },
        ),
    )


def issue_poll_token(claim: SiteEnrollmentClaim) -> str:
    token = secrets.token_urlsafe(32)
    claim.poll_token_hash = secret_hash(token)
    claim.expires_at = utc_now() + timedelta(hours=24)
    return token
