# UPM Implementation Status

**Evidence snapshot:** `codex/site-rooms-operational-workflow` on 2026-08-10. This document reports current engineering evidence. It does not redefine the [target architecture](../architecture/UPM_MASTER_ARCHITECTURE.md).

## Completed foundations and working milestones

### Architecture and repository foundation

- Monorepo boundaries exist for Central, Site, shared contracts, Windows clients, databases, infrastructure, tests, scripts, and documentation.
- Central and Site are independent Python 3.13/FastAPI applications with independent SQLAlchemy metadata, configuration, packages, Docker images, networks, and Compose stacks.
- The shared React/TypeScript source builds separate Central and Site production images. Caddy routes local health/API/web traffic; each frontend calls only its local API boundary.
- Accepted ADR-0001 through ADR-0008 record backend, storage, jobs, ingestion, migrations, sync, deployment snapshots, and frontend decisions.

### PostgreSQL and migrations

- Central and Site use separate PostgreSQL-native models and Alembic histories; no SQLite fallback exists.
- UUIDv7 generation and language-neutral Pydantic contracts are shared without sharing ORM metadata or sessions.
- One-shot, version-matched migration services gate APIs, workers, and sync services independently in Compose.
- Repository tests verify database/version-table separation, local foreign keys, migration packaging, and deployment gate ordering.

### Durable jobs and outbox

- Central and Site persist separate ProcessingJob, TransferJob, SyncEvent, OutboxEvent, and WorkerIdentity records.
- Queue primitives implement `FOR UPDATE SKIP LOCKED` claims, leases, heartbeat/reclaim, priority, capabilities, idempotency, retry scheduling, exhaustion, cancellation state, and worker identity.
- PostgreSQL integration tests cover concurrent claims, recovery, retry, priority, capabilities, queue isolation, transfer-job distinction, and transactional outbox behavior.
- This milestone implements durable infrastructure; many future domain handlers and an operator job console remain absent.

### Site media ingestion and storage

- Site owns configurable StorageTarget and MediaObject records using safe logical object keys rather than file paths as identity.
- Upload streams into same-target staging while calculating SHA-256, validates size/capacity, publishes without replacement, and commits availability plus an inspection job.
- Original metadata is preserved; equal filenames/hashes do not collapse identity; open-file and presentation-linked ingestion remain distinct.
- Cleanup and reconciliation cover interrupted upload and publication/database failure windows. APIs expose metadata, status, and storage-target health.
- The Site UI lists target capacity/health and a read-only managed-media catalog with presentation/version links, size/type, availability, ingestion, processing, and checksum state. It does not expose filesystem-browser or mutation actions.
- Presentation records now carry a stable operator-facing identifier and its imported/generated
  provenance. Central program creation/import allocates identifiers, ADR-0007 snapshots transport
  them unchanged, and a disconnected Site can allocate UUID-derived `UPM-{origin}-{token}`
  identifiers and version UUIDs without contacting Central.
- Central and Site share deterministic, event-timezone-aware canonical filename, matching, and
  operational-sort primitives. Site-linked ingestion persists both original and canonical names;
  Site APIs expose expected/missing media, search, deterministic match preview, local presentation
  creation, and serialized local version allocation.
- Central now has an authenticated streaming upload API backed by a persistent media-import row and
  an explicitly mounted staging volume. It hashes while streaming, persists exact/ambiguous/
  unmatched evidence, supports audited manual reassignment, allocates versions transactionally,
  and queues the existing durable TransferJob with an idempotency key. Transfer execution and
  reverse replication are not yet implemented, so queued media does not yet reach a Site.

### Central/Site registration and synchronization

- Site bootstrap creates one permanent Site UUID. Enrollment uses hashed claim/poll material, administrator approval, revocable per-Site credentials, and encrypted Site credential storage.
- Protocol v1 implements Site push/Central poll, durable monotonic sequence, receipts, per-direction cursors, at-least-once handling, authority/schema validation, bounded retry, and explicit acknowledgement.
- Heartbeat/settings flows, duplicate handling, revocation/re-enrollment, outage queueing, and recovery have PostgreSQL integration coverage.
- Co-located stacks communicate over an explicit integration network; independent hosts use the same HTTPS application protocol. No database URL crosses deployments.

### Event deployment and Site-local program projection

- Central stores one Event+Site deployment plus immutable complete revisions and sends them through the existing protocol-v1 outbox.
- Sites apply newer complete snapshots transactionally, accept skip-ahead after outage, reject stale rollback, preserve deployment history, and report applied/failed/stale/revoked status.
- Snapshot v1 projects event metadata, event-scoped people, participation, sessions, session presenters, logical presentations, presentation relationships, versions, external identifiers, and relevant room mapping.
- New snapshots deactivate omitted Central-owned program rows without deleting Site-owned media or operational history.
- Central and Site browser pages expose deployment and locally retained program state. Site reads continue without Central.
- Central Event metadata edits automatically publish a complete revision to every active deployment;
  the Site idempotently updates its local Event projection after reconnecting when necessary.
- Central Event detail provides a Site selector and committed-program deployment preview with counts,
  warnings, blocking conflicts, explicit deploy/update/retry controls, Site names, revisions, and
  applied/update-available status. Program changes no longer publish silently.

### Event program, permanent identity, and imports

- Central models permanent Person separately from EventParticipation, Session, SessionParticipant, Presentation, PresentationSession, PresentationPresenter, PresentationVersion, PresentationAsset, and ExternalIdentifier.
- API services support people, protected deletion impact/delete, participants, sessions, presenters, presentations, explicit relationships, external identifiers, and concurrency revisions.
- CSV/XLSX imports preserve source bytes/hash, raw/normalized/corrected rows, issues, identity evidence, decisions, candidates, counts, and entity lineage.
- Reconciliation uses explicit UUID, unique external identifier, and normalized email evidence; name-only evidence requires review. Commit is transactional and guarded against stale program/person revisions.
- Central browser administration supports modal Event creation/editing with dates and searchable
  IANA timezones, functional upload/review/reconciliation/commit, relationship lists, Site enrollment
  approval, deployment, Site room mapping, and useful failure states.
- Central Event and permanent Person administration provide lifecycle deletion previews, exact-name
  confirmation, durable processing, progress/error state, and target-independent audit evidence.
  Event cleanup retains person-owned participation history and propagates Site purge tombstones
  through protocol v1; archive and deployment revocation remain non-destructive retention states.
  Permanent Event deletion now orders presentation-media imports and replication receivers ahead of
  presentation/version cleanup, detaches durable transport history, retains historical Event UUIDs
  in audit records without a live Event foreign key, and fails deterministic database errors with a
  supported operator retry instead of retrying indefinitely. Site tombstones also purge durable
  Event media transfer/replication sessions while preserving reusable rooms and shared media.
- Permanent People administration also supports one authorized, exact-phrase bulk deletion. The
  request snapshots all targeted Person UUIDs into a validated durable-job payload, reuses the
  individual Person cleanup rules, and publishes ordered person tombstones plus updated ADR-0007
  snapshots for affected deployments so offline Sites converge through the existing outbox transport.

### Rooms

- Site Room and RoomAssignment persistence exists. Site API/UI can create, list, edit, enable, archive, and open physical rooms by stable UUID.
- Central stores per-Site mappings from normalized imported room label to an existing Site room UUID/label.
- Site also owns explicit per-event imported-label mappings. Deployment automatically reuses a deterministic existing room or creates a UUIDv7 Site room for otherwise unmapped locations; ambiguous matches remain unresolved. Operators can remap or deliberately unmap a deployed program location, and later snapshots preserve that Site authority.
- A room workspace shows chronological sessions, presentations, current version/media metadata, processing state, and derived operational state. The dashboard surfaces persisted missing/error presentation, failed job, and upcoming-session conditions.
- Site API/UI assigns enrolled active devices to primary and backup roles with server-authoritative history and duplicate-active-assignment constraints. Because no Agent runtime reports heartbeat/network/interface/version data, the UI truthfully marks telemetry as unavailable.

### Authentication and browser foundation

- Central implements administrator bootstrap, salted `scrypt` password hashes, opaque cookie sessions stored as digests, CSRF protection, lockout, logout/revocation, and password change.
- React login and route guards use the session endpoint; the legacy administrator token remains for automation compatibility and is no longer entered/stored by the browser.
- Only the Administrator workflow is currently functional. Site human authentication and Administrator/Operator/restricted RBAC remain incomplete.
- Glass/Classic themes and Full/Reduced/Off motion share markup, persist preferences, honor reduced-motion preference, and have frontend tests.

## Known Operational Gaps

- **Room operations:** the Site room-centered read/coordination workflow and primary/backup assignment UI are implemented. Automated readiness policy, operator acknowledgements, manual status overrides, endpoint availability, and presentation-control actions are not.
- **Agent and room clients:** no Windows executable, secure device enrollment flow, heartbeat/status reporting, assignment recovery client, diagnostics, transfer client, presentation launch/control, or primary/backup synchronization.
- **Transfer execution:** Central-to-Site Site-pull has a resumable offset reader, Site worker, and
  Site-to-Central progress projection. Site-local presentation ingestion now queues durable reverse
  replication; the Site push worker resumes at Central's receiver-owned offset, and Central fsyncs,
  verifies, and publishes the replica against the same PresentationVersion UUID. The Site media
  workspace now exposes byte progress, retry state, failure detail, and an operator retry action;
  bandwidth control remains absent.
- **Transfer architecture:** ADR-0011 now fixes Site-initiated Central pull, Site-initiated reverse
  push, durable byte-offset resume, Site-scoped authorization, SHA-256 finalization, partial
  ownership, retry/replay, and separate replication/readiness semantics. Runtime coverage remains
  partial as described below.
- **Central-to-Site media pull:** Central now publishes authenticated Site-scoped manifests through
  the existing outbox, exposes bounded offset reads, and Site persists sessions and pulls real
  blocks into private partial storage. Each block is fsynced before its offset transaction commits;
  restart truncates unacknowledged tail bytes, full SHA-256 gates idempotent existing Site ingestion,
  and the default block is configurable at 4 MiB. Every durable block/state advancement publishes
  a Site-authoritative progress event; Central consumes it monotonically and projects byte progress,
  failure, completed Site media identity, and READY only after verified ingestion completion.
- **Site-originated metadata:** disconnected Site Presentation and PresentationVersion creation now
  queues stable-identity outbox events. Central materializes the same UUIDs idempotently within an
  authorized Event deployment and rejects identity/version conflicts. Reverse Site-to-Central
  binary replication uses Site-initiated authenticated blocks, stable sessions, idempotent
  finalization, and database-authoritative cleanup of expired terminal partials.
- **Production presentation-media workflow:** identifier allocation, canonical naming, deterministic
  ID matching, Site-local creation/version APIs, canonical metadata at Site ingestion, and Central
  durable staging/match/version/transfer-queue APIs and bidirectional binary movement are implemented
  foundations. Shared Central/Site Event Media workspaces now provide streamed multi-file upload,
  deterministic and explicitly confirmed manual matching, unmatched-media retention, paginated
  presentation lists, version history, local readiness, and Site-to-Central replication progress.
  The shared upload queue accepts single or multiple PPT/PPTX/PDF files, recursively selected browser
  directories, bounded per-file streaming, retryable partial failures, skipped incidental-file
  reporting, and separately validated source-relative-path provenance.
  Folder uploads now use two active request slots, bounded exponential retry for transient pressure,
  pause/resume and retry-failed controls, preserve non-junk unknown extensions for review, and use
  longest-boundary matching against known Event Presentation Identifiers and external IDs. Central
  accepts uploads after durable staging and atomically queues suggestion analysis in the existing
  PostgreSQL worker. Canonical promotion is a separate durable job queued only by explicit operator
  confirmation; the worker publishes storage before creating the confirmed PresentationVersion.
  Server admission bounds concurrent staging requests and returns retryable pressure without
  occupying the durable processing queue. Central retains durably staged bytes as needs-review
  records when matching/version post-processing fails. Site Presentation Media is a bounded,
  paginated reconciliation queue with compact rows and debounced server-side candidate lookup.
  Explicit individual or bulk confirmation creates the next version of the selected logical
  Presentation and removes it from active intake. The separate Site Presentations page reports
  canonical readiness independently from latest room-delivery state.
  Concurrent revision reconciliation, complete Site audit/outbox handlers, Site RBAC, guarded
  unmatched deletion/rename, and complete bulk Central-to-Site deployment controls remain incomplete.
  Running-stack end-to-end verification is still required before calling the overall workflow
  production-complete.
- **Bulk intake visibility and operational logs:** Central browser imports now register durable batch
  identity, retain selected/skipped accounting, correlate media lifecycle events, and render complete
  large selections through a searchable/filterable windowed list. Central and Site independently
  expose time-bounded structured operational logs with service/severity/context filters, polling,
  export, typed correlation IDs, and recursive secret redaction. Operational retention is configured
  separately from audit history. Broader instrumentation of untouched workflows remains incremental.
- **File management:** a read-only Site managed-media list exists, but there is no filesystem browser, rename/move/copy/guarded delete, preview, download, print, upload, or entry-linking UI.
- **PDF conversion:** derivative data structures and job primitives exist, but no converter, retry handler, status surface, preview, or room delivery workflow exists.
- **Storage administration:** a reusable, authenticated Media Storage service exposes explicitly
  mounted targets, real probes/capacity, persistent role assignments, durable staging references,
  and content-addressed SHA-256 commits. Central and Site Compose run independent instances with
  persistent default volumes or independently configurable staging/media/temp host bind mounts.
  Their shared themed page remains rendered during service outage, can test/select compatible
  targets, and distinguishes backing-filesystem use from recursively measured UPM-owned bytes.
  Central browser uploads stream through the service, retain staging target/key identity, commit
  immutable SHA-256 objects through the active media assignment, and serve Site-pull ranges without
  a shared API filesystem. Site browser ingestion uses its independent service and Site-to-Central
  replication reads committed object ranges through that boundary. Site-pull receive sessions also
  retain service staging target/key references, append at durable offsets, verify/commit through the
  Site service, and materialize local MediaObject metadata without copying through a worker path.
  The database-owned resumable main-media migration worker remains incomplete.
- **Program editing:** backend CRUD is broader than the browser; Sessions, Presenters, Presentations, and People pages are primarily read views after import.
- **Imports:** current CSV/XLSX parsing is synchronous and limited to 25 MiB; arbitrary operator column mapping and worker processing are deferred.
- **Authentication/RBAC:** Site admin authentication, Operator/restricted roles, user management, recovery, rotation UI, and a complete authorization policy are absent.
- **Kiosk:** no presenter check-in/upload/branding client or Site management workflow.
- **Digital Signage:** architecture is documented, but no independent stack, PostgreSQL schema, sync/deployment path, scheduler, renderer, device, or administration workflow exists.
- **Diagnostics/observability:** room-oriented persisted conditions are visible, but there is no integrated iperf/latency/throughput Agent diagnostics, comprehensive service/worker/queue dashboard, transfer telemetry, live endpoint status, or device fleet view.
- **Backup/recovery:** backup expectations and migration cautions are documented, but independent tested Central/Site backup and restore automation/runbooks are incomplete.
- **Deployment parity:** Central has a controlled Linux deployment script; the standalone production Site and Signage deployment/update procedures are not equivalent yet.
- **Presentation runtime:** no high-fidelity PowerPoint/Google Slides/Canva/Figma Slides/Keynote/PDF runtime adapters or control plane.
- **Ingestion breadth:** Agent upload, inbound email, AirDrop-compatible ingestion, and full server file-browser import are planned only.

## Documentation conflicts and ambiguities found

- ADR-0008 accurately records that production authentication/RBAC was deferred when the shared frontend decision was accepted. The later `admin-functional-program-import` milestone implemented Central administrator login/session/CSRF but not full RBAC. The frontend guide has been updated to reflect current behavior; ADR-0008 remains historical and does not need rewriting.
- The Master Architecture previously described Signage product behavior but did not state the required independently deployable Docker/PostgreSQL stack or explicit room/display distinction. Those target constraints are now explicit. A Signage data/transport/runtime implementation ADR is still needed before implementation.
- Client directories are boundary placeholders only. Their README presence must not be interpreted as Agent, Kiosk, Signage, or Room Client implementation.
- Queue and media models represent genuine infrastructure, but a model row alone does not establish a completed conversion, transfer, device, or presentation-control workflow.

## Evidence map

- Architecture decisions: [`docs/architecture/decisions`](../architecture/decisions/README.md)
- Domain ownership: [`docs/architecture/domain-data-foundation.md`](../architecture/domain-data-foundation.md)
- Program/import behavior: [`event-program-domain.md`](event-program-domain.md) and [`admin-functional-program-import.md`](admin-functional-program-import.md)
- Synchronization: [`central-site-synchronization.md`](central-site-synchronization.md) and [`event-deployment-synchronization.md`](event-deployment-synchronization.md)
- Jobs: [`durable-jobs-and-outbox.md`](durable-jobs-and-outbox.md)
- Frontend: [`frontend.md`](frontend.md)
- Media: [`site/media/README.md`](../../site/media/README.md)
- Site room operations: `site/python/src/upm_site/room_operations.py`, `site/python/src/upm_site/operations_api.py`, and `web/src/pages/site/SitePages.tsx`
- Room-operations coverage: `tests/python/test_site_room_operations_postgres.py` and `web/src/test/siteRooms.test.tsx`
- Tests: `tests/python/` and `web/src/test/`

## Decisions that should receive future ADRs

- Signage service boundaries, Site-local data ownership, Central/Site-to-Signage deployment contracts, redundancy, scheduling, override precedence, and renderer lifecycle.
- Windows client technology, secure enrollment, discovery, update policy, cached recovery state, and primary/backup control protocol.
- Presentation runtime fidelity/adapters and explicit fallback behavior by source format.
- PDF conversion engine, sandboxing, derivative lifecycle, and failure/retry policy.
- Backup/restore, retention, and disaster-recovery objectives for Central, Site, Signage, PostgreSQL, media, and trust material.
- Full authentication/RBAC across Central, Site, operators, devices, and service identities, superseding temporary or partial boundaries where appropriate.

### Operator-confirmed presentation media matching

Central presentation uploads now retain Event-scoped, explained candidate rankings as suggestions rather than automatically creating PresentationVersions. The Event candidate API and shared review UI support presenter/session/Presentation search, changing a suggestion, re-running unresolved matching, individual confirmation, and explicit selected bulk confirmation with per-item results. Confirmation is Event-validated and retry-idempotent. The persistence-free matcher is shared with Site, but the equivalent Site unresolved-import review/confirmation API remains future integration work; Site-local direct uploads to an explicitly chosen Presentation continue to use existing offline-capable semantics.

Roster-shaped program rows whose imported Presentation ID maps to `Session.session_code` now
materialize an idempotent canonical Presentation and mirror Session presenters to that Presentation.
Import commit enforces the assignable-Presentation invariant. Re-run matching repairs historical
imported Sessions before rebuilding candidates, and an explicit Event repair API exposes the same
idempotent operation. Repaired program metadata uses the existing Event revision/deployment snapshot
path; media matching remains suggestion-only and creates no PresentationVersion before confirmation.
