# ADR-0012: Deployment-local staging and media storage roots

- **Status:** Accepted
- **Date:** 2026-08-16
- **Scope:** Central and Site persistent filesystem configuration

## Context

Central's importer previously obtained one path only from process environment. A missing mount or
permissions failure therefore produced the generic `Media staging storage is unavailable` error.
Central had no durable storage-root record or shared health service. Site already had authoritative
`StorageTarget` plus logical object keys, but exposed only a minimal capacity table.

## Decision

Every deployment owns persistent staging and media roles. Container defaults are `/data/staging`
and `/data/objects`; the host supplies a durable bind mount or named volume. Central persists role,
filesystem backend, path, enabled state, and successful-check time in its PostgreSQL database. An
import records the staging-root UUID selected when it begins, so changing the active root retains
the location identity of in-flight and reviewable bytes.

Site retains ADR-0002 target identity and ADR-0004 same-filesystem finalization. Its private staging
directory is reported separately while remaining beneath the selected target, preserving atomic
no-replace publication. A future physically separate Site staging target must preserve equivalent
crash-safe publication rather than silently replacing ADR-0004 with a cross-filesystem rename.

Health performs directory, read, exclusive write, fsync, read-back, delete, and capacity checks.
Default warning and critical thresholds are 15 and 5 percent free and are service configuration,
not UI constants. Paths are configuration, not media identity. Central and Site never share storage
or database state.

## Consequences

- API and worker containers mount the same durable deployment-local storage.
- Staging cutover validates before activation and retains the disabled previous root.
- Main-media relocation cannot be a raw path edit; a durable verified copy/cutover is required.
- Host administrators remain responsible for RAID, network mounts, and permissions.
