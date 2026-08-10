# Site media ingestion

The Site is authoritative for its local files. A database `StorageTarget.root_path` names a filesystem root already mounted into the API and worker containers. Physical disk setup, mounting, RAID, and pooling remain host responsibilities.

## Configuration

Bind the host media directory with `SITE_MEDIA_HOST_PATH` and expose it inside every Site media process at `UPM_SITE_MEDIA_MOUNT_PATH`. Configure an enabled primary `StorageTarget` whose `root_path` equals that container-visible path. Each target has warning, critical, and safety-reserve byte thresholds. Runtime capacity observations are returned by the API and are not stored as permanent truth.

`UPM_SITE_MAX_UPLOAD_BYTES` is an admission ceiling, not an expected show size. The default is 512 GiB. Reverse proxies and infrastructure must permit the intended request size. `UPM_SITE_STAGING_MAX_AGE_SECONDS` controls `python -m upm_site.media.cleanup`; it marks abandoned staging records failed, removes only old inactive artifacts, and preserves recent staging plus all finalizing records for reconciliation.

## Lifecycle and layout

`POST /api/v1/media/ingestions` accepts the raw file body and streams it in bounded ASGI chunks without multipart pre-buffering. `site_id`, category, optional expected size, and optional presentation link are query parameters. `X-UPM-Original-Filename` is required; `Idempotency-Key` is optional. The original filename is validated and retained only as metadata. The generated object key has this shape:

```text
open-files/2026/08/<media UUID>
presentation-versions/2026/08/<media UUID>
```

Staging is `<target>/.ingestion-staging/<media UUID>.upload`. SHA-256 and byte count are computed during the stream. Finalization never overwrites an existing authoritative path. Media becomes available and its `media.inspect` ProcessingJob is enqueued together only after the final path exists.

Open-file and signage uploads do not require Presentation records. A presentation upload supplies `presentation_version_id` and receives an original `PresentationAsset` link. Equal hashes remain separate MediaObjects.

See [ADR-0004](../../docs/architecture/decisions/ADR-0004-site-media-ingestion-finalization.md) for recovery windows and state transitions.

## Local inspection and API testing

Use metadata and health APIs instead of exposing filesystem browsing:

- `GET /api/v1/media/{media_object_id}`
- `GET /api/v1/media/{media_object_id}/status`
- `GET /api/v1/storage-targets/health`

For local Compose testing, migrate the Site database, configure a Site and primary StorageTarget, then run `scripts/site-media-smoke.py` inside the API container. It uploads a real PDF signature, retrieves metadata/status, and checks target health. Administrators inspecting the mount directly should treat generated paths as application-owned and must not rename or replace authoritative files.

Full resumable upload/chunk negotiation is deferred to a separate transfer-protocol decision. Current idempotency and availability semantics are intended to remain unchanged.
