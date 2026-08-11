# UPM Feature Matrix

**Evidence snapshot:** `codex/project-knowledge-foundation`, based on implementation through commit `eb4f0e1` (2026-08-10). This table describes working repository behavior, not the full target in the [Master Architecture](../architecture/UPM_MASTER_ARCHITECTURE.md).

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
| People | Protected person deletion | API | N/A | N/A | N/A | Partial | Dependency-impact and guarded delete API exist; browser deletion workflow does not. |
| Events | Event create/list/edit | API + UI | Projection | No | No | Partial | Central create/list UI and API edits exist; full lifecycle administration is incomplete. |
| Program | Participants, sessions, presentations domain | API + UI | Projection + UI | No | No | Implemented | Normalized backend relationships, migrations, snapshot projection, and read views exist. |
| Program | Direct browser program editing | Read-oriented | Read-only | No | No | Partial | Backend CRUD is broad; browser pages mostly list imported data rather than edit it. |
| Imports | CSV/XLSX stage/review/reconcile/commit | Yes | N/A | No | No | Implemented | Source preservation, validation, identity review, transactional commit, UI, and PostgreSQL tests exist. |
| Imports | Arbitrary source-column remapping | No | N/A | No | No | Deferred | Common aliases are detected; unknown vendor-heading editor is not exposed. |
| Imports | Durable worker parsing for large imports | Queue only | N/A | No | No | Deferred | Parsing is synchronous and capped at 25 MiB. |
| Sites | Site enrollment and lifecycle | API + UI | Client state | N/A | N/A | Implemented | Pending approval, credential delivery, revoke/disable/re-enroll APIs, UI approval, and tests exist. |
| Sync | Bidirectional protocol-v1 transport | Yes | Yes | N/A | No | Implemented | Durable sequences, outboxes, receipts, checkpoints, retry, auth, and duplicate handling exist. |
| Sync | Event deployment snapshots | Yes | Yes | N/A | No | Implemented | Immutable complete revisions, skip-ahead, stale rejection, revocation, and status events are tested. |
| Sync | WAN outage/reconnect behavior | Yes | Yes | N/A | No | Implemented | Integration tests cover interruption, queued delivery, duplicate application, and recovery. |
| Rooms | Site room catalog | Visibility via mappings | API + UI | No | No | Partial | Site can create/list rooms; broader room operations and readiness do not exist. |
| Rooms | Imported label to Site room reconciliation | API + UI | Snapshot apply | No | No | Implemented | Per-Site mappings preserve Site authority and expose unmapped/conflict states. |
| Rooms | Device assignments and readiness | Model only | Model only | No | No | Foundation | Models exist; enrollment/control/status workflow is not implemented. |
| Media | Configurable Site storage targets | Replica model | API/model | No | No | Partial | Model, safe resolution, health API, thresholds, and read-only UI exist; target administration is absent. |
| Media | Streaming Site ingestion/finalization | Metadata only | API/worker state | No | No | Implemented | Hashing, same-target staging, no-replace publication, recovery, cleanup, and tests exist. |
| Media | Entry-linked vs open-file ingestion | Metadata only | API | No | No | Implemented | Distinct categories and optional presentation-version linking are tested. |
| Media | Full server file browser | No | No | No | No | Planned | No browse/rename/move/copy/delete/search workflow or media listing UI. |
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
| Authorization | Administrator/Operator/restricted RBAC | Admin only | No | No | No | Foundation | Role field/permission boundary exists; complete RBAC and Site auth are not implemented. |
| Browser UI | Separate Central and Site production apps | Yes | Yes | N/A | No | Implemented | Shared React source builds into independently deployed images using local APIs. |
| Browser UI | Glass/Classic themes | Yes | Yes | N/A | No | Implemented | Shared semantic theme tokens, persistence, and UI tests exist. |
| Browser UI | Full/Reduced/Off motion | Yes | Yes | N/A | No | Implemented | `prefers-reduced-motion` behavior and persistence are tested. |
| Storage | Backup and restore workflow | No | No | No | No | Planned | Deployment guidance warns about backups; no tested end-to-end backup/restore tooling exists. |
| Observability | Service/worker/queue/device/transfer console | Basic | Basic | No | No | Foundation | Health and some sync/storage state exist; comprehensive operational observability does not. |
| Deployment | Controlled Central Linux deployment | Yes | Compose only | N/A | No | Partial | Central deployment script exists; equivalent Site production update/backup runbook is incomplete. |
| AI/LLM | Optional GPU-capable work | Queue capability | Queue capability | N/A | No | Foundation | Capability matching supports `gpu`; no AI/LLM or GPU handler is implemented. |
