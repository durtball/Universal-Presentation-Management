# Event Program and Identity Domain

UPM Central owns the permanent person and event-program model. UPM Sites receive event-scoped,
Site-local relational projections only through the complete revision snapshots defined by ADR-0007.
No row replication or domain-specific synchronization channel exists.

## Identity and participation

`Person` is a durable identity across events. It stores structured and display names, normalized
matching fields, contact/professional attributes, active/deleted state, external identifiers, and
revision metadata. `EventParticipation` stores event-specific title, organization, display name,
status, presenter flag, notes, and provenance. The `(event_id, person_id)` constraint permits one
person to participate in many events without duplicating permanent identity.

Names are never authoritative matching keys. Reconciliation evaluates, in order, an explicitly
supplied UPM person UUID, unique external identifiers, normalized email, and name-only candidate
evidence. Name-only evidence always requires review. Protected Person deletion requires exact-name
confirmation and queues durable cleanup of operational relationships and person-owned retained
history. Event deletion instead removes active program/deployment data after preserving intentional
participation facts under the permanent Person UUID. Archive retains Event data, and ADR-0007
revocation retains the Site projection. Target-independent audit evidence survives deletion.

## Program relationships and workflow

Sessions belong to events and persist timezone-aware instants. The Event's required IANA timezone
controls event-local display and travels in every deployment snapshot. A database constraint rejects
a session end at or before its start. Session presenters use independently identified association
rows with role, order, primary flag, provenance, and notes.

Presentations are logical work items, not files. Human workflow status and operational processing
status are separate enums. A preferred session remains for common queries and backward
compatibility, while `PresentationSession` is the authoritative extensible association model.
`PresentationPresenter` links directly to event participation, so presentation presenters are not
incorrectly inferred from the entire session roster. Presentation versions/assets remain separate.

`ExternalIdentifier` supports global and event-scoped namespaces without vendor-specific columns.
It protects both a source identifier from mapping to multiple entities in one scope and an entity
from receiving conflicting mappings in one namespace/scope.

## Import and reconciliation workflow

CSV and XLSX imports use durable stages:

1. `ImportSource` preserves bytes, filename, media type, size, SHA-256, actor, and source type.
2. `ImportBatch` records target event, lifecycle, review revision, counts, failure, and commit state.
3. `ImportRow` preserves raw, normalized, and separately corrected values plus proposed actions,
   identity evidence, candidates, confidence, resolution, and committed entity UUIDs.
4. `ImportValidationIssue` records field-level warning or blocking-error reasons.
5. `ReconciliationDecision` records actor, decision, selected identity/corrections, reason, and time
   without changing the original raw row.
6. Transactional commit creates or links normalized records, writes audit events, and advances active
   event deployments once after the batch.

The same event plus source SHA-256 and importer type identifies a duplicate upload. A commit retry
returns the committed batch. Review records the event revision; commit refuses stale assumptions
after program changes. Matching-person revisions are also checked before commit.

Spreadsheet content is untrusted. Only `.csv` and macro-free `.xlsx` parsing is enabled; formulas
are read as cached values and never executed, filenames are not paths, and uploads are size limited.

## ADR-0007 Site projection

Snapshot schema v1 contains event description/timezone, event-scoped people and participation,
sessions and presenter associations, presentations and explicit session/presenter associations,
workflow/processing state, and relevant external identifiers. It never includes unrelated Central
identity history or presentation binaries.

Site application is transactional and revision-aware. Event program rows carry an `active` flag.
Before applying a newer complete snapshot, existing Central-owned rows for that event are made
inactive, then every row present in the snapshot is upserted and activated. This removes stale
relationships from operational use without deleting Site-local media, operational state, deployment
history, or audit evidence. Equal revisions remain idempotent; stale revisions cannot roll back data.

## Administration

Central exposes versioned admin APIs under `/api/v1/admin` for people, deletion impact, event
participants, sessions and presenters, presentations and explicit relationships, external
identifiers, staged imports, reconciliation decisions, and commit. Important updates require an
expected entity revision and return `409` on concurrent modification.

Browser administration is available at Central `/admin/program` and `/admin/people`, alongside the
existing Sites and Events pages. Site exposes its autonomous read-only projection at
`/admin/program` and `/api/v1/events/{event_id}/program`. All Central writes retain the existing
administrator authentication boundary.
