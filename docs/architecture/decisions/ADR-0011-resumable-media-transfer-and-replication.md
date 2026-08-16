# ADR-0011: Resumable Presentation Media Transfer and Replication

- **Status:** Accepted
- **Date:** 2026-08-16
- **Scope:** Central/Site presentation-media transfer, replication, integrity, and machine authorization

## Context

ADR-0006 establishes that Sites push Site-authoritative events and poll for Central-authoritative
events. Central stores only a verifier for each per-Site credential, while the Site stores the
usable credential encrypted locally. Sites must remain operational behind NAT and through Central
or WAN outages. ADR-0007 deploys complete program snapshots through that same direction. Central
now has durable presentation-media staging and Site has authoritative local ingestion, but the
binary transfer direction, resume contract, and authorization boundary were previously undecided.

## Decision

This ADR extends, and does **not supersede**, ADR-0006. **Sites initiate every network connection to
Central. Central never requires inbound reachability to a Site.** Central-to-Site media uses
Site-initiated pull; Site-to-Central media replication uses Site-initiated push. Both use
authenticated HTTPS and one common offset-based transfer contract.

This direction permits NAT and customer firewalls to remain closed to inbound Central traffic,
requires no public Site API or Central-originated firewall pinhole, keeps hotel/customer networks
simple, preserves the existing polling authority, and ensures Central outages never block local
Site media use. A Central-hosted Site uses the same protocol and receives no filesystem shortcut.

### Central to Site: Site-initiated pull

1. Central stages and hashes media, creates a durable Site-specific transfer manifest/job, and
   announces availability through the existing Central outbox and Site polling path.
2. Site records its own pending transfer and chooses when its durable worker pulls it.
3. Site authenticates to a Central download endpoint, which authorizes the Site, deployment,
   Event, transfer session, and requested media relationship.
4. Site requests bytes beginning at its durable confirmed offset and stores partial data in
   Site-owned private staging.
5. Site verifies complete size and SHA-256, then finalizes through existing Site ingestion and
   associates the result with the manifest's PresentationVersion UUID.
6. Site reports progress, completion, readiness, and failure through its existing outbox/event
   direction. Central does not poll or call the Site.

Central download endpoints expose opaque transfer identity, metadata, and authorized byte ranges,
never filesystem paths. Repeated range reads are safe. Revoked, expired, wrong-Site, undeployed, or
unrelated transfer requests are rejected without revealing unrelated metadata.

### Site to Central: Site-initiated push

Site-local ingestion and readiness complete without Central. Site then queues metadata sync and a
binary replication job. When Central is reachable, Site authenticates and creates or resumes a
Central receive session, queries the durable confirmed offset, sends contiguous bytes from that
offset, and requests finalization. Central owns the partial receive state, verifies full size and
SHA-256, safely publishes its synchronized copy, links it to the same PresentationVersion UUID, and
acknowledges durable completion. Only then does Site mark replication synchronized.

Central receiving endpoints support create/resume, progress query, offset upload, finalization,
idempotent completed replay, and safe abort/expiry. They use the same range and integrity semantics
as Central downloads; a separate incompatible protocol is prohibited.

## Transfer identity and manifest

Every direction and destination has a stable application-generated UUIDv7 transfer-session ID.
The ID and its Site-scoped idempotency key survive API, worker, Central, Site, and network restarts.
A retry resumes the same session unless that session was deliberately expired; expiry may create a
new session referring to the same logical media without changing Presentation or version identity.

A wire manifest contains at minimum:

- transfer-session UUID, origin system, origin Site when applicable, and destination Site when applicable;
- Event, Presentation, and PresentationVersion UUIDs plus operator Presentation Identifier;
- original and canonical filename labels, expected byte size, SHA-256, media type, and creation time;
- transfer and retry state.

Source storage references remain receiver/sender-internal and are never placed on the wire.
Identical media hashes do not merge Presentations or versions. Every destination Site has its own
manifest, session, offset, retry, and completion state.

## Offset and range semantics

The authoritative resume position is `confirmed_offset`: the first byte not yet durably persisted.
For example, `104857600` acknowledges contiguous bytes `0..104857599` and resumes at byte
`104857600`. Senders may use implementation-defined block sizes, but numbered chunks are not
identity or durable resume state.

- Progress is contiguous from byte zero; gaps never advance `confirmed_offset`.
- The receiver reports its highest contiguous durable offset.
- A range beginning at `confirmed_offset` may append data up to expected size.
- Replay wholly below the offset is accepted only after receiver verification, or rejected with a
  deterministic already-persisted response. It never appends duplicate bytes.
- Partial overlap is verified and accepted idempotently or rejected deterministically; it never
  silently changes persisted bytes.
- A range beginning above the offset is rejected as a gap.
- Negative offsets and offsets beyond expected size are invalid.
- Finalization is forbidden until `confirmed_offset == expected_size`.
- Counters and ownership are PostgreSQL durable; process memory is never authoritative.

An acknowledged offset means the receiver has durably persisted the acknowledged range. The exact
fsync/transaction sequence is implementation-specific, but acknowledgement cannot describe bytes
held only in memory or an unflushed buffer that ordinary restart can lose.

## Partial ownership, verification, and finalization

For pull, Site owns the partial file and session state. For push, Central owns them. Partial files
live in private receiver staging, outside final authoritative media, under opaque generated keys;
user filenames never determine paths and publication never replaces an existing object.

Finalization requires complete expected length, independently calculated matching SHA-256, valid
session state, current authorization, and idempotency validation. Hash mismatch persists explicit
failure and never publishes or marks media ready. A successful finalization replay returns the
existing media/completion result and does not create duplicate media, associations, or processing
jobs. SHA-256 is binary-integrity evidence, not logical Presentation identity.

## Authentication and resource authorization

The existing per-Site credential is the machine-authentication foundation; this ADR introduces no
second credential system. Site presents the usable credential over production HTTPS, Central
validates its stored verifier, and credentials never appear in URLs or logs. Authentication proves
Site identity. A separate resource authorization check proves that Site may access the specific
Event deployment and transfer session. A valid credential is never a generic file credential.
Production endpoints permit rate, size, and concurrency limits and do not disclose unrelated
transfer existence on authorization failure. Central never needs the usable Site credential.

Human browser authentication and Site Administrator/Operator/restricted RBAC are a separate future
ADR. Machine authorization here does not make unauthenticated media UI production-safe.

## Jobs, retry, progress, and lifecycle

Transfer work uses existing PostgreSQL TransferJob claim, capability, lease, heartbeat/reclaim,
bounded exponential retry, `retry_wait`, exhaustion, and idempotency conventions. Timeout,
transient HTTP error, process crash, network loss, and restart resume the same session at the
receiver's durable offset. Permanent authentication, authorization, schema, or integrity errors
remain visible and do not retry blindly.

Transfer lifecycle is `queued`, `available`, `transferring`, `retry_wait`, `verifying`, `completed`,
`failed`, `cancelled`, or `expired`. Replication lifecycle is independently `local_only`, `queued`,
`syncing`, `synced`, `retry_wait`, `failed`, or `conflict`. Site persists size, offset, percentage,
last progress time, retry count, and state, then reports progress through Site-authoritative events.

Partial sessions carry activity timestamps and expire only after configurable retention. Cleanup
skips active, leased, and retryable work; logs/audits cleanup; and never deletes finalized media.
No retention duration is fixed by this ADR. Implementations retain extension points for concurrency
limits, bandwidth caps, event priority, and adaptive block size without requiring them initially.

## Readiness and conflict boundary

Site media readiness and replication are separate state machines. `READY + replication pending` or
`READY + replication failed` is valid: READY means locally verified, ingested, version-associated,
and usable. Central availability cannot gate it.

The transfer layer preserves every addressed binary but never decides which concurrent
PresentationVersion is current. Version conflict detection and operator resolution are higher-level
domain behavior. Selecting a current version cannot cause the transfer layer to overwrite or delete
another conflicting binary.

## Failure behavior

- **Central unavailable:** Site remains operational; pulls and pushes wait and resume later.
- **Site unavailable:** Central retains its manifest; there is no inbound retry storm.
- **Network interruption or restart:** worker lease recovery resumes at durable receiver offset.
- **Hash mismatch:** verification fails; partial data is not authoritative or READY.
- **Duplicate request/finalization:** receiver returns verified existing progress or completion.
- **Expired partial:** policy may create a replacement session for the same logical media.

## Architectural invariants

1. Sites initiate network communication with Central.
2. Central does not require inbound reachability to Sites.
3. Central-to-Site media is Site-pull.
4. Site-to-Central media is Site-push.
5. Transfers resume by durable contiguous byte offset/range.
6. Stable UUIDv7 transfer-session identity survives restart.
7. Full-file SHA-256 and expected size verify completion.
8. The existing per-Site credential is the machine-authentication foundation.
9. Authentication and scoped resource authorization are separate checks.
10. Partial files are never authoritative media.
11. Finalization is idempotent.
12. Replayed transfer operations are safe and deterministic.
13. Each Site owns independent transfer state.
14. Site local readiness never depends on Central.
15. Media replication state remains separate from local media state.
16. PresentationVersion conflict resolution is above the transfer layer.
17. No direct Central/Site PostgreSQL connection is introduced.
18. No required shared filesystem is introduced.
19. No second Site machine credential system is introduced.
20. Human Site RBAC remains a separate ADR.

## Consequences

Sites can operate behind NAT without public ingress while Central can coordinate multiple
independent delivery states. Receivers must durably coordinate filesystem and PostgreSQL progress,
and implementation must add authenticated range endpoints, partial reconciliation, cleanup,
progress events, and worker handlers. This ADR fixes those semantics but does not claim that binary
transfer execution, UI, human RBAC, or conflict resolution is implemented.
