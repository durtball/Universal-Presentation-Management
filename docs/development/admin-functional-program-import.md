# Central Admin authentication and functional program import

## Implementation audit

This milestone audited `main` at `f102ac2` before implementation. The existing architecture was
reused as follows.

| Capability | Existing implementation reused | Browser gap addressed |
| --- | --- | --- |
| Events and Sites | `Event`, `Site`, enrollment and `/api/v1/admin/events`, `/sites` APIs | Event creation, Site selection, deployment status and publish controls |
| Permanent identities | `Person`, identity signals/links, external identifiers, `EventParticipation` | Presenter email, organization, permanent UUID and session relationships |
| Sessions and presentations | Normalized `Session`, `Presentation`, participant/session/presenter association tables | Persisted list pages now expose useful relationships rather than fake rows |
| CSV/XLSX ingestion | `ImportSource`, `ImportBatch`, `ImportRow`, issues, decisions and `imports.py` | Upload, header/mapping review, samples, validation, reconciliation, preview and commit |
| Identity reconciliation | UUID, external ID and unique-email exact matching; name-only ambiguity rules | Candidate selection, create-new and reject actions are operator accessible |
| Source lineage | Original bytes, SHA-256, raw/normalized/corrected row values and committed UUIDs | Full staged rows and issues are visible in Import Review |
| Room handling | Imported `Session.location_name`; Site-owned `Room` and `RoomAssignment` | Per-Site reusable mapping records and Site-local assignment/conflict projection |
| Deployment | ADR-0007 complete immutable snapshots on protocol-v1 outbox | Event detail publishes through the existing deployment service |
| Site program | Site-local event/person/session/presentation projections | Room mapping status and physical room display added; no live Central read |
| Authentication | Machine/Site credentials and a legacy automation admin header only | Human users, hashed passwords, opaque sessions, CSRF, login, logout and route guards |

The audit found no reason to create a second importer or synchronization protocol. The durable-job
foundation remains available for a future large-import handoff; synchronous imports remain capped at
25 MiB.

## Administrator authentication

On first Central API use after migration, Central creates one administrator only when the
`admin_users` table is empty. Development defaults are:

- Username: `admin`
- Password: `admin`

Passwords use salted `scrypt` hashes. Browser session tokens are random, stored only as SHA-256
digests in PostgreSQL, and delivered in `HttpOnly`, `SameSite=Lax` cookies. Unsafe administrator API
requests also require the per-session CSRF token. Five consecutive failures lock the account for
five minutes. Logout revokes the database session before clearing the cookie.

Set `UPM_CENTRAL_BOOTSTRAP_ADMIN_USERNAME` and
`UPM_CENTRAL_BOOTSTRAP_ADMIN_PASSWORD` before the first production startup. The bootstrap never
resets an existing user. The current milestone has no user-management/password-change UI, but an
authenticated administrator can change the password through the API. `admin/admin` is a development
credential and must not be used in production. Use `POST /api/v1/auth/password`, the current session's
`X-CSRF-Token`, and JSON `{"current_password":"...","new_password":"..."}`. New passwords must
be at least 12 characters. A successful change re-hashes the password and revokes the user's other
sessions.

`UPM_CENTRAL_ADMIN_TOKEN` remains a non-browser compatibility credential for automation and existing
integration clients. The React interface no longer stores or accepts it.

## Program import workflow

1. Create or select an Event in Central.
2. Open **Imports**, choose the Event, and upload UTF-8 CSV or XLSX.
3. Central stores the source bytes and digest, parses on the server, and persists every source row.
4. Review detected source headers, normalized field mapping, sample rows and preview counts.
5. Review row-level errors/warnings. Ambiguous identities can match a candidate permanent person,
   create a distinct person, or reject the row. Names alone never cause a merge.
6. Commit when blocking conflicts are resolved. Commit is transactional and idempotently returns an
   already-committed batch for a duplicate request.
7. Review persisted Presenters, Sessions and Presentations. Their relationship UUIDs and source
   batch lineage remain in PostgreSQL.

The importer recognizes common aliases such as `first_name`, `last_name`, `company`, `start`, `end`,
`room`, `location`, `session`, and `presentation`. Unsupported files, invalid workbooks, invalid
schedules, missing required fields, unresolved references and duplicate identifiers produce
operator-visible errors. Files over 25 MiB receive HTTP 413; they are not silently truncated.

Production roster-shaped workbooks also recognize Presentation/Session IDs, Presenter/Speaker/Person
IDs, split names, professional title, role and roster order, event-local date/start/end, track,
format, and room aliases through the same centralized vocabulary. A Presentation ID in this schema
groups roster rows into one Session and materializes one Event-scoped canonical `Presentation`
linked to that Session when no explicit Presentation row already supplies one. The imported code is
the stable Presentation identity, and Session presenter relationships are mirrored to
`PresentationPresenter`. Repeated rows and repeated commits reuse those UUID-backed records. Every
populated worksheet row remains an `ImportRow`, including its original
headers/values, worksheet, row number, canonical values, reconciliation state, issues, and committed
Person/Session/relationship lineage. Repeated presenter identifiers are valid identity evidence;
conflicting authoritative values on a repeated program identifier block commit without deleting
either row. Blank trailing XLSX columns and completely blank rows do not participate in review.

Schedule normalization retains the source Date, Start Time, and End Time cells while interpreting
their canonical values separately. Native Excel date/time cells, ISO values, US month/day/year
dates, 12-hour AM/PM times, and 24-hour times converge to ISO date/time values. Only the Event's IANA
timezone is applied when those local values are combined into the timezone-aware Session schedule;
Excel values never supply or imply timezone authority. Invalid values remain attached to their
source row as field-specific blocking validation issues.

Commit validates that every imported presentation-bearing Session resolves to an assignable
Presentation. A failure is reported as a program-materialization conflict rather than silently
leaving an unusable Session. For Events imported before this invariant, re-running media matching
or calling `POST /api/v1/admin/events/{event_id}/presentation-materialization` idempotently repairs
missing Presentations and presenter/session links; no Event deletion or media re-upload is needed.

## Room reconciliation

Imported `room`, `room_name` or `location` values remain logical labels on Central sessions. Central
does not create physical Site rooms from these strings.

The **Room Mapping** page scopes optional operator overrides by Site. A confirmed mapping records an
existing Site-owned room UUID and label and may differ between Sites. It is not a prerequisite for
deployment: ADR-0009 automatically reuses a deterministic Site-room match or creates an Event-owned
UUID-backed room when no match exists. Ambiguous normalized matches remain unresolved rather than
being guessed, and explicit operator mappings/unmaps remain authoritative on redeployment.

Sites expose `GET /api/v1/rooms` and `POST /api/v1/rooms` for their local room catalog. A deployment
snapshot carries mappings for its destination Site. During apply, the Site materializes missing
rooms and creates event-scoped mappings and authoritative `RoomAssignment` rows transactionally.
Stable mappings and room UUIDs are reused across complete-snapshot revisions.

## Deployment and offline behavior

The Event detail **Deploy to Site** action calls the existing ADR-0007 deployment API. Central builds
an immutable complete snapshot and sends it through the existing per-Site durable outbox. Site sync
applies the snapshot transactionally to Site PostgreSQL and reports applied revision and summary
counts through the existing Site-owned status event.

`GET /api/v1/events/{event_id}/program` reads only the Site database. The Site UI therefore continues
to show the last applied event, presenters, sessions, presentations and room state while Central or
the WAN is unavailable.

## Browser/API endpoints

- `POST /api/v1/auth/login`
- `GET /api/v1/auth/session`
- `POST /api/v1/auth/logout`
- `POST /api/v1/auth/password`
- `POST|GET /api/v1/admin/events`
- `GET /api/v1/admin/events/{event_id}/participants|sessions|presentations|imports`
- `GET /api/v1/admin/imports/{batch_id}`
- `POST /api/v1/admin/import-rows/{row_id}/decision`
- `POST /api/v1/admin/imports/{batch_id}/commit`
- `GET /api/v1/admin/events/{event_id}/room-mappings?site_id=...`
- `PUT /api/v1/admin/room-mappings`
- `POST /api/v1/admin/events/{event_id}/deployments`
- `GET /api/v1/admin/events/{event_id}/deployment-preview?site_id=...`
- `POST /api/v1/admin/event-deployments/{deployment_id}/push|retry|revoke`
- `GET|POST /api/v1/rooms` (Site-local)
- `GET /api/v1/events/{event_id}/program` (Site-local)

## Troubleshooting

- **Login rejected:** verify bootstrap environment values and check the five-minute lockout window.
- **Import remains Review:** open row issues and resolve every blocking identity or validation error.
- **Commit says program changed:** another operation advanced the Event revision; upload/review again
  so stale reconciliation decisions cannot overwrite newer data.
- **Room remains unmapped:** create/identify the room at the Site, then save its exact UUID in the
  Central mapping and push a newer deployment revision.
- **Deployment failed:** inspect Central deployment failure text and Site sync status, correct the
  cause, then use the existing retry action.

## Current limitations

- Column mapping is automatically detected and reviewable; arbitrary operator-defined remapping of
  unknown vendor headings is not yet exposed as a batch-level editor. Corrected row values remain
  supported by the existing reconciliation API.
- Synchronous parsing remains limited to 25 MiB; durable worker handoff is deferred.
- Site human authentication is not introduced in this milestone. The Central mechanism is designed
  for reuse when Site administrative authentication is implemented.
- Room creation is Site-local through its API; Central intentionally does not create physical rooms
  across the distributed boundary.
