"""Runnable Central durable worker process."""

import argparse
import json
import os
import signal
import socket
import tempfile
from datetime import timedelta
from pathlib import Path
from threading import Event

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, ProgrammingError

from upm_central.config import CentralDatabaseSettings
from upm_central.lifecycle import run_bulk_people_deletion, run_deletion
from upm_central.media_replication import cleanup_replication_partials
from upm_central.persistence.database import create_central_engine, create_central_session_factory
from upm_central.persistence.models import DeletionOperation, utc_now
from upm_central.persistence.queue import CentralQueue
from upm_shared.enums import JobStatus
from upm_shared.identifiers import new_uuid7
from upm_shared.jobs import BulkPeopleDeletionJobPayload, LifecycleDeletionJobPayload


def log(event: str, **context: object) -> None:
    print(json.dumps({"event": event, **context}, default=str), flush=True)


def execute_processing_job(session, queue: CentralQueue, work, worker_id: str) -> bool:
    """Dispatch one claimed Central processing job; return false when it was failed."""
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
        CentralQueue(session).register_worker(
            worker_id=worker_id,
            worker_type="sync" if sync else "general",
            hostname=socket.gethostname(),
            service_role=role,
            capabilities=capabilities,
        )
    log("worker_started", worker_id=worker_id, role=role, capabilities=sorted(capabilities))
    try:
        while not stop.is_set():
            ready_file.touch()
            with factory.begin() as session:
                queue = CentralQueue(session)
                queue.register_worker(
                    worker_id=worker_id,
                    worker_type="sync" if sync else "general",
                    hostname=socket.gethostname(),
                    service_role=role,
                    capabilities=capabilities,
                )
                cleanup_replication_partials(
                    session,
                    Path(settings.media_staging_path),
                    utc_now() - timedelta(seconds=settings.transfer_partial_retention_seconds),
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
                    if kind == "processing":
                        try:
                            with session.begin_nested():
                                executed = execute_processing_job(session, queue, work, worker_id)
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
                    queue.complete(work, worker_id)
                    log("job_completed", worker_id=worker_id, job_kind=kind, work_id=work_id)
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
