# ADR-0021: Site-scoped automatic Signage enrollment and LAN discovery

- **Status:** Accepted
- **Date:** 2026-09-11

## Context

Signage is an independent Site-local subsystem, but requiring an operator to copy matching bearer
credentials and an Event UUID into two deployments violates UPM's integrated-product model and
causes unsafe operational workarounds. Signage must retain its own PostgreSQL database and offline
playback while Site remains the authority for local event/program data.

## Decision

A co-located Site and Signage deployment share only a deployment-generated, permission-restricted,
one-time enrollment proof file. It is runtime state and is never committed. Signage persists a
UUID installation identity, presents it to Site, and receives a unique scoped projection credential.
Site stores only its SHA-256 verifier in a revocable `signage_installations` record. Signage encrypts
the credential at rest using a key derived from the enrollment proof. Re-enrollment proves either
the current credential or the co-located enrollment proof and rotates the credential, immediately
invalidating the previous value. Revoked installations cannot enroll or read projections.

The Site service contract enumerates the complete local Event catalog. The Signage worker uses the
scoped credential to synchronize each Event into Signage-local projections; a configured Event ID
is retained only as an explicit legacy fixed-event filter.

Signage advertises a friendly HTTPS endpoint over the existing UPM multicast discovery family on a
distinct protocol/port. Advertisements contain product, friendly Site name, hostname, endpoint,
installation identity, and version. This discovery path does not depend on WSDD. Internal IDs are
transport identity and are not primary operator labels.

## Consequences

Normal co-located startup requires no matching environment tokens or manually entered Event UUID.
Site and Signage databases and lifecycles remain separate. The enrollment proof file must remain
host-local and permission restricted. Separately hosted deployments may use an administrator-
approved/bootstrap enrollment proof or the legacy advanced credential configuration. Caddy remains
the TLS boundary, and players retain independent per-display credentials.
