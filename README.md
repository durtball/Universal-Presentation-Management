# Universal Presentation Management

Universal Presentation Management (UPM) is a clean rebuild of the earlier SpeakerReady system. It is intended for reliable presentation operations across conferences, hotels, events, meeting rooms, and speaker-ready environments, including periods of degraded or unavailable WAN connectivity.

The repository contains Central/Site persistence, durable jobs, synchronization, Site media ingestion, event/program import and projection, Central administrator authentication, and production browser administration foundations. This is an incremental implementation of a larger fixed target architecture; see the status documents below before assuming a capability is complete.

## System responsibilities

**UPM Central** is the global control plane. It owns global identities, multi-site and cross-event visibility, global configuration, synchronization coordination, aggregate health, and optional Site media replication.

**UPM Site** is the local operational system for a venue or event. It owns local device and room state, authoritative Site media, transfers, processing jobs, kiosks, signage, and event-day operations. A Site must continue operating when Central or the WAN is unavailable and safely synchronize after connectivity returns.

Central and Site are separate Linux/Docker deployments. They have distinct processes, PostgreSQL databases, storage, configuration, networks, and lifecycles. A Site can run either on a standalone Linux appliance or beside Central on the same Linux host; both cases use the same reusable Site Compose definition. Co-location does not create shared state or a special Central-hosted Site implementation.

## Repository structure

```text
central/                 Central API, web, worker, sync, Caddy, and PostgreSQL boundaries
site/                    Site API, web, worker, sync, media, device, Caddy, and PostgreSQL boundaries
web/                     Shared typed browser shell, design system, components, APIs, pages, and tests
clients/                 Windows Agent, Kiosk, Signage, and Room Client roots
shared/                  Contracts, models, schemas, and utilities
database/                Separate Central and Site migration roots
infrastructure/          Central, Site, Caddy, and Docker deployment assets
scripts/                 Developer bootstrap and foundation validation
tests/                   Integration, synchronization, and system test roots
docs/                    Architecture, API, deployment, and development documentation
.github/workflows/       Foundation-level continuous integration
```

Start with the root [AGENTS.md](AGENTS.md). The authoritative specification is [docs/architecture/UPM_MASTER_ARCHITECTURE.md](docs/architecture/UPM_MASTER_ARCHITECTURE.md); read both before substantial implementation work.

Detailed backend decisions and the implemented domain ownership model are in
[docs/architecture/decisions](docs/architecture/decisions) and
[docs/architecture/domain-data-foundation.md](docs/architecture/domain-data-foundation.md).

Project knowledge is organized here:

- [Product Requirements](docs/product/PRODUCT_REQUIREMENTS.md)
- [SpeakerReady Lessons](docs/product/SPEAKERREADY_LESSONS.md)
- [Feature Matrix](docs/product/FEATURE_MATRIX.md)
- [Development Rules](docs/development/DEVELOPMENT_RULES.md)
- [Implementation Status and known gaps](docs/development/IMPLEMENTATION_STATUS.md)
- [Development and setup guide](docs/development/README.md)

The Master Architecture and Product Requirements describe the target. The Feature Matrix and Implementation Status describe repository evidence at a point in time.

## Development and test topology

The intended topology tests Central and standalone Site as independent systems. The development workstation is for builds only. Central runs on a Linux server and may run the unchanged Site stack beside it; a separate machine runs an independent Site; Windows 11 machines exercise Agent, Kiosk, Signage, primary/backup room, interruption, and reconnect scenarios.

## Run the Docker foundation

Requirements are Git, Docker, and Docker Compose v2. On Windows, verify prerequisites and prepare ignored local runtime directories with:

```powershell
./scripts/bootstrap-dev.ps1
```

The checked-in environment file contains placeholders for local scaffolding only. Review it before use, then start either independent stack:

```powershell
docker compose --env-file .env.example -f docker-compose.central.yml up -d
docker compose --env-file .env.example -f docker-compose.site.yml up -d
```

These definitions run independent Central and Site APIs, workers, synchronization services,
production web frontends, PostgreSQL databases, and Caddy edges. Use
distinct project names and host ports when running multiple Site instances.
Never create or commit a real `.env` file.

The browser applications are available through Caddy at `http://localhost:8080/`
for Central and `http://localhost:9080/` for Site. API health is available at
`/health`. See the
[development guide](docs/development/README.md) for build, startup, health, and
shutdown commands.

The Site media bind mount is configured through `SITE_MEDIA_HOST_PATH`; the
container-visible path is configured separately through
`UPM_SITE_MEDIA_MOUNT_PATH`. The host remains responsible for the filesystem.

## Python development

Install Python 3.13 through `uv`, then synchronize the committed workspace lock:

```powershell
uv python install 3.13
uv sync --locked --all-packages
```

Central and Site are separate FastAPI applications backed by separate
PostgreSQL databases and Alembic histories. See the
[development guide](docs/development/README.md) for validation and migration
commands.

Stop the stacks independently:

```powershell
docker compose --env-file .env.example -f docker-compose.central.yml down
docker compose --env-file .env.example -f docker-compose.site.yml down
```

Named volumes are intentionally retained by these commands. Add `--volumes` only when deliberately discarding local data.

## Validation

Run the safe repository checks with:

```powershell
./scripts/validate-foundation.ps1
docker compose --env-file .env.example -f docker-compose.central.yml config --quiet
docker compose --env-file .env.example -f docker-compose.site.yml config --quiet
```

Production credentials and certificate issuance policy remain deployment-owned concerns. See the [frontend guide](docs/development/frontend.md) for browser development and validation.
