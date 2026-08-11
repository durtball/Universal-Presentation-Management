# Development Documentation

- [Development rules](DEVELOPMENT_RULES.md)
- [Current implementation status and known gaps](IMPLEMENTATION_STATUS.md)
- [Central Admin authentication and functional program import](admin-functional-program-import.md)

See [UPM Admin frontend](frontend.md) for browser architecture, design tokens, local development, production builds, tests, routing, and smoke testing.

UPM server development uses Python 3.13 and
[uv](https://docs.astral.sh/uv/) without global application dependency
installation.

```powershell
uv python install 3.13
uv sync --locked --all-packages
uv run ruff check .
uv run ruff format --check .
uv run pytest -m "not postgres"
```

PostgreSQL-backed migration and constraint tests require independent Central
and Site PostgreSQL databases. Configure them with
`UPM_CENTRAL_DATABASE_URL` and `UPM_SITE_DATABASE_URL`; never point both values
at the same database. Apply migrations independently:

```powershell
uv run alembic -c database/central/alembic.ini upgrade head
uv run alembic -c database/site/alembic.ini upgrade head
```

The root `pyproject.toml` owns development tools. `shared/python`,
`central/python`, and `site/python` remain separate workspace packages. See
`docs/architecture/domain-data-foundation.md` for ownership and data-model
details.

See [Central/Site registration and synchronization](central-site-synchronization.md) for
enrollment, bidirectional synchronization, and failure-recovery procedures.

## Independent Docker API services

Copy `.env.example` to an ignored local `.env` and replace every placeholder
password before using it outside disposable local development. Never commit the
local file. Build the API images independently from the repository root:

```powershell
docker compose --env-file .env -f docker-compose.central.yml build central-api
docker compose --env-file .env -f docker-compose.site.yml build site-api
```

Start either deployment without starting the other:

```powershell
docker compose --env-file .env -f docker-compose.central.yml up -d
docker compose --env-file .env -f docker-compose.site.yml up -d
```

Each Compose deployment first waits for its own PostgreSQL health check, then runs its
one-shot migration service (`central-migrate` or `site-migrate`). The API, worker, and
synchronization services start only after `alembic upgrade head` exits successfully. A migration
failure makes Compose fail and leaves dependent services stopped. Repeating `up` safely applies
only migrations that are not already recorded in that deployment's Alembic version table.

The application images contain their corresponding migration directory. Production and test
execution must not bind-mount `database/` from a Git working tree.

Check container state and the existing API health endpoints through each
deployment's Caddy edge:

```powershell
docker compose --env-file .env -f docker-compose.central.yml ps
Invoke-RestMethod http://localhost:8080/api/health

docker compose --env-file .env -f docker-compose.site.yml ps
Invoke-RestMethod http://localhost:9080/api/health
```

The responses identify `upm-central` and `upm-site` respectively. Shut down the
deployments independently; named PostgreSQL and Caddy volumes are retained:

```powershell
docker compose --env-file .env -f docker-compose.central.yml down
docker compose --env-file .env -f docker-compose.site.yml down
```

The API containers run as non-root users. Central and Site use distinct images,
configuration variables, PostgreSQL services, networks, and deployment
lifecycles. Central migration code cannot reach the Site database and Site migration code cannot
reach the Central database. See [ADR-0005](../architecture/decisions/ADR-0005-container-migration-gates.md)
for deployment ordering and failure semantics.

On Linux with Docker available, the isolated fresh-migration, repeated-migration, API
failure-gate, and database-separation smoke test is:

```bash
./scripts/test-compose-migrations.sh
```

The smoke test creates uniquely named disposable Compose projects and removes only those test
projects and their volumes when it exits. It starts each API with only its PostgreSQL and
migration dependencies; it does not require the web, proxy, worker, or sync services.
