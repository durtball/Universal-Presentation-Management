# ADR-0010: Operational lifecycle deletion and permanent-person retention

- **Status:** Accepted
- **Date:** 2026-08-14
- **Scope:** Central Event deletion, permanent Person deletion, and Site purge propagation

## Context

Archive, revocation, operational deletion, and permanent identity erasure have different retention
requirements. Events own active programs and deployment projections, while permanent Person UUIDs
and deliberately retained history span Events. Sites can be offline, and rooms and media can be shared.

## Decision

Central records a durable deletion operation and processing job after an administrator reviews an
impact graph and types the exact display name. A worker performs ordered transactional cleanup;
foreign-key cascades are not the lifecycle mechanism. Target-independent audit and operation rows
survive removal and expose stages, retries, errors, media results, and per-Site state.

Before Event operational rows are removed, participation is copied into person-owned retained
history containing minimal stable facts. Its source Event UUID is a value, not a foreign key. Event
deletion never deletes a Person. Permanent Person deletion separately removes operational
relationships, identity signals/links, and retained history.

Event purge messages use the existing protocol-v1 per-Site durable outbox and carry a tombstone
without an Event foreign key. They remain pending during outages. Site application explicitly
removes the projection and acknowledges through the existing receipt/checkpoint path. Reusable
rooms and shared media survive reference checks; only explicitly Event-owned rooms and unreferenced
media are eligible.

Archive retains Event data. ADR-0007 revocation retains the Site projection. Neither is deletion.

## Consequences

- Event cleanup cannot erase permanent identity or deliberately retained history.
- Offline Sites converge through the established transport.
- Filesystem cleanup may outlive relational cleanup and remains visible and retryable.
- Future person-owned content must follow the same explicit ownership/reference rules.
