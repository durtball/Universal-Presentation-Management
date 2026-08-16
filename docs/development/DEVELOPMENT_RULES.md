# UPM Development Rules

These rules operationalize the [Master Architecture](../architecture/UPM_MASTER_ARCHITECTURE.md) and root [AGENTS.md](../../AGENTS.md). Accepted [ADRs](../architecture/decisions/README.md) provide detailed decisions.

## Inspect before implementing

1. Read the governing documents and applicable ADRs completely.
2. Inspect backend models, services, migrations, routes, workers, contracts, UI components, tests, and deployment files.
3. Search for an existing capability before adding one. A missing UI is not proof of a missing backend.
4. Confirm domain ownership and authority before choosing where state or behavior belongs.
5. Extend existing components and contracts; do not create duplicate models, APIs, queues, importers, or synchronization paths.

## Preserve system boundaries

- Central and Site remain separate deployments, processes, packages, configuration, databases, migration histories, storage, and lifecycles.
- A co-located Site is the same Site stack used anywhere else; never create a Central-specific Site implementation.
- Site never reads Central PostgreSQL, and Central never reads Site PostgreSQL. Signage and clients never bypass APIs to read Central PostgreSQL.
- A Site remains operational from Site-local data through WAN and Central outages.
- Central-owned and Site-owned state crosses boundaries only through explicit, secure, versioned contracts and established synchronization mechanisms.
- Event/program propagation extends ADR-0007 complete snapshots; do not add ad hoc row replication or a parallel domain channel.
- Rooms and signage displays are separate identity types. Imported strings are not physical-resource identities; automatic Site room materialization must follow ADR-0009 and preserve UUID and manual-override authority.

## Data, identity, and migrations

- Use PostgreSQL. Do not add SQLite as a development, test, or temporary production architecture.
- Use application-generated UUIDv7 identifiers stored as PostgreSQL UUIDs. Names, titles, filenames, labels, and row numbers are not identity.
- Keep permanent people, event participation, sessions, presentations, versions, assets, rooms, devices, and displays distinct.
- Use foreign keys, constraints, transactions, explicit ownership, and restrictive deletion where history or permanent identity requires it.
- Add schema changes through the owning Alembic history. Never modify production schema ad hoc.
- Test upgrade, downgrade where supported, and re-upgrade. Keep Central and Site migration validation independent.
- Never rewrite historical ADRs or migrations merely to make later behavior appear original. Add a superseding decision or migration.

## Jobs, media, transfer, and synchronization

- Use the existing PostgreSQL durable job/outbox design for heavy, retryable, restart-sensitive, or show-critical work.
- Do not use process-local background tasks as the sole record of critical work.
- Make handlers idempotent because lease recovery and delivery are at least once.
- Preserve original media. Derivatives are separate linked records and processing never overwrites source objects.
- Use storage target plus validated relative object key; absolute host paths are deployment configuration, not media identity.
- Keep API processes responsive by streaming large bodies and handing expensive work to workers.
- Transfers must eventually verify identity, size, and hash, expose progress, and recover partial work; do not invent a disposable non-resumable critical protocol.
- Implement media transfer according to ADR-0011: Sites initiate Central pulls and pushes, durable
  contiguous byte offset is the resume contract, and the existing Site credential authenticates
  each separately authorized transfer resource. Never add Central-initiated Site connectivity.

## Server and client authority

- Server state is authoritative for room/device assignment, permissions, presentation association, and operational policy.
- A client may cache the last assignment for recovery but may not overwrite server assignment after reconnect.
- Windows clients must recover from transient network/service failure and remain compatible with Windows 11 security defaults.
- SMB, when used, is authenticated and modern, but never the sole critical transfer mechanism.

## UI and API behavior

- Essential Central and Site functions remain browser accessible and call only their deployment's APIs.
- Glass and Classic share markup and behavior; use theme/CSS layers rather than separate apps.
- Support Full, Reduced, and Off motion and honor `prefers-reduced-motion`.
- Buttons and links must perform real actions. If a capability is unavailable, label it clearly; do not simulate success or use fake rows.
- Loading, empty, degraded, offline, permission, validation, and failure states must be explicit and actionable.
- Keep APIs versioned and contracts language neutral. ORM objects are not transport contracts.

## Security and operations

- Never commit secrets, real `.env` files, private keys, certificates, tokens, credentials, or secret-bearing logs/fixtures.
- Use deployment-scoped configuration, least privilege, rotation, revocation, secure hashing/encryption, and audit where appropriate.
- Keep certificate issuance and TLS termination at the Caddy/infrastructure boundary.
- Avoid disruptive automatic production updates; deployments are controlled and migration gated.
- Do not make the development workstation, current MS-01 paths, or GPU availability a product dependency.
- Core Site/Central work takes priority over optional AI, LLM, or GPU jobs under resource pressure.

## Tests and validation

- Add focused unit tests for domain and validation logic.
- Use PostgreSQL integration tests for persistence, constraints, migrations, queues, and synchronization semantics.
- Add distributed tests for duplicates, retry, outages, reconnection, stale events, partial work, and restart recovery.
- Add system tests as Agent, Kiosk, Signage, room, and end-to-end workflows become real.
- Validate formatting, linting, type checking, tests, Compose configuration, and production builds relevant to the change.
- Documentation-only work still validates Markdown paths/links and checks claims against code.

## Architecture and documentation discipline

- Milestones fill in the fixed target architecture; they do not introduce a disposable "v1" intended for later replacement.
- Stop and report a requested architectural conflict before implementation.
- Significant new decisions require an ADR. Preserve accepted ADR history and mark supersession explicitly.
- Update the Product Requirements, Feature Matrix, Implementation Status, runbooks, and applicable architecture documentation when behavior changes.
- Clearly distinguish implemented behavior, a foundation, and target/planned behavior in every status claim.
