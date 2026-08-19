"""Runnable Central durable worker process."""

import argparse
import asyncio
import json
import os
import signal
import socket
import tempfile
from datetime import timedelta
from pathlib import Path
from threading import Event
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError, ProgrammingError

from upm_central.config import CentralDatabaseSettings
from upm_central.lifecycle import run_bulk_people_deletion, run_deletion
from upm_central.operational_logs import prune_logs, record_log
from upm_central.persistence.database import create_central_engine, create_central_session_factory
from upm_central.persistence.models import (
    DeletionOperation,
    PresentationMediaImport,
    ProcessingJob,
    utc_now,
)
from upm_central.persistence.queue import CentralQueue
from upm_central.presentation_media import CentralMediaStagingService
from upm_central.smb_intake import (
    INGEST_JOB as SMB_INGEST_JOB,
)
from upm_central.smb_intake import (
    RETIRE_JOB as SMB_RETIRE_JOB,
)
from upm_central.smb_intake import (
    SCAN_JOB as SMB_SCAN_JOB,
)
from upm_central.smb_intake import (
    enqueue_reconciliation as enqueue_smb_reconciliation,
)
from upm_central.smb_intake import (
    enqueue_retirement as enqueue_smb_retirement,
)
from upm_central.smb_intake import (
    ingest as ingest_smb,
)
from upm_central.smb_intake import (
    reconcile as reconcile_smb,
)
from upm_central.smb_intake import retire as retire_smb
from upm_central.smb_presentations import JOB as SMB_PRESENTATIONS_JOB
from upm_central.smb_presentations import enqueue as enqueue_smb_presentations
from upm_central.smb_presentations import reconcile as reconcile_smb_presentations
from upm_shared.enums import JobStatus
from upm_shared.identifiers import new_uuid7
from upm_shared.jobs import BulkPeopleDeletionJobPayload, LifecycleDeletionJobPayload
from upm_shared.media_storage_client import AsyncMediaStorageClient, MediaStorageClient


def log(event: str, **context: object) -> None:
    print(json.dumps({"event": event, **context}, default=str), flush=True)


def execute_processing_job(
    session, queue: CentralQueue, work, worker_id: str, media_processor=None
) -> bool:
    """Dispatch one claimed Central processing job; return false when it was failed."""
    if work.job_type == "presentation_media.process":
        if media_processor is None:
            raise RuntimeError("presentation media processor is unavailable")
        media_import_id = work.payload.get("data", {}).get("media_import_id")
        media_processor.analyze(UUID(str(media_import_id)))
        return True
    if work.job_type == "operational_logs.prune":
        prune_logs(session, int(work.payload.get("data", {}).get("retention_days", 30)))
        return True
    if work.job_type == "presentation_media.promote":
        if media_processor is None:
            raise RuntimeError("presentation media processor is unavailable")
        data = work.payload.get("data", {})
        asyncio.run(
            media_processor.promote_and_assign(
                UUID(str(data.get("media_import_id"))),
                UUID(str(data.get("presentation_id"))),
                actor=str(data.get("actor") or "central-admin"),
            )
        )
        return True
    if work.job_type not in {"lifecycle.delete_event", "lifecycle.delete_person"}:
        if work.job_type != "lifecycle.delete_people_bulk":
            return True
        payload = BulkPeopleDeletionJobPayload.model_validate(work.payload)
        operation = session.get(DeletionOperation, payload.data.deletion_operation_id)
        if operation is None:
            queue.fail(
                work,
                worker_id,
                error_code="deletion_missing",
                message="bulk deletion operation does not exist",
                retryable=False,
                base_delay_seconds=1,
            )
            return False
        run_bulk_people_deletion(session, operation, payload.data.person_ids)
        return True
    payload = LifecycleDeletionJobPayload.model_validate(work.payload)
    operation = session.get(DeletionOperation, payload.data.deletion_operation_id)
    if operation is None:
        queue.fail(
            work,
            worker_id,
            error_code="deletion_missing",
            message="deletion operation does not exist",
            retryable=False,
            base_delay_seconds=1,
        )
        return False
    run_deletion(session, operation)
    return True


def processing_deletion_operation_id(work):
    if work.job_type == "lifecycle.delete_people_bulk":
        return BulkPeopleDeletionJobPayload.model_validate(work.payload).data.deletion_operation_id
    if work.job_type in {"lifecycle.delete_event", "lifecycle.delete_person"}:
        return LifecycleDeletionJobPayload.model_validate(work.payload).data.deletion_operation_id
    return None


def deletion_failure_details(exc: Exception) -> dict[str, object]:
    """Extract safe PostgreSQL diagnostics without credentials or a traceback."""
    original = getattr(exc, "orig", None)
    diagnostic = getattr(original, "diag", None)
    return {
        "error_type": type(exc).__name__,
        "database_constraint": getattr(diagnostic, "constraint_name", None),
        "database_table": getattr(diagnostic, "table_name", None),
        "database_detail": getattr(diagnostic, "message_detail", None) or str(original or exc),
    }


def run(*, sync: bool = False, once: bool = False) -> int:
    settings = CentralDatabaseSettings()
    engine = create_central_engine(settings)
    factory = create_central_session_factory(engine)
    role = "central-sync" if sync else "central-worker"
    worker_id = f"{role}:{socket.gethostname()}:{os.getpid()}:{new_uuid7()}"
    capabilities = {
        item.strip() for item in settings.worker_capabilities.split(",") if item.strip()
    }
    media_processor = CentralMediaStagingService(
        factory,
        AsyncMediaStorageClient(settings.media_storage_url, settings.media_storage_token),
        settings.max_upload_bytes,
    )
    storage_client = MediaStorageClient(settings.media_storage_url, settings.media_storage_token)
    stop = Event()

    def request_stop(_signum: int, _frame: object) -> None:
        stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    lease = timedelta(seconds=settings.worker_lease_seconds)
    ready_file = Path(settings.worker_ready_file + ("-sync" if sync else ""))
    if os.name == "nt" and not ready_file.parent.exists():
        ready_file = Path(tempfile.gettempdir()) / ready_file.name
    with factory.begin() as session:
        session.execute(text("SELECT 1"))
        startup_queue = CentralQueue(session)
        startup_queue.register_worker(
            worker_id=worker_id,
            worker_type="sync" if sync else "general",
            hostname=socket.gethostname(),
            service_role=role,
            capabilities=capabilities,
        )
        prune_key = f"operational-logs-prune:{utc_now().date().isoformat()}"
        if (
            session.scalar(
                select(ProcessingJob).where(
                    ProcessingJob.job_type == "operational_logs.prune",
                    ProcessingJob.idempotency_key == prune_key,
                )
            )
            is None
        ):
            startup_queue.enqueue_processing(
                job_type="operational_logs.prune",
                payload={"data": {"retention_days": settings.operational_log_retention_days}},
                idempotency_key=prune_key,
                required_capabilities=["cpu"],
            )
        enqueue_smb_reconciliation(session)
        enqueue_smb_presentations(session, delay_seconds=0)
    log("worker_started", worker_id=worker_id, role=role, capabilities=sorted(capabilities))
    try:
        while not stop.is_set():
            ready_file.touch()
            media_work_id = None
            with factory.begin() as session:
                queue = CentralQueue(session)
                queue.register_worker(
                    worker_id=worker_id,
                    worker_type="sync" if sync else "general",
                    hostname=socket.gethostname(),
                    service_role=role,
                    capabilities=capabilities,
                )
                if not sync:
                    work = queue.claim_processing(worker_id, capabilities, lease)
                    kind = "processing"
                    if work is None:
                        work = queue.claim_transfer(worker_id, capabilities, lease)
                        kind = "transfer"
                else:
                    # Site polling owns delivery acknowledgement. This process maintains
                    # the dedicated lifecycle/health boundary without completing events.
                    work = None
                    kind = "sync"
                if work is not None:
                    work_id = getattr(
                        work, f"{kind}_event_id" if kind == "outbox" else f"{kind}_job_id"
                    )
                    log(f"{kind}_claimed", worker_id=worker_id, work_id=work_id)
                    if kind == "processing" and (
                        work.job_type.startswith("presentation_media.")
                        or work.job_type
                        in {SMB_SCAN_JOB, SMB_INGEST_JOB, SMB_RETIRE_JOB, SMB_PRESENTATIONS_JOB}
                    ):
                        # Commit the durable claim and release this connection before matching or
                        # calling Media Storage. Completion/failure uses a fresh short transaction.
                        media_work_id = work.processing_job_id
                    elif kind == "processing":
                        try:
                            with session.begin_nested():
                                executed = execute_processing_job(
                                    session, queue, work, worker_id, media_processor
                                )
                        except Exception as exc:
                            operation_id = processing_deletion_operation_id(work)
                            operation = (
                                session.get(DeletionOperation, operation_id)
                                if operation_id
                                else None
                            )
                            details = deletion_failure_details(exc)
                            deterministic = isinstance(exc, (IntegrityError, ProgrammingError))
                            queue.fail(
                                work,
                                worker_id,
                                error_code="deletion_execution_failed",
                                message=str(exc),
                                retryable=not deterministic,
                                base_delay_seconds=5,
                                metadata={
                                    **details,
                                    "phase": operation.stage if operation else "dispatch",
                                },
                            )
                            if operation is not None:
                                operation.status = (
                                    "failed"
                                    if work.status in {JobStatus.FAILED, JobStatus.EXHAUSTED}
                                    else "retry_wait"
                                )
                                operation.stage = operation.status
                                table = details["database_table"] or "A database dependency"
                                operation.last_error = (
                                    "Deletion processing failed transiently and will be retried."
                                    if operation.status == "retry_wait"
                                    else f"Deletion failed: {table} still references "
                                    "this Event. The operation can be retried after "
                                    "the dependency issue is resolved."
                                )
                            log(
                                "deletion_execution_failed",
                                worker_id=worker_id,
                                work_id=work_id,
                                deletion_operation_id=operation_id,
                                target_event_id=operation.target_id
                                if operation and operation.target_type == "event"
                                else None,
                                target_event_name=operation.target_display_name
                                if operation and operation.target_type == "event"
                                else None,
                                phase=operation.stage if operation else "dispatch",
                                retry_count=work.attempt_count,
                                next_retry_at=work.next_attempt_at
                                if work.status == JobStatus.RETRY_WAIT
                                else None,
                                **details,
                            )
                            continue
                        if not executed:
                            continue
                    if media_work_id is None:
                        queue.complete(work, worker_id)
                        log("job_completed", worker_id=worker_id, job_kind=kind, work_id=work_id)
            if media_work_id is not None:
                smb_ingested = False
                smb_ingested_sha256 = None
                retirement_result = None
                try:
                    with factory() as session:
                        media_work = session.get(ProcessingJob, media_work_id)
                        session.expunge(media_work)
                    if media_work.job_type.startswith("presentation_media."):
                        execute_processing_job(None, None, media_work, worker_id, media_processor)
                    elif media_work.job_type == SMB_SCAN_JOB:
                        with factory.begin() as smb_session:
                            reconcile_smb(
                                session=smb_session,
                                storage=storage_client,
                                stability_seconds=settings.smb_intake_stability_seconds,
                                scan_interval_seconds=settings.smb_intake_scan_interval_seconds,
                                current_job_id=media_work.processing_job_id,
                            )
                    elif media_work.job_type == SMB_INGEST_JOB:
                        ingested = ingest_smb(media_work, media_processor, storage_client)
                        smb_ingested = True
                        smb_ingested_sha256 = ingested.sha256
                    elif media_work.job_type == SMB_PRESENTATIONS_JOB:
                        with factory.begin() as smb_session:
                            result = reconcile_smb_presentations(
                                smb_session,
                                storage_client,
                                current_job_id=media_work.processing_job_id,
                            )
                        log(
                            "smb_presentations_reconciliation_completed",
                            work_id=media_work_id,
                            **result,
                        )
                    else:
                        retirement_result = retire_smb(media_work, storage_client)
                    with factory.begin() as session:
                        media_work = session.get(ProcessingJob, media_work_id)
                        if smb_ingested:
                            enqueue_smb_retirement(session, media_work, sha256=smb_ingested_sha256)
                        if retirement_result is not None:
                            data = media_work.payload["data"]
                            record_log(
                                session,
                                service="central-worker",
                                severity=(
                                    "warning" if retirement_result.get("source_changed") else "info"
                                ),
                                event_type=(
                                    "smb.intake.retirement_skipped"
                                    if retirement_result.get("source_changed")
                                    else "smb.intake.source_retired"
                                ),
                                message=(
                                    "SMB Incoming source changed and was retained"
                                    if retirement_result.get("source_changed")
                                    else "SMB Incoming source retired after durable staging"
                                ),
                                event_id=UUID(data["event_id"]),
                                worker_id=worker_id,
                                context={"relative_path": data["relative_path"]},
                            )
                        CentralQueue(session).complete(media_work, worker_id)
                    log(
                        "job_completed",
                        worker_id=worker_id,
                        job_kind="processing",
                        work_id=media_work_id,
                    )
                except Exception as exc:
                    with factory.begin() as session:
                        media_work = session.get(ProcessingJob, media_work_id)
                        retirement_failed = media_work.job_type == SMB_RETIRE_JOB
                        CentralQueue(session).fail(
                            media_work,
                            worker_id,
                            error_code=(
                                "smb_retirement_failed"
                                if retirement_failed
                                else (
                                    "smb_presentations_materialization_failed"
                                    if media_work.job_type == SMB_PRESENTATIONS_JOB
                                    else "presentation_media_processing_failed"
                                )
                            ),
                            message=str(exc),
                            retryable=True,
                            base_delay_seconds=settings.worker_retry_base_seconds,
                            metadata={"error_type": type(exc).__name__},
                        )
                        media_import_id_value = media_work.payload.get("data", {}).get(
                            "media_import_id"
                        )
                        media_import_id = (
                            UUID(str(media_import_id_value)) if media_import_id_value else None
                        )
                        media_import = (
                            session.get(PresentationMediaImport, media_import_id)
                            if media_import_id
                            else None
                        )
                        record_log(
                            session,
                            service="central-worker",
                            severity="error",
                            event_type=(
                                "smb.intake.retirement_failed"
                                if retirement_failed
                                else "presentation_media_processing_failed"
                            ),
                            message=(
                                "SMB Incoming source retirement failed and remains retryable"
                                if retirement_failed
                                else str(exc)[:1024]
                            ),
                            batch_id=media_import.batch_id if media_import else None,
                            media_import_id=media_import_id,
                            event_id=media_import.event_id if media_import else None,
                            worker_id=worker_id,
                            context={
                                "work_id": str(media_work_id),
                                "error_code": (
                                    "smb_retirement_failed"
                                    if retirement_failed
                                    else getattr(exc, "code", "processing_failed")
                                ),
                                "exception_type": type(exc).__name__,
                                "attempt": media_work.attempt_count,
                                "max_attempts": media_work.max_attempts,
                                "next_retry_at": str(media_work.next_attempt_at),
                            },
                        )
                    log(
                        "smb_intake_failed"
                        if media_work.job_type.startswith("smb.")
                        else "presentation_media_processing_failed",
                        worker_id=worker_id,
                        work_id=media_work_id,
                        error_type=type(exc).__name__,
                        error_code=getattr(exc, "code", "processing_failed"),
                        safe_message=str(exc)[:1024],
                    )
            if once:
                break
            stop.wait(settings.worker_poll_interval_seconds)
    finally:
        ready_file.unlink(missing_ok=True)
        engine.dispose()
        log("worker_stopped", worker_id=worker_id, role=role)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sync", action="store_true")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    return run(sync=args.sync, once=args.once)


if __name__ == "__main__":
    raise SystemExit(main())
