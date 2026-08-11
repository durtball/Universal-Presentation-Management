# UPM Admin Frontend

## Architecture

`web/` is the shared React/TypeScript application source. A compile-time deployment discriminator selects Central or Site routes, while both builds use the same shell, design tokens, feedback surfaces, tables, forms, preferences, and API primitives. It is not a combined backend: Central and Site web images are built, deployed, health-checked, and restarted independently.

Central pages are Login, Dashboard, Sites, Events and event detail, People, Sessions, Presenters, Presentations, Imports, and Room Mapping. Site pages are Overview, local Program, Rooms, and Storage. Site views depend only on Site-local endpoints and remain usable when Central/WAN connectivity is unavailable.

## Design System

Components use semantic tokens from `web/src/styles.css`.

- UPM Glass is the default dark, translucent, softly highlighted theme.
- UPM Classic uses the same markup with dense spacing, flat gray surfaces, square geometry, classic borders, and high contrast.

The Settings dialog switches themes immediately without navigation or reload. `upm.theme` persists in `localStorage` and defaults to `glass`.

Motion is `full`, `reduced`, or `off`. `upm.motion` persists in `localStorage`. A browser/OS reduced-motion preference changes an explicit `full` selection to the effective `reduced` mode. Motion is limited to event-driven transitions, dialogs, feedback, and route entry; there is no perpetual decoration.

Status labels and tones are centralized in `StatusBadge`. Each status includes text and a shape, so meaning does not depend on color.

## API and Session Boundaries

`ApiClient` provides base URL handling, injected headers, JSON parsing, typed structured errors, status classification, timeouts, AbortSignal cancellation, safe GET-only retry, query serialization, and stale-response protection through `useApi`. `centralApi` and `siteApi` expose ownership-specific operations.

Central browser authentication uses the implemented server-issued administrator session. Login credentials are exchanged for an opaque `HttpOnly`, `SameSite=Lax` cookie; unsafe requests include the session's CSRF token. Route guards recover the session from `/api/v1/auth/session`, and logout revokes it in PostgreSQL. The legacy `UPM_CENTRAL_ADMIN_TOKEN` remains a non-browser compatibility credential for automation and integration clients; the React application neither requests nor stores it.

The current authorization slice provides an Administrator role only. `SessionProvider`, `user`, roles, `can()`, unauthorized surfaces, and injected client behavior remain the extension boundary for Operator/restricted roles. Site human authentication is not implemented, and Site endpoints used by this frontend currently require no browser session.

## Route Ownership

For both deployments:

| Route | Owner |
| --- | --- |
| `/health` | local FastAPI service through Caddy |
| `/api/*` | local FastAPI service through Caddy |
| `/`, `/admin`, `/admin/*` | local web service through Caddy |
| `/healthz` inside the container | Nginx frontend health only |

Caddy uses dynamic DNS A-record upstream discovery for web containers, refreshed every five seconds, so container recreation does not leave a stale web address. Nginx uses an SPA fallback for browser navigation and immutable caching only for content-hashed assets.

## Local Development

From `web/`:

```powershell
npm ci
npm run dev:central
# or
npm run dev:site
```

Vite proxies `/health` and `/api` to a local API on port 8080. Production checks are:

```powershell
npm run typecheck
npm run lint
npm run test
npm run build:central
npm run build:site
npm audit --audit-level=high
```

## Production and Smoke Testing

Compose builds the appropriate web image and waits for its independent health check before Caddy starts. Use the existing `.env` deployment configuration; never use `.env.example` for production secrets.

After startup, verify each Caddy port, deep-link navigation, local API data, Glass/Classic runtime switching and persistence, all motion choices, refresh/recreation recovery, and Site pages while Central is stopped or unreachable. HTTP checks do not replace the rendered browser smoke test.

Current limitations: import parsing remains synchronous; multi-role RBAC and Site authentication, worker-specific health endpoints, broad program-editing forms, media file management, and advanced diagnostics remain later milestones.
