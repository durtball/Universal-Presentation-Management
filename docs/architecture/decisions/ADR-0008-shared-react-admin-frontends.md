# ADR-0008: Shared React Admin Frontends with Separate Production Images

**Status:** Accepted
**Date:** 2026-08-10

## Context

UPM Central and UPM Site require permanent browser interfaces, but they remain separate systems with separate deployment, API, database, configuration, storage, and lifecycle boundaries. UPM Glass and UPM Classic must share behavior and markup. The repository had inert `central-web` and `site-web` service scaffolds and no established frontend framework.

## Decision

Use one React 19 and TypeScript source tree in `web/`, built with Vite into two deployment-targeted static applications.

- `central/web/Dockerfile` runs the Central build.
- `site/web/Dockerfile` runs the Site build.
- Each production image serves static assets through an unprivileged Nginx process.
- Central and Site use independent Compose services and images; no runtime or database state is shared.
- Client-side routes live under `/admin`. Caddy owns `/health` and `/api/*` routing to the local API and sends application routes to the local web service.
- Semantic CSS tokens implement Glass and Classic without branching component markup.
- Local preferences persist in browser `localStorage`; the temporary Central administrator token is accepted at runtime and held only in tab-scoped `sessionStorage`.

The API layer owns HTTP parsing, timeouts, cancellation, safe GET retry, structured errors, headers, and future authentication hooks. Pages do not call `fetch` directly.

## Consequences

The component, routing, theme, motion, table, error, and session conventions can be extended without replacing the shell. Central and Site remain separately deployable and Site pages call only Site-local APIs. The production image contains no development server and no UPM secret.

Full authentication and RBAC remain deferred. Replacing the temporary token provider with a server-issued session does not require page or API-client redesign.
