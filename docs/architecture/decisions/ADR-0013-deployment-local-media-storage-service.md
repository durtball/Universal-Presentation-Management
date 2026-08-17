# ADR-0013: Deployment-local Media Storage service boundary

- **Status:** Accepted
- **Date:** 2026-08-16
- **Scope:** Central and Site storage mechanics, mounted targets, and application file access
- **Supersedes:** ADR-0012's direct application-container filesystem access; refines ADR-0002 and ADR-0004

## Context

Central and Site application processes directly probed and manipulated configured container paths.
That made a path meaningful only inside an API/worker container, duplicated filesystem behavior,
and allowed a missing mount to escape as a Storage-page HTTP 500 or the non-actionable message
`Media staging storage is unavailable.` Site's primary target also doubled as hidden staging and
could not be selected meaningfully in the shared administration UI.

## Decision

UPM Central and every UPM Site each run one deployment-local Media Storage service. Central and
Site use the same image but separate service processes, configuration, state, networks, and mounted
storage. No global service serves multiple Sites, and Site storage never depends on Central.

The service owns filesystem-level target probing, capacity, private staging, immutable
content-addressed object publication, checksum verification, key resolution, copying, and migration
mechanics. Application databases remain authoritative for Presentations, versions, MediaObjects,
imports, transfers, lifecycle decisions, durable jobs, and audit. Higher-level code addresses bytes
by `storage_target_id` and validated relative `storage_key`, never by a host path.

Only explicitly configured roots mounted into the service are targets. The API rejects absolute and
traversing keys. Its private API requires a deployment-scoped bearer credential and is attached only
to the deployment's internal Docker network; Caddy does not publish it. `/health` alone is available
for container health checks.

Staging and media assignments are independent and persisted on a service state volume. A staged
record retains both target UUID and key. Committed originals use
`objects/sha256/<prefix>/<sha256>`. A target change first performs a real probe. Main-media cutover
with existing objects is completed only by a database-owned durable migration job that copies and
verifies every object before atomically changing the assignment; old bytes are retained until an
explicit lifecycle cleanup.

Default Compose deployments mount distinct named volumes at `/storage/temp`, `/storage/media`, and
`/state`, so image replacement cannot delete media and a fresh development deployment is healthy.
Production operators may replace or add only explicit bind mounts and matching JSON target
configuration.

## Failure behavior

Application proxy endpoints convert service connection failures to a structured `Unavailable`
state so Storage pages remain usable. Mount, permission, read-only, probe, and capacity failures are
reported as target health and concise operator messages; detailed filesystem exceptions stay in
service logs.

## Consequences

- API and worker images no longer define the storage-management boundary.
- Central and Site remain independently restartable and offline-capable.
- Storage-service state is operational configuration, not a competing media-domain database.
- Replication senders read committed objects through the local service and receivers allocate local
  service staging before verification and commit.
- Cross-filesystem publication is a verified copy rather than an unsafe rename.

## Ingestion integration

Central and Site API/worker processes stream presentation bytes through their local service. A
durable staged record retains target UUID and key; accepted and unmatched files are checksum-verified
into the active media assignment. Transfer senders request bounded object ranges, and receivers use
durable append offsets in service staging before commit. Application containers do not mount a
parallel `/data/staging` or `/data/objects` presentation store.
