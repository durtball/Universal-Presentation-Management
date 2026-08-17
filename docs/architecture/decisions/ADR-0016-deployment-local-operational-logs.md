# ADR-0016: Deployment-local structured operational logs

- **Status:** Accepted
- **Date:** 2026-08-17
- **Scope:** Central and Site operator-visible diagnostics

## Decision

Central and Site each own an independent PostgreSQL operational-log table and query API. Operational
logs record service and workflow outcomes with typed correlation columns and sanitized JSON context.
They are distinct from authorization/change `AuditRecord` history and from container stdout. Site
queries only its local database and therefore remains available without Central or WAN.

Queries are server-filtered, time-bounded, descending, and paginated. Browser live tail uses bounded
polling rather than an unbounded websocket stream. Context is recursively redacted before persistence.
Operational retention defaults to 30 days; audit retention remains a separate policy.

Presentation-media batches and imports correlate lifecycle events through stable UUIDs. Typed columns
index high-value filters; JSON is supporting context, not the only query structure.
