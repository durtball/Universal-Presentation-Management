# SpeakerReady Lessons for UPM

SpeakerReady is a requirements, operational-workflow, and lessons-learned reference only. UPM is a clean rebuild and must not inherit SpeakerReady's architecture. The [Master Architecture](../architecture/UPM_MASTER_ARCHITECTURE.md) governs all implementation choices.

## Preserve as product behavior

- Entry-based presentation workflow for scheduled program records
- Room-centered event operation
- Presentation version handling and historical traceability
- Clear operational visibility
- Presenter check-in and kiosk workflow
- Primary and backup presentation-computer concepts
- Reliable presentation-file transfer
- Server-managed room assignments
- Explicit session, presenter, and presentation relationships
- Fast operator access to ad hoc/open files alongside roster-linked work

These are workflow outcomes, not permission to copy old code, schemas, network assumptions, or service boundaries.

## Improve in UPM

- Replace the monolithic server design with independently deployable, single-purpose services.
- Keep Central, Site, and Signage deployments and databases explicit and separate.
- Eliminate ambiguous room/signage identity; displays may reference rooms but never become rooms.
- Eliminate agent-authoritative assignments; the server is authoritative and clients only cache for recovery.
- Eliminate the fragile dual-network/bridge mode; UPM targets the venue/customer's existing network.
- Improve large-file reliability with resumable, verified, bandwidth-aware transfer.
- Improve memory use by streaming and handing heavy work to durable workers.
- Improve restart recovery through durable state, leases, idempotency, and reconciliation.
- Improve integrated network, service, worker, media, transfer, and device diagnostics.
- Use PostgreSQL, explicit migrations, UUID identities, constraints, and transactions from the start.
- Use explicit versioned Central/Site synchronization, never WAN database access or ad hoc row replication.
- Preserve original media and model versions and derivatives explicitly.
- Distinguish permanent people from event participation and imported labels from identity.
- Make operational failure and degraded state visible instead of silently masking it.

## Product boundaries learned from operations

- A Site must keep running when Central or WAN connectivity is lost.
- Open-file assets such as walk-in slides, videos, logos, and holding slides must not require fake presenter or session records.
- Primary/backup behavior needs explicit authority and recovery semantics, not filename or workstation conventions.
- Room readiness depends on program, media, transfer, endpoint, and status relationships being traceable end to end.
- Windows 11 networking and security defaults are baseline constraints; insecure legacy SMB is not a foundation.
- Operators need real actions and truthful state. Placeholder buttons and optimistic status are operational hazards.

## Explicitly do not carry forward

- SpeakerReady's monolithic architecture
- Direct or shared database coupling between product roles
- Dual-network or bridge-mode assumptions
- Names, filenames, or room labels as identifiers
- Client-owned assignment truth
- Static-image-only presentation architecture as the permanent runtime
- SMB as the sole critical transfer mechanism
- Disposable implementation milestones that require a later architectural rewrite
