# ADR-0020: Single-process UPM Room Agent product and zero-configuration enrollment

- Status: Accepted
- Date: 2026-08-31
- Supersedes: the separate Agent-service/interactive-companion process requirement in ADR-0017 for UPM Room Agent deployments

## Decision

UPM Room Agent is one operator-facing, per-user Windows application and executable.
The WinUI process hosts the durable Agent runtime, loopback HTTP boundary, room UI,
and kiosk UI. Closing the main window hides it to the notification area; explicit
Exit ends the runtime. Logical service/UI boundaries and the Site-mediated control
path remain intact even though deployment no longer requires two Windows processes.
Site Manager remains a separate product and never connects inbound to an Agent.

Site advertises DNS-SD `_upm-site._tcp.local`. The bundled Windows client uses a
signed multicast discovery protocol as its compatible fallback. A short-lived HMAC
discovery ticket authorizes zero-configuration bootstrap.
The first request carries a durable locally generated Agent UUID and machine metadata;
Site exact-matches one unclaimed Device by normalized machine name or creates an
Unassigned Device. Fuzzy matching is prohibited. Site issues a unique persistent
credential, stores only its hash, and remains authoritative for Event, Room, and
product role. Windows protects the credential with DPAPI LocalMachine.

The discovery ticket grants enrollment only. Subsequent configuration, sync,
heartbeat, and media transfer require the Site-issued device credential. Provisioned
Agents continue discovery to recover a changed Site address while retaining cached
offline state.

## Consequences

Room operators launch one application and enter no network addresses, UUIDs, or
tokens. Site and Site Manager expose unassigned devices and assignment controls.
Windows login startup replaces a Session-0 service for this interactive product; the
runtime operates while the tray application is running. Verified local presentation,
original-filename, no-SMB, Site authority, offline autonomy, and durable SQLite queue
rules are unchanged.
