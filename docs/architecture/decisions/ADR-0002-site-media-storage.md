# ADR-0002: Site-Authoritative Configurable Media Storage

- **Status:** Accepted
- **Date:** 2026-08-10
- **Scope:** Site media configuration, logical media location, and storage health foundation

## Context

Each UPM Site must operate independently during Central or WAN outages and therefore owns the authoritative media needed for local operations. Deployments vary from one local disk to multiple host-mounted filesystems or future mounted network/archive targets. Container filesystems and anonymous volumes are not stable authoritative storage locations.

The Linux host or infrastructure platform already owns physical storage concerns such as partitioning, formatting, filesystem creation, mounting, and RAID. UPM must not duplicate those responsibilities.

## Decision

UPM Site introduces a Site-owned `StorageTarget` identified by a globally unique `storage_target_id`.

A storage target records configuration including:

- owning `site_id`
- administrator-facing display name
- storage type
- host-provided root/mount path exposed to Site services
- enabled state and primary-media designation
- current configured health state
- configurable warning and critical free-space thresholds
- creation and update timestamps

Every Site may configure multiple targets. UPM does not automatically pool them, implement RAID, or invent overflow/placement policy. Future archive, migration, replication, and alternate-storage policies must be explicit decisions.

## Logical media location

Absolute operating-system paths are not media identity. A `MediaObject` location consists of:

- `storage_target_id`
- a validated relative logical `object_key`

The storage subsystem resolves the object key beneath the configured target root. Object keys cannot be absolute paths or contain traversal segments. This permits storage migration or mount-path changes without changing every media object's logical identity.

Logical categories include presentations, versions, open files, derivatives, PDF derivatives, previews, thumbnails, signage, ingestion staging, temporary processing, and archive. Categories organize object keys and do not require separate physical disks.

## Originals and derivatives

Original presentation assets are preserved. A derivative is a distinct asset/media record that explicitly references its source asset or source media object. Processing never overwrites the original object.

## Storage health and capacity

Configured health state and thresholds belong to the storage-target domain. Capacity totals, used bytes, and free bytes are runtime observations, not permanently authoritative configuration facts.

The model supports future reporting of:

- available, warning, critical, unavailable, read-only, and unknown health
- missing mounts and write failures
- total, used, and available capacity observations
- per-target warning and critical thresholds
- pre-transfer capacity and safety-threshold checks

This ADR establishes the domain/configuration foundation only; it does not implement the monitoring UI, transfer admission logic, filesystem management, RAID, or automatic storage pooling.

## Deployment consequences

- Site media configuration uses host-provided bind mounts or equivalent explicit mounts.
- No current hardware path or disk size is hard-coded.
- The reusable Site stack works on a standalone Site appliance or alongside Central with its own configuration and media storage.
- Current event usage around 50 GB informs testing but is not treated as a product limit.

## Resource priority and GPU capability

PostgreSQL, APIs, synchronization, core workers, and local Site operations take priority over optional AI, LLM, or noncritical accelerated processing. GPU availability is optional. Future GPU-capable workers may advertise capabilities, but core UPM operation must retain CPU-compatible behavior and must not depend on the RTX 3050 or any GPU.
