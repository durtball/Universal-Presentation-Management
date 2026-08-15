"""Central-only durable queue and transactional outbox operations."""

import logging
from datetime import timedelta
from typing import Any, TypeVar

from sqlalchemy import and_, cast, or_, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session

from upm_central.persistence.models import (
    OutboxEvent,
    ProcessingJob,
    TransferJob,
    WorkerIdentity,
)
from upm_shared.enums import JobStatus
from upm_shared.jobs import (
    JobPayload,
    LifecycleDeletionJobPayload,
    OutboxPayload,
    retry_delay,
    utc_now,
)

JobModel = TypeVar("JobModel", ProcessingJob, TransferJob)
logger = logging.getLogger(__name__)


class CentralQueue:
    """Queue operations scoped exclusively to a Central database session."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def enqueue_processing(self, **values: Any) -> ProcessingJob:
        payload_model = (
            LifecycleDeletionJobPayload
            if values.get("job_type") in {"lifecycle.delete_event", "lifecycle.delete_person"}
            else JobPayload
        )
        values["payload"] = payload_model.model_validate(values.get("payload", {})).model_dump(
            mode="json"
        )
        job = ProcessingJob(**values)
        self.session.add(job)
        self.session.flush()
        return job

    def enqueue_transfer(self, **values: Any) -> TransferJob:
        values["payload"] = JobPayload.model_validate(values.get("payload", {})).model_dump()
        job = TransferJob(**values)
        self.session.add(job)
        self.session.flush()
        return job

    def claim_processing(
        self, worker_id: str, capabilities: set[str], lease: timedelta
    ) -> ProcessingJob | None:
        return self._claim(ProcessingJob, worker_id, capabilities, lease)

    def claim_transfer(
        self, worker_id: str, capabilities: set[str], lease: timedelta
    ) -> TransferJob | None:
        return self._claim(TransferJob, worker_id, capabilities, lease)

    def _claim(
        self,
        model: type[JobModel],
        worker_id: str,
        capabilities: set[str],
        lease: timedelta,
    ) -> JobModel | None:
        now = utc_now()
        available = and_(
            model.status.in_([JobStatus.PENDING, JobStatus.RETRY_WAIT]),
            model.next_attempt_at <= now,
        )
        expired = and_(model.status == JobStatus.RUNNING, model.lease_expires_at < now)
        statement = (
            select(model)
            .where(or_(available, expired))
            .where(model.required_capabilities.op("<@")(cast(sorted(capabilities), JSONB)))
            .order_by(model.priority.desc(), model.created_at, getattr(model, model_id_name(model)))
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        job = self.session.scalars(statement).first()
        if job is None:
            return None
        reclaimed = job.status == JobStatus.RUNNING
        job.status = JobStatus.RUNNING
        job.claimed_by_worker_id = worker_id
        job.lease_expires_at = now + lease
        job.heartbeat_at = now
        job.started_at = job.started_at or now
        job.attempt_count += 1
        self.session.flush()
        logger.info(
            "job_claimed",
            extra={
                "event": "lease_reclaimed" if reclaimed else "job_claimed",
                "worker_id": worker_id,
            },
        )
        return job

    def heartbeat(self, job: JobModel, worker_id: str, lease: timedelta) -> None:
        self._require_claim(job, worker_id)
        now = utc_now()
        job.heartbeat_at = now
        job.lease_expires_at = now + lease
        self.session.flush()
        logger.info("job_heartbeat", extra={"event": "job_heartbeat", "worker_id": worker_id})

    def complete(self, job: JobModel, worker_id: str) -> None:
        self._require_claim(job, worker_id)
        job.status = JobStatus.SUCCEEDED
        job.progress = 100
        job.completed_at = utc_now()
        job.lease_expires_at = None
        self.session.flush()
        logger.info("job_completed", extra={"event": "job_completed", "worker_id": worker_id})

    def fail(
        self,
        job: JobModel,
        worker_id: str,
        *,
        error_code: str,
        message: str,
        retryable: bool,
        base_delay_seconds: float,
        jitter_fraction: float = 0,
        metadata: dict[str, object] | None = None,
    ) -> None:
        self._require_claim(job, worker_id)
        job.error_code = error_code[:100]
        job.last_error = message[:2048]
        job.error_metadata = metadata
        job.claimed_by_worker_id = None
        job.lease_expires_at = None
        if retryable and job.attempt_count < job.max_attempts:
            job.status = JobStatus.RETRY_WAIT
            job.next_attempt_at = utc_now() + retry_delay(
                job.attempt_count,
                base_delay_seconds=base_delay_seconds,
                jitter_fraction=jitter_fraction,
            )
        else:
            job.status = JobStatus.EXHAUSTED if retryable else JobStatus.FAILED
            job.completed_at = utc_now()
        self.session.flush()
        logger.info(
            "job_failure_recorded",
            extra={
                "event": "job_retry_scheduled"
                if job.status == JobStatus.RETRY_WAIT
                else "job_exhausted",
                "worker_id": worker_id,
            },
        )

    @staticmethod
    def _require_claim(job: JobModel, worker_id: str) -> None:
        if job.status != JobStatus.RUNNING or job.claimed_by_worker_id != worker_id:
            raise ValueError("job is not claimed by this worker")

    def enqueue_outbox(self, **values: Any) -> OutboxEvent:
        payload = OutboxPayload.model_validate(values.pop("payload"))
        event = OutboxEvent(
            source_system=payload.source_system,
            payload=payload.data,
            payload_schema_version=payload.schema_version,
            **values,
        )
        self.session.add(event)
        self.session.flush()
        return event

    def claim_outbox(self, worker_id: str, lease: timedelta) -> OutboxEvent | None:
        now = utc_now()
        statement = (
            select(OutboxEvent)
            .where(
                or_(
                    and_(
                        OutboxEvent.status.in_([JobStatus.PENDING, JobStatus.RETRY_WAIT]),
                        OutboxEvent.available_at <= now,
                    ),
                    and_(
                        OutboxEvent.status == JobStatus.RUNNING,
                        OutboxEvent.lease_expires_at < now,
                    ),
                )
            )
            .order_by(
                OutboxEvent.priority.desc(),
                OutboxEvent.created_at,
                OutboxEvent.outbox_event_id,
            )
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        event = self.session.scalars(statement).first()
        if event is None:
            return None
        event.status = JobStatus.RUNNING
        event.claimed_by_worker_id = worker_id
        event.lease_expires_at = now + lease
        event.heartbeat_at = now
        event.attempt_count += 1
        self.session.flush()
        logger.info("outbox_claimed", extra={"event": "outbox_claimed", "worker_id": worker_id})
        return event

    def process_outbox(self, event: OutboxEvent, worker_id: str) -> None:
        self._require_outbox_claim(event, worker_id)
        event.status = JobStatus.SUCCEEDED
        event.processed_at = utc_now()
        event.lease_expires_at = None
        self.session.flush()
        logger.info("outbox_processed", extra={"event": "outbox_processed", "worker_id": worker_id})

    def fail_outbox(
        self,
        event: OutboxEvent,
        worker_id: str,
        *,
        error_code: str,
        message: str,
        retryable: bool,
        base_delay_seconds: float,
    ) -> None:
        self._require_outbox_claim(event, worker_id)
        event.error_code = error_code[:100]
        event.last_error = message[:2048]
        event.claimed_by_worker_id = None
        event.lease_expires_at = None
        if retryable and event.attempt_count < event.max_attempts:
            event.status = JobStatus.RETRY_WAIT
            event.available_at = utc_now() + retry_delay(
                event.attempt_count, base_delay_seconds=base_delay_seconds
            )
        else:
            event.status = JobStatus.EXHAUSTED if retryable else JobStatus.FAILED
        self.session.flush()
        logger.info(
            "outbox_failure_recorded", extra={"event": "outbox_retry", "worker_id": worker_id}
        )

    @staticmethod
    def _require_outbox_claim(event: OutboxEvent, worker_id: str) -> None:
        if event.status != JobStatus.RUNNING or event.claimed_by_worker_id != worker_id:
            raise ValueError("outbox event is not claimed by this worker")

    def register_worker(
        self,
        *,
        worker_id: str,
        worker_type: str,
        hostname: str,
        service_role: str,
        capabilities: set[str],
    ) -> WorkerIdentity:
        now = utc_now()
        worker = self.session.get(WorkerIdentity, worker_id)
        if worker is None:
            worker = WorkerIdentity(
                worker_id=worker_id,
                worker_type=worker_type,
                hostname=hostname,
                service_role=service_role,
                capabilities=sorted(capabilities),
                started_at=now,
                last_heartbeat=now,
            )
            self.session.add(worker)
        else:
            worker.capabilities = sorted(capabilities)
            worker.last_heartbeat = now
        self.session.flush()
        return worker


def model_id_name(model: type[ProcessingJob] | type[TransferJob]) -> str:
    return "processing_job_id" if model is ProcessingJob else "transfer_job_id"
