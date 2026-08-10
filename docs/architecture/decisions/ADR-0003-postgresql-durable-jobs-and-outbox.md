# ADR-0003: PostgreSQL Durable Jobs and Transactional Outbox

- **Status:** Accepted
- **Date:** 2026-08-10
- **Scope:** Central and Site background execution and event publication

## Context

Every UPM Site must continue operating through WAN and Central outages, and every Central and Site deployment already owns an independent PostgreSQL database. Jobs and synchronization events must survive worker, process, and host restarts without adding an event-day dependency whose failure can halt local operations.

## Decision

PostgreSQL is the authoritative durable store for ProcessingJob, TransferJob, SyncEvent, and transactional OutboxEvent records. Redis, RabbitMQ, Kafka, NATS, Celery broker infrastructure, and other external brokers are not required for the core job path.

ProcessingJob and TransferJob remain separate domain tables. SyncEvent is a durable synchronization-domain record. When transport or downstream processing is required, the application writes an OutboxEvent in the same transaction as the domain change (and SyncEvent where applicable). An outbox worker later performs at-least-once delivery or processing. Outbox persistence is not the wire protocol.

Central and Site each own their models, SQLAlchemy metadata, sessions, migrations, database, and workers. A Site worker never connects to Central PostgreSQL and a Central worker never connects to Site PostgreSQL.

## Claiming and recovery

A worker claims one eligible row in a database transaction using `SELECT ... FOR UPDATE SKIP LOCKED`. The transaction changes it to `running`, records the worker identity, increments the attempt count, and establishes a lease. Concurrent workers skip locked rows instead of blocking or double-claiming them.

Long-running handlers extend `heartbeat_at` and `lease_expires_at`. A running row with an expired lease is eligible for transactional reclaim. Handlers must therefore be idempotent: a crash can occur after a side effect but before completion is committed. The queue enforces enqueue uniqueness through domain-scoped idempotency keys; handlers and downstream consumers enforce side-effect idempotency using the same stable operation/event identity.

Workers publish stable runtime identity records containing role, hostname, capabilities, startup time, and last heartbeat. Container readiness uses a worker-owned liveness marker written only after the database is reachable and the queue loop has started. SIGTERM/SIGINT stop polling cleanly after the current transaction.

## Lifecycle and retries

The job lifecycle is:

```text
pending -> running -> succeeded
                 \-> retry_wait -> running
                 \-> failed       (terminal, non-retryable)
                 \-> exhausted    (retryable but max attempts reached)
pending/retry_wait -> cancelled    (when cancellation is safe)
```

Failures retain a bounded error code, concise message, retryable decision, and optional structured metadata. Full logs stay in the logging system. Retry scheduling is application logic: `min(base_delay * 2^(attempt-1), maximum_delay)` with optional bounded jitter. Database triggers do not calculate delays.

## Priority and capabilities

Jobs use a simple numeric priority corresponding to critical operational, high, normal, low, and optional/background work. Higher values claim first, followed by creation order. Workers advertise capabilities such as `cpu`, `gpu`, `pdf-conversion`, `media-analysis`, `transfer`, and `sync`; a job is eligible only when its required capability set is a subset of the worker set.

GPU is optional. Core synchronization, presentation processing, room-readiness transfer, and service-health maintenance outrank AI analysis, media enrichment, and archival analysis. Required core operation must have a CPU-capable path and must not wait for a GPU worker.

## Consequences

- Job/outbox state and retry timing survive restarts.
- Domain state and an event requiring publication commit atomically, eliminating the commit-then-publish lost-event window.
- Delivery and execution are at least once, so idempotent handlers are mandatory.
- PostgreSQL row locking provides sufficient initial coordination without another service to deploy, monitor, secure, and recover at every Site.
- Queue depth and high write volume must be observed; terminal records require an explicit future retention policy.

## Future broker escape hatch

A specialized broker may be added only when measured workload, latency, fan-out, or integration requirements demonstrate that PostgreSQL cannot meet a concrete need and the architecture is deliberately amended. PostgreSQL remains the authoritative job/event state unless that decision explicitly changes. A broker must not weaken Site autonomy or merge Central and Site persistence.
