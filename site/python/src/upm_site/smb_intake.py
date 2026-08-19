"""Durable offline Site SMB Incoming reconciliation into canonical Site ingestion."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from upm_shared.enums import JobStatus, MediaCategory
from upm_shared.identifiers import new_uuid7
from upm_shared.media_storage_client import MediaStorageClient
from upm_shared.smb_intake import event_and_filename, incoming_identity, intake_candidate
from upm_site.media.ingestion import IngestionRequest
from upm_site.operational_logs import record_log
from upm_site.persistence.models import Event, ProcessingJob, utc_now
from upm_site.persistence.queue import SiteQueue

SCAN_JOB = "smb.incoming.reconcile"
INGEST_JOB = "smb.incoming.ingest"
ACTIVE = (JobStatus.PENDING, JobStatus.RETRY_WAIT, JobStatus.RUNNING)


def enqueue_reconciliation(session: Session, site_id: UUID, *, delay_seconds: float = 0):
    if session.scalar(
        select(ProcessingJob).where(
            ProcessingJob.site_id == site_id,
            ProcessingJob.job_type == SCAN_JOB,
            ProcessingJob.status.in_(ACTIVE),
        )
    ):
        return None
    return SiteQueue(session).enqueue_processing(
        site_id=site_id,
        job_type=SCAN_JOB,
        payload={"data": {"site_id": str(site_id)}},
        idempotency_key=f"smb-scan:{new_uuid7()}",
        required_capabilities=["cpu"],
        max_attempts=10,
        next_attempt_at=utc_now() + timedelta(seconds=delay_seconds),
    )


def reconcile(
    session: Session,
    storage: MediaStorageClient,
    *,
    site_id: UUID,
    stability_seconds: float,
    scan_interval_seconds: float,
    current_job_id: UUID | None = None,
) -> int:
    queued = 0
    now_ns = int(utc_now().timestamp() * 1_000_000_000)
    active_events = session.scalars(
        select(Event)
        .where(Event.site_id == site_id, Event.archived_at.is_(None))
        .order_by(Event.created_at.desc())
        .limit(2)
    ).all()
    for item in storage.list_smb_incoming():
        relative = str(item["relative_path"])
        if not intake_candidate(relative):
            continue
        if "/" not in relative and len(active_events) == 1:
            event_id, filename = active_events[0].event_id, relative
        else:
            try:
                event_id, filename = event_and_filename(relative)
            except (ValueError, IndexError):
                continue
        if session.get(Event, event_id) is None:
            continue
        size, modified = int(item["size_bytes"]), int(item["modified_ns"])
        if now_ns - modified < int(stability_seconds * 1_000_000_000):
            continue
        identity = incoming_identity(relative, size, modified)
        if session.scalar(
            select(ProcessingJob).where(
                ProcessingJob.site_id == site_id,
                ProcessingJob.job_type == INGEST_JOB,
                ProcessingJob.idempotency_key == identity,
            )
        ):
            continue
        SiteQueue(session).enqueue_processing(
            site_id=site_id,
            job_type=INGEST_JOB,
            payload={
                "data": {
                    "site_id": str(site_id),
                    "event_id": str(event_id),
                    "relative_path": relative,
                    "original_filename": filename,
                    "size_bytes": size,
                    "modified_ns": modified,
                    "source_share": "Incoming",
                }
            },
            idempotency_key=identity,
            required_capabilities=["cpu"],
            max_attempts=8,
            next_attempt_at=utc_now() + timedelta(seconds=stability_seconds),
        )
        record_log(
            session,
            service="site-worker",
            event_type="smb.intake.queued",
            message="SMB Incoming file queued after first stable observation",
            site_id=site_id,
            event_id=event_id,
            context={"filename": filename, "size_bytes": size, "source_share": "Incoming"},
        )
        queued += 1
    other_scan = session.scalar(
        select(ProcessingJob).where(
            ProcessingJob.site_id == site_id,
            ProcessingJob.job_type == SCAN_JOB,
            ProcessingJob.status.in_(ACTIVE),
            ProcessingJob.processing_job_id != current_job_id,
        )
    )
    if other_scan is None:
        SiteQueue(session).enqueue_processing(
            site_id=site_id,
            job_type=SCAN_JOB,
            payload={"data": {"site_id": str(site_id)}},
            idempotency_key=f"smb-scan:{new_uuid7()}",
            required_capabilities=["cpu"],
            max_attempts=10,
            next_attempt_at=utc_now() + timedelta(seconds=scan_interval_seconds),
        )
    return queued


def ingest(work, ingestion_service, storage: MediaStorageClient, *, chunk_bytes: int = 4_194_304):
    data = work.payload["data"]
    relative, size, modified = (
        data["relative_path"],
        int(data["size_bytes"]),
        int(data["modified_ns"]),
    )
    current = next(
        (item for item in storage.list_smb_incoming() if item["relative_path"] == relative), None
    )
    if current is None:
        raise FileNotFoundError("SMB Incoming source is no longer present")
    if int(current["size_bytes"]) != size or int(current["modified_ns"]) != modified:
        raise RuntimeError("SMB Incoming source changed before its second stability observation")

    async def chunks():
        offset = 0
        while offset < size:
            block = storage.read_smb_incoming(relative, offset, min(chunk_bytes, size - offset))
            if not block:
                raise RuntimeError("SMB Incoming source ended before its recorded size")
            offset += len(block)
            yield block

    result = asyncio.run(
        ingestion_service.ingest_async(
            IngestionRequest(
                site_id=UUID(data["site_id"]),
                event_id=UUID(data["event_id"]),
                original_filename=data["original_filename"],
                category=MediaCategory.OPEN_FILE,
                expected_size=size,
                idempotency_key=work.idempotency_key,
                source_relative_path=relative,
                replicate_to_central=True,
                intake_origin="smb",
                source_actor="smb",
                source_share="Incoming",
            ),
            chunks(),
        )
    )
    storage.complete_smb_incoming(relative, size_bytes=size, modified_ns=modified)
    return result
