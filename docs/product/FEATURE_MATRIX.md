# UPM Feature Matrix

**Evidence snapshot:** `codex/site-rooms-operational-workflow` on 2026-08-10. This table describes working repository behavior, not the full target in the [Master Architecture](../architecture/UPM_MASTER_ARCHITECTURE.md).

Status meanings:

- **Implemented:** a working backend/workflow exists and is tested at an appropriate layer.
- **Partial:** a useful working slice exists, but the product workflow is incomplete.
- **Foundation:** models, contracts, deployment boundary, or scaffolding exists without a complete operator workflow.
- **Planned:** documented target with no meaningful implementation yet.
- **Deferred:** explicitly postponed by an accepted decision or current milestone.
- **Unknown / Needs Audit:** repository evidence is insufficient.

| Domain | Capability | Central | Site | Agent | Signage | Status | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Platform | Separate FastAPI/PostgreSQL applications | Yes | Yes | N/A | No | Implemented | Distinct packages, metadata, configuration, migrations, databases, Compose stacks, and boundary tests. |
| Platform | Containerized API/web/worker/sync/Caddy services | Yes | Yes | N/A | No | Implemented | Separate images/services and migration gates exist; Signage stack does not. |
| Database | Independent Alembic histories | Yes | Yes | N/A | No | Implemented | Upgrade paths and isolation are tested; release-specific downgrade support varies by revision. |
| Identity | UUIDv7 internal identity | Yes | Yes | No | No | Implemented | Shared generator/contracts and PostgreSQL UUID columns are tested. |
| People | Permanent person identities | Yes | Projection | No | No | Implemented | Central model/API/import matching and Site event-scoped projection exist. |
| People | Protected person deletion | API + UI | N/A | N/A | N/A | Implemented | Detailed impact, exact-name confirmation, durable cleanup, retained-history removal, progress, and surviving audit evidence. |
| People | Protected bulk person deletion | API + UI | Durable tombstone + snapshot convergence | N/A | N/A | Implemented | Exact `delete all` confirmation queues one durable target snapshot, reuses person cleanup/reference safety, audits results, and republishes affected Event deployments. |
| Events | Operational lifecycle deletion | API + UI | Durable purge projection | N/A | N/A | Implemented | Explicit cleanup preserves Person history and shared resources and sends offline-safe protocol-v1 tombstones. |
| Events | Event create/list/edit | API + UI | Projection | No | No | Implemented | One modal creates and edits names, dates, and IANA timezones; deployed metadata edits automatically publish durable complete snapshots while preserving identity and relationships. |
| Program | Participants, sessions, presentations domain | API + UI | Projection + UI | No | No | Implemented | Normalized backend relationships, migrations, snapshot projection, and read views exist. |
| Program | Direct browser program editing | Read-oriented | Read-only | No | No | Partial | Backend CRUD is broad; browser pages mostly list imported data rather than edit it. |
| Imports | CSV/XLSX stage/review/reconcile/commit | Yes | N/A | No | No | Implemented | Source preservation, validation, identity review, transactional commit, idempotent Session-to-Presentation materialization/repair, UI, and PostgreSQL tests exist. |
| Imports | Arbitrary source-column remapping | No | N/A | No | No | Deferred | Common aliases are detected; unknown vendor-heading editor is not exposed. |
| Imports | Durable worker parsing for large imports | Queue only | N/A | No | No | Deferred | Parsing is synchronous and capped at 25 MiB. |
| Sites | Site enrollment and lifecycle | API + UI | Client state | N/A | N/A | Implemented | Pending approval, credential delivery, revoke/disable/re-enroll APIs, UI approval, and tests exist. |
| Sync | Bidirectional protocol-v1 transport | Yes | Yes | N/A | No | Implemented | Durable sequences, outboxes, receipts, checkpoints, retry, auth, and duplicate handling exist. |
| Sync | Event deployment snapshots | Yes | Yes | N/A | No | Implemented | Immutable complete revisions, skip-ahead, stale rejection, revocation, and status events are tested. |
| Sync | WAN outage/reconnect behavior | Yes | Yes | N/A | No | Implemented | Integration tests cover interruption, queued delivery, duplicate application, and recovery. |
| Rooms | Site room catalog and operational workspace | Visibility via mappings | API + UI | No | No | Implemented | Site can create, edit, enable, archive, list, and open rooms by stable UUID; detail joins mappings, schedule, presentations, media state, and assigned endpoints. |
| Rooms | Imported label to Site room reconciliation | API + UI | API + UI + snapshot apply | No | No | Implemented | Site-authoritative mappings resolve normalized imported labels to physical room UUIDs, safely reuse or materialize Site rooms during deployment, preserve explicit manual remaps/unmaps, reconcile projected sessions, and survive newer snapshots. |
| Rooms | Primary/backup device assignments and readiness | Model only | API + UI | No | No | Partial | Site validates enrolled active devices and maintains authoritative assignment history. No Agent enrollment runtime, heartbeat telemetry, diagnostics, control, or failover exists, so endpoint status is explicitly unavailable. |
| Media | Configurable Site storage targets | Replica model | API/model | No | No | Partial | Model, safe resolution, health API, thresholds, and read-only UI exist; target administration is absent. |
| Media | Streaming Site ingestion/finalization | Metadata only | API/worker state | No | No | Implemented | Hashing, same-target staging, no-replace publication, recovery, cleanup, and tests exist. |
| Media | Entry-linked vs open-file ingestion | Metadata only | API | No | No | Implemented | Distinct categories and optional presentation-version linking are tested. |
| Media | Managed media catalog | Metadata only | API + UI | No | No | Partial | Site lists managed media with presentation/version links, type, size, availability, ingestion, processing, and checksum state; it is read-only. |
| Media | Full server file browser | No | No | No | No | Planned | No filesystem browse/rename/move/copy/delete/search workflow or file actions. |
| Media | Preview/download/print | No | No | No | No | Planned | Metadata/status APIs do not expose file delivery or previews. |
| Conversion | Linked PDF derivative workflow | Models/jobs | Models/jobs | No | No | Foundation | Derivative model and durable job primitives exist; converter/status/UI/retry handler do not. |
| Jobs | Durable processing/transfer/outbox queues | Yes | Yes | N/A | No | Implemented | Claim, lease, heartbeat, retry, priority, capability, idempotency, and isolation are tested. |
| Jobs | Operator queue/job console | No | No | No | No | Planned | Logs and direct database inspection are the current diagnostic path. |
| Transfers | Resumable verified media transfer | Job model | Job model | No | No | Foundation | TransferJob persistence exists; protocol and execution handlers do not. |
| Agent | Windows 11 Agent | N/A | Boundary only | README only | N/A | Planned | No executable client, enrollment, assignment, transfer, diagnostics, or recovery implementation. |
| Room endpoint | Launch/control and primary/backup sync | N/A | Model only | No | N/A | Planned | No room-client runtime or presentation-control protocol. |
| Kiosk | Presenter check-in/upload client | N/A | Boundary only | N/A | N/A | Planned | Directory and requirement boundary only. |
| Signage | Independent Docker service stack | Global target only | Local target only | N/A | README only | Planned | No Signage database, services, runtime, administration, or deployment files. |
| Diagnostics | Agent network diagnostics | Aggregate target | Target UI | No | No | Planned | No iperf/latency/throughput collection or Site history. |
| Authentication | Central administrator login/session/CSRF | Yes | No | No | No | Partial | Secure hashed passwords, opaque sessions, lockout, logout, and tests exist; only administrator role is usable. |
| Authentication | General users and separate native SMB credentials | API + UI | API + UI | N/A | N/A | Partial | Site now authenticates against local scrypt verifier projections with opaque sessions, CSRF, backend RBAC, lockout, logout, and offline operation. Event deployment snapshots carry only Site-scoped Central users and non-plaintext verifier material; projection preserves Site-local users and durably revokes Samba on authorization removal. Broader permission tuning and recovery UI remain incomplete. |
| File sharing | SMB3 shares and Windows discovery | Edge service | Edge service | N/A | N/A | Partial | Independent Samba services enforce SMB3 and role-scoped shares. Media Storage enumerates and conditionally retires Incoming sources; restart-safe durable reconciliation jobs require two stable metadata observations, filter temporary artifacts, idempotently stream Event-scoped files into the existing Central/Site intake and matching workflows with SMB provenance, and retain failed sources for retry. Presentation Media polls for live rows. Managed-view materialization, service settings/health UI, SMB-user audit correlation, and Windows end-to-end validation remain incomplete. |
| Authorization | Administrator/Operator/restricted RBAC | Admin only | No | No | No | Foundation | Role field/permission boundary exists; complete RBAC and Site auth are not implemented. |
| Browser UI | Separate Central and Site production apps | Yes | Yes | N/A | No | Implemented | Shared React source builds into independently deployed images using local APIs. |
| Browser UI | Glass/Classic themes | Yes | Yes | N/A | No | Implemented | Shared semantic theme tokens, persistence, and UI tests exist. |
| Browser UI | Full/Reduced/Off motion | Yes | Yes | N/A | No | Implemented | `prefers-reduced-motion` behavior and persistence are tested. |
| Storage | Backup and restore workflow | No | No | No | No | Planned | Deployment guidance warns about backups; no tested end-to-end backup/restore tooling exists. |
| Observability | Service/worker/queue/device/transfer console | Basic | Room-oriented partial | No | No | Partial | Site dashboard surfaces persisted room, missing/error presentation, failed processing/transfer job, and upcoming-session conditions. Comprehensive service, queue, transfer, and device telemetry remains absent. |
| Deployment | Controlled Central Linux deployment | Yes | Compose only | N/A | No | Partial | Central deployment script exists; equivalent Site production update/backup runbook is incomplete. |
| AI/LLM | Optional GPU-capable work | Queue capability | Queue capability | N/A | No | Foundation | Capability matching supports `gpu`; no AI/LLM or GPU handler is implemented. |
