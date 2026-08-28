"""Central-owned enrollment, authentication, and durable sync application."""

import hashlib
import hmac
import secrets
from datetime import timedelta
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from upm_central.persistence.models import (
    DeletionOperation,
    Event,
    EventDeployment,
    OutboxEvent,
    Presentation,
    PresentationMediaImport,
    PresentationVersion,
    Site,
    SiteCredential,
    SiteEnrollmentClaim,
    SiteManagedSetting,
    SyncCursor,
    SyncReceipt,
    SyncSequence,
    TransferJob,
    utc_now,
)
from upm_central.persistence.queue import CentralQueue
from upm_shared.contracts.deployments import SiteDeploymentStatus
from upm_shared.contracts.media_transfer import MediaTransferProgress
from upm_shared.contracts.sync import (
    UPM_SYNC_PROTOCOL_VERSION,
    EventAcknowledgement,
    SyncEventEnvelope,
)
from upm_shared.enums import (
    AuthorityScope,
    EnrollmentState,
    EventDeploymentStatus,
    JobStatus,
    MediaImportState,
    MediaTransferState,
    SourceSystem,
    SyncState,
)
from upm_shared.jobs import OutboxPayload


def secret_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def secrets_match(value: str, expected_hash: str) -> bool:
    return hmac.compare_digest(secret_hash(value), expected_hash)


def require_protocol(version: int) -> None:
    if version != UPM_SYNC_PROTOCOL_VERSION:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="incompatible_sync_protocol")


def outbox_health(session: Session, *, stuck_after: timedelta = timedelta(minutes=15)) -> dict:
    """Summarize durable delivery health without inspecting process-local worker state."""
    now = utc_now()
    counts = dict(
        session.execute(select(OutboxEvent.status, func.count()).group_by(OutboxEvent.status)).all()
    )
    stuck = (
        session.scalar(
            select(func.count())
            .select_from(OutboxEvent)
            .where(
                OutboxEvent.status == JobStatus.RUNNING,
                OutboxEvent.lease_expires_at < now,
            )
        )
        or 0
    )
    delayed = (
        session.scalar(
            select(func.count())
            .select_from(OutboxEvent)
            .where(
                OutboxEvent.status.in_([JobStatus.PENDING, JobStatus.RETRY_WAIT]),
                OutboxEvent.created_at < now - stuck_after,
            )
        )
        or 0
    )
    dead_letter = sum(counts.get(state, 0) for state in (JobStatus.FAILED, JobStatus.EXHAUSTED))
    return {
        "healthy": stuck == 0 and dead_letter == 0,
        "counts": {state.value: count for state, count in counts.items()},
        "stuck_leases": stuck,
        "delayed": delayed,
        "dead_letter": dead_letter,
        "checked_at": now,
    }


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
    elif event.event_type in {
        "site.event_deployment.received",
        "site.event_deployment.applied",
        "site.event_deployment.failed",
        "site.event_deployment.status",
        "site.event_deployment.stale",
        "site.event_deployment.revoked",
    }:
        try:
            report = SiteDeploymentStatus.model_validate(event.payload)
        except ValueError as exc:
            return EventAcknowledgement(
                event_id=event.event_id,
                accepted=False,
                error_code="malformed_deployment_status",
                detail=str(exc)[:512],
            )
        deployment = session.get(EventDeployment, report.deployment_id)
        if (
            deployment is None
            or report.site_id != site.site_id
            or deployment.site_id != site.site_id
            or deployment.event_id != report.event_id
            or event.entity_id != deployment.deployment_id
        ):
            return EventAcknowledgement(
                event_id=event.event_id,
                accepted=False,
                error_code="invalid_deployment_identity",
            )
        deployment.last_synchronization_at = utc_now()
        deployment.site_status = report.status
        deployment.summary_counts = report.summary_counts
        if report.status in {"applied", "stale"}:
            deployment.acknowledged_revision = max(
                deployment.acknowledged_revision,
                min(report.applied_revision, deployment.desired_revision),
            )
            if deployment.acknowledged_revision == deployment.desired_revision:
                deployment.status = (
                    EventDeploymentStatus.REVOKED
                    if report.status == "revoked"
                    else EventDeploymentStatus.DEPLOYED
                )
                deployment.successfully_deployed_at = utc_now()
                deployment.failure_at = None
                deployment.failure_reason = None
            else:
                deployment.status = EventDeploymentStatus.UPDATE_PENDING
        elif report.status == "received":
            if deployment.status in {
                EventDeploymentStatus.PENDING,
                EventDeploymentStatus.UPDATE_PENDING,
            }:
                deployment.status = EventDeploymentStatus.DEPLOYING
        elif report.status == "failed":
            deployment.status = EventDeploymentStatus.FAILED
            deployment.failure_at = utc_now()
            deployment.failure_reason = report.failure_reason or "Site failed to apply deployment"
        elif report.status == "revoked":
            deployment.acknowledged_revision = max(
                deployment.acknowledged_revision,
                min(report.desired_revision, deployment.desired_revision),
            )
            deployment.status = EventDeploymentStatus.REVOKED
            deployment.failure_at = None
            deployment.failure_reason = None
        site.last_seen_at = utc_now()
        site.last_successful_sync_at = utc_now()
    elif event.event_type == "site.media_transfer.progress":
        try:
            progress = MediaTransferProgress.model_validate(event.payload)
        except ValueError as exc:
            return EventAcknowledgement(
                event_id=event.event_id,
                accepted=False,
                error_code="malformed_transfer_progress",
                detail=str(exc)[:512],
            )
        if progress.site_id != site.site_id or event.entity_id != progress.transfer_session_id:
            return EventAcknowledgement(
                event_id=event.event_id,
                accepted=False,
                error_code="invalid_transfer_progress_identity",
            )
        transfer = session.get(TransferJob, progress.transfer_session_id, with_for_update=True)
        media_import = session.scalar(
            select(PresentationMediaImport).where(
                PresentationMediaImport.transfer_job_id == progress.transfer_session_id
            )
        )
        if (
            transfer is None
            or media_import is None
            or transfer.owning_site_id != site.site_id
            or media_import.event_id != progress.event_id
            or media_import.presentation_version_id != progress.presentation_version_id
            or progress.expected_size != media_import.size_bytes
        ):
            return EventAcknowledgement(
                event_id=event.event_id,
                accepted=False,
                error_code="unknown_transfer_progress",
            )
        previous = int(transfer.payload.get("confirmed_offset", 0))
        completed = transfer.status is JobStatus.SUCCEEDED
        if progress.confirmed_offset >= previous and not completed:
            transfer.payload = {
                **transfer.payload,
                "confirmed_offset": progress.confirmed_offset,
                "expected_size": progress.expected_size,
                "site_state": progress.state,
                "last_progress_at": progress.last_progress_at.isoformat(),
                "local_media_ready": progress.local_media_ready,
            }
            transfer.progress = (
                100
                if progress.expected_size == 0
                else progress.confirmed_offset * 100 / progress.expected_size
            )
            if progress.state is MediaTransferState.COMPLETED and progress.local_media_ready:
                transfer.status = JobStatus.SUCCEEDED
                transfer.completed_at = utc_now()
                transfer.error_code = None
                transfer.last_error = None
                media_import.import_state = MediaImportState.SITE_READY
                media_import.site_media_object_id = progress.media_object_id
                media_import.sync_state = SyncState.SYNCHRONIZED
                media_import.error_code = None
                media_import.error_detail = None
            elif progress.state is MediaTransferState.FAILED:
                transfer.status = JobStatus.FAILED
                transfer.last_error = progress.error_detail
                media_import.import_state = MediaImportState.FAILED
            else:
                media_import.import_state = MediaImportState.TRANSFERRING
        site.last_seen_at = utc_now()
        site.last_successful_sync_at = utc_now()
    elif event.event_type == "site.presentation.upserted":
        payload = event.payload
        try:
            presentation_id = UUID(str(payload["presentation_id"]))
            event_id = UUID(str(payload["event_id"]))
            session_id = UUID(str(payload["session_id"])) if payload.get("session_id") else None
            revision = int(payload.get("revision", 1))
        except (KeyError, TypeError, ValueError) as exc:
            return EventAcknowledgement(
                event_id=event.event_id,
                accepted=False,
                error_code="malformed_presentation",
                detail=str(exc)[:512],
            )
        deployment = session.scalar(
            select(EventDeployment).where(
                EventDeployment.site_id == site.site_id,
                EventDeployment.event_id == event_id,
            )
        )
        if deployment is None or event.entity_id != presentation_id:
            return EventAcknowledgement(
                event_id=event.event_id, accepted=False, error_code="invalid_presentation_scope"
            )
        item = session.get(Presentation, presentation_id)
        if item is None:
            item = Presentation(
                presentation_id=presentation_id,
                event_id=event_id,
                session_id=session_id,
                title=str(payload["title"]),
                presentation_identifier=str(payload["presentation_identifier"]),
                presentation_identifier_source=str(payload["presentation_identifier_source"]),
                external_presentation_id=payload.get("external_presentation_id"),
                source="site",
                source_metadata={"origin_site_id": str(site.site_id)},
                revision=revision,
            )
            session.add(item)
        elif revision > item.revision:
            item.title = str(payload["title"])
            item.session_id = session_id
            item.external_presentation_id = payload.get("external_presentation_id")
            item.revision = revision
    elif event.event_type == "site.presentation_version.created":
        payload = event.payload
        try:
            version_id = UUID(str(payload["presentation_version_id"]))
            presentation_id = UUID(str(payload["presentation_id"]))
            version_number = int(payload["version_number"])
        except (KeyError, TypeError, ValueError) as exc:
            return EventAcknowledgement(
                event_id=event.event_id,
                accepted=False,
                error_code="malformed_presentation_version",
                detail=str(exc)[:512],
            )
        presentation = session.get(Presentation, presentation_id)
        if presentation is None or event.entity_id != version_id:
            return EventAcknowledgement(
                event_id=event.event_id,
                accepted=False,
                error_code="presentation_metadata_required",
            )
        version = session.get(PresentationVersion, version_id)
        if version is None:
            session.add(
                PresentationVersion(
                    presentation_version_id=version_id,
                    presentation_id=presentation_id,
                    version_number=version_number,
                )
            )
        elif version.presentation_id != presentation_id or version.version_number != version_number:
            return EventAcknowledgement(
                event_id=event.event_id,
                accepted=False,
                error_code="presentation_version_conflict",
            )
    elif event.event_type == "site.event_deletion.applied":
        try:
            operation_id = UUID(str(event.payload["deletion_operation_id"]))
            deleted_event_id = UUID(str(event.payload["event_id"]))
        except (KeyError, TypeError, ValueError) as exc:
            return EventAcknowledgement(
                event_id=event.event_id,
                accepted=False,
                error_code="malformed_event_deletion_acknowledgement",
                detail=str(exc)[:512],
            )
        operation = session.get(DeletionOperation, operation_id, with_for_update=True)
        if operation is None or operation.target_id != deleted_event_id:
            return EventAcknowledgement(
                event_id=event.event_id,
                accepted=False,
                error_code="unknown_event_deletion",
            )
        statuses = [dict(item) for item in operation.site_statuses]
        for item in statuses:
            if item.get("site_id") == str(site.site_id):
                item["status"] = "completed"
        operation.site_statuses = statuses
        if statuses and all(item.get("status") == "completed" for item in statuses):
            operation.status = "completed"
            operation.stage = "completed"
            operation.completed_at = utc_now()
    elif event.event_type == "site.event_deletion.requested":
        try:
            deleted_event_id = UUID(str(event.payload["event_id"]))
        except (KeyError, TypeError, ValueError) as exc:
            return EventAcknowledgement(
                event_id=event.event_id,
                accepted=False,
                error_code="malformed_event_deletion_request",
                detail=str(exc)[:512],
            )
        target = session.get(Event, deleted_event_id)
        if target is not None:
            from upm_central.lifecycle import request_deletion

            request_deletion(
                session,
                "event",
                deleted_event_id,
                target.name,
                f"site:{site.site_id}",
            )
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
