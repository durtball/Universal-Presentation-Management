# UPM Domain and Data Foundation

This document summarizes the first implementation of the decisions in
[ADR-0001](decisions/ADR-0001-backend-persistence-stack.md) and
[ADR-0002](decisions/ADR-0002-site-media-storage.md). The Master Architecture
Specification remains authoritative.

## Backend and dependency workflow

Central and Site server components use Python 3.13, FastAPI, Pydantic v2,
SQLAlchemy 2.x, psycopg 3, Alembic, and PostgreSQL. `uv` manages the workspace
and the committed `uv.lock`; application dependencies must not be installed
globally. Run `uv sync --locked --all-packages` from the repository root.

Central and Site are distinct FastAPI applications, Python packages, metadata
contexts, session factories, database URLs, and Alembic histories. They never
select a role at runtime and neither migration history imports the other.
Pydantic contracts—not ORM objects—are the basis for generated OpenAPI and JSON
Schema consumed by other runtimes.

## Identifiers and identity lifecycle

UPM entity identifiers are application-generated RFC 9562 UUIDv7 values stored
in PostgreSQL native `uuid` columns. Python 3.13 lacks built-in UUIDv7
generation, so the small `uuid6` package supplies it. Names, labels, filenames,
and imported row numbers are never primary identities.

Central owns permanent `Person` records. A person survives event archival and
can have many `EventParticipation` records. Structured names, normalized email/name fields,
identity signals, scoped external identifiers, and administrator-confirmed historical links feed
deterministic exact, strong-candidate, no-match, ambiguous, and conflict classifications.
Display-name equality is never enough to merge people. The implemented staged-import workflow is
documented in [event-program-domain.md](../development/event-program-domain.md).

## Entity relationships

The foundation models Person, Site, Event, EventParticipation, Session,
SessionParticipant, Presentation, PresentationVersion, PresentationAsset,
Room, RoomAssignment, Device, DeviceAssignment, StorageTarget, MediaObject,
TransferJob, ProcessingJob, SyncEvent, and AuditRecord where ownership requires
them. Stable UUID association records permit many presenters and presentations; assignment records
retain room/device flexibility; version and asset records preserve originals
and explicitly link derivatives to sources.

Every Event persists an IANA timezone. Session instants use PostgreSQL `timestamptz`; event-local
rendering uses the Event timezone. Presentation workflow and processing status are deliberately
separate. Presentation/session and presentation/presenter relationships are explicit rather than
inferred from one nullable foreign key or the full session roster.

Person and event relationships use restrictive deletion. Event archival is a
state change, not a cascade into permanent identity. Selected synchronized
records expose revision, timestamps, synchronization state, and deletion state
where tombstones may be needed; these fields are not indiscriminately applied.

## Ownership and synchronization

| Concern | Authority | Other-side representation |
| --- | --- | --- |
| Permanent people and identity signals | Central | Site person projection for offline operations |
| Global Site identity and coordination | Central | Site-local identity/configuration record |
| Event/session/presentation coordination | Central or explicitly owning Site | Same UUID with revision/sync metadata |
| Rooms, devices, and assignments | Site | Future coordinated projection only |
| Storage, media availability, transfer, processing | Site | Central media replica metadata where visibility is needed |
| Sync coordination and audit context | Local database for its responsibility | Explicit events/contracts, never shared ORM/database |

`SyncEvent` provides durable identifiers, source, aggregate, idempotency key,
sequence, retry metadata, and payload. It is a persistence foundation, not a
completed synchronization engine.

## Migrations

Central migrations live only in `database/central/migrations` and use
`UPM_CENTRAL_DATABASE_URL` plus `alembic_version_central`. Site migrations live
only in `database/site/migrations` and use `UPM_SITE_DATABASE_URL` plus
`alembic_version_site`. Each database can be migrated while the other is
unreachable. PostgreSQL—not SQLite—is used for tests involving database
semantics.

## Site media storage

Site is currently authoritative for media. A Site can configure multiple
`StorageTarget` records, with at most one enabled primary target per Site. A
target identifies a host-provided absolute Linux mount, type, enabled/primary
state, health, and configurable free-space warning and critical thresholds.
Capacity totals are runtime observations rather than permanent authoritative
facts.

Media identity never contains an absolute host path. `MediaObject` stores a
`storage_target_id` and canonical relative `object_key`; the storage subsystem
will resolve that pair against the configured root. This supports moving media
without rewriting logical identities. It does not pool disks or implement RAID,
partitioning, formatting, or filesystem management.

Health vocabulary covers unknown, healthy, warning, critical, unavailable,
read-only, and write-failure states. Full probing, admission checks, transfer
policies, archive/migration policies, and administration UI are deferred.

## Hardware and optional acceleration

The architecture supports the current MS-01 and independent Site laptop without
depending on the Windows development workstation. It assumes neither a fixed
disk path nor a 50 GB event ceiling. Host administrators can add or remount
storage targets without changing media identity.

Core PostgreSQL, API, synchronization, worker, and local Site operations take
priority over optional compute. GPU availability is never required. Future
processing jobs can advertise a capability requirement and be routed to a
separate GPU-capable worker; no scheduler or AI/LLM feature is implemented here.
