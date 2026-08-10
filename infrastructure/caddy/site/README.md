# Site Caddy Configuration

Site Caddy is the reusable Site edge. `/health`, `/api/health`, and `/api/*` route to `site-api`; `/`, `/admin`, and all other browser routes go to `site-web`. The web upstream uses dynamic A-record resolution so Site web-container recreation is recoverable without changing Caddy configuration.

TLS and hostname policy remain Site deployment configuration. Site browser operations use Site-local APIs and do not route through Central.
