# ADR-0019: Site-Originated Events and Complete Recovery Snapshots

- **Status:** Accepted
- **Date:** 2026-08-28
- **Scope:** Site-local Event authority, program import, Central synchronization, and replacement recovery

## Context

ADR-0007 defined complete Central-to-Site Event deployment snapshots and Site offline operation,
but did not define how a show created while Central is unavailable becomes recoverable. Per-entity
replication would create a second program channel, permit partially reconstructed Events, and
conflict with the complete-snapshot rule. Site already has an ordered authenticated outbox and the
ADR-0011 Site-initiated resumable media push.

## Decision

A Site may create an Event and commit CSV/XLSX program data in its own PostgreSQL transaction. The
Site allocates UUID identities for the Event graph, retains the original import and row-to-entity
commit evidence, and immediately operates from that graph. Every meaningful schedule row creates
one Presentation Entry unless an explicit source presentation identifier groups rows.

Site-originated program propagation extends ADR-0007: after a local program transaction the Site
enqueues one versioned **complete recovery snapshot** through the existing ordered Site outbox.
Central authenticates the origin Site, rejects foreign/Central-owned identity replacement, applies
the complete graph idempotently by UUID, records the latest recovery payload, and creates the
ordinary Event+Site deployment relationship required by existing media replication. It does not
accept ad-hoc database access or an unordered row-replication channel.

The recovery payload includes Event, people/participation, Sessions, Presentation Entries,
relationships, version metadata, physical-room reconstruction inputs, and Site override evidence.
Presentation bytes remain outside the snapshot and use ADR-0011 Site-initiated HTTPS push with
durable offsets, size, and SHA-256 verification. A replacement Site is restored through the normal
deployment snapshot/media-manifest mechanisms. Central acknowledgement marks only metadata sync;
binary backup and room endpoint cache remain separate observable states.

Staged presentation intake remains non-authoritative until operator confirmation. Confirmation
creates immutable version/asset relationships and a durable promotion job. The managed-media
database record is authoritative; the existing SMB materializer runs only after authoritative
promotion and remains an optional human-readable reference, never Central transport or Site
Manager's managed transfer path.

## Consequences

- Site operation and import do not require WAN availability.
- Central receives an atomic, reconstructable Event graph rather than partial row events.
- UUID/source evidence and monotonic snapshot revision make retry idempotent.
- Identity conflicts stop synchronization for operator reconciliation rather than overwriting.
- Site-local metadata sync, Central media backup, SMB representation, and Agent cache/deployment
  remain distinct states.
