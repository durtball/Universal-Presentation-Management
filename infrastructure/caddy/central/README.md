# Central Caddy Configuration

Central Caddy is the deployment edge. `/health` and `/api/*` route to `central-api`; `/`, `/admin`, and all other browser routes go to `central-web`. The web upstream uses Caddy's dynamic A-record resolver with a five-second refresh so container recreation does not retain a stale address.

TLS and hostname policy remain deployment configuration. Certificate-management behavior is not distributed into application services.
