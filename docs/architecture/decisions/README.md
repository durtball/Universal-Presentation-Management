# UPM Architecture Decision Records

Architecture Decision Records (ADRs) capture implementation decisions that refine the [UPM Master Architecture Specification](../UPM_MASTER_ARCHITECTURE.md). The master specification remains authoritative; ADRs provide the detailed rationale, constraints, and consequences for accepted decisions.

| ADR | Status | Decision |
| --- | --- | --- |
| [ADR-0001](ADR-0001-backend-persistence-stack.md) | Accepted | Backend and persistence stack |
| [ADR-0002](ADR-0002-site-media-storage.md) | Accepted | Site-authoritative configurable media storage |
| [ADR-0003](ADR-0003-postgresql-durable-jobs-and-outbox.md) | Accepted | PostgreSQL durable jobs and transactional outbox |

New decisions must not silently weaken Central/Site separation, Site offline autonomy, PostgreSQL requirements, or other master-architecture constraints.
