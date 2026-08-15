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

from upm_central.config import CentralDatabaseSettings
from upm_central.lifecycle import run_deletion
from upm_central.persistence.database import create_central_engine, create_central_session_factory
from upm_central.persistence.models import DeletionOperation
from upm_central.persistence.queue import CentralQueue
from upm_shared.identifiers import new_uuid7
from upm_shared.jobs import LifecycleDeletionJobPayload


def log(event: str, **context: object) -> None:
    print(json.dumps({"event": event, **context}, default=str), flush=True)


def execute_processing_job(session, queue: CentralQueue, work, worker_id: str) -> bool:
    """Dispatch one claimed Central processing job; return false when it was failed."""
    if work.job_type not in {"lifecycle.delete_event", "lifecycle.delete_person"}:
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
                    if kind == "processing" and not execute_processing_job(
                        session, queue, work, worker_id
                    ):
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
