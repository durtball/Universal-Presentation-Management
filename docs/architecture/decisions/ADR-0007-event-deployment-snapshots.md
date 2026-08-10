# ADR-0007: Versioned Event Deployment Snapshots

- **Status:** Accepted
- **Date:** 2026-08-10
- **Scope:** Central-to-Site event/show deployment and Site operational acknowledgement

## Context

Central owns global event definitions and assignment intent while every Site must operate from its
own PostgreSQL database during complete WAN loss. The existing protocol-v1 synchronization transport
already provides authenticated per-Site polling, monotonic sequences, receipts, cursors, leases,
bounded retry, and transactional outboxes. Event deployment must extend that transport rather than
create row replication or a parallel API.

## Decision

Central stores one durable deployment per Event + Site and an immutable revision record for every
deployment action. A deployment change, its complete schema-v1 snapshot (or revocation contract),
and its outbound event are committed in one Central transaction.

The lifecycle is explicit:

```text
draft -> pending -> deploying -> deployed -> update_pending
   |         |          |            |              |
   +-------> archived    +----------> failed <-------+
              ^                    /   |
              +------ revoked <---+    +-> pending/update_pending (retry)
```

The service rejects transitions outside the declared transition map. Initial deployment creates
revision 1. Every push after deployable Central data changes creates the next monotonic revision for
that Event + Site. Revisions for different Sites are independent.

Snapshot schema v1 contains stable UUID references and the implemented Event timezone/metadata,
event-scoped permanent-person profiles, event participation, session and ordered presenter
associations, logical presentations, explicit presentation-session/presenter associations,
presentation workflow/processing state, presentation-version metadata, and relevant external
identifiers. Configuration sections for organization, rooms, signage, branding, workflow, and
future extensions remain defined for their authoritative domains. Presentation binaries are never
embedded and unrelated Central identity history is never sent to a Site.

Snapshots are complete authoritative deployment contracts. Therefore a Site that applied revision
1 may safely apply revision 4 without first receiving revisions 2 and 3. Equal revisions are
idempotent and acknowledged again. Lower revisions are recorded as stale and never roll local state
back. Application and its Site status outbox event share a Site transaction. Unsupported schema or
malformed application is persisted/reported as a failure where deployment identity is recoverable,
while the transport receipt advances so one poison event cannot permanently block later sequences.

Revocation is a state change, not deletion. The Site retains its event projection, media references,
deployment snapshots, audit/history, and operational records until a future explicit retention
workflow removes eligible data.

Within the Site relational projection, rows omitted by a newer complete snapshot are deactivated
before present rows are upserted/reactivated. This converges operational program state without
deleting Site-owned media or historical/operational records.

## Consequences

- Central can show desired and Site-applied revisions without reading Site PostgreSQL.
- Sites converge after long disconnection and remain operational from their last valid snapshot.
- Permanent Central person UUIDs remain distinct from event participation and session relationships.
- Snapshot payload size grows with event metadata; large media uses the separate transfer architecture.
- Future incompatible contracts require a new explicit schema version and handler.
