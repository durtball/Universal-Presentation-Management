# ADR-0014: Operator-authoritative presentation media matching

- **Status:** Accepted
- **Date:** 2026-08-17
- **Scope:** Central and Site presentation-media candidate discovery and assignment

## Context

Filename and program metadata can efficiently identify likely Presentation targets, but filenames are labels rather than identity. Incorrect automatic assignment is operationally dangerous, especially when external identifiers are reused across Events or presenter names are ambiguous.

## Decision

Presentation media matching is suggestion-driven and operator-authoritative. UPM may automatically discover and deterministically rank Event-scoped candidate Presentation matches, but it may not create a PresentationVersion, transfer assignment, or confirmed media association without explicit operator confirmation. Exact identifiers remain suggestions. Operators may individually confirm or bulk-confirm only an explicitly selected set.

Valid uploaded media is preserved when evidence is absent, weak, ambiguous, or conflicting. Those outcomes are review states rather than ingest failures. Candidate explanations are retained as non-authoritative evidence; the confirmed Presentation UUID and PresentationVersion relationship remain relational authority. Re-running matching updates only unresolved suggestions and never changes a confirmed assignment.

Central and Site use the same persistence-free matching rules. Each deployment confirms against its local Event program projection and database, so Site confirmation remains available during WAN or Central outages. Confirmed metadata crosses the established snapshot/outbox and media-transfer boundaries rather than introducing direct database access or a new synchronization channel.

An imported program item that can receive presentation media must materialize an Event-scoped
canonical Presentation linked to its Session and presenters. Import and idempotent repair establish
this invariant before candidate matching; matching never substitutes a Session or Person for the
authoritative Presentation target.

## Consequences

- Operators can review high-confidence suggestions efficiently without silent assignment.
- Confirmation endpoints must validate Event ownership and be idempotent.
- Bulk confirmation returns per-item results so stale records do not roll back unrelated confirmations.
- Candidate search must use the Event program domain, not media rows.
