# Universal Presentation Management (UPM)
## Master Architecture Specification

**Document status:** Authoritative architecture source of truth  
**Product:** Universal Presentation Management (UPM)  
**Repository:** `durtball/Universal-Presentation-Management`

---

## 1. Purpose

Universal Presentation Management (UPM) is a new distributed presentation-management platform and a clean rebuild of SpeakerReady.

SpeakerReady may be used only as a requirements reference, operational-workflow reference, and lessons-learned reference.

**Do not copy SpeakerReady's architecture.**

This document defines UPM's architectural invariants, system boundaries, authority model, and target architecture.

It does not claim that every described capability is currently implemented.

Implementation status and detailed product requirements belong in their respective product and development documentation.

---

## 2. Architecture Philosophy

UPM is designed as one coherent target architecture implemented incrementally.

Do not build disposable intermediate architectures with the intention of replacing them later.

Milestones add capability to the target architecture rather than introducing temporary alternatives.

Implementation must preserve established:

- Central/Site separation
- Site autonomy
- service boundaries
- database ownership
- authority boundaries
- identity rules
- synchronization contracts
- media ownership
- durable processing
- security boundaries
- deployment boundaries

Significant changes to these architectural contracts require deliberate architectural review and an ADR where appropriate.

---

## 3. Architectural Invariants

The following rules apply throughout UPM.

### 3.1 Central and Site are separate systems

UPM Central and UPM Site are independently deployable and independently restartable systems.

They may run on the same physical Linux server, but they remain separate deployments with separate:

- PostgreSQL databases
- migration histories
- service lifecycles
- configuration
- storage ownership
- operational state

Do not merge Central and Site into one application or database.

### 3.2 Site autonomy

A production UPM Site must remain operational during loss of WAN or Central connectivity.

Routine active-show operation must not require a live Central call after required deployment data and media have synchronized.

### 3.3 Explicit distributed-system boundaries

Sites must never connect directly to Central PostgreSQL.

Central must never depend on direct access to a Site database.

Cross-boundary communication uses explicit, secure, versioned APIs, events, synchronization mechanisms, and media-transfer contracts.

This rule applies even when Central and Site are running on the same physical machine.

### 3.4 PostgreSQL

Central and Site use PostgreSQL.

Persistent schema changes use explicit Alembic migrations.

Central and Site maintain independent migration graphs.

Do not introduce SQLite as an application persistence shortcut.

### 3.5 UUID identity

Internal persistent entities use PostgreSQL UUID identity.

New distributed identifiers should use UUIDv7 where applicable.

Names, labels, filenames, imported strings, room names, presentation titles, and spreadsheet row numbers are not identity.

### 3.6 Server-authoritative operational state

Operational assignments are authoritative at the appropriate server layer.

This includes:

- room assignments
- device assignments
- presentation associations
- delivery state
- operational permissions

A reconnecting client must not overwrite newer server-authoritative state with stale cached state.

### 3.7 Durable processing

Restart-sensitive, retryable, heavy, asynchronous, or show-critical work uses the established PostgreSQL durable job/outbox architecture.

Do not substitute process-local or in-memory background tasks for work requiring durable execution.

### 3.8 Modular services

Services should be:

- single-purpose
- independently testable
- independently restartable
- observable
- replaceable without destabilizing unrelated services

Do not collapse unrelated responsibilities into a monolithic application or container.

### 3.9 Original media preservation

Original presentation/media files are preserved.

Conversions and processing operations create linked derivatives rather than replacing source media.

### 3.10 Security is explicit

Authentication, authorization, machine trust, service trust, secrets, enrollment, and transport security are architectural concerns and must not be added as afterthoughts.

Secrets must never be committed to Git.

---

## 4. Product Topology

UPM consists of several cooperating components.

### UPM Central

Global control plane responsible for areas including:

- Sites
- Events
- permanent identity
- global configuration
- deployments
- aggregate visibility
- cross-site synchronization coordination
- optional media replication
- global administration

### UPM Site

Local operational authority for a physical venue, hotel, or event location.

A Site owns the local operational environment required to run an event independently.

### UPM Site Manager

Native Windows operator, high-volume file-management, and Site-mediated room-control application. It uses Site HTTP/media APIs and a durable client-owned transfer queue. It never connects inbound to Agent devices, accesses Site PostgreSQL or filesystem paths, or uses SMB for managed transfers.

### UPM Agent

A separate presentation-computer product: a lightweight background service for authenticated outbound Site communication, durable commands, cache, and saveback, plus an interactive per-user companion for PowerPoint operations. The supported control path is Site Manager → Site → Agent. Agent online state is derived only from fresh heartbeat telemetry. Review saveback always names its base immutable version; concurrent revisions produce a preserved, explicit conflict rather than overwrite.

### UPM Room Client / Room Endpoint

Room presentation endpoint used for delivery, verification, launching, status, and primary/backup workflows.

### UPM Kiosk

Site-connected presenter/check-in workflow client.

### UPM Signage

Digital-signage subsystem supporting scheduled, event-aware, and non-event-aware signage.

### Shared platform services

UPM also includes:

- PostgreSQL databases
- Media Storage services
- workers
- synchronization services
- shared contracts
- Caddy edge services
- browser administration interfaces

---

## 5. Central Architecture

UPM Central:

- runs on Linux;
- is containerized;
- uses PostgreSQL;
- is fully operable through a browser;
- uses Caddy as the standard edge proxy/TLS layer;
- communicates with Sites through explicit secure services;
- maintains global identities and global/multi-site configuration;
- coordinates deployments and synchronization;
- provides aggregate health and operational visibility.

Central must not become a required dependency for Site-local active-show operation.

### Central-hosted Site

A Central Linux server may also host a UPM Site.

When enabled, it runs the **same reusable Site deployment stack** used by a standalone Site.

The co-located Site maintains its own:

- Site PostgreSQL database
- migrations
- configuration
- media
- workers
- API
- synchronization state
- device/room state
- operational lifecycle

Do not create a special Central-only implementation of Site functionality.

---

## 6. Site Architecture

UPM Site is the local operational system.

A production Site:

- runs on Linux;
- is containerized;
- uses PostgreSQL;
- owns authoritative local operational media;
- runs local APIs and workers;
- coordinates local clients and endpoints;
- provides browser-based operations;
- continues operating without Central;
- queues synchronization work during disconnection;
- safely reconciles after connectivity returns.

During active operations, Site is authoritative for local operational state including:

- devices
- room assignments
- presentation placement
- media availability
- delivery state
- kiosk state
- signage execution state
- local jobs
- local readiness
- local operational actions

---

## 7. Synchronization Architecture

Central and Site must be treated as distributed systems.

Synchronization uses:

- globally unique identifiers
- explicit ownership
- durable outbound state
- durable inbound processing
- idempotency
- retry with backoff
- conflict handling
- checkpoints
- auditability
- resumable media transfer
- visible synchronization state

Sites must remain safe under:

- WAN outages
- high latency
- packet loss
- service restarts
- extended disconnection
- duplicate delivery
- stale reconnecting clients
- interrupted transfers

Central-to-Site and Site-to-Central synchronization must respect explicit authority and ownership rules.

See the applicable synchronization ADRs for protocol details.

---

## 8. Identity Architecture

UPM Central owns permanent person identity.

A permanent `person_id` persists across events.

Event participation references permanent identity rather than creating a new person identity for every event.

Person matching may use evidence including:

- email
- alternate email
- phone
- organization
- external identifiers
- import identifiers
- historical links
- administrator-confirmed matches

Display names alone are insufficient identity evidence.

Ambiguous identities must not be silently merged.

Permanent-person deletion is a deliberate protected Central operation and must not occur automatically through event deletion, archival, Site cleanup, or routine synchronization.

---

## 9. Core Domain Model

UPM uses normalized relationships.

Core concepts include:

- Person
- Event
- Event participation
- Session
- Session participant
- Presentation
- Presentation version
- Presentation asset
- Room
- Room assignment
- Device
- Device assignment
- Site
- Media object
- Transfer job
- Processing job
- Sync event
- Audit record

Relationships must support real event workflows, including:

- multiple presenters per session
- multiple sessions per presenter
- multiple presentations
- presentation version history
- room changes
- schedule changes
- original and derivative assets
- primary and backup delivery

Do not collapse these concepts into a single presentation or schedule record.

---

## 10. Presentation Architecture

UPM supports two parallel presentation/media workflows.

### Entry-based presentations

Scheduled presentations linked to program data.

They may be associated with:

- presenters
- sessions
- rooms
- presentation versions
- delivery state
- readiness state

### Open-file assets

Ad hoc assets that do not require a scheduled Presentation record.

Examples include:

- walk-in slides
- images
- video
- logos
- holding slides
- sponsor assets
- emergency content
- miscellaneous event media

Open-file assets remain distinct from scheduled Presentation entities while using appropriate shared media/storage infrastructure.

---

## 11. Presentation Media Matching

Presentation Media is an intake and reconciliation workflow rather than the permanent operational home of a confirmed presentation.

Matching is **suggestion-driven and operator-authoritative**.

UPM may:

- discover candidates
- rank candidates
- display confidence/evidence
- allow bulk selection of suggested matches

UPM must not automatically finalize a presentation assignment without explicit operator confirmation.

Unmatched or uncertain valid files remain available for operator review and are not considered ingestion failures.

Once confirmed, media enters the canonical Presentation / PresentationVersion / PresentationAsset workflow.

---

## 12. Media and Storage Architecture

Each deployment owns the storage required for its responsibilities.

Central and every Site run an independent deployment-local Media Storage service.

The Media Storage service owns filesystem mechanics and explicit storage mounts.

Application services retain domain authority in PostgreSQL and interact with Media Storage through authenticated internal APIs.

Media identity must not depend on absolute operating-system paths.

Storage references use stable storage identities and validated logical object keys.

The media architecture supports:

- original preservation
- hashing
- versioning
- derivatives
- metadata
- duplicate detection
- resumable transfer
- verification
- processing state
- storage health
- recovery

Detailed storage mechanics belong in the storage ADRs and subsystem documentation.

---

## 13. File Access and SMB

SMB may provide operator-friendly filesystem access, but it is not the sole critical UPM transfer mechanism.

SMB must:

- support current Windows security expectations;
- use authenticated access;
- avoid insecure legacy protocols;
- reconnect reliably;
- respect UPM media authority and lifecycle rules.

Managed SMB views may expose canonical presentation media through operator-friendly folder structures without changing canonical media identity.

The UPM API/media-transfer architecture remains the authoritative application transfer mechanism.

---

## 14. Presentation Processing

Heavy media operations run through durable workers.

Examples include:

- hashing
- PDF conversion
- preview generation
- media analysis
- replication
- large file operations
- import parsing
- indexing

Processing must preserve original media.

Generated files are linked derivatives with explicit status and error information.

GPU acceleration may be used when available and beneficial, but required core UPM workflows must not depend on GPU availability.

---

## 15. Transfer Architecture

Large presentation files and unreliable networks are expected operating conditions.

Transfer architecture supports:

- resumability
- chunking where appropriate
- expected-size verification
- SHA-256 verification
- retries
- partial-transfer recovery
- configurable concurrency
- progress visibility
- independent destination progress

Site-to-Central and Central-to-Site transfers respect the established synchronization direction and trust model.

A Site's local readiness must not depend on optional Central media replication completing.

---

## 16. Client and Device Architecture

Managed clients authenticate to the appropriate Site and receive server-authoritative configuration.

Clients must tolerate:

- Site restarts
- address changes
- temporary connectivity loss
- stale cached state
- interrupted transfers

Clients must reconnect and reconcile rather than becoming permanently stuck in an offline/error state.

Windows operational clients target Windows 11 compatibility.

Detailed Agent, Room Client, Kiosk, and Signage behavior belongs in the appropriate subsystem requirements.

---

## 17. Signage Architecture

UPM Signage is a distinct subsystem and must not be implemented by overloading presentation-operation tables.

Signage supports concepts such as:

- displays
- display assignments
- templates
- playlists
- schedules
- content/assets
- temporary overrides
- render state
- device health

Rooms and signage displays retain separate UUID identities even when labels are similar.

Signage may be:

- event-aware;
- schedule-driven;
- manually overridden; or
- configured independently of events.

Site-local signage operation must continue during Central/WAN outages once required configuration and media are locally available.

---

## 18. Ingestion Architecture

UPM supports multiple ingestion sources, including:

- browser upload
- Agent upload
- SMB Incoming
- server/file-browser import
- open-file import
- structured event-data imports
- future AirDrop-compatible ingestion
- future inbound email attachment ingestion

Ingestion must preserve valid media even when matching or metadata reconciliation is uncertain.

Invalid association is not equivalent to invalid media.

---

## 19. Structured Imports

Structured imports such as Excel/CSV event exports are ingestion workflows, not direct database overwrites.

Typical stages include:

1. upload;
2. parse;
3. validate;
4. map;
5. match identity;
6. detect duplicates;
7. preview/review where required;
8. commit;
9. retain provenance/audit information.

Stable external identifiers should be preserved when supplied.

Valid source rows must not be silently discarded merely because automatic reconciliation is uncertain.

---

## 20. Jobs and Workers

Central and Site maintain independent PostgreSQL-backed durable work queues.

Durable jobs should support, as appropriate:

- unique IDs
- state
- progress
- retries
- retry scheduling
- leases
- worker identity
- timestamps
- errors
- idempotency
- priority
- capability matching
- cancellation where safe

Heavy or show-critical work must not depend on API-process lifetime.

---

## 21. Security and Trust

Security is part of the base architecture.

UPM should support:

- secure device enrollment
- revocable machine identity
- service authentication
- role-based authorization
- credential rotation
- deployment-separated secrets
- TLS
- mTLS where appropriate

At minimum, permissions distinguish administrative and operational roles.

Do not use universal shared credentials as a platform shortcut.

---

## 22. Caddy and TLS

Caddy is the standard UPM edge proxy and TLS termination layer.

Certificate strategy may use:

- public ACME certificates where appropriate;
- internal/private CA certificates for private deployments.

UPM maintains a narrow integration with Caddy.

Certificate-management behavior must not be duplicated throughout application services.

---

## 23. Browser Administration

Core Central and Site administration is browser-based.

No Windows desktop application may be required for core Central or Site administration.

Optional native management clients may exist only as clients of the server-side architecture.

### Themes

UPM supports interchangeable presentation layers over the same functionality:

- **UPM Glass**
- **UPM Classic**

Themes are CSS/presentation layers rather than separate applications.

Motion supports:

- Full
- Reduced
- Off

The UI honors `prefers-reduced-motion`.

---

## 24. Container and Service Architecture

UPM server deployments use Docker.

Central and Site are separate application stacks.

Typical Central services include:

- caddy
- central-api
- central-web
- central-worker
- central-sync
- central-media-storage
- central-postgres

Typical Site services include:

- caddy
- site-api
- site-web
- site-worker
- site-sync
- site-media-storage
- site-postgres

Additional single-purpose services may be introduced when justified by a clear service boundary.

Do not merge unrelated services merely to reduce container count.

---

## 25. Server Technology Baseline

Unless superseded by an accepted architecture decision, UPM server components use the established stack:

- Python 3.13
- FastAPI
- Pydantic v2
- SQLAlchemy 2.x
- psycopg 3
- Alembic
- PostgreSQL
- uv-managed `pyproject.toml` projects
- OpenAPI / JSON Schema for external contracts

Technology choices for Windows clients may differ where appropriate.

---

## 26. Database Migrations

Persistent database changes use explicit versioned migrations.

Central and Site:

- have separate Alembic configurations;
- maintain independent revision graphs;
- do not require the other database to migrate;
- use deployment migration gates before dependent application services start.

Avoid ad hoc production schema changes.

---

## 27. Observability

UPM must expose meaningful operational state.

Relevant areas include:

- service health
- database health
- workers
- queue depth
- jobs
- storage
- synchronization
- Sites
- devices
- transfers
- media processing
- conversion
- authentication
- API failures

Operators must be able to distinguish conditions such as:

- Healthy
- Warning
- Failed
- Offline
- Degraded
- Synchronizing

Operational events and audit records are separate concerns.

---

## 28. Audit

Audit records document meaningful administrative and operational changes.

Examples include:

- identity merge/deletion
- presentation deletion
- room reassignment
- device enrollment/revocation
- configuration changes
- permission changes
- import approval
- manual synchronization actions

Audit records should retain appropriate actor, action, timestamp, target, Site/Event context, and before/after information.

---

## 29. Backup and Recovery

Central and each Site require independent backup and recovery procedures.

Backup scope may include:

- PostgreSQL
- configuration
- authoritative metadata
- required media
- identity data
- audit records
- required secure configuration/certificate material

Site recovery must not require restoring Central first.

Recovery procedures must be testable.

---

## 30. Existing Network Architecture

UPM is designed for existing customer/event networks.

It must tolerate environments including:

- corporate networks
- hotel networks
- event VLANs
- private LANs
- DHCP
- static addressing
- routed networks

The system must tolerate address changes, DNS delays, reconnects, service restarts, latency, and temporary outages.

Do not introduce a separate Managed Network product architecture.

---

## 31. Software Update Policy

Production application updates are explicitly controlled.

UPM must not perform disruptive automatic application upgrades during active events.

Windows client reliability configuration may suppress or defer disruptive operating-system behavior where administratively permitted.

---

## 32. Testing Architecture

UPM requires testing at multiple levels.

### Unit

Examples:

- domain logic
- validation
- transformations
- identity matching
- synchronization logic
- media/path logic

### Integration

Examples:

- PostgreSQL
- APIs
- workers
- Media Storage
- authentication
- transfers
- Caddy routing

### Distributed-system

Examples:

- Central outage
- Site outage
- reconnect
- duplicate delivery
- conflict handling
- extended disconnection
- interrupted transfer
- restart during work

### System

Examples:

- Agent
- Kiosk
- Signage
- Room Client
- primary/backup workflows
- end-to-end show operation

Tests requiring binary fixtures should generate them at runtime rather than committing generated binary artifacts unless a binary is an intentional repository dependency.

---

## 33. Repository Architecture

UPM uses a monorepo.

Major repository areas should remain clearly separated by responsibility, including:

- Central
- Site
- Signage
- clients
- shared contracts/utilities
- Central migrations
- Site migrations
- infrastructure
- documentation
- tests

Repository organization may evolve as implementation grows, provided architectural service and ownership boundaries remain intact.

---

## 34. Architecture Governance

This document is the architectural source of truth.

Accepted ADRs refine specific implementation decisions.

Historical ADRs must not be rewritten to make later decisions retroactive.

A significant architectural change requires a new or superseding ADR where appropriate.

When implementation and architecture disagree:

1. determine whether architecture intentionally changed;
2. if it changed, document and approve the architectural change;
3. otherwise, correct the implementation.

### Agent documentation policy

Codex and other automated development agents must follow the documentation-loading policy defined in the repository root `AGENTS.md`.

The Master Architecture remains authoritative, but agents should load only the architecture sections and ADRs relevant to the requested work unless the task is genuinely cross-cutting.

Do **not** require automated agents to read this entire document for every routine bug, UI change, naming change, test fix, or narrowly scoped implementation task.

---

## 35. Prohibited Architectural Shortcuts

Do not:

- use SpeakerReady's monolithic architecture as the UPM foundation;
- merge Central and Site into one application;
- merge Central and Site databases;
- let Site or clients read Central PostgreSQL directly;
- require Central for routine Site-local active-show operation;
- introduce SQLite as temporary application persistence;
- treat labels or filenames as identity;
- merge people solely because display names match;
- replace originals during conversion;
- store authoritative operational state only on clients;
- depend on a Windows Central application for core administration;
- make a development workstation a runtime dependency;
- use automatic disruptive production upgrades;
- bypass durable jobs for restart-sensitive/show-critical work;
- introduce parallel implementations when a canonical subsystem already exists;
- justify architectural shortcuts as something to fix in a future "v2."

---

## 36. Architecture Documentation Map

Detailed architecture should be divided into focused subsystem documents so developers and automated agents can load only relevant context.

Recommended structure:

```text
docs/
  architecture/
    UPM_MASTER_ARCHITECTURE.md

    components/
      central-site.md
      data-identity.md
      media-storage.md
      synchronization.md
      clients-devices.md
      signage.md
      security-network.md
      deployment-runtime.md
      jobs-observability.md
      testing-development.md

    decisions/
      README.md
      ADR-xxxx-*.md
