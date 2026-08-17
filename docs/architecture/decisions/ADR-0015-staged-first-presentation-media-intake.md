# ADR-0015: Staged-first presentation media intake

- **Status:** Accepted
- **Date:** 2026-08-17
- **Scope:** Central browser presentation-media intake, analysis, and canonical promotion

## Context

Large concurrent browser uploads previously coupled authentication activity writes, request-body
streaming, canonical media publication, Event-wide candidate matching, and assignment work. A
shared browser session caused concurrent requests to contend on `admin_sessions.last_seen_at`, while
long request transactions retained scarce PostgreSQL connections. Canonical publication before
operator confirmation also contradicted ADR-0014's operator-authoritative assignment boundary.

## Decision

Central presentation-media intake is staged first. Authentication validation uses a short read-only
session; throttled activity metadata uses a separate best-effort transaction. Intake creates its
record in a short transaction, streams through the deployment-local Media Storage service with no
database connection checked out, then atomically records durable staging metadata and enqueues an
idempotent PostgreSQL analysis job in a second short transaction.

Analysis reads only staged metadata and produces `suggested` or `needs_review`. It never calls
canonical storage commit, creates a PresentationVersion, or confirms an assignment. Exact matches
remain suggestions under ADR-0014.

Explicit operator confirmation enqueues a bounded durable promotion job. That worker calls the
deployment-local Media Storage service to idempotently promote the staged object while holding no
database transaction. Only after complete canonical publication does a short transaction store the
canonical reference, create the PresentationVersion, confirm the assignment, audit it, and expose
normal transfer/synchronization work.

## Consequences

- Browser transfer slots refill after durable staging rather than after matching or promotion.
- Staged media and analysis jobs survive browser, API, and worker restarts.
- Bulk confirmation creates durable bounded work rather than simultaneous storage operations.
- PostgreSQL connections and `admin_sessions` row locks are not held while request or storage I/O
  occurs.
- Recovery may encounter an already-promoted object before its database transaction committed;
  Media Storage commit and the promotion handler must therefore remain idempotent.
