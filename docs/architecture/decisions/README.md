# UPM Architecture Decision Records

Architecture Decision Records (ADRs) capture implementation decisions that refine the [UPM Master Architecture Specification](../UPM_MASTER_ARCHITECTURE.md). The master specification remains authoritative; ADRs provide the detailed rationale, constraints, and consequences for accepted decisions.

| ADR | Status | Decision |
| --- | --- | --- |
| [ADR-0001](ADR-0001-backend-persistence-stack.md) | Accepted | Backend and persistence stack |
| [ADR-0002](ADR-0002-site-media-storage.md) | Accepted | Site-authoritative configurable media storage |
| [ADR-0003](ADR-0003-postgresql-durable-jobs-and-outbox.md) | Accepted | PostgreSQL durable jobs and transactional outbox |
| [ADR-0004](ADR-0004-site-media-ingestion-finalization.md) | Accepted | Site media ingestion and filesystem finalization |
| [ADR-0005](ADR-0005-container-migration-gates.md) | Accepted | One-shot container migration gates |
| [ADR-0006](ADR-0006-central-site-registration-and-sync.md) | Accepted | Central/Site registration and synchronization |
| [ADR-0007](ADR-0007-event-deployment-snapshots.md) | Accepted | Event deployment snapshots |
| [ADR-0008](ADR-0008-shared-react-admin-frontends.md) | Accepted | Shared React admin frontends with separate production images |
| [ADR-0009](ADR-0009-site-room-materialization.md) | Accepted | Site room materialization from deployed program locations |
| [ADR-0010](ADR-0010-lifecycle-deletion-and-retention.md) | Accepted | Operational lifecycle deletion and permanent-person retention |
| [ADR-0011](ADR-0011-resumable-media-transfer-and-replication.md) | Accepted | Site-initiated resumable presentation-media transfer and replication |
| [ADR-0012](ADR-0012-deployment-storage-roots.md) | Accepted | Deployment-local staging and media storage roots |
| [ADR-0013](ADR-0013-deployment-local-media-storage-service.md) | Accepted | Deployment-local Media Storage service boundary |

New decisions must not silently weaken Central/Site separation, Site offline autonomy, PostgreSQL requirements, or other master-architecture constraints.
