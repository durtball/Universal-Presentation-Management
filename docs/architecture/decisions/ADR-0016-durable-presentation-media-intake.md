# ADR-0016: Durable Presentation Media Intake Disposition

- **Status:** Accepted
- **Date:** 2026-08-19
- **Scope:** Central/Site presentation-media Intake, promotion, rejection, and asset roles
- **Supersedes:** ADR-0004 availability finalization and ADR-0015 staging retention where stated

## Context

Temporary transport staging is not an operator work queue. Central retained reviewable uploads in
staging while Site immediately published unassigned uploads as authoritative objects. Neither
model provided a durable, inspectable rejection disposition or supplemental asset roles.

## Decision

Central and Site use the same logical lifecycle:

`staging -> intake -> authoritative | rejected`.

Media Storage owns three content-addressed logical namespaces: `intake/sha256`, `objects/sha256`,
and `rejected/sha256`. Namespace transitions verify SHA-256, publish without replacement, and are
idempotent when replayed after the source name has already been removed. PostgreSQL retains the
same import/media UUID through every transition and is authoritative for operator decisions,
provenance, relationships, and durable jobs.

Confirmation and rejection record the decision before enqueuing retryable disposition work.
Workers perform filesystem operations through the deployment-local Media Storage service and then
commit the resulting storage reference. Central and Site remain separate and Site decisions never
require Central availability.

Presentation assets retain `original` and `derivative`; independently supplied media use explicit
`image`, `video`, `document`, or `other` roles. Only generated derivatives require a source asset.
No match evidence, including folders or exact identifiers, confirms media automatically.

## Consequences

- Pending operator work survives API, worker, and storage-service restarts outside temporary staging.
- Rejected bytes remain retained and auditable rather than being silently deleted.
- Replayed publish, promotion, and rejection operations converge on one content-addressed object.
- Files and Presentation Media views must query the same PostgreSQL-backed disposition state.
- Cleanup may release transport staging but may not remove Intake or rejected objects without an
  explicit retained-media lifecycle policy.
