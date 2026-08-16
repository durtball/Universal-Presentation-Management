# Event deployment synchronization

Central administrators manage events at `/admin/events`. The authenticated APIs support creating and
editing an Event, previewing and deploying it independently to active Sites, explicitly pushing a
program revision, retrying a failed application, and non-destructive revocation. Event metadata
edits (name, dates, or IANA timezone) atomically create a new complete snapshot revision for every
active deployment and enqueue it through protocol v1; an offline Site catches up without blocking
the edit. Other program edits advance the Central Event revision and display **Update available**;
the operator reviews and explicitly deploys those broader program changes.

Event types are:

- `central.event_deployment.requested`
- `central.event_deployment.updated`
- `central.event_deployment.revoked`
- `site.event_deployment.applied`
- `site.event_deployment.failed`
- `site.event_deployment.stale`
- `site.event_deployment.revoked`

The Site polls these through the existing `/api/v1/sync/central-events` endpoint. It validates the
protocol, schema version, authenticated local Site UUID, deployment UUID, Event UUID, references, and
revision before applying. `/admin/event-deployments` reads only Site-local data and remains useful
while Central is offline.

## Room materialization and reconciliation

During transactional snapshot application, Site automatically materializes program locations that
have neither a Central mapped-room instruction nor an existing Site-owned mapping. It reuses an exact
or uniquely normalized Site room match; otherwise it creates a new UUIDv7 Room and event-scoped
program-room mapping. Ambiguous normalized matches remain unresolved. Existing mappings, including
deliberate operator unmaps, are never overwritten by later snapshots. See
[ADR-0009](../architecture/decisions/ADR-0009-site-room-materialization.md).

`GET /api/v1/admin/events/{event_id}/deployment-preview?site_id=...` summarizes committed rooms,
sessions, presenters, and presentations and reports non-blocking relationship warnings plus blocking
mapping conflicts. Deployment consumes the committed Event Program state, never the source workbook.
The Site Rooms catalog identifies UUID-backed rooms created for an Event deployment separately from
reusable Site infrastructure.

## Revision and recovery semantics

Every payload is a complete snapshot. Equal revisions are duplicate-safe, lower revisions are stale,
and any newer revision may be applied even when intermediate revisions were missed. Central retains
each generated revision; Site retains each applied revision plus its current snapshot. Site status
events eventually make `desired_revision == applied_revision` at Central.

Transport retry remains bounded by the existing outbox `max_attempts`, lease recovery, and retry
delay. A failed Site application preserves its preceding valid projection and sends a durable failure
report. Use the Central Retry action after correcting the cause; it retransmits the same desired
revision with a new envelope identity.

## Troubleshooting and validation

```powershell
docker compose -f docker-compose.central.yml ps
docker compose -f docker-compose.site.yml ps
curl.exe -fsS http://127.0.0.1:8080/health
curl.exe -fsS http://127.0.0.1:9080/health
curl.exe -fsS http://127.0.0.1:9080/api/v1/event-deployments
docker compose -f docker-compose.central.yml logs --no-color central-api central-sync
docker compose -f docker-compose.site.yml logs --no-color site-api site-sync
```

At Central, compare outbound sequence with the Site-acknowledged checkpoint and desired revision with
applied revision. At Site, inspect the locally persisted failure reason and Central connection state.
Do not repair queue or deployment state with ad hoc SQL.
