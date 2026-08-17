# UPM Product Requirements

This document organizes known product requirements by domain. The [Master Architecture](../architecture/UPM_MASTER_ARCHITECTURE.md) remains authoritative for technical boundaries, while the [Feature Matrix](FEATURE_MATRIX.md) and [Implementation Status](../development/IMPLEMENTATION_STATUS.md) describe what currently exists.

Requirement labels:

- **AP — Architectural principle:** binding target-system constraint.
- **ER — Existing requirement:** required product behavior, whether or not complete.
- **FP — Future planned capability:** intended direction that is not yet a completed workflow.

## Central

- **AP:** Central is the Linux/Docker/PostgreSQL global control plane, browser administered and fronted by Caddy.
- **ER:** Manage events, permanent people, Sites, deployment intent, global synchronization state, health, administration, and global signage concerns.
- **AP:** A co-located Site uses the unchanged reusable Site stack, with separate database, storage, configuration, services, and lifecycle.
- **AP:** Central availability must not be required for routine Site event operation.

## Site

- **AP:** Each production Site is a locally autonomous Linux/Docker/PostgreSQL deployment with authoritative local operational media.
- **ER:** Run local APIs, workers, sync, room/device coordination, kiosk coordination, signage coordination, and browser administration.
- **ER:** Queue disconnected work and recover safely after Central, WAN, process, or host interruption.
- **AP:** Site must not connect to Central PostgreSQL or depend on live Central reads during an active show.

## Events

- **ER:** Events use stable UUID identity, IANA timezone, lifecycle state, metadata, and explicit Site deployments.
- **ER:** Central deploys versioned complete event/program snapshots independently per Site.
- **FP:** Event archival and retention workflows must preserve required history and related permanent identities.

## People

- **ER:** Central owns permanent person UUIDs that persist across events.
- **ER:** Matching uses strong evidence; display-name equality alone never merges people.
- **ER:** Event participation references a permanent person and stores event-specific attributes.
- **ER:** Protected deletion requires exact confirmation, dependency impact, authorization, and audit behavior.
- **FP:** Rich administrator identity review, merge, split, and deletion interfaces.

## Program imports

- **ER:** CSV/XLSX import is staged, validated, reconciled, previewed, approved where necessary, transactionally committed, and audited.
- **ER:** Preserve source bytes, raw rows, normalized/corrected values, issues, decisions, lineage, and stable external identifiers.
- **ER:** Detect duplicates and prevent stale review decisions from overwriting newer program state.
- **ER:** Every imported presentation-bearing program item materializes an idempotent Event-scoped
  Presentation linked to its Session and presenters so media can resolve to PresentationVersion.
- **FP:** Operator-defined mapping for unknown vendor headings and durable worker handoff for large imports.

## Presenters

- **ER:** Presenters are event participation roles linked to permanent people, not duplicate person records.
- **ER:** Session and presentation presenter relationships are explicit, ordered where needed, and UUID based.
- **FP:** Complete presenter administration, communication, and operational status workflows.

## Sessions

- **ER:** Sessions belong to events, use timezone-aware instants, and can have multiple presenters and presentations.
- **ER:** Session room labels from imports remain labels until deliberately reconciled to a Site room.
- **FP:** Full browser create/edit/reschedule and operational session-state workflows.

## Presentations

- **ER:** A logical presentation is distinct from versions, assets, files, sessions, and presenters.
- **ER:** Keep workflow state separate from processing state and retain source lineage.
- **ER:** Support both entry-linked presentations and a visibly distinct open-file workflow.
- **FP:** Room delivery, launch/control, primary/backup coordination, and runtime status.

## Rooms

- **AP:** Rooms use stable UUIDs and remain distinct from signage displays and imported labels.
- **ER:** Room/device assignment is server authoritative; reconnecting Agents may not overwrite it.
- **ER:** Deployment materializes unmapped imported locations as UUID-identified Site rooms, reuses only deterministic matches, and preserves manual remaps/unmaps.
- **ER:** Support `Room -> Sessions -> Presentations -> Media -> Agents/Endpoints -> Status` traceability.
- **FP:** Complete room readiness, endpoint assignment, monitoring, and operator control workflows.

## Media and file management

- **AP:** Site is authoritative for its operational presentation media.
- **ER:** Represent media by storage target plus validated logical object key, not arbitrary host path identity.
- **ER:** Preserve originals; record versions, hashes, metadata, availability, derivatives, processing, and transfer state.
- **ER:** Provide guarded browse, upload, rename, move, copy, delete, search, metadata, linking, and open-file operations.
- **FP:** Full media browser, lifecycle controls, previews, and operational transfer views.

## Agents and room endpoints

- **ER:** Windows 11 clients securely discover and authenticate to the correct Site and receive server-authoritative assignments.
- **ER:** Recover after Site restart and network interruption, verify local media, and expose connection/diagnostic state.
- **ER:** Support resumable presentation transfer and optional primary/backup synchronized behavior.
- **FP:** Presentation launch/control, shared state, safe desynchronization recovery, and room-operator controls.

## Kiosks

- **ER:** Windows 11 kiosks connect to local Site data and remain functional during Central outages.
- **ER:** Support global branding defaults plus explicit Speaker Upload, Speaker Kiosk, and Signage Kiosk overrides.
- **FP:** Presenter check-in, upload, branding administration, and kiosk fleet management.

## Signage

- **AP:** Signage is an independently deployable Docker stack, separate from Central and core Site, with Site-local PostgreSQL state and media.
- **ER:** Continue operating during Central or main Site API outages and never connect directly to Central PostgreSQL.
- **ER:** Model displays, assignments, templates, playlists, schedules, assets, overrides, devices/status, and render state separately from presentation operations.
- **ER:** Central owns global deployment/visibility; Site owns local execution, assignments, temporary overrides, and health.
- **FP:** Time-, session-, room-, and schedule-driven playback, redundancy, and multi-site administration.

## Synchronization

- **AP:** Use secure HTTPS, mTLS where appropriate, versioned language-neutral contracts, UUIDs, durable outboxes, idempotency, receipts, and checkpoints.
- **ER:** Sites push Site-owned events and poll for Central-owned events; timestamps are not cursors.
- **ER:** Program propagation follows ADR-0007 complete deployment snapshots and supports long outage recovery.
- **AP:** No direct database access, arbitrary row replication, or parallel domain-specific sync path.

## Networking and diagnostics

- **AP:** UPM is an Existing Network product for corporate, hotel, production VLAN, DHCP, static, private, and routed networks.
- **ER:** Tolerate address changes, DNS delay, temporary outages, and reconnect without fragile one-time discovery.
- **ER:** Agents report latency, upload/download throughput, iperf results where available, packet loss/retransmission where available, interface/IPs, timestamp, and pass/warn/fail summary.
- **FP:** Site browser diagnostics history and practical Site-to-Agent network tests.

## Authentication and security

- **ER:** Use secure enrollment, revocable per-device/Site trust, rotation, expiration, and mTLS where appropriate.
- **ER:** Role-based permissions distinguish Administrator, Operator, and restricted operational users.
- **ER:** Secrets are deployment-specific, never committed, and delivered through secure configuration mechanisms.
- **ER:** Audit meaningful identity, deletion, assignment, enrollment, configuration, permission, sync, and import actions.

## Administration

- **AP:** Essential Central and Site administration remains browser based; an optional Windows client is never required infrastructure.
- **ER:** Show truthful health, degraded/offline/synchronizing state, actionable errors, and real controls.
- **FP:** Complete operator/admin permissions, configuration, audit, worker, queue, transfer, and device consoles.

## Ingestion

- **ER:** Architecture supports browser upload, Agent upload, file-browser/open-file import, external program imports, manual workflows, inbound email attachments, and AirDrop-compatible ingestion.
- **ER:** Email ingestion must validate sender/destination/file type, restrict attachments, preserve audit, and expose malware-processing hooks.
- **AP:** Do not hard-code the ingestion architecture around one method.

## Backup and recovery

- **ER:** Central and every Site have independently testable backup and restore procedures covering PostgreSQL, configuration, required media/metadata, identity, audit, and securely handled certificates/configuration.
- **ER:** Recovery accounts for migration versions and Sites remain independently recoverable.
- **FP:** Operator-visible backup preparation, verification, retention, and restore rehearsal workflows.

## Presentation runtime

- **ER:** Preserve high fidelity where technically possible for PowerPoint, Google Slides, Canva, Figma Slides, Keynote, and PDF.
- **ER:** Architecture must retain transitions, builds, timed/click reveals, video, audio, sequence, and timing; static images are only an explicit fallback.
- **ER:** Server conversion preserves originals and creates linked, retryable PDF derivatives with status/errors, preview, download, print, and room delivery.
- **FP:** Runtime adapters and high-fidelity playback/control implementations.

## Jobs, transfers, and observability

- **AP:** Heavy and restart-sensitive work uses isolated Central/Site PostgreSQL durable queues, leases, heartbeats, retries, idempotency, priorities, and capability matching.
- **ER:** Jobs expose identity, progress, policy, errors, cancellation where safe, timestamps, worker, and operator-visible status.
- **ER:** Large transfers support resume/chunking where appropriate, hash/size verification, retry, concurrency control, interruption recovery, progress, and network-saturation awareness.
- **ER:** Observe service/database/worker health, queue depth, sync lag, device status, storage, transfers, processing/conversion, and security failures.

## Deployment, hardware, and updates

- **AP:** Central and Site are separated container services fronted by Caddy, with version-matched migration gates and controlled manual production updates.
- **ER:** Use public ACME where customer DNS permits and private/internal CA approaches for local deployments.
- **AP:** The development desktop and RTX 3050 are never hard runtime dependencies; required core work has a CPU path and resource priority over optional AI/LLM work.
- **FP:** Capability-advertising GPU workers for suitable media/AI processing while preserving resource headroom.

## UI/UX

- **ER:** UPM Glass and UPM Classic use the same HTML/functionality through theme layers.
- **ER:** Motion supports Full, Reduced, and Off, honors `prefers-reduced-motion`, and avoids constant decoration.
- **ER:** Interfaces remain high-signal, keyboard accessible, status-readable without color alone, and honest about incomplete capabilities.
