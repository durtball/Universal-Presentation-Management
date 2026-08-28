# ADR-0017: Native Site Manager and durable Agent control plane

- Status: Accepted
- Date: 2026-08-28

## Decision

UPM Site Manager is a native WinUI 3 Windows operator, file-management, and control application. UPM Agent is a separate presentation-computer product composed of a background service and, for interactive desktop work, a per-user companion joined by authenticated local IPC. Shared Windows libraries do not merge their executable, privilege, or product boundaries.

The only supported remote-control path is **Site Manager → Site → Agent**. Site Manager uses authenticated Site HTTP APIs, never Agent inbound connectivity, Site filesystem paths, direct PostgreSQL access, or SMB. An Agent authenticates an outbound persistent connection or polling fallback to Site. PostgreSQL `device_commands` remain the durable source of truth; a socket is only a delivery optimization and delivery is at least once.

Device online state is derived from heartbeat freshness. Missing telemetry is `unknown`; stale telemetry is `offline`. A persisted unconditional online flag is prohibited.

Presentation review records retain their base `presentation_version_id`. Agent saveback uploads bytes to Site media ingestion, then Site atomically creates a new immutable version only after verified media is available. If the base is no longer latest, Site records a conflict and retains both the newer canonical revision and Agent working copy. Explicit audited conflict resolution may promote the room copy as another revision. Silent replacement or discard is prohibited.

Site Manager owns a SQLite queue only for client profiles, transfer checkpoints, and preferences. It is never authoritative for Site event, room, media, device, command, or review data. Credentials are held by Windows Credential Manager. Site SMB remains optional legacy/operator edge functionality and is not part of Site Manager transfer or control architecture.

## Consequences

Commands and accepted uploads recover across process and network interruption. Agents must deduplicate commands by command/idempotency identity. Interactive PowerPoint launch is delegated to the logged-in-user companion rather than attempted from Windows Session 0. Site remains autonomously operable without Central.
