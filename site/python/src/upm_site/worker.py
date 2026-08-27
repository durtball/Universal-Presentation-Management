"""Runnable Site durable worker process."""

import argparse
import json
import os
import signal
import socket
import tempfile
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from threading import Event
from uuid import UUID

import httpx
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from upm_shared.enums import JobStatus, MediaReplicationState, MediaTransferState
from upm_shared.identifiers import new_uuid7
from upm_shared.media_storage_client import AsyncMediaStorageClient, MediaStorageClient
from upm_shared.smb import SmbControlClient
from upm_site.config import SiteSettings
from upm_site.media.ingestion import MediaIngestionService
from upm_site.media.replication import execute_central_push
from upm_site.media.transfer import (
    cleanup_transfer_partials,
    enqueue_transfer_progress,
    execute_central_pull,
    recover_exhausted_finalizations,
)
from upm_site.operational_logs import prune_logs, record_log
from upm_site.persistence.database import create_site_engine, create_site_session_factory
from upm_site.persistence.models import (
    MediaObject,
    MediaReplicationSession,
    MediaTransferSession,
    ProcessingJob,
    TransferJob,
    utc_now,
)
from upm_site.persistence.queue import SiteQueue
from upm_site.presentation_media_api import (
    ASSET_RECONCILIATION_JOB,
    INTAKE_PROMOTE_JOB,
    INTAKE_REJECT_JOB,
    backfill_confirmed_original_assets,
    enqueue_asset_reconciliation,
)
from upm_site.smb_intake import (
    INGEST_JOB as SMB_INGEST_JOB,
)
from upm_site.smb_intake import (
    RETIRE_JOB as SMB_RETIRE_JOB,
)
from upm_site.smb_intake import (
    SCAN_JOB as SMB_SCAN_JOB,
)
from upm_site.smb_intake import (
    enqueue_reconciliation as enqueue_smb_reconciliation,
)
from upm_site.smb_intake import (
    enqueue_retirement as enqueue_smb_retirement,
)
from upm_site.smb_intake import (
    ingest as ingest_smb,
)
from upm_site.smb_intake import (
    reconcile as reconcile_smb,
)
from upm_site.smb_intake import retire as retire_smb
from upm_site.smb_presentations import JOB as SMB_PRESENTATIONS_JOB
from upm_site.smb_presentations import enqueue as enqueue_smb_presentations
from upm_site.smb_presentations import reconcile as reconcile_smb_presentations
from upm_site.sync import bootstrap_identity
from upm_site.sync_transport import synchronize_once

PULL_TRANSFER = "presentation_media.central_pull"
PUSH_TRANSFER = "presentation_media.central_push"


class TransferExecutors:
    """Direction-specific bounded transfer slots, separate from ordinary worker work."""

    def __init__(self, pull_limit: int, push_limit: int) -> None:
        self.limits = {PULL_TRANSFER: pull_limit, PUSH_TRANSFER: push_limit}
        self.executors = {
            PULL_TRANSFER: ThreadPoolExecutor(max_workers=pull_limit, thread_name_prefix="pull"),
            PUSH_TRANSFER: ThreadPoolExecutor(max_workers=push_limit, thread_name_prefix="push"),
        }
        self.active: dict[Future[None], tuple[str, UUID]] = {}

    def available(self, transfer_type: str) -> int:
        active = sum(direction == transfer_type for direction, _job_id in self.active.values())
        return self.limits[transfer_type] - active

    def submit(self, transfer_type: str, job_id: UUID, operation) -> bool:
        if self.available(transfer_type) <= 0:
            return False
        future = self.executors[transfer_type].submit(operation)
        self.active[future] = (transfer_type, job_id)
        return True

    def reap(self) -> list[tuple[UUID, BaseException]]:
        failures = []
        for future, (_direction, job_id) in list(self.active.items()):
            if not future.done():
                continue
            del self.active[future]
            error = future.exception()
            if error is not None:
                failures.append((job_id, error))
        return failures

    def shutdown(self) -> None:
        for executor in self.executors.values():
            executor.shutdown(wait=True, cancel_futures=False)


def execute_transfer_work(
    factory: sessionmaker[Session],
    settings: SiteSettings,
    worker_id: str,
    transfer_job_id: UUID,
) -> None:
    """Execute one leased transfer in its own transaction and HTTP client."""
    with factory.begin() as session:
        work = session.get(TransferJob, transfer_job_id, with_for_update=True)
        if (
            work is None
            or work.status is not JobStatus.RUNNING
            or work.claimed_by_worker_id != worker_id
        ):
            return
        queue = SiteQueue(session)
        completed = True
        if work.transfer_type == PULL_TRANSFER:
            if session.get(MediaTransferSession, work.transfer_job_id) is None:
                defer_orphaned_pull(work)
                log("media_pull_deferred_missing_session", work_id=transfer_job_id)
                return
            try:
                with httpx.Client(timeout=30.0) as client:
                    completed = execute_central_pull(session, factory, settings, work, client)
            except Exception as error:
                fail_central_pull(session, queue, work, worker_id, error, settings)
                log("media_pull_failed", work_id=transfer_job_id, detail=str(error)[:2048])
                return
        elif work.transfer_type == PUSH_TRANSFER:
            try:
                with httpx.Client(timeout=30.0) as client:
                    completed = execute_central_push(session, factory, settings, work, client)
            except Exception as error:
                replication = session.get(MediaReplicationSession, transfer_job_id)
                if replication is not None:
                    replication.retry_count += 1
                    replication.last_error = str(error)[:2048]
                queue.fail(
                    work,
                    worker_id,
                    error_code="media_push_failed",
                    message=str(error),
                    retryable=True,
                    base_delay_seconds=settings.worker_retry_base_seconds,
                )
                if replication is not None:
                    replication.state = (
                        MediaReplicationState.RETRY_WAIT
                        if work.status is JobStatus.RETRY_WAIT
                        else MediaReplicationState.FAILED
                    )
                log("media_push_failed", work_id=transfer_job_id, detail=str(error)[:2048])
                return
        else:
            raise ValueError(f"unsupported transfer type: {work.transfer_type}")
        if not completed:
            work.status = JobStatus.PENDING
            work.claimed_by_worker_id = None
            work.lease_expires_at = None
            return
        queue.complete(work, worker_id)
        log("job_completed", worker_id=worker_id, job_kind="transfer", work_id=transfer_job_id)


def defer_orphaned_pull(work: TransferJob) -> None:
    """Fence an invalid pull intent from workers until sync can materialize dependencies."""
    work.status = JobStatus.PENDING
    work.required_capabilities = ["sync-dependencies"]
    work.claimed_by_worker_id = None
    work.lease_expires_at = None
    work.heartbeat_at = None
    work.error_code = "sync_dependency_materialization_required"
    work.last_error = "transfer session is missing; returned to dependency reconciliation"


def fill_transfer_slots(
    factory: sessionmaker[Session],
    settings: SiteSettings,
    worker_id: str,
    capabilities: set[str],
    lease: timedelta,
    executors: TransferExecutors,
) -> None:
    for transfer_type in (PULL_TRANSFER, PUSH_TRANSFER):
        while executors.available(transfer_type) > 0:
            with factory.begin() as session:
                work = SiteQueue(session).claim_transfer(
                    worker_id,
                    capabilities,
                    lease,
                    transfer_type=transfer_type,
                )
                if work is None:
                    break
                transfer_job_id = work.transfer_job_id
            executors.submit(
                transfer_type,
                transfer_job_id,
                lambda job_id=transfer_job_id: execute_transfer_work(
                    factory, settings, worker_id, job_id
                ),
            )


def fail_central_pull(
    session: Session,
    queue: SiteQueue,
    work: TransferJob,
    worker_id: str,
    error: Exception,
    settings: SiteSettings,
) -> None:
    """Persist a retryable pull failure without allowing progress replay to escape."""
    queue.fail(
        work,
        worker_id,
        error_code="media_pull_failed",
        message=str(error),
        retryable=True,
        base_delay_seconds=settings.worker_retry_base_seconds,
    )
    transfer = session.get(MediaTransferSession, work.transfer_job_id)
    if transfer is None:
        return
    transfer.retry_count += 1
    transfer.error_detail = str(error)[:2048]
    transfer.last_progress_at = utc_now()
    transfer.state = (
        MediaTransferState.RETRY_WAIT
        if work.status is JobStatus.RETRY_WAIT
        else MediaTransferState.FAILED
    )
    enqueue_transfer_progress(session, transfer)


STARTUP_MAINTENANCE_LOCK_ID = 7_091_625_311


def log(event: str, **context: object) -> None:
    print(json.dumps({"event": event, **context}, default=str), flush=True)


def enqueue_startup_maintenance(
    session: Session,
    *,
    site_id: UUID,
    retention_days: int,
) -> ProcessingJob | None:
    """Idempotently enqueue Site-scoped startup work for either worker role."""
    # The general worker and sync process commonly start together. Serialize only this tiny
    # check/insert boundary so they cannot race on the durable idempotency constraint.
    session.execute(select(func.pg_advisory_xact_lock(STARTUP_MAINTENANCE_LOCK_ID)))
    prune_key = f"operational-logs-prune:{utc_now().date().isoformat()}"
    existing = session.scalar(
        select(ProcessingJob).where(
            ProcessingJob.site_id == site_id,
            ProcessingJob.job_type == "operational_logs.prune",
            ProcessingJob.idempotency_key == prune_key,
        )
    )
    if existing is not None:
        return None
    return SiteQueue(session).enqueue_processing(
        site_id=site_id,
        job_type="operational_logs.prune",
        payload={"data": {"retention_days": retention_days}},
        idempotency_key=prune_key,
        required_capabilities=["cpu"],
    )


def run(*, sync: bool = False, once: bool = False) -> int:
    settings = SiteSettings()
    engine = create_site_engine(settings)
    factory = create_site_session_factory(engine)
    role = "site-sync" if sync else "site-worker"
    worker_id = f"{role}:{socket.gethostname()}:{os.getpid()}:{new_uuid7()}"
    capabilities = {
        item.strip() for item in settings.worker_capabilities.split(",") if item.strip()
    }
    stop = Event()
    # Rebuilding a large SMB presentation view can legitimately take longer than
    # the client's short control-plane default even while Media Storage is healthy.
    storage_client = MediaStorageClient(
        settings.media_storage_url, settings.media_storage_token, timeout=300
    )
    ingestion_service = MediaIngestionService(
        factory,
        max_upload_bytes=settings.max_upload_bytes,
        storage_client=AsyncMediaStorageClient(
            settings.media_storage_url, settings.media_storage_token
        ),
    )

    def request_stop(_signum: int, _frame: object) -> None:
        stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    lease = timedelta(seconds=settings.worker_lease_seconds)
    transfer_executors = (
        None
        if sync
        else TransferExecutors(
            settings.transfer_pull_concurrency,
            settings.transfer_push_concurrency,
        )
    )
    ready_file = Path(settings.worker_ready_file + ("-sync" if sync else ""))
    if os.name == "nt" and not ready_file.parent.exists():
        ready_file = Path(tempfile.gettempdir()) / ready_file.name
    with factory.begin() as session:
        session.execute(text("SELECT 1"))
        site, _registration = bootstrap_identity(session, settings)
        startup_queue = SiteQueue(session)
        startup_queue.register_worker(
            worker_id=worker_id,
            worker_type="sync" if sync else "general",
            hostname=socket.gethostname(),
            service_role=role,
            capabilities=capabilities,
        )
        enqueue_startup_maintenance(
            session,
            site_id=site.site_id,
            retention_days=settings.operational_log_retention_days,
        )
        enqueue_smb_reconciliation(session, site.site_id)
        enqueue_smb_presentations(session, site.site_id, delay_seconds=0)
        enqueue_asset_reconciliation(session, site.site_id)
        recover_exhausted_finalizations(session)
    log("worker_started", worker_id=worker_id, role=role, capabilities=sorted(capabilities))
    try:
        while not stop.is_set():
            ready_file.touch()
            if transfer_executors is not None:
                for failed_job_id, error in transfer_executors.reap():
                    log(
                        "transfer_task_failed",
                        work_id=failed_job_id,
                        error_type=type(error).__name__,
                        detail=str(error)[:2048],
                    )
                fill_transfer_slots(
                    factory,
                    settings,
                    worker_id,
                    capabilities,
                    lease,
                    transfer_executors,
                )
            with factory.begin() as session:
                queue = SiteQueue(session)
                queue.register_worker(
                    worker_id=worker_id,
                    worker_type="sync" if sync else "general",
                    hostname=socket.gethostname(),
                    service_role=role,
                    capabilities=capabilities,
                )
                cleanup_transfer_partials(
                    session,
                    settings,
                    utc_now() - timedelta(seconds=settings.transfer_partial_retention_seconds),
                )
                if not sync:
                    work = queue.claim_processing(worker_id, capabilities, lease)
                    kind = "processing"
                else:
                    work = None
                    kind = "sync"
                if work is not None:
                    work_id = getattr(
                        work, f"{kind}_event_id" if kind == "outbox" else f"{kind}_job_id"
                    )
                    log(f"{kind}_claimed", worker_id=worker_id, work_id=work_id)
                    completed = True
                    if kind == "processing" and work.job_type == "operational_logs.prune":
                        prune_logs(session, settings.operational_log_retention_days)
                    elif kind == "processing" and work.job_type == ASSET_RECONCILIATION_JOB:
                        repaired = backfill_confirmed_original_assets(session, work.site_id)
                        enqueue_asset_reconciliation(
                            session, work.site_id, current_job_id=work.processing_job_id
                        )
                        log("presentation_assets_reconciled", repaired=repaired)
                    elif kind == "processing" and work.job_type in {
                        INTAKE_PROMOTE_JOB,
                        INTAKE_REJECT_JOB,
                    }:
                        media = session.get(
                            MediaObject,
                            UUID(work.payload["data"]["media_object_id"]),
                            with_for_update=True,
                        )
                        if media is None or not media.content_hash:
                            raise RuntimeError("Intake media is missing")
                        if work.job_type == INTAKE_PROMOTE_JOB:
                            result = storage_client.promote_intake(
                                media.storage_target_id, media.object_key, media.content_hash
                            )
                            media.disposition = "authoritative"
                        else:
                            result = storage_client.reject_intake(
                                media.storage_target_id, media.object_key, media.content_hash
                            )
                            media.disposition = "rejected"
                            media.availability = "quarantined"
                        media.storage_target_id = UUID(result["storage_target_id"])
                        media.object_key = result["storage_key"]
                    elif kind == "processing" and work.job_type == SMB_SCAN_JOB:
                        try:
                            reconcile_smb(
                                session,
                                storage_client,
                                site_id=UUID(work.payload["data"]["site_id"]),
                                stability_seconds=settings.smb_intake_stability_seconds,
                                scan_interval_seconds=settings.smb_intake_scan_interval_seconds,
                                current_job_id=work.processing_job_id,
                            )
                        except Exception as error:
                            queue.fail(
                                work,
                                worker_id,
                                error_code="smb_reconciliation_failed",
                                message=str(error),
                                retryable=True,
                                base_delay_seconds=settings.worker_retry_base_seconds,
                            )
                            log(
                                "smb_reconciliation_failed",
                                work_id=work_id,
                                detail=str(error)[:2048],
                            )
                            continue
                    elif kind == "processing" and work.job_type == SMB_INGEST_JOB:
                        try:
                            result = ingest_smb(work, ingestion_service, storage_client)
                            enqueue_smb_retirement(session, work, sha256=result.content_hash)
                            record_log(
                                session,
                                service="site-worker",
                                event_type="smb.intake.completed",
                                message="SMB Incoming file entered Presentation Media",
                                site_id=work.site_id,
                                event_id=UUID(work.payload["data"]["event_id"]),
                                media_import_id=result.media_object_id,
                                worker_id=worker_id,
                                context={
                                    "relative_path": work.payload["data"]["relative_path"],
                                    "size_bytes": result.size_bytes,
                                    "source_share": "Incoming",
                                },
                            )
                        except Exception as error:
                            queue.fail(
                                work,
                                worker_id,
                                error_code="smb_intake_failed",
                                message=str(error),
                                retryable=True,
                                base_delay_seconds=settings.worker_retry_base_seconds,
                            )
                            log("smb_intake_failed", work_id=work_id, detail=str(error)[:2048])
                            record_log(
                                session,
                                service="site-worker",
                                severity="error",
                                event_type="smb.intake.failed",
                                message="SMB Incoming intake failed and remains retryable",
                                site_id=work.site_id,
                                event_id=UUID(work.payload["data"]["event_id"]),
                                worker_id=worker_id,
                                context={
                                    "relative_path": work.payload["data"]["relative_path"],
                                    "attempt": work.attempt_count,
                                    "error_type": type(error).__name__,
                                },
                            )
                            continue
                    elif kind == "processing" and work.job_type == SMB_RETIRE_JOB:
                        try:
                            retirement = retire_smb(work, storage_client)
                            record_log(
                                session,
                                service="site-worker",
                                severity=(
                                    "warning" if retirement.get("source_changed") else "info"
                                ),
                                event_type=(
                                    "smb.intake.retirement_skipped"
                                    if retirement.get("source_changed")
                                    else "smb.intake.source_retired"
                                ),
                                message=(
                                    "SMB Incoming source changed and was retained"
                                    if retirement.get("source_changed")
                                    else "SMB Incoming source retired after durable staging"
                                ),
                                site_id=work.site_id,
                                event_id=UUID(work.payload["data"]["event_id"]),
                                worker_id=worker_id,
                                context={"relative_path": work.payload["data"]["relative_path"]},
                            )
                        except Exception as error:
                            queue.fail(
                                work,
                                worker_id,
                                error_code="smb_retirement_failed",
                                message=str(error),
                                retryable=True,
                                base_delay_seconds=settings.worker_retry_base_seconds,
                            )
                            log("smb_retirement_failed", work_id=work_id, detail=str(error)[:2048])
                            continue
                    elif kind == "processing" and work.job_type == SMB_PRESENTATIONS_JOB:
                        try:
                            result = reconcile_smb_presentations(
                                session,
                                storage_client,
                                site_id=work.site_id,
                                current_job_id=work.processing_job_id,
                            )
                            log(
                                "smb_presentations_reconciliation_completed",
                                work_id=work_id,
                                **result,
                            )
                        except Exception as error:
                            queue.fail(
                                work,
                                worker_id,
                                error_code="smb_presentations_materialization_failed",
                                message=str(error),
                                retryable=True,
                                base_delay_seconds=settings.worker_retry_base_seconds,
                            )
                            log(
                                "smb_presentations_materialization_failed",
                                work_id=work_id,
                                detail=str(error)[:2048],
                            )
                            continue
                    elif kind == "processing" and work.job_type == "smb.user.revoke":
                        SmbControlClient(
                            settings.smb_control_url, settings.smb_control_token
                        ).revoke(str(work.payload["data"]["username"]))
                    if not completed:
                        work.status = JobStatus.PENDING
                        work.claimed_by_worker_id = None
                        work.lease_expires_at = None
                        continue
                    queue.complete(work, worker_id)
                    log("job_completed", worker_id=worker_id, job_kind=kind, work_id=work_id)
            if sync:
                try:
                    synchronize_once(factory, settings, worker_id)
                except Exception as error:
                    log(
                        "sync_cycle_failed",
                        worker_id=worker_id,
                        error_type=type(error).__name__,
                        detail=str(error)[:2048],
                    )
            if once:
                break
            stop.wait(settings.worker_poll_interval_seconds)
    finally:
        if transfer_executors is not None:
            transfer_executors.shutdown()
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
