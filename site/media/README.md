# Site Media

Site media is authoritative and remains available while Central or WAN connectivity is absent.
Browser ingestion, Central-pull receive buffers, committed objects, and Site-to-Central replication
use the deployment-local `site-media-storage` HTTP boundary. Site API and worker containers do not
mount or resolve presentation-media filesystem paths.

Durable database records retain a Media Storage target UUID plus a validated relative key. The
storage service resolves those references beneath only its explicitly configured `/storage/*`
mounts, stages streamed bytes, verifies SHA-256, and commits immutable content-addressed objects.
The Site database remains authoritative for MediaObject, PresentationAsset, processing, transfer,
replication, and readiness state.

Configure physical storage with `UPM_SITE_STAGING_HOST_PATH`, `UPM_SITE_MEDIA_HOST_PATH`, and
`UPM_SITE_TEMP_HOST_PATH`; unset variables retain Docker named-volume development defaults. See
[`docs/deployment/README.md`](../../docs/deployment/README.md) for bind mounts, permissions, and
safe migration of existing volume data.
