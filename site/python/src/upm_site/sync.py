"""Site-local identity, protected credential storage, and sync application."""

import base64
import hashlib
import hmac
from datetime import UTC, datetime
from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from upm_shared.contracts.deployments import EVENT_DEPLOYMENT_SCHEMA_VERSION, SiteDeploymentStatus
from upm_shared.contracts.media_transfer import MediaTransferManifest
from upm_shared.contracts.sync import (
    UPM_SYNC_PROTOCOL_VERSION,
    EventAcknowledgement,
    SyncEventEnvelope,
)
from upm_shared.enums import (
    AuthorityScope,
    EnrollmentState,
    EventDeploymentStatus,
    MediaTransferState,
    SourceSystem,
)
from upm_shared.jobs import OutboxPayload
from upm_site.config import SiteSettings
from upm_site.event_deployments import (
    apply_event_deletion,
    apply_people_deletion,
    apply_revocation_event,
    apply_snapshot_event,
)
from upm_site.persistence.models import (
    CentralRegistration,
    Event,
    LocalSiteIdentity,
    ManagedSetting,
    MediaTransferSession,
    OutboxEvent,
    Site,
    SyncCursor,
    SyncReceipt,
    SyncSequence,
    TransferJob,
)
from upm_site.persistence.queue import SiteQueue

# PostgreSQL transaction-scoped advisory lock reserved for Site singleton initialization.
# The lock spans identity and registration creation so every Site process observes one
# permanent identity without relying on exception handling after a uniqueness violation.
SITE_IDENTITY_BOOTSTRAP_LOCK_ID = 0x55504D5349544501


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
    session.execute(select(func.pg_advisory_xact_lock(SITE_IDENTITY_BOOTSTRAP_LOCK_ID)))
    identity = session.scalar(select(LocalSiteIdentity).where(LocalSiteIdentity.singleton_key == 1))
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
    cursor = session.get(SyncCursor, "central_to_site")
    durable_sequence = (
        session.scalar(select(func.max(SyncReceipt.source_sequence))) or 0
        if cursor is None
        else cursor.last_sequence
    )
    if session.scalar(select(SyncReceipt).where(SyncReceipt.event_id == event.event_id)):
        if cursor is None:
            session.add(SyncCursor(direction="central_to_site", last_sequence=durable_sequence))
        return EventAcknowledgement(event_id=event.event_id, accepted=True, duplicate=True)
    last = durable_sequence
    if event.source_sequence != last + 1:
        return EventAcknowledgement(
            event_id=event.event_id,
            accepted=False,
            error_code="sequence_gap",
            detail=f"expected {last + 1}",
        )
    application_error = None
    if event.event_type == "site.configuration.updated":
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
    elif event.event_type in {
        "central.event_deployment.requested",
        "central.event_deployment.updated",
    }:
        try:
            apply_snapshot_event(session, event)
        except PermissionError:
            return EventAcknowledgement(
                event_id=event.event_id, accepted=False, error_code="invalid_authority"
            )
        except (KeyError, TypeError, ValueError) as exc:
            application_error = str(exc)[:2048]
            _enqueue_deployment_failure(session, event, application_error)
    elif event.event_type == "central.event_deployment.revoked":
        try:
            apply_revocation_event(session, event)
        except PermissionError:
            return EventAcknowledgement(
                event_id=event.event_id, accepted=False, error_code="invalid_authority"
            )
        except (KeyError, TypeError, ValueError) as exc:
            application_error = str(exc)[:2048]
            _enqueue_deployment_failure(session, event, application_error)
    elif event.event_type == "central.event.deleted":
        try:
            apply_event_deletion(session, event)
        except (KeyError, TypeError, ValueError) as exc:
            application_error = str(exc)[:2048]
    elif event.event_type == "central.people.deleted":
        try:
            apply_people_deletion(session, event)
        except (KeyError, TypeError, ValueError) as exc:
            application_error = str(exc)[:2048]
    elif event.event_type == "central.media_transfer.available":
        try:
            manifest = MediaTransferManifest.model_validate(event.payload)
            site_id = _local_site_id(session)
            if manifest.destination_site_id != site_id:
                return EventAcknowledgement(
                    event_id=event.event_id,
                    accepted=False,
                    error_code="invalid_transfer_destination",
                )
            transfer = session.get(TransferJob, manifest.transfer_session_id)
            if transfer is None:
                transfer = TransferJob(
                    transfer_job_id=manifest.transfer_session_id,
                    site_id=site_id,
                    transfer_type="presentation_media.central_pull",
                    payload=manifest.model_dump(mode="json"),
                    required_capabilities=["transfer"],
                    idempotency_key=f"central-pull:{manifest.transfer_session_id}",
                )
                session.add(transfer)
            elif transfer.payload != manifest.model_dump(mode="json"):
                return EventAcknowledgement(
                    event_id=event.event_id,
                    accepted=False,
                    error_code="transfer_manifest_conflict",
                )
            local_session = session.get(MediaTransferSession, manifest.transfer_session_id)
            if local_session is None:
                session.add(
                    MediaTransferSession(
                        transfer_session_id=manifest.transfer_session_id,
                        site_id=site_id,
                        event_id=manifest.event_id,
                        presentation_id=manifest.presentation_id,
                        presentation_version_id=manifest.presentation_version_id,
                        original_filename=manifest.original_filename,
                        canonical_filename=manifest.canonical_filename,
                        expected_size=manifest.expected_size,
                        sha256=manifest.sha256,
                        media_type=manifest.media_type,
                        partial_key=f"transfers/{manifest.transfer_session_id}.partial",
                        state=MediaTransferState.AVAILABLE,
                    )
                )
            elif (
                local_session.expected_size != manifest.expected_size
                or local_session.sha256 != manifest.sha256
                or local_session.presentation_version_id != manifest.presentation_version_id
            ):
                return EventAcknowledgement(
                    event_id=event.event_id,
                    accepted=False,
                    error_code="transfer_manifest_conflict",
                )
        except (TypeError, ValueError) as exc:
            application_error = str(exc)[:2048]
    else:
        return EventAcknowledgement(
            event_id=event.event_id, accepted=False, error_code="unsupported_event_type"
        )
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
    return EventAcknowledgement(
        event_id=event.event_id,
        accepted=True,
        error_code="application_failed" if application_error else None,
        detail=application_error,
    )


def _enqueue_deployment_failure(session: Session, event: SyncEventEnvelope, reason: str) -> None:
    """Report a permanently malformed deployment while still advancing the transport cursor."""
    try:
        deployment_id = event.entity_id or UUID(str(event.payload["deployment_id"]))
        event_id = UUID(str(event.payload["event_id"]))
        site_id = _local_site_id(session)
        desired_revision = max(1, int(event.payload.get("deployment_revision", 1)))
    except (KeyError, TypeError, ValueError):
        return
    from upm_site.persistence.models import EventDeploymentProjection

    deployment = session.get(EventDeploymentProjection, deployment_id)
    applied_revision = deployment.applied_revision if deployment else 0
    if deployment is not None:
        deployment.status = EventDeploymentStatus.FAILED
        deployment.desired_revision = max(deployment.desired_revision, desired_revision)
        deployment.failure_at = datetime.now(UTC)
        deployment.failure_reason = reason
    payload = SiteDeploymentStatus(
        deployment_id=deployment_id,
        event_id=event_id,
        site_id=site_id,
        desired_revision=desired_revision,
        applied_revision=applied_revision,
        status="failed",
        failure_reason=reason,
        observed_at=datetime.now(UTC),
    )
    sequence = next_sequence(session)
    SiteQueue(session).enqueue_outbox(
        event_type="site.event_deployment.failed",
        aggregate_type="event_deployment",
        aggregate_id=deployment_id,
        event_id=event_id if session.get(Event, event_id) else None,
        site_id=site_id,
        source_sequence=sequence,
        correlation_id=deployment_id,
        causation_id=event.event_id,
        idempotency_key=f"deployment-failed:{deployment_id}:{desired_revision}:{event.event_id}",
        payload=OutboxPayload(
            source_system=SourceSystem.SITE,
            schema_version=EVENT_DEPLOYMENT_SCHEMA_VERSION,
            data=payload.model_dump(mode="json"),
        ),
    )


def _local_site_id(session: Session) -> UUID:
    from upm_site.persistence.models import LocalSiteIdentity

    identity = session.scalar(select(LocalSiteIdentity).where(LocalSiteIdentity.singleton_key == 1))
    if identity is None:
        raise ValueError("Site identity has not been initialized")
    return identity.site_id


def credential_matches(
    settings: SiteSettings, registration: CentralRegistration, bearer: str | None
) -> bool:
    expected = decrypt_secret(settings, registration.credential_encrypted)
    return bool(expected and bearer and hmac.compare_digest(expected, bearer))
