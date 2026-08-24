"""Site synchronization worker transport; all local state remains PostgreSQL durable."""

import secrets
import socket
from datetime import timedelta

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from upm_shared.contracts.sync import (
    UPM_SYNC_PROTOCOL_VERSION,
    EnrollmentRequest,
    EnrollmentRequestResponse,
    EnrollmentStatusResponse,
    EventAckRequest,
    OutboundSyncResponse,
    SyncBatchRequest,
    SyncBatchResponse,
)
from upm_shared.enums import EnrollmentState, JobStatus
from upm_site.config import SiteSettings
from upm_site.persistence.models import CentralRegistration, OutboxEvent, Site, SyncCursor, utc_now
from upm_site.persistence.queue import SiteQueue
from upm_site.sync import (
    apply_central_event,
    bootstrap_identity,
    decrypt_secret,
    encrypt_secret,
    enqueue_heartbeat,
    envelope,
    reconcile_deferred_media_transfers,
)

TRANSIENT_STATUS_CODES = {408, 425, 429, 502, 503, 504}
PERMANENT_STATUS_CODES = {400, 401, 403, 409, 413, 422}


class DeliveryFailure(RuntimeError):
    def __init__(self, code: str, detail: str, *, retryable: bool) -> None:
        super().__init__(detail)
        self.code = code
        self.retryable = retryable


def checked(response: httpx.Response) -> httpx.Response:
    if response.is_success:
        return response
    retryable = response.status_code in TRANSIENT_STATUS_CODES or response.status_code >= 500
    raise DeliveryFailure(f"http_{response.status_code}", response.text[:2048], retryable=retryable)


def enrollment_step(
    factory: sessionmaker[Session], settings: SiteSettings, client: httpx.Client
) -> None:
    with factory.begin() as session:
        site, registration = bootstrap_identity(session, settings)
        central_url = registration.central_url
        state = registration.state
        if not central_url:
            return
        if state == EnrollmentState.UNREGISTERED:
            claim_secret = secrets.token_urlsafe(48)
            registration.claim_secret_encrypted = encrypt_secret(settings, claim_secret)
            request = EnrollmentRequest(
                site_id=site.site_id,
                display_name=site.display_name,
                application_version=settings.application_version,
                protocol_version=UPM_SYNC_PROTOCOL_VERSION,
                claim_secret=claim_secret,
                reported_hostname=socket.gethostname(),
                capabilities=["sync-v1", "site-health", "managed-settings"],
            )
        else:
            request = None
            poll_token = decrypt_secret(settings, registration.poll_token_encrypted)
    if request is not None:
        response = checked(
            client.post(
                f"{central_url}/api/v1/sites/enrollment-requests",
                json=request.model_dump(mode="json"),
            )
        )
        result = EnrollmentRequestResponse.model_validate(response.json())
        with factory.begin() as session:
            registration = session.get(CentralRegistration, result.site_id)
            registration.state = result.state
            registration.poll_token_encrypted = encrypt_secret(settings, result.poll_token or "")
            registration.last_connection_at = utc_now()
            registration.protocol_compatible = True
            registration.last_error = None
        return
    if state == EnrollmentState.PENDING and poll_token:
        response = checked(
            client.get(
                f"{central_url}/api/v1/sites/{site.site_id}/enrollment-status",
                headers={"X-UPM-Poll-Token": poll_token},
            )
        )
        result = EnrollmentStatusResponse.model_validate(response.json())
        with factory.begin() as session:
            registration = session.get(CentralRegistration, result.site_id)
            registration.state = result.state
            registration.last_connection_at = utc_now()
            registration.protocol_compatible = result.protocol_version == UPM_SYNC_PROTOCOL_VERSION
            registration.last_error = result.reason
            if result.credential:
                registration.credential_encrypted = encrypt_secret(settings, result.credential)
                registration.claim_secret_encrypted = None
                registration.poll_token_encrypted = None


def enqueue_due_heartbeat(factory: sessionmaker[Session], settings: SiteSettings) -> None:
    with factory.begin() as session:
        site, registration = bootstrap_identity(session, settings)
        if registration.state != EnrollmentState.ACTIVE:
            return
        pending = session.scalar(
            select(func.count())
            .select_from(OutboxEvent)
            .where(
                OutboxEvent.event_type == "site.heartbeat",
                OutboxEvent.status.in_(
                    [JobStatus.PENDING, JobStatus.RUNNING, JobStatus.RETRY_WAIT]
                ),
            )
        )
        due = (
            registration.last_heartbeat_at is None
            or utc_now() - registration.last_heartbeat_at
            >= timedelta(seconds=settings.heartbeat_interval_seconds)
        )
        if due and not pending:
            queue_counts = dict(
                session.execute(
                    select(OutboxEvent.status, func.count()).group_by(OutboxEvent.status)
                ).all()
            )
            enqueue_heartbeat(
                session,
                site,
                {
                    "observed_at": utc_now().isoformat(),
                    "application_version": settings.application_version,
                    "protocol_version": UPM_SYNC_PROTOCOL_VERSION,
                    "site_health": "healthy",
                    "database_health": "healthy",
                    "worker_health": "healthy",
                    "storage": {},
                    "queue": {str(key): value for key, value in queue_counts.items()},
                    "capabilities": ["sync-v1", "site-health", "managed-settings"],
                    "hostname": socket.gethostname(),
                },
            )


def auth_context(
    session: Session, settings: SiteSettings
) -> tuple[Site, CentralRegistration, dict[str, str]]:
    site, registration = bootstrap_identity(session, settings)
    token = decrypt_secret(settings, registration.credential_encrypted)
    if registration.state != EnrollmentState.ACTIVE or not token or not registration.central_url:
        raise DeliveryFailure("not_registered", "Site enrollment is not active", retryable=False)
    return (
        site,
        registration,
        {"Authorization": f"Bearer {token}", "X-UPM-Site-ID": str(site.site_id)},
    )


def deliver_site_events(
    factory: sessionmaker[Session], settings: SiteSettings, client: httpx.Client, worker_id: str
) -> None:
    with factory.begin() as session:
        site, registration, headers = auth_context(session, settings)
        queue = SiteQueue(session)
        claimed = []
        for _ in range(settings.sync_batch_count):
            event = queue.claim_outbox(
                worker_id,
                timedelta(seconds=settings.worker_lease_seconds),
                synchronization_only=True,
            )
            if event is None:
                break
            claimed.append(event.outbox_event_id)
        central_url = registration.central_url
    if not claimed:
        return
    try:
        with factory() as session:
            events = [session.get(OutboxEvent, event_id) for event_id in claimed]
            payload = SyncBatchRequest(
                protocol_version=UPM_SYNC_PROTOCOL_VERSION,
                events=[envelope(event) for event in events if event is not None],
            )
        response = checked(
            client.post(
                f"{central_url}/api/v1/sync/site-events",
                headers=headers,
                json=payload.model_dump(mode="json"),
            )
        )
        result = SyncBatchResponse.model_validate(response.json())
        acknowledgements = {ack.event_id: ack for ack in result.acknowledgements}
        with factory.begin() as session:
            queue = SiteQueue(session)
            registration = session.get(CentralRegistration, site.site_id)
            for event_id in claimed:
                event = session.get(OutboxEvent, event_id)
                ack = acknowledgements.get(event_id)
                if ack and ack.accepted:
                    queue.process_outbox(event, worker_id)
                    if event.event_type == "site.heartbeat":
                        registration.last_heartbeat_at = utc_now()
                else:
                    queue.fail_outbox(
                        event,
                        worker_id,
                        error_code=ack.error_code if ack else "missing_ack",
                        message=ack.detail
                        if ack and ack.detail
                        else "receiver did not acknowledge event",
                        retryable=bool(ack and ack.error_code == "sequence_gap"),
                        base_delay_seconds=settings.worker_retry_base_seconds,
                    )
            registration.last_successful_sync_at = utc_now()
            registration.last_connection_at = utc_now()
            registration.last_error = None
    except (httpx.TransportError, DeliveryFailure) as exc:
        failure = (
            exc
            if isinstance(exc, DeliveryFailure)
            else DeliveryFailure("transport_error", str(exc), retryable=True)
        )
        with factory.begin() as session:
            queue = SiteQueue(session)
            for event_id in claimed:
                event = session.get(OutboxEvent, event_id)
                if event and event.status == JobStatus.RUNNING:
                    queue.fail_outbox(
                        event,
                        worker_id,
                        error_code=failure.code,
                        message=str(failure),
                        retryable=failure.retryable,
                        base_delay_seconds=settings.worker_retry_base_seconds,
                    )
            registration = session.get(CentralRegistration, site.site_id)
            registration.last_error = str(failure)
            if failure.code in {"http_401", "http_403"}:
                registration.state = EnrollmentState.REVOKED
            if failure.code == "http_409":
                registration.protocol_compatible = False
        raise


def pull_central_events(
    factory: sessionmaker[Session], settings: SiteSettings, client: httpx.Client
) -> None:
    with factory.begin() as session:
        site, registration, headers = auth_context(session, settings)
        central_url = registration.central_url
    response = checked(client.get(f"{central_url}/api/v1/sync/central-events", headers=headers))
    outbound = OutboundSyncResponse.model_validate(response.json())
    if not outbound.events:
        return
    with factory.begin() as session:
        acknowledgements = [apply_central_event(session, event) for event in outbound.events]
        rejected = [ack for ack in acknowledgements if not ack.accepted]
        if rejected:
            raise DeliveryFailure(
                rejected[0].error_code or "apply_failed",
                rejected[0].detail or "Central event rejected",
                retryable=False,
            )
        cursor = session.get(SyncCursor, "central_to_site")
        checkpoint = cursor.last_sequence if cursor else 0
    ack = EventAckRequest(
        protocol_version=UPM_SYNC_PROTOCOL_VERSION,
        event_ids=[event.event_id for event in outbound.events],
        checkpoint_sequence=checkpoint,
    )
    checked(
        client.post(
            f"{central_url}/api/v1/sync/central-events/ack",
            headers=headers,
            json=ack.model_dump(mode="json"),
        )
    )
    with factory.begin() as session:
        registration = session.get(CentralRegistration, site.site_id)
        registration.last_successful_sync_at = utc_now()
        registration.last_connection_at = utc_now()
        registration.last_error = None


def synchronize_once(
    factory: sessionmaker[Session],
    settings: SiteSettings,
    worker_id: str,
    client: httpx.Client | None = None,
) -> None:
    owns_client = client is None
    client = client or httpx.Client(timeout=10.0)
    try:
        # Deferred manifests are durable Site state and can become runnable without a new
        # Central event (for example, after deploying identity-reconciliation code).
        with factory.begin() as session:
            reconcile_deferred_media_transfers(session)
        enrollment_step(factory, settings, client)
        enqueue_due_heartbeat(factory, settings)
        try:
            deliver_site_events(factory, settings, client, worker_id)
            pull_central_events(factory, settings, client)
        except DeliveryFailure as exc:
            if exc.code != "not_registered":
                raise
        except httpx.TransportError as exc:
            with factory.begin() as session:
                _, registration = bootstrap_identity(session, settings)
                registration.last_error = str(exc)[:2048]
            raise
    finally:
        if owns_client:
            client.close()
