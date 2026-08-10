# Docker Infrastructure

Central and Site use separate production-oriented API Dockerfiles at `central/Dockerfile`
and `site/Dockerfile`, plus separate frontend Dockerfiles under `central/web/` and
`site/web/`. API images build from Python 3.13 and the committed uv workspace lock.
Web images use deterministic Node builds and serve static output from unprivileged Nginx.

The Compose projects retain separate images, networks, volumes, configuration,
PostgreSQL services, and lifecycles. API, worker, synchronization, and web roles remain
explicit independently observable service boundaries.
