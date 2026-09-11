# UPM Signage Architecture

UPM Signage is an independently deployable Site-local subsystem. `docker-compose.signage.yml` owns its PostgreSQL database, migration history, media volume, API, durable worker, administration web app, browser player, networks, and Caddy edge. It never reads Site or Central PostgreSQL, mounts Site media, or uses SMB.

## Authority and synchronization

Site remains authoritative for Event, room, schedule, cancellation, and public presenter projections. Signage authenticates to the versioned Site projection contract and commits each snapshot generation and cursor transactionally in its own database. An expired cursor or changed source-installation identity causes a full replacement in one transaction. Source failure updates connectivity diagnostics but does not change local playback health or erase the last committed projection.

Signage owns display UUIDs, credentials, layouts, playlists, assignments, timed overrides, publications, and playback telemetry. Room UUIDs are references and never become display identities. Pairing requires the Signage operator password and returns a generated, per-display player credential.

## Playback and outage guarantees

The API schedules room-door content from cached UTC timestamps and ignores tombstoned/cancelled sessions. The browser player bundles all application assets locally, caches its shell with a service worker, and stores the last manifest in IndexedDB. It keeps rendering during transient Site and local API outages. A browser-only endpoint cannot promise recovery after the browser/OS is terminated; deployments requiring unattended restart must supervise kiosk browser startup and retain the Signage stack volumes.

The first native template supports portrait 9:16 and landscape 16:9 room-door signs with Event, room, current session, public presenter names, and next session. Non-event playlist mode is represented independently. Only approved image/video media may enter future asset publication. PowerPoint and PDF are not supported until sandboxed derivative conversion exists.

## Media and publication boundary

`Asset.confirmed_offset`, hash, expected size, and state are the durable resumable-download journal. `Publication.active_manifest` and `previous_manifest` are separate so a future media worker can verify all staged bytes before an atomic database activation and preserve the last-known-good manifest. Media transfer is not complete until that worker and Site byte-range endpoint are implemented; the current release therefore reports native schedule readiness only.
