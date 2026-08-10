# Universal Presentation Management (UPM)
## Master Architecture Specification

**Document status:** Authoritative architecture source of truth  
**Product:** Universal Presentation Management (UPM)  
**Repository:** `durtball/Universal-Presentation-Management`  
**Architecture principle:** Design the complete target system first, then implement it incrementally without replacing the architecture later.

---

## 1. Purpose

Universal Presentation Management (UPM) is a new product and a complete rebuild of the previous SpeakerReady system.

The existing SpeakerReady codebase is a **requirements and lessons-learned reference only**. It is not the architectural foundation for UPM.

UPM is designed for production use in conference, hotel, event, meeting-room, and speaker-ready-room environments where presentation files, presenters, rooms, clients, kiosks, signage, synchronization, and multi-site control must remain reliable even when network connectivity is degraded or unavailable.

The system must be modular, observable, independently testable, restartable, and replaceable by service without destabilizing the rest of the platform.

---

## 2. Core Architecture Principles

UPM must follow these principles throughout implementation:

1. **No temporary architecture**
   - Do not build a short-lived "v1 now, redesign later" system.
   - Milestones fill in the predetermined target architecture.

2. **Central and Site are separate systems**
   - UPM Central and UPM Site are distinct, independently deployable containerized systems.
   - They may run side by side on the same physical Linux server but must not be merged into one application process or one database.

3. **Site autonomy**
   - Every production Site must remain fully operational during WAN or Central outages.
   - Local workflows cannot depend on live access to Central.

4. **PostgreSQL from day one**
   - Central and Site both use PostgreSQL.
   - Their databases remain logically and operationally separate.

5. **API-driven coordination**
   - Sites communicate with Central through explicit secure APIs.
   - Sites must never connect directly to Central PostgreSQL.

6. **Existing Network only**
   - UPM is designed for customer/event networks.
   - Do not build a separate managed-network mode as a core product requirement.

7. **Caddy as the standard edge layer**
   - Caddy provides reverse proxying and TLS termination.
   - UPM must maintain a narrow relationship with Caddy rather than embedding certificate-management logic throughout the product.

8. **Background jobs for heavy operations**
   - File processing, conversion, synchronization, hashing, previews, replication, indexing, and similar operations run through durable workers/jobs.

9. **Server-authoritative operational state**
   - Device assignments, room assignments, presentation associations, and operational permissions are authoritative at the server layer.

10. **Strong observability**
    - Health, logs, metrics, job state, synchronization state, device state, storage state, and transfer state must be visible to administrators.

---

## 3. Product Topology

UPM consists of the following major product components:

- UPM Central
- UPM Site
- UPM Agent
- UPM Room Client / room endpoint
- UPM Kiosk
- UPM Signage
- Shared contracts and schemas
- Central and Site PostgreSQL databases
- Background worker services
- Media and storage services
- Synchronization services
- Caddy edge services
- Browser-based administration and operations interfaces

---

## 4. UPM Central

UPM Central is the global control plane for managing multiple Sites, events, identity records, global configuration, aggregate visibility, synchronization coordination, and cross-site operations.

### Central requirements

UPM Central must:

- Run on Linux.
- Be deployed through Docker containers.
- Be fully operable through a browser-based HTML interface.
- Require no Windows desktop application for core administration.
- Use PostgreSQL.
- Use Caddy as the edge proxy/TLS termination layer.
- Coordinate Sites through secure HTTPS APIs and preferably mTLS for machine-to-machine trust.
- Maintain global identity records.
- Maintain cross-site/event visibility.
- Support optional media replication from Sites.
- Track Site health and synchronization status.
- Never require direct access to Site databases.

### Central-hosted Site capability

UPM Central may also operate as a fully functional UPM Site when Site capability is enabled on the Central Linux/Docker server.

When enabled:

- Central runs the exact same reusable Site deployment profile used by standalone Site appliances.
- The Central-hosted Site has its own Site-local PostgreSQL database/context.
- The Central-hosted Site has its own authoritative media storage.
- The Central-hosted Site has its own workers, Site API, room/device coordination, kiosks, signage, and operational state.
- Central and the co-located Site remain separate deployments with separate lifecycles, storage, configuration, and database boundaries.
- Communication between Central and the co-located Site still occurs through explicit services/APIs rather than through hidden shared-state assumptions.

There must not be a special-case Central-only Site implementation.

---

## 5. UPM Site

UPM Site is the operational system for a physical hotel, venue, or event location.

### Site requirements

A production UPM Site must:

- Run on Linux.
- Be containerized from the beginning.
- Use PostgreSQL.
- Maintain authoritative local media storage.
- Run local APIs.
- Run local workers.
- Coordinate local UPM Agents, room endpoints, kiosks, and signage.
- Remain fully functional during loss of WAN or Central connectivity.
- Queue synchronization work while disconnected and resume safely when connectivity returns.
- Provide browser-based administration and operational interfaces.
- Avoid direct dependency on Central for event-day operations.

### Site authority

During event operations, the Site is authoritative for:

- Local device state
- Room assignments
- Presentation placement
- Local media availability
- Local transfer state
- Local kiosk state
- Local signage state
- Local processing jobs
- Local room readiness
- Local operational actions

---

## 6. Central-to-Site Synchronization

Central and Site operate as distributed systems.

### Synchronization model

Use:

- Event-driven synchronization
- Globally unique internal IDs
- Explicit ownership fields
- Explicit event ownership
- Explicit Site ownership
- Idempotent operations
- Durable outbound queues
- Durable inbound processing
- Retry with backoff
- Conflict detection
- Audit history
- Checkpointing
- Resumable transfers
- Clear sync status reporting

Internal entity identifiers use PostgreSQL-native UUID columns. New identifiers are generated as UUIDv7 in the application/domain layer so Central, Sites, and disconnected clients can create globally unique IDs without sequential database coordination. Names, labels, titles, filenames, and imported row numbers are not identity keys.

### Connectivity expectations

The design must assume:

- Temporary WAN outages
- High latency
- Packet loss
- Central restarts
- Site restarts
- Long periods of disconnection
- Files changing while transfers are in progress
- Devices reconnecting with stale state

### Database isolation

Central and Site must not share a PostgreSQL instance as a logical shortcut.

Even if they run on the same physical host:

- Central database remains Central-owned.
- Site database remains Site-owned.
- Shared state crosses boundaries through defined APIs/events.

---

## 7. Identity Architecture

UPM uses permanent person identities.

### Permanent person UUID

A person UUID is a permanent Central identity record.

The same person should be matched to the same `person_id` when they participate in future events.

Event participation records must reference the permanent person identity rather than creating a new person identity for every show.

### Person matching

Person matching must never rely only on display name.

The identity system should support multiple matching signals, such as:

- Primary email
- Alternate email
- Phone
- Organization
- External source identifiers
- Event-import identifiers
- Historical identity links
- Administrator-confirmed matches

Ambiguous matches must not be silently merged.

### Person deletion

UPM Central must provide an authorized identity-management area where administrators can deliberately delete a permanent person record.

Deletion must:

- Be explicit
- Require confirmation
- Display dependency impact
- Respect audit requirements
- Avoid accidental cascade deletion of unrelated historical records
- Never occur automatically when an event is archived
- Never occur automatically when Site data is cleaned up

---

## 8. Event, Session, Presenter, and Presentation Data Model

The domain model must normalize relationships rather than embed everything in one presentation row.

Core entities should include, at minimum:

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

### Relationship expectations

A person can:

- Participate in multiple events
- Present in multiple sessions
- Have multiple presentations
- Serve multiple roles

A session can:

- Have multiple presenters
- Have multiple presentations
- Move between rooms
- Be rescheduled

A presentation can:

- Have multiple versions
- Have original and derivative files
- Be assigned to rooms
- Be mirrored to backup endpoints
- Exist with processing states
- Have preview/converted derivatives

---

## 9. Presentation Workflow

UPM must retain the proven entry-based workflow while also adding an open-file workflow.

### Entry-based workflow

Used for scheduled sessions and roster-linked presentations.

Supports:

- Presenter association
- Session association
- Room association
- Revision tracking
- Status tracking
- Primary/backup delivery
- Check-in
- Room readiness
- Historical tracking

### Open-file workflow

Supports assets without roster/database entries, including:

- Walk-in slides
- Images
- Videos
- Logos
- Holding slides
- Sponsor content
- Emergency assets
- Ad hoc media

Open-file assets must be manageable through the Site file browser and upload flow without requiring a presentation entry.

They must remain clearly distinguishable from entry-linked presentations.

---

## 10. File and Media Architecture

Each Site owns authoritative local media required for its operations.

Site media storage is administrator-configurable through Site-owned storage targets. Each target has a globally unique `storage_target_id` and refers to a host-provided filesystem root or mount. The Linux host/infrastructure layer owns partitioning, formatting, mounting, RAID, and hardware management; UPM does not implement those functions.

Media locations reference a `storage_target_id` plus a validated logical relative object key rather than treating an absolute operating-system path as media identity. Sites may configure multiple targets, but UPM does not automatically pool them or invent placement policy. Storage health, runtime capacity observations, and configurable warning/critical thresholds must be visible independently for each target.

Detailed storage decisions are recorded in [ADR-0002](decisions/ADR-0002-site-media-storage.md).

Site ingestion uses same-target staging, generated logical object keys, SHA-256 verification,
explicit availability states, no-replace atomic publication, and recovery reconciliation as
recorded in [ADR-0004](decisions/ADR-0004-site-media-ingestion-finalization.md).

### Media requirements

The media subsystem must support:

- Original-file preservation
- Version history
- File hashing
- Duplicate detection
- File metadata
- Processing state
- Derivative linking
- Resumable transfer
- Transfer verification
- Partial-transfer recovery
- Replication policy
- Storage health visibility

### Server file browser

UPM Site must include a more complete server-side file browser for managing presentation and media assets.

The file browser should support:

- Browsing
- Upload
- Rename
- Move
- Copy
- Delete with safeguards
- Metadata inspection
- Entry linking
- Open-file assets
- Search
- Processing state
- Transfer state

---

## 11. Presentation Format Support

The presentation runtime and ingestion architecture must be prepared for all initial target formats:

- PowerPoint
- Google Slides
- Canva
- Figma Slides
- Keynote
- PDF

The architecture must not assume static-slide rendering is sufficient.

Long-term target playback fidelity includes:

- Slide transitions
- Object/build animations
- Click-triggered reveals
- Timed reveals
- Embedded video
- Audio where applicable
- Sequencing
- Timing behavior
- High visual fidelity to source presentation behavior

---

## 12. PDF Conversion

UPM must provide reliable server-side PDF conversion for supported presentation/document formats.

Requirements:

- Preserve the original file.
- Create a linked PDF derivative.
- Track conversion status.
- Track errors.
- Support retries.
- Expose the PDF in the admin UI.
- Allow preview.
- Allow download.
- Allow print.
- Allow sending the PDF derivative to room devices.
- Never replace the original source file.

---

## 13. UPM Agent

UPM Agent is a Windows 11-compatible operational client.

It must:

- Support Windows 11.
- Discover and connect to the correct Site.
- Authenticate securely.
- Receive server-authoritative assignments.
- Support presentation transfer.
- Support resumable transfers.
- Recover automatically after Site restarts or network interruptions.
- Expose connection and diagnostics state.
- Avoid getting stuck on offline/error pages after transient failures.

### Presentation synchronization

UPM Agent should support synchronized presentation behavior between primary and backup endpoints when enabled.

The system should provide:

- Optional primary/backup sync
- Shared presentation state
- Coordinated open/start behavior
- Room-operator controls
- Safe desynchronization recovery

---

## 14. UPM Room Client / Room Endpoint

Room endpoints are Windows 11 clients used to present or control content in meeting rooms.

They must support:

- Server-authoritative room assignment
- Primary and backup designation
- Presentation delivery
- Presentation open/launch
- Status reporting
- Recovery after reconnect
- Local file verification
- Operator visibility
- Synchronization options between primary and backup systems

---

## 15. UPM Kiosk

UPM Kiosk is used for check-in and speaker/presenter workflows.

It must:

- Run on Windows 11 where required.
- Connect to the local Site.
- Remain functional during Central outages.
- Use Site-local data.
- Support branding configuration.
- Respect global branding fallback behavior.

### Branding hierarchy

UPM must provide:

- Global Branding
- Speaker Upload Page branding
- Speaker Kiosk branding
- Signage Kiosk branding

Global branding acts as the default.

A page-specific setting overrides Global only when a local value is explicitly configured.

---

## 16. UPM Signage

UPM Signage supports timed and operational event signage.

Signage content should support automatic display based on:

- Time
- Session state
- Room state
- Event schedule

Presentation media workflows may be manual or automatic depending on configuration.

Signage must remain operational from Site-local data during WAN/Central outages.

---

## 17. Upload and Ingestion

UPM should support multiple ingestion paths.

Target ingestion methods include:

- Browser upload
- UPM Agent upload
- File-browser import
- Open-file import
- AirDrop-compatible ingestion
- Inbound email attachment ingestion
- External event-data imports

### Email ingestion

The architecture should allow a Site or approved ingestion service to receive inbound email and import eligible attachments into the upload workflow.

This must include:

- Sender validation options
- Attachment restrictions
- File-type validation
- Audit trail
- Malware/security processing hooks
- Clear destination rules

---

## 18. Import Architecture

UPM must support structured imports such as Excel/CSV event exports.

Imports must be treated as ingestion jobs rather than direct database overwrites.

Import flow should include:

1. File upload
2. Parsing
3. Validation
4. Mapping
5. Identity matching
6. Duplicate detection
7. Preview
8. Administrator approval when needed
9. Transactional commit
10. Import audit record

Imports should support stable external IDs where supplied.

---

## 19. Jobs and Workers

Heavy operations must not block API/web processes.

Examples of worker-managed operations:

- File hashing
- PDF conversion
- Presentation processing
- Preview generation
- Media replication
- Large file copy
- Central/Site synchronization
- Email ingestion
- Import parsing
- Search indexing
- Archive generation
- Backup preparation

### Job requirements

Jobs must have:

- Durable state
- Unique job IDs
- Progress reporting
- Retry policy
- Error details
- Cancellation where safe
- Idempotency where possible
- Created/start/end timestamps
- Worker identity
- Operator-visible status

Central and Site use their own PostgreSQL-backed durable job tables and transactional outboxes. Workers claim eligible work transactionally with non-blocking row locks, leases, heartbeats, retry scheduling, idempotency metadata, priority, and capability matching. Central and Site queues remain isolated with their respective databases. Detailed decisions are recorded in [ADR-0003](decisions/ADR-0003-postgresql-durable-jobs-and-outbox.md).

---

## 20. Transfer Architecture

Presentation transfer must be designed for large files and unreliable networks.

Requirements:

- Resumable upload/download
- Chunking where appropriate
- Hash verification
- File-size verification
- Partial-transfer recovery
- Retry
- Bandwidth-aware behavior
- Configurable concurrency
- Network-saturation awareness
- Clear progress indicators

Transfers should be able to adapt based on network saturation rather than blindly consuming all available bandwidth.

---

## 21. Network Diagnostics

UPM Agent should include built-in network diagnostics.

Diagnostics should support:

- iperf-based testing
- Practical Site-to-Agent bandwidth tests
- Latency
- Upload throughput
- Download throughput
- Packet loss where available
- Retransmissions where available
- Interface used
- Local IP
- Server IP
- Timestamp
- Pass/warn/fail summary

Results must be visible in the Site/admin UI.

---

## 22. Existing Network Requirements

UPM is an Existing Network product.

It must work reliably on:

- Corporate networks
- Hotel networks
- Event production VLANs
- Private LANs
- DHCP networks
- Static-address deployments
- Routed networks

The architecture must tolerate:

- Address changes
- DNS delay
- Device reconnects
- Temporary service outages

Discovery must not depend on fragile one-time assumptions.

---

## 23. Security Architecture

Security must be built in from the beginning.

### Machine/device trust

Use secure device enrollment.

Potential mechanisms include:

- Enrollment tokens
- Device certificates
- Site trust records
- mTLS for service-to-service connections
- Revocation
- Expiration
- Rotation

### Authentication and authorization

UPM must support role-based permissions.

Roles should distinguish at least:

- Administrator
- Operator
- Restricted operational user

General principle:

- Operator has individual operational controls.
- Administrator has full control.
- Operators may allow configured actions.
- Administrators can override where policy permits.

### Secrets

Secrets must:

- Never be committed to Git
- Be delivered through environment/configuration mechanisms
- Support rotation
- Be separated by deployment
- Avoid shared universal credentials

---

## 24. TLS and Caddy

Caddy is the standard UPM edge proxy/TLS termination layer.

### Certificate strategy

Use:

- Public ACME certificates where customer DNS/public reachability is available
- Internal/private CA certificates for local/private deployments

UPM should configure what it needs from Caddy, but certificate-management logic must not be spread throughout application services.

---

## 25. Browser Interfaces

UPM Central and UPM Site core interfaces must be browser-based.

Central and Site functionality must remain available even if any optional Windows management client is absent.

### Optional Windows Central client

A future Windows-based Central interface may exist, but it must only be a client/front end.

It must not contain required Central logic.

---

## 26. Container Architecture

UPM Central and UPM Site use Docker from the beginning.

Central and Site server-side APIs, workers, synchronization services, and related processing services use Python 3.13, FastAPI, Pydantic v2, SQLAlchemy 2.x, psycopg 3, Alembic, PostgreSQL, and uv-managed `pyproject.toml` projects. OpenAPI and JSON Schema provide language-neutral external contracts. This decision does not select a technology for Windows clients.

Central and Site remain separate applications with separate persistence metadata, sessions, configuration, and migration histories. See [ADR-0001](decisions/ADR-0001-backend-persistence-stack.md).

### Central service boundaries

Expected Central deployment includes separate services such as:

- caddy
- central-api
- central-web
- central-worker
- central-sync
- central-postgres

### Site service boundaries

Expected Site deployment includes separate services such as:

- caddy
- site-api
- site-web
- site-worker
- site-sync
- site-postgres

Additional services may be introduced where they create a clear single-purpose boundary.

Do not collapse unrelated concerns into a monolithic container.

---

## 27. Repository Architecture

UPM should use a monorepo.

Recommended top-level structure:

```text
docs/
  architecture/
    decisions/
  api/
  deployment/
  development/

central/
  api/
  web/
  workers/
  sync/

site/
  api/
  web/
  workers/
  media/
  device-management/

clients/
  agent/
  kiosk/
  signage/
  room-client/

shared/
  contracts/
  models/
  schemas/
  utilities/

database/
  central/
    migrations/
  site/
    migrations/

infrastructure/
  central/
  site/
  caddy/
  docker/

scripts/

tests/
  integration/
  sync/
  system/
```

The repository should also contain:

- `.gitignore`
- `.editorconfig`
- `.env.example`
- Central Compose definition
- Site Compose definition
- GitHub Actions
- Bootstrap scripts
- Architecture documentation

---

## 28. Database Migrations

Database changes must use explicit migrations.

Requirements:

- Central migrations and Site migrations are separate.
- No ad hoc production schema modification.
- Migrations are versioned.
- Migration state is observable.
- Backup/recovery procedures account for migration versions.
- Downgrade/rollback strategy should be defined where feasible.
- Alembic is the migration framework for the Python server stack.
- Central and Site use separate Alembic configurations and independent revision graphs; neither database must be reachable to migrate the other.

---

## 29. Observability

UPM must provide meaningful operational visibility.

Track:

- Service health
- Database health
- Worker health
- Queue depth
- Job failures
- Device connectivity
- Device versions
- Site connectivity
- Central connectivity
- Sync lag
- Transfer throughput
- Failed transfers
- Storage usage
- Media-processing failures
- Conversion failures
- API errors
- Authentication failures

Operators should be able to distinguish:

- Healthy
- Warning
- Failed
- Offline
- Degraded
- Synchronizing

---

## 30. Logging and Audit

Application logs and audit records are different concerns.

### Logs

Logs support troubleshooting and observability.

### Audit records

Audit records track meaningful administrative and operational changes.

Examples:

- Identity merge
- Identity deletion
- Presentation deletion
- Room reassignment
- Device enrollment
- Device revocation
- Configuration change
- User permission change
- Manual sync action
- Import approval

Audit data should include:

- Actor
- Action
- Timestamp
- Target
- Site
- Event
- Before/after context where appropriate

---

## 31. Backup and Recovery

Central and every Site require defined backup and restore procedures.

Backups should include:

- PostgreSQL
- Configuration
- Authoritative media metadata
- Critical Site media according to policy
- Identity records at Central
- Audit records
- Required certificates/configuration material according to secure backup policy

Recovery must be tested.

Sites should be recoverable independently from Central.

---

## 32. Software Updates

Production updates are controlled.

The current requirement is:

- Manual software updates only unless the policy is explicitly changed later.

UPM must not perform disruptive automatic application upgrades during events.

Windows clients should also be configured for show reliability where approved.

---

## 33. Windows 11 Reliability

UPM Agent, UPM Kiosk, UPM Signage, and room endpoints must support Windows 11.

The installer may apply approved reliability configuration, including:

- Suppressing disruptive restarts during event operation
- Deferring updates where administratively permitted
- Hardening SMB compatibility
- Supporting current Windows security defaults
- Ensuring required local services/configuration are present

Changes must be explicit and auditable.

---

## 34. SMB and File Sharing

Where SMB is used:

- Support current Windows 11 security defaults.
- Avoid insecure legacy protocols.
- Use authenticated access.
- Support stable reconnect behavior.
- Do not make SMB the sole transfer mechanism for critical UPM workflows.

The UPM API/transfer architecture remains the primary long-term application transfer mechanism.

---

## 35. Development and Test Hardware Topology

Current development/test environment:

### UPM Central

**MINISFORUM MS-01**
- Linux
- 32 GB RAM
- NVIDIA RTX 3050 GPU
- Runs UPM Central/database services
- May also run the reusable UPM Site stack
- GPU may be used for local LLM tasks and suitable processing offload

### Development machine

**Desktop computer**
- Development/build workstation only
- UPM must never depend on this machine for runtime operations

### Independent UPM Site

**Laptop #1**
- Current Site development/test machine
- Temporary Windows 11 Site host during early development if needed
- Production Site target remains Linux/Docker

### Additional laptops

Used for:

- UPM Agents
- UPM Kiosks
- UPM Signage
- Primary room endpoints
- Backup room endpoints
- Failure/reconnect tests
- Multi-device tests

### Required topology testing

Central and standalone Site must be tested as genuinely independent systems.

Tests must include:

- Disconnecting Site from Central
- Disconnecting Central from WAN
- Reconnecting after extended outage
- Running Central-hosted Site capability
- Comparing standalone and co-located Site behavior
- Client reconnect after server reboot
- Transfer interruption and resume

---

## 36. GPU / Compute Offload

The MS-01 includes an RTX 3050 GPU.

UPM architecture should allow suitable processing tasks to use GPU acceleration when beneficial without making GPU availability a hard runtime dependency.

Potential uses include:

- Local LLM services
- Media analysis
- Thumbnail/image processing
- Future presentation analysis
- Future AI-assisted ingestion

CPU fallback must remain possible for required core functions.

Core PostgreSQL, API, synchronization, worker, and local Site operations take priority over optional AI, LLM, and noncritical accelerated workloads during resource pressure. Future GPU-capable workers may advertise capability requirements, but GPU availability must not be required for core UPM operation.

---

## 37. Testing Strategy

Testing must exist at multiple layers.

### Unit tests

For:

- Domain logic
- Validation
- Data transformation
- Identity matching
- Sync logic
- File metadata logic

### Integration tests

For:

- PostgreSQL
- APIs
- Workers
- File processing
- Transfers
- Authentication
- Caddy routing

### Distributed-system tests

For:

- Central/Site outage
- Reconnect
- Retry
- Duplicate event delivery
- Sync conflicts
- Long disconnection
- Partial transfer
- Restart during transfer

### System tests

For:

- Agent
- Kiosk
- Signage
- Room clients
- Primary/backup workflows
- End-to-end event operations

---

## 38. CI/CD

GitHub Actions should validate the repository automatically.

Initial CI should include:

- Repository structure validation
- Formatting
- Linting
- Unit tests
- Integration tests where feasible
- Docker Compose validation
- Secret scanning
- Dependency/security scanning where practical

Production deployment remains explicitly controlled.

---

## 39. Development Workflow

Normal development should use branches and pull requests.

Recommended flow:

```text
Codex / developer change
        ↓
local automated validation
        ↓
commit
        ↓
push branch
        ↓
GitHub Actions
        ↓
review
        ↓
merge to main
```

`main` should remain stable.

Architecture-changing work must update this specification before or with the implementation change.

---

## 40. Architecture Governance

This document is the architectural source of truth.

When code and this document disagree:

1. Determine whether the architecture has intentionally changed.
2. If architecture changed, update this document explicitly.
3. If architecture did not change, correct the implementation.

Codex and other automated development agents should be instructed to read this document before major work.

Recommended instruction:

```text
Read docs/architecture/UPM_MASTER_ARCHITECTURE.md completely before making changes.

The Master Architecture Specification is authoritative.
Do not introduce architecture that conflicts with it.

If a requested implementation appears to require an architectural change,
stop and explain the conflict before proceeding.
```

---

## 41. Non-Goals and Prohibited Shortcuts

Do not:

- Treat the old SpeakerReady monolith as the new foundation.
- Build Central and Site as one application.
- Let Sites connect directly to Central PostgreSQL.
- Require Central availability for local Site operation.
- Create a temporary SQLite architecture.
- Store important application state only in client machines.
- Use presentation names as identity keys.
- Merge people solely because names match.
- Overwrite originals during conversion.
- Depend on a Windows Central desktop app for administration.
- Introduce automatic disruptive upgrades.
- Make the development desktop a runtime dependency.
- Use a future "v2 redesign" as an excuse for architectural shortcuts.

---

## 42. Implementation Milestone Philosophy

Implementation may be incremental, but the architecture is fixed.

A milestone may implement only a subset of capabilities, but the subset must fit the final architecture.

For example:

- An early milestone may implement only Central identity APIs.
- A later milestone may add Site synchronization.
- Another milestone may add presentation conversion.

Those milestones must use the final service boundaries, identity model, database direction, sync concepts, and deployment strategy.

Milestones add capability. They do not replace the architecture.

---

## 43. Immediate Repository Foundation

The first repository implementation should establish:

- Architecture documentation
- Monorepo folder structure
- Central service boundaries
- Site service boundaries
- Shared contracts directory
- Separate database migration roots
- Infrastructure folders
- Docker Compose skeletons
- Caddy configuration skeletons
- Environment templates
- Bootstrap scripts
- GitHub Actions
- Testing roots

Application business logic should be added only after this foundation is validated.

---

## 44. Final Architectural Objective

UPM should behave as a professional distributed presentation-management platform where:

- Each Site can run an event independently.
- Central provides global control without becoming a single point of operational failure.
- Presentation assets are reliable and traceable.
- People retain durable identity across events.
- Large files transfer safely.
- Clients recover automatically.
- Failures are visible.
- Services have clear boundaries.
- Security is explicit.
- The deployment model is consistent.
- The architecture remains understandable years after initial implementation.

This document governs the implementation until deliberately amended.
