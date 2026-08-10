# Durable Jobs and Outbox Development Guide

UPM Central and Site each run two independent processes against only their own PostgreSQL database: a general worker for ProcessingJob and TransferJob, and a sync worker for OutboxEvent. SyncEvent is domain history; an OutboxEvent is the durable request to transport or process that history. Neither table defines the eventual network protocol.

## Transaction flow

```text
API database transaction
  -> domain state change
  -> optional SyncEvent domain record
  -> OutboxEvent row with the same commit
commit
  -> sync worker SELECT ... FOR UPDATE SKIP LOCKED
  -> publish/process idempotently
  -> mark OutboxEvent succeeded and processed_at
```

Rolling back the API transaction rolls back both the state change and outbox row. Workers provide at-least-once execution: an expired lease can be reclaimed after a crash, so a handler must use the durable job/event identity to prevent duplicate destructive side effects.

## Scheduling and recovery

Eligible `pending` and `retry_wait` rows are ordered by descending priority and then creation order. Required capabilities must be a subset of a worker's advertised capabilities. A claim changes the state to `running`, increments `attempt_count`, assigns `claimed_by_worker_id`, and sets `lease_expires_at` and `heartbeat_at` in the locking transaction.

Heartbeats extend the lease. If a worker exits or a host fails, a later worker can reclaim a running row after its lease expires. Retryable failures enter `retry_wait` with application-calculated exponential backoff and optional jitter. They become `exhausted` at `max_attempts`; non-retryable failures become `failed`. Safe cancellation uses `cancelled`.

Priority values are critical 400, high 300, normal 200, low 100, and optional 0. Event-critical synchronization, processing, and transfers must outrank optional AI/GPU analysis. GPU jobs declare `gpu`; core functionality must not require it.

## Running locally

Apply each migration independently, then start the desired process:

```powershell
$env:UPM_CENTRAL_DATABASE_URL = 'postgresql+psycopg://...central...'
uv run alembic -c database/central/alembic.ini upgrade head
uv run python -m upm_central.worker
uv run python -m upm_central.worker --sync

$env:UPM_SITE_DATABASE_URL = 'postgresql+psycopg://...site...'
uv run alembic -c database/site/alembic.ini upgrade head
uv run python -m upm_site.worker
uv run python -m upm_site.worker --sync
```

Use `--once` for a database/readiness smoke test. Docker Compose runs the same entrypoints and uses a readiness marker created only after database connectivity and queue-loop startup. SIGTERM and SIGINT request graceful shutdown.

## Inspecting development state

Use read-only SQL against the correct database:

```sql
SELECT status, count(*) FROM processing_jobs GROUP BY status ORDER BY status;
SELECT status, count(*) FROM transfer_jobs GROUP BY status ORDER BY status;
SELECT status, count(*) FROM outbox_events GROUP BY status ORDER BY status;
SELECT worker_id, service_role, capabilities, last_heartbeat FROM worker_identities;
SELECT * FROM processing_jobs WHERE status IN ('retry_wait', 'failed', 'exhausted');
```

Never repair queue state with ad hoc production updates. Use deliberate administrative operations when those are implemented. Logs include claims, completions, retries/exhaustion, lease reclaims, outbox processing, worker identity, and graceful shutdown without payloads or secrets.
