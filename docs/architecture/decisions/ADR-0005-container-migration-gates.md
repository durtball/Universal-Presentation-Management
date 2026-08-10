# ADR-0005: Container Migration Gates

- **Status:** Accepted
- **Date:** 2026-08-10
- **Scope:** Central and Site container deployment lifecycle

## Context

Central and Site workers require their deployment-owned PostgreSQL schema before they can
register identities or claim durable jobs. Starting long-running services immediately after
PostgreSQL becomes reachable creates a race: PostgreSQL can be healthy while the application
schema is absent or behind the application version. Running Alembic manually from a Git working
tree is not a repeatable production deployment mechanism and allows application code and
migration code to differ.

## Decision

Central and Site each have a dedicated one-shot Docker Compose migration service. Each migration
service:

- uses the exact application image selected for that deployment;
- contains only its deployment's Alembic configuration and revision graph;
- connects only to its deployment-owned PostgreSQL service and internal network;
- waits for PostgreSQL health, runs `alembic upgrade head`, and exits;
- has no continuous restart policy; and
- gates the API, worker, and synchronization services through Compose's
  `service_completed_successfully` dependency condition.

Central uses `central-migrate` with `database/central/alembic.ini`. Site independently uses
`site-migrate` with `database/site/alembic.ini`. Neither image contains the other deployment's
migration tree. Caddy waits for the applicable API health check before becoming a dependency-ready
edge service.

The production deployment command explicitly recreates the one-shot migration service before it
updates long-running services, so migration runs on every controlled deployment without forcing
PostgreSQL recreation. Alembic's `upgrade head` behavior makes an already-current database a
successful no-op. Long-running services retain `unless-stopped` restart policies.

## Failure semantics

A nonzero migration exit prevents new API, worker, and synchronization containers from starting
and causes the deployment command to fail. During an update, already-running containers remain on
their previous image while the failed migration is investigated. Operators inspect the migration
logs, correct the cause, restore a compatible database backup when necessary, and retry.
Deployment automation does not delete or recreate PostgreSQL or media volumes to recover from a
migration failure.

Application rollback does not imply schema downgrade. Before deploying a revision with migrations,
operators take a PostgreSQL backup and review forward/backward compatibility. If an older
application cannot run against the upgraded schema, rollback requires the release-specific,
tested database recovery procedure rather than an automatic generic Alembic downgrade.

## Consequences

- Workers cannot start against an unmigrated schema through the Compose lifecycle.
- Migration code and application code are version-aligned and do not depend on a host checkout
  bind mount.
- Central and Site migration histories, networks, databases, and deployment failures remain
  isolated.
- A migration failure is visible and intentionally blocks the affected deployment.
- Operators must continue to treat database backup and migration compatibility as release work.
