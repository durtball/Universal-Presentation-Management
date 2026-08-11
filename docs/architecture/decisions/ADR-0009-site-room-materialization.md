# ADR-0009: Site room materialization from deployed program locations

- **Status:** Accepted
- **Date:** 2026-08-11
- **Scope:** Central-to-Site program deployment room reconciliation

## Context

Imported session location labels are program data, not identity. Requiring an operator to create and
map every Site room before a deployed program becomes operational is repetitive and leaves otherwise
complete deployments unresolved. At the same time, automatic reconciliation must not merge ambiguous
rooms, replace an existing room UUID, or undo a Site operator's explicit mapping or unmap.

## Decision

When a Site applies a complete event deployment snapshot, each active imported location which is not
already covered by a Central mapped-room instruction or a Site-owned program-room mapping is
materialized locally. The Site first seeks a deterministic room match within its own room catalog:
an exact label match is preferred; otherwise exactly one normalized-label match may be reused. The
normalization trims, case-folds, and collapses whitespace. Multiple normalized candidates are
ambiguous and remain unresolved rather than being silently merged.

If no candidate exists, Site creates a new `Room` with a new application-generated UUIDv7, then
creates an event-scoped `ProgramRoomMapping` from the normalized imported label to that UUID. A label
is match evidence and a default display value, never the room's identity. Existing rooms retain their
UUIDs, and Central-provided target UUIDs are never recreated locally when absent.

Existing Site mappings always win, including a mapping whose room is null because an operator
explicitly unmapped the label. Later complete snapshots therefore do not reverse manual remapping or
unmapping. Central mappings continue through the existing snapshot contract and are excluded from
automatic local materialization.

## Consequences

- Newly deployed unmapped program locations become usable Site rooms without a separate setup pass.
- Repeated or newer snapshots are idempotent with respect to room and mapping identity.
- Safe label matches reuse existing Site UUID identity; ambiguous matches remain visible for manual
  resolution.
- Site remains authoritative and can remap or unmap any automatically created mapping.
- Room creation and mapping occur in the same transaction as snapshot application and status outbox
  creation, preserving retry and rollback behavior.
